"""Source repositories: the second way an integrator id is published.

An allowlist entry may bind its id to public repositories it controls, next to or
instead of `github_users`. The intake workflow copies each new record from a
source onto a branch here and opens a pull request as himalayas-accessor[bot];
the gate then checks the file is byte-identical to the same path in a source
bound to the record's id. Identity is who can push to that repository, which
GitHub enforces, and binding it is a maintainer-approved allowlist change.

A source is read by its numeric repository id, never by name, so a renamed or
deleted repository whose name someone else takes cannot inherit the binding. The
id does follow a transfer to another owner, so a transfer is a reason to review
the binding; the intake warns when `repo` no longer matches the id.

A source is one of two kinds. A `branch` source holds records as files on a
branch. A `workflow-run` source is a repository whose CI writes one record per
pointer move and uploads it as an artifact; which records it owes, and where
each lands, is derived here from pointers.json, never from the run, so a run
cannot choose its own date.
"""
import datetime, io, json, os, re, urllib.error, urllib.parse, urllib.request, zipfile

from result_path import MAX_FILE_BYTES

# himalayas-accessor[bot]. Bound by user id: the App was renamed once
# (himalaya-accessor), and a login is only a name. The same literal sits in
# validate-submission.yml, which closes this bot's rejected pull requests; the
# self-test asserts the two agree.
INTAKE_BOT_ID = 332461223
INTAKE_BRANCH_PREFIX = "intake/"

LABEL_RE = re.compile(r"[a-z][a-z0-9]{0,15}(?:-[a-z][a-z0-9]{0,15}){0,3}")
REF_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")
# Names the contents API would resolve to something other than a branch: a pull
# request's head is writable by anyone who opens one.
NOT_A_BRANCH_RE = re.compile(r"refs/.*|pull/.*|[0-9a-f]{40}|.*\.\..*|.*/|/.*")
DIR_RE = re.compile(r"(?:[A-Za-z0-9_-][A-Za-z0-9._-]*/)*")
REPO_RE = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
WORKFLOW_RE = re.compile(r"\.github/workflows/[A-Za-z0-9_-][A-Za-z0-9._-]*\.ya?ml")
ARTIFACT_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,99}")

RUN_KIND = "workflow-run"
# GitHub keeps an artifact 90 days at most, so an older move can no longer be
# copied and is not reported missing either.
RETENTION = datetime.timedelta(days=90)


class SourceUnreachable(Exception):
    """A source could not be read for a reason other than the file being absent."""


def _is_branch(name):
    return isinstance(name, str) and bool(REF_RE.fullmatch(name)) \
        and not NOT_A_BRANCH_RE.fullmatch(name)


def _repo_problems(where, s):
    out = []
    if not isinstance(s.get("repo"), str) or not REPO_RE.fullmatch(s["repo"]):
        out.append(f"{where}.repo must be `owner/name`")
    rid = s.get("repo_id")
    if not isinstance(rid, int) or isinstance(rid, bool) or rid <= 0:
        out.append(f"{where}.repo_id must be the repository's numeric id")
    return out


def _branch_source(where, s):
    problems = _repo_problems(where, s)
    path, label = s.get("path", ""), s.get("label")
    if not _is_branch(s.get("ref")):
        problems.append(f"{where}.ref must be a branch name")
    if not isinstance(path, str) or not DIR_RE.fullmatch(path):
        problems.append(f"{where}.path must be empty or a directory ending in `/`")
    if label is not None and (not isinstance(label, str) or not LABEL_RE.fullmatch(label)):
        problems.append(f"{where}.label must be a filename label such as `octt`")
    return {"kind": "branch", "repo": s.get("repo"), "repo_id": s.get("repo_id"),
            "ref": s.get("ref"), "path": path, "label": label}, problems


