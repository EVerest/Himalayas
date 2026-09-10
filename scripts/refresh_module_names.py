#!/usr/bin/env python3
"""Regenerate schema/everest-modules.txt from EVerest's module tree.

WHY THIS SCRIPT EXISTS

The list it writes was once produced by hand, and it matched only the top level of
modules/. Every EVerest module lives one or more directories deeper - under
modules/HardwareDrivers/PowerMeters/, modules/API/EVerestAPI/, and so on - so the
committed list held 58 names against 118 real modules, and the gate told integrators
their real hardware module names were not EVerest module names. A warning that is
mostly false teaches people to ignore the ones that are true.

So the walk is RECURSIVE and it is a script, not a procedure someone repeats from
memory. A module is a directory containing manifest.yaml, and its name is that
directory's name - the same rule EVerest's own build uses to discover modules.

    python3 scripts/refresh_module_names.py                 # at the current pointer tip
    python3 scripts/refresh_module_names.py --commit <sha>   # at a specific commit

Reads GITHUB_TOKEN if it is set; the tree endpoint works unauthenticated but is
rate-limited hard.
"""
import argparse, json, os, re, sys, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "schema", "everest-modules.txt")
REPO = "EVerest/EVerest"
MANIFEST_RE = re.compile(r"^modules/(.+)/manifest\.yaml$")

HEADER = """\
# EVerest module names, from modules/**/manifest.yaml at {short} ({date}).
# Regenerate with `python3 scripts/refresh_module_names.py`; never hand-edit, or
# the list rots the same way it did at 58 names against 118 real modules.
# Used to WARN on likely typos in extra.build.modules. Warn, not reject: the list
# drifts with EVerest, and a submission against a newer version must still pass.
"""


def api(path):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}",
                                 headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def pointer_tip():
    """The newest testing/main sha in the log, which is what submissions test against."""
    with open(os.path.join(ROOT, "pointers.json")) as f:
        log = json.load(f)["log"]
    tips = [e["sha"] for e in log if e.get("pointer") == "testing/main"]
    if not tips:
        raise SystemExit("error: pointers.json logs no testing/main move; pass --commit")
    return tips[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", help="full 40-char sha; defaults to the testing/main tip")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    sha = args.commit or pointer_tip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        print(f"error: --commit must be a full 40-character SHA, got {sha!r}", file=sys.stderr)
        return 2

    try:
        commit = api(f"commits/{sha}")
        tree = api(f"git/trees/{sha}?recursive=1")
    except urllib.error.HTTPError as e:                        # noqa: BLE001
        print(f"error: GitHub API {e.code} for {sha}", file=sys.stderr)
        return 2

    # A truncated tree is the silent-loss case all over again: the response is
    # well-formed, just short, and the list would shrink with nothing to show why.
    if tree.get("truncated"):
        print("error: the tree response was truncated; the list would silently lose "
              "modules. Clone EVerest and walk modules/ locally instead.", file=sys.stderr)
        return 2

    names = sorted({m.group(1).rsplit("/", 1)[-1]
                    for m in (MANIFEST_RE.match(e["path"]) for e in tree["tree"]
                              if e.get("type") == "blob")
                    if m}, key=str.lower)
    if not names:
        print("error: no modules/**/manifest.yaml in the tree", file=sys.stderr)
        return 2

    date = commit["commit"]["committer"]["date"][:10]
    with open(args.out, "w") as f:
        f.write(HEADER.format(short=sha[:9], date=date))
        f.write("".join(n + "\n" for n in names))
    print(f"wrote {len(names)} module names from {sha[:9]} ({date}) to "
          f"{os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
