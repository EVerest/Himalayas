#!/usr/bin/env python3
"""Copy new records from bound source repositories into pull requests.

    intake_sources.py plan  --out plan.json     # GITHUB_TOKEN, read only
    intake_sources.py apply --plan plan.json    # APP_TOKEN, himalayas-accessor

PLAN reads every source bound in integrators/allowlist.yaml by its numeric
repository id and lists the files under its `path` that would be a valid
results/<id>/<pointer>/<file> path carrying the source's `label`. A file is new
unless that path is already on main or a branch intake/<id>/<pointer>/<file>
with a pull request exists. That branch is the ledger: it exists while the pull
request is open, and it is kept when a rejected pull request is closed, so a
rejected record is not retried every run. A record that was rejected is fixed by
publishing it again under a new name, as results/ is append-only anyway. A
branch left without a pull request, by a run that died between the two, is
taken again.

APPLY opens one pull request per record, from a branch holding exactly that
file, so one bad record fails alone and its verdict is the comment on its own
pull request. It writes nothing but intake/ branches and pull requests, and
merges nothing: the gate in validate-submission.yml re-reads the source and
arms auto-merge as it does for any other submission. It needs only the
standard library, so the job holding the App's token installs nothing.

One source failing does not stop the others; the run fails at the end instead.
"""
import argparse, base64, json, os, sys, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sources                                                 # noqa: E402
from result_path import MAX_FILE_BYTES, RESULT_PATH_RE         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Each record costs four writes, and GitHub's secondary rate limit allows about
# 80 a minute. A backlog is drained over several runs instead.
MAX_PER_RUN = 15


def load_allowlist(root):
    import yaml
    with open(os.path.join(root, "integrators", "allowlist.yaml")) as f:
        data = yaml.safe_load(f) or {}
    return {e["id"]: e for e in (data.get("integrators") or [])
            if isinstance(e, dict) and "id" in e}


def branch_for(dest):
    """results/<id>/<pointer>/<file> -> intake/<id>/<pointer>/<file>."""
    return sources.INTAKE_BRANCH_PREFIX + dest[len("results/"):]


def candidates(iid, src, tree_paths):
    """(source path, results path) for every file under the source's path that
    would be a valid record path for this id, with a label the source admits."""
    out = []
    for p in tree_paths:
        if not p.startswith(src["path"]):
            continue
        dest = f"results/{iid}/{p[len(src['path']):]}"
        m = RESULT_PATH_RE.match(dest)
        if m and sources.label_admits(src, m.group("label")):
            out.append((p, dest))
    return out


def new_records(iid, src, tree_paths, on_main, branches):
    return [(p, d) for p, d in candidates(iid, src, tree_paths)
            if d not in on_main and branch_for(d) not in branches]


def source_tree(src, token):
    """The commit the branch points at, and the size of every blob in its tree."""
    rid = src["repo_id"]
    name = sources.api_json(f"/repositories/{rid}", token).get("full_name", "")
    if name.lower() != src["repo"].lower():
        print(f"::warning::repo_id {rid} is now {name}, not {src['repo']}; "
              "review the binding if it was transferred")
    q = urllib.parse.quote(src["ref"], safe="/")
    commit = sources.api_json(f"/repositories/{rid}/branches/{q}", token)["commit"]["sha"]
    tree = sources.api_json(f"/repositories/{rid}/git/trees/{commit}?recursive=1", token)
    if tree.get("truncated"):
        raise sources.SourceUnreachable(f"{src['repo']}: tree too large to list in one call")
    return commit, {t["path"]: t.get("size", 0)
                    for t in tree.get("tree") or [] if t.get("type") == "blob"}


def intake_branches(repo, token):
    refs = sources.api_list(
        f"/repos/{repo}/git/matching-refs/heads/{sources.INTAKE_BRANCH_PREFIX}?per_page=100",
        token)
    return {r["ref"][len("refs/heads/"):] for r in refs}


def has_pull_request(repo, branch, token):
    owner = repo.split("/")[0]
    head = urllib.parse.quote(f"{owner}:{branch}", safe="")
    return bool(sources.api_json(f"/repos/{repo}/pulls?head={head}&state=all", token))