def _run_source(where, s):
    problems = _repo_problems(where, s)
    if not isinstance(s.get("workflow"), str) or not WORKFLOW_RE.fullmatch(s["workflow"]):
        problems.append(f"{where}.workflow must be `.github/workflows/<file>.yml`")
    if not _is_branch(s.get("branch")):
        problems.append(f"{where}.branch must be a branch name")
    if not isinstance(s.get("artifact"), str) or not ARTIFACT_RE.fullmatch(s["artifact"]):
        problems.append(f"{where}.artifact must be an artifact name prefix")
    # YAML reads an unquoted date as a date, a quoted one as a string.
    since = s.get("since")
    if isinstance(since, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        try:
            since = datetime.date.fromisoformat(since)
        except ValueError:
            pass
    if not isinstance(since, datetime.date) or isinstance(since, datetime.datetime):
        problems.append(f"{where}.since must be a date, `YYYY-MM-DD`")
    return {"kind": RUN_KIND, "repo": s.get("repo"), "repo_id": s.get("repo_id"),
            "workflow": s.get("workflow"), "branch": s.get("branch"),
            "artifact": s.get("artifact"), "since": since, "label": None}, problems


KINDS = {"branch": (_branch_source, {"kind", "repo", "repo_id", "ref", "path", "label"}),
         RUN_KIND: (_run_source, {"kind", "repo", "repo_id", "workflow", "branch",
                                  "artifact", "since"})}


def entry_sources(entry):
    """The validated sources of one allowlist entry, and the problems that stop it.

    A malformed source is a maintainer's mistake in the allowlist, not the
    integrator's, so callers treat any problem as a tool error.
    """
    raw = entry.get("sources")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["`sources` is not a list"]
    out, problems, labels = [], [], set()
    for i, s in enumerate(raw):
        where = f"`sources[{i}]`"
        if not isinstance(s, dict):
            problems.append(f"{where} is not a mapping")
            continue
        kind = s.get("kind", "branch")
        if kind not in KINDS:
            problems.append(f"{where}.kind must be one of {sorted(KINDS)}")
            continue
        check, keys = KINDS[kind]
        src, bad = check(where, s)
        unknown = set(s) - keys
        if unknown:
            bad.insert(0, f"{where} has unknown key(s) {sorted(unknown)}")
        label = src["label"]
        if not bad and label is not None:
            if label in labels:
                bad.append(f"{where}.label `{label}` is declared twice")
            labels.add(label)
        problems += bad
        if not bad:
            out.append(src)
    # An unlabeled branch source would admit the names a run source owes, and
    # the gate takes any source that holds the bytes.
    if sum(s["kind"] == RUN_KIND for s in out) > 1:
        problems.append(f"an entry may have one `{RUN_KIND}` source: they would owe the same names")
    if any(s["kind"] == RUN_KIND for s in out):
        problems += [f"a branch source beside a `{RUN_KIND}` source needs a `label`: "
                     f"`{s['repo']}` has none" for s in out
                     if s["kind"] == "branch" and s["label"] is None]
    return out, problems


def allowlist_problems(allow):
    """Every structural problem with sources across the whole allowlist."""
    problems, owner = [], {}
    for iid, entry in allow.items():
        srcs, bad = entry_sources(entry)
        problems += [f"`{iid}`: {p}" for p in bad]
        for s in srcs:
            rid = s["repo_id"]
            if rid in owner and owner[rid] != iid:
                problems.append(f"repo_id {rid} is bound to both `{owner[rid]}` and `{iid}`")
            owner.setdefault(rid, iid)
    return problems


def label_admits(src, label):
    """Whether a record with this filename label may come from this source. A
    workflow-run source's names are derived, and carry no label."""
    if src.get("kind") == RUN_KIND:
        return label is None
    want = src.get("label")
    return want is None or (label is not None and (label == want or label.startswith(want + "-")))


def reserved_label(srcs, label):
    """The source that reserves this filename label, if any. A record from any
    other route may not use it. A workflow-run source reserves the names with no
    label: they are derived, and one taken first would stand in for its record."""
    for s in srcs:
        if (s.get("label") or s.get("kind") == RUN_KIND) and label_admits(s, label):
            return s
    return None


def source_path(src, pointer, filename):
    return f"{src['path']}{pointer}/{filename}"


def moved_at(entry):
    return datetime.datetime.strptime(entry["moved_at"], "%Y-%m-%dT%H:%M:%SZ") \
        .replace(tzinfo=datetime.timezone.utc)


def expected_records(iid, log, since=None, now=None):
    """The record a workflow-run source owes for each move in the pointer log.

    Each lands at <pointer>/<YYYYMMDD>[-N].json, dated by `moved_at` in UTC; N
    counts that pointer's moves that UTC day in log order, the first having no
    suffix. A move before `since`, or past artifact retention at `now`, owes
    nothing, but still counts toward N.
    """
    out, nth = [], {}
    for e in log:
        at = moved_at(e)
        key = (e["pointer"], at.date())
        nth[key] = n = nth.get(key, 0) + 1
        if (since and at.date() < since) or (now and now - at > RETENTION):
            continue
        name = at.strftime("%Y%m%d") + ("" if n == 1 else f"-{n}") + ".json"
        path = f"{e['pointer']}/{name}"
        out.append({"pointer": e["pointer"], "sha": e["sha"], "moved_at": at,
                    "path": path, "dest": f"results/{iid}/{path}"})
    return out


def artifact_name(prefix, pointer, sha):
    return f"{prefix}_{pointer.replace('/', '-')}_{sha}"


def run_admits(src, run):
    """Whether a run may publish for the source: a successful manual dispatch of
    its workflow on its branch, in its repository. A pull request's run, or any
    other event, can be started by someone who cannot push there."""
    return (run.get("path") == src["workflow"] and run.get("head_branch") == src["branch"]
            and run.get("event") == "workflow_dispatch" and run.get("conclusion") == "success"
            and (run.get("repository") or {}).get("id") == src["repo_id"])


def _one_file(blob, inner):
    """The bytes of `inner`, or why the zip is refused: it must hold that file alone."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        files = [i for i in z.infolist() if not i.is_dir()]
    except zipfile.BadZipFile:
        return None, "is not a zip"
    if [i.filename for i in files] != [inner]:
        held = ", ".join(f"`{n.replace('`', '')}`" for n in sorted(i.filename for i in files))
        return None, f"holds [{held}], not `{inner}` alone"
    if files[0].file_size > MAX_FILE_BYTES:
        return None, f"holds {files[0].file_size:,} bytes, over the {MAX_FILE_BYTES:,}-byte cap"
    return z.read(files[0]), None


def fetch_artifact_file(src, name, inner, token, fixtures=None):
    """([(run, bytes)], [refusals]) for every unexpired artifact `name` in the
    source's repository, oldest first, uploaded by a run the source admits and
    holding exactly the file `inner`.

    A run's head_branch is only a ref name, which a tag can share, so its commit
    must also be on the branch: a commit there ran the branch's workflow file.

    `fixtures` stands in for GitHub as in fetch_file, laid out as
    <repo_id>/artifacts/<name>/<run id>.json (the run, `artifact_expired` set if
    the artifact is), <run id>.zip, and <repo_id>/commits/<branch> listing the
    branch's commits.
    """
    found, refused = [], []
    for run, read, expired in _artifact_runs(src, name, token, fixtures):
        if not run_admits(src, run):
            continue
        if expired:
            refused.append(f"`{name}` from run {run['id']} expired")
            continue
        if not on_branch(src, run.get("head_sha"), token, fixtures):
            refused.append(f"`{name}` from run {run['id']} is at `{run.get('head_sha')}`, "
                           f"not on `{src['branch']}`")
            continue
        try:
            blob = read()
        except SourceUnreachable as e:
            raise SourceUnreachable(f"{src['repo']} ({src['repo_id']}): `{name}`: {e}") from e
        data, why = (None, f"is over {2 * MAX_FILE_BYTES:,} bytes") if blob is None \
            else _one_file(blob, inner)
        if why:
            refused.append(f"`{name}` from run {run['id']} {why}")
        else:
            found.append((run, data))
    return found, refused


def on_branch(src, sha, token, fixtures=None):
    """Whether the commit is on the source's branch."""
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return False
    if fixtures:
        p = os.path.join(fixtures, str(src["repo_id"]), "commits", src["branch"])
        if not os.path.isfile(p):
            return False
        with open(p) as f:
            return sha in f.read().split()
    # Compare commits, not names: a bare name in compare could resolve to a tag.
    q = urllib.parse.quote(src["branch"], safe="/")
    rid = src["repo_id"]
    try:
        tip = api_json(f"/repositories/{rid}/branches/{q}", token)["commit"]["sha"]
        cmp = api_json(f"/repositories/{rid}/compare/{sha}...{tip}?per_page=1", token)
    except SourceUnreachable as e:
        raise SourceUnreachable(f"{src['repo']} ({src['repo_id']}): {e}") from e
    return cmp.get("status") in ("ahead", "identical")


def _artifact_runs(src, name, token, fixtures):
    """(run, read, expired) per artifact, oldest first; read() is its zip, or None
    if too big."""
    rid, limit = src["repo_id"], 2 * MAX_FILE_BYTES
    if fixtures:
        down = os.path.join(fixtures, str(rid))
        if os.path.isfile(down):
            raise SourceUnreachable(f"{src['repo']} ({rid}): fixture unreachable")
        d = os.path.join(down, "artifacts", name)
        ids = sorted(int(f[:-5]) for f in os.listdir(d) if f.endswith(".json")) \
            if os.path.isdir(d) else []
        for i in ids:
            with open(os.path.join(d, f"{i}.json")) as f:
                run = json.load(f)
            with open(os.path.join(d, f"{i}.zip"), "rb") as f:
                blob = f.read()
            yield (run, lambda blob=blob: blob if len(blob) <= limit else None,
                   run.pop("artifact_expired", False))
        return
    q = urllib.parse.quote(name, safe="")
    try:
        arts = api_list(f"/repositories/{rid}/actions/artifacts?name={q}&per_page=100",
                        token, key="artifacts")
        for a in sorted(arts, key=lambda a: a["id"]):
            run_id = (a.get("workflow_run") or {}).get("id")
            if not isinstance(run_id, int):
                continue                         # no run to admit it
            run = api_json(f"/repositories/{rid}/actions/runs/{run_id}", token)
            yield (run, lambda a=a: None if a.get("size_in_bytes", 0) > limit else download(
                f"/repositories/{rid}/actions/artifacts/{a['id']}/zip", token, limit),
                   bool(a.get("expired")))
    except SourceUnreachable as e:
        raise SourceUnreachable(f"{src['repo']} ({rid}): {e}") from e


API = "https://api.github.com"


class ApiError(Exception):
    """An HTTP error from GitHub, with the response body GitHub explains it in."""

    def __init__(self, method, path, code, detail):
        super().__init__(f"{method} {path}: HTTP {code} {detail}".rstrip())
        self.code = code


def request(method, path, token, body=None, accept="application/vnd.github+json"):
    """(body bytes, response headers) of one GitHub API call."""
    req = urllib.request.Request(API + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Accept": accept,
                                          "User-Agent": "everest-himalayas-intake"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(), r.headers
    except urllib.error.HTTPError as e:
        detail = e.read(500).decode("utf-8", "replace")
        raise ApiError(method, path, e.code, detail) from e


def api_json(path, token):
    try:
        return json.loads(request("GET", path, token)[0])
    except Exception as e:                                     # noqa: BLE001
        raise SourceUnreachable(str(e)) from e


def api_list(path, token, key=None):
    """Every item of a list endpoint, following its `Link: rel="next"` pages. `key`
    names the list in an endpoint that wraps it in an object."""
    items = []
    while path:
        try:
            raw, headers = request("GET", path, token)
            page = json.loads(raw)
            items += page[key] if key else page
        except Exception as e:                                 # noqa: BLE001
            raise SourceUnreachable(str(e)) from e
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link") or "")
        path = nxt.group(1)[len(API):] if nxt and nxt.group(1).startswith(API) else None
    return items


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def download(path, token, limit):
    """The bytes behind an endpoint that redirects to pre-signed storage, or None
    if over `limit`. The redirect is followed without the token, which is
    GitHub's and must not reach another host."""
    req = urllib.request.Request(API + path, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "everest-himalayas-intake",
        **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        try:
            with urllib.request.build_opener(_NoRedirect).open(req, timeout=30) as r:
                data = r.read(limit + 1)
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
                raise ApiError("GET", path, e.code, e.read(500).decode("utf-8", "replace")) from e
            if urllib.parse.urlsplit(e.headers["Location"]).scheme != "https":
                raise ApiError("GET", path, e.code, "redirect is not https") from e
            loc = urllib.request.Request(e.headers["Location"],
                                         headers={"User-Agent": "everest-himalayas-intake"})
            with urllib.request.urlopen(loc, timeout=60) as r:
                data = r.read(limit + 1)
    except Exception as e:                                     # noqa: BLE001
        raise SourceUnreachable(str(e)) from e
    return data if len(data) <= limit else None


def fetch_file(src, relpath, token, fixtures=None):
    """The bytes at relpath on the source's ref, or None if no such file.

    `fixtures` is the self-test's stand-in for GitHub: a directory laid out as
    <repo_id>/<ref>/<relpath>. A file at <repo_id> instead stands for a source
    GitHub would not serve.
    """
    if fixtures:
        down = os.path.join(fixtures, str(src["repo_id"]))
        if os.path.isfile(down):
            raise SourceUnreachable(f"{src['repo']} ({src['repo_id']}): fixture unreachable")
        p = os.path.join(down, src["ref"], relpath)
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            return f.read()
    q = urllib.parse.quote
    path = (f"/repositories/{src['repo_id']}/contents/"
            f"{q(relpath)}?ref={q(src['ref'], safe='')}")
    # The raw media type, because the JSON form returns no content over 1 MB.
    try:
        return request("GET", path, token, accept="application/vnd.github.raw")[0]
    except ApiError as e:
        if e.code == 404:
            return None
        raise SourceUnreachable(f"{src['repo']} ({src['repo_id']}): {e}") from e
    except Exception as e:                                     # noqa: BLE001
        raise SourceUnreachable(f"{src['repo']} ({src['repo_id']}): {e}") from e
