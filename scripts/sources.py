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
"""
import json, os, re, urllib.error, urllib.parse, urllib.request

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


class SourceUnreachable(Exception):
    """A source could not be read for a reason other than the file being absent."""


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
        before = len(problems)
        unknown = set(s) - {"repo", "repo_id", "ref", "path", "label"}
        if unknown:
            problems.append(f"{where} has unknown key(s) {sorted(unknown)}")
        repo, rid, ref = s.get("repo"), s.get("repo_id"), s.get("ref")
        path, label = s.get("path", ""), s.get("label")
        if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
            problems.append(f"{where}.repo must be `owner/name`")
        if not isinstance(rid, int) or isinstance(rid, bool) or rid <= 0:
            problems.append(f"{where}.repo_id must be the repository's numeric id")
        if not isinstance(ref, str) or not REF_RE.fullmatch(ref) or NOT_A_BRANCH_RE.fullmatch(ref):
            problems.append(f"{where}.ref must be a branch name")
        if not isinstance(path, str) or not DIR_RE.fullmatch(path):
            problems.append(f"{where}.path must be empty or a directory ending in `/`")
        if label is not None:
            if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
                problems.append(f"{where}.label must be a filename label such as `octt`")
            elif label in labels:
                problems.append(f"{where}.label `{label}` is declared twice")
            else:
                labels.add(label)
        if len(problems) == before:
            out.append({"repo": repo, "repo_id": rid, "ref": ref, "path": path, "label": label})
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
    """Whether a record with this filename label may come from this source."""
    want = src.get("label")
    return want is None or (label is not None and (label == want or label.startswith(want + "-")))


def reserved_label(srcs, label):
    """The source that reserves this filename label, if any. A record from any
    other route may not use it."""
    for s in srcs:
        if s.get("label") and label_admits(s, label):
            return s
    return None


def source_path(src, pointer, filename):
    return f"{src['path']}{pointer}/{filename}"


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


def api_list(path, token):
    """Every item of a list endpoint, following its `Link: rel="next"` pages."""
    items = []
    while path:
        try:
            raw, headers = request("GET", path, token)
            items += json.loads(raw)
        except Exception as e:                                 # noqa: BLE001
            raise SourceUnreachable(str(e)) from e
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link") or "")
        path = nxt.group(1)[len(API):] if nxt and nxt.group(1).startswith(API) else None
    return items


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