def plan(args):
    token = os.environ.get("GITHUB_TOKEN")
    allow = load_allowlist(ROOT)
    problems = sources.allowlist_problems(allow)
    if problems:
        print("allowlist sources are malformed:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    try:
        branches = intake_branches(args.repo, token)
    except sources.SourceUnreachable as e:
        print(f"::error::cannot list intake branches: {e}")
        return 2
    items, failed, seen, deferred = [], [], set(), 0
    for iid, entry in sorted(allow.items()):
        srcs, _ = sources.entry_sources(entry)
        for src in srcs:
            try:
                commit, sizes = source_tree(src, token)
                found = candidates(iid, src, sizes)
                on_main = {d for _, d in found if os.path.exists(os.path.join(ROOT, d))}
                stranded = {branch_for(d) for _, d in found
                            if d not in on_main and branch_for(d) in branches
                            and not has_pull_request(args.repo, branch_for(d), token)}
            except sources.SourceUnreachable as e:
                failed.append(str(e))
                continue
            pinned = dict(src, ref=commit)
            for p, d in new_records(iid, src, sizes, on_main, branches - stranded):
                if d in seen:
                    continue                    # two sources of one id hold the same file
                seen.add(d)
                if sizes[p] > MAX_FILE_BYTES:
                    failed.append(f"{src['repo']}:{p} is {sizes[p]:,} bytes, over the "
                                  f"{MAX_FILE_BYTES:,}-byte cap; it is not taken")
                    continue
                if len(items) == MAX_PER_RUN:
                    deferred += 1
                    continue
                # Read here, at the listed commit, with the workflow's own token:
                # the App's token is scoped to this repository.
                try:
                    data = sources.fetch_file(pinned, p, token)
                except sources.SourceUnreachable as e:
                    failed.append(str(e))
                    continue
                if data is None:
                    failed.append(f"{src['repo']}:{p} listed but not readable at {commit}")
                    continue
                items.append({"id": iid, "repo": src["repo"], "commit": commit,
                              "source_path": p, "dest": d, "branch": branch_for(d),
                              "resume": branch_for(d) in stranded,
                              "content": base64.b64encode(data).decode()})
                print(f"new   {src['repo']}:{p} -> {d}")
    with open(args.out, "w") as f:
        json.dump(items, f, indent=2)
    if deferred:
        print(f"::notice::{deferred} more new record(s) left for the next run")
    for e in failed:
        print(f"::error::{e}")
    return 1 if failed else 0


def call(method, path, token, body=None):
    raw = sources.request(method, path, token, body)[0]
    return json.loads(raw) if raw else None


def open_one(item, repo, base_sha, token):
    branch = item["branch"]
    # This App can bypass main's ruleset, so where it writes is checked here
    # rather than trusted from the plan.
    if not branch.startswith(sources.INTAKE_BRANCH_PREFIX) or ".." in branch:
        raise ValueError(f"refusing to write outside {sources.INTAKE_BRANCH_PREFIX}: {branch}")
    ref = f"/repos/{repo}/git/refs/heads/{urllib.parse.quote(branch)}"
    if item.get("resume"):
        call("DELETE", ref, token)              # a branch a dead run left with no pull request
    call("POST", f"/repos/{repo}/git/refs", token,
         {"ref": f"refs/heads/{branch}", "sha": base_sha})
    try:
        call("PUT", f"/repos/{repo}/contents/{urllib.parse.quote(item['dest'])}", token,
             {"message": f"results: {item['id']} {item['dest'].split('/', 2)[2]}",
              "content": item["content"], "branch": branch})
        blob = (f"https://github.com/{item['repo']}/blob/{item['commit']}/"
                f"{urllib.parse.quote(item['source_path'])}")
        pr = call("POST", f"/repos/{repo}/pulls", token, {
            "title": f"intake: {item['id']} {item['dest'].split('/', 2)[2]}",
            "head": branch, "base": "main",
            "body": (f"Copied by the source intake from [`{item['repo']}`]({blob}) at "
                     f"`{item['commit']}`, for `{item['id']}`.\n\n"
                     "The gate re-reads the source and checks this file is byte-identical "
                     "before accepting it. See CONTRIBUTING.md, Setting up a source "
                     "repository.")})
    except Exception:
        try:
            call("DELETE", ref, token)
        except Exception as e:                                 # noqa: BLE001
            print(f"::warning::{branch} left without a pull request; the next run "
                  f"takes it again ({e})")
        raise
    call("POST", f"/repos/{repo}/issues/{pr['number']}/labels", token,
         {"labels": [f"intake:{item['id']}"]})
    return pr["html_url"]


def apply(args):
    token = os.environ["APP_TOKEN"]
    with open(args.plan) as f:
        items = json.load(f)
    base_sha = call("GET", f"/repos/{args.repo}/git/ref/heads/main", token)["object"]["sha"]
    failed = 0
    for n, item in enumerate(items):
        try:
            print(f"opened {open_one(item, args.repo, base_sha, token)}")
        except Exception as e:                                 # noqa: BLE001
            failed += 1
            print(f"::error::{item['dest']}: {e}")
            # Rate limited: every later call would fail too, and fail to clean up.
            if isinstance(e, sources.ApiError) and e.code in (403, 429):
                print(f"::notice::stopped; {len(items) - n - 1} record(s) left for the next run")
                break
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--out", required=True)
    a = sub.add_parser("apply")
    a.add_argument("--plan", required=True)
    for x in (p, a):
        x.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "EVerest/Himalayas"))
    args = ap.parse_args()
    return plan(args) if args.cmd == "plan" else apply(args)


if __name__ == "__main__":
    sys.exit(main())
