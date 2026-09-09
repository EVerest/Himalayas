#!/usr/bin/env python3
"""Verify the floating pointer branches, and the log that makes them auditable.

WHY THIS EXISTS
---------------
Tags were replaced by floating branches that EVerest CI force-pushes forward.
That buys a simple "fetch this branch to get the agreed base", and it costs the
one property a tag had for free: immutability. A tag pins a commit forever, so
"I tested test/2026.09.2" stays checkable forever. A force-pushed branch does
not, so "I tested testing/main" means nothing a week later.

pointers.json is the append-only log that buys the property back. This script
checks the log and the live branches agree, and that each pointer points
somewhere legitimate.

CHECKS
  1. every declared pointer branch exists
  2. every pointer's tip is REACHABLE FROM the branch it claims to track -
     a pointer pointing at a commit that is not on main is the failure mode
     that matters, and nothing else would catch it
  3. the log's newest entry per pointer matches the live tip
  4. the log only ever moves forward: each sha is a descendant of the one
     before it, so the pointer was never rewound. Checked over the last
     --forward-window pairs, not just the newest one: comparing only the
     newest pair meant a single further move hid an earlier rewind forever
  5. no pointer has gone unmoved for longer than --max-age-days, which is how
     a silently dead CI job shows up. Reads --now if given.

An API error on check 2 or check 4 is a FAILURE, not a note. Both used to
degrade to a note while the script still printed "all checks passed" and exited
0, so a rate-limited token turned the daily audit into a green no-op - the exact
state the audit exists to detect. A check that could not run has not passed.

The clock is injectable with --now. Without it, check 5 compares against the
real wall clock, which made the self-test go red on 2026-09-20 with no code
change at all, under a check named "pointer log is internally consistent" - a
failure that points at the log rather than at the calendar.

Exit 0 all checks pass, 1 a check failed, 2 tool error.
"""
import argparse, json, os, sys, urllib.request, urllib.error, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.github.com"


def api(path, token):
    req = urllib.request.Request(f"{API}{path}", headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "everest-himalayas-pointer-check",
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:                                     # noqa: BLE001
        return None, str(e)


def compare(repo, base, head, token):
    """Is `head` reachable from `base`? Returns one of GitHub's compare statuses."""
    d, err = api(f"/repos/{repo}/compare/{base}...{head}", token)
    if err:
        return None, err
    # behind / identical  => head is an ancestor of base, i.e. reachable from it
    # ahead / diverged    => head is NOT on base
    return d.get("status"), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="EVerest/EVerest")
    ap.add_argument("--pointers", default=os.path.join(ROOT, "pointers.json"))
    ap.add_argument("--max-age-days", type=int, default=10,
                    help="a pointer unmoved for longer than this is treated as a dead CI job")
    ap.add_argument("--forward-window", type=int, default=10, metavar="N",
                    help="how many of the most recent moves per pointer to check for a "
                         "rewind. One API call each, so this is the cost/coverage dial. "
                         "A rewind older than this window is no longer detectable here, "
                         "which is why the pointer branches should not be left writable "
                         "by hand.")
    ap.add_argument("--offline", action="store_true",
                    help="check only the log's internal consistency")
    ap.add_argument("--now", metavar="ISO8601",
                    help="treat this as the current time, e.g. 2026-09-10T00:00:00Z. "
                         "The liveness check is the only one that reads a clock, and a "
                         "test for it that reads the real one is a test with a date on "
                         "which it starts failing.")
    args = ap.parse_args()

    try:
        with open(args.pointers) as f:
            doc = json.load(f)
    except Exception as e:                                     # noqa: BLE001
        print(f"error: cannot read {args.pointers}: {e}", file=sys.stderr)
        return 2

    if args.now:
        try:
            now = datetime.datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError:
            print(f"error: --now is not an ISO timestamp: {args.now!r}", file=sys.stderr)
            return 2
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
    else:
        now = datetime.datetime.now(datetime.timezone.utc)

    declared = doc.get("pointers") or {}
    log = doc.get("log") or []
    token = os.environ.get("GITHUB_TOKEN")
    fails, notes = [], []

    # newest-last ordering per pointer, as written
    per = {}
    for i, e in enumerate(log):
        per.setdefault(e.get("pointer"), []).append((i, e))

    for name, spec in declared.items():
        tracks = spec.get("tracks")
        entries = per.get(name) or []
        if not entries:
            fails.append(f"{name}: declared but has no log entry")
            continue
        _, newest = entries[-1]

        # --- 4. the log only moves forward -------------------------------
        # Over a WINDOW of recent pairs, not only the newest. It used to compare
        # entries[-1] against entries[-2] alone, so one further move hid an
        # earlier rewind for good.
        if not args.offline and args.forward_window > 0:
            window = entries[-(args.forward_window + 1):]
            checked = 0
            for (_, prev), (_, cur) in zip(window, window[1:]):
                st, err = compare(args.repo, cur["sha"], prev["sha"], token)
                checked += 1
                if err:
                    fails.append(f"{name}: the forward-only check could not run ({err}). "
                                 "A check that did not run has not passed - a rate-limited "
                                 "token must not turn this audit into a green no-op.")
                    break
                if st not in ("behind", "identical"):
                    fails.append(
                        f"{name}: log went backwards - {prev['sha'][:12]} is not an ancestor "
                        f"of {cur['sha'][:12]} (compare says '{st}'). A pointer must never "
                        "be rewound.")
            if checked:
                notes.append(f"{name}: forward-only checked over the last {checked} "
                             f"move(s) of {len(entries) - 1}")

        # --- 5. liveness ---------------------------------------------------
        try:
            moved = datetime.datetime.fromisoformat(
                newest["moved_at"].replace("Z", "+00:00"))
            age = (now - moved).days
            if age > args.max_age_days:
                fails.append(f"{name}: last moved {age} days ago, limit is "
                             f"{args.max_age_days}. Has the CI job stopped running?")
        except Exception:                                      # noqa: BLE001
            fails.append(f"{name}: moved_at is not an ISO timestamp: {newest.get('moved_at')!r}")

        if args.offline:
            continue

        # --- 1. the branch exists ------------------------------------------
        br, err = api(f"/repos/{args.repo}/branches/{name.replace('/', '%2F')}", token)
        if err:
            fails.append(f"{name}: branch not found on {args.repo} ({err})")
            continue
        tip = br["commit"]["sha"]

        # --- 3. live tip matches the log -----------------------------------
        if tip != newest["sha"]:
            fails.append(f"{name}: live tip {tip[:12]} but the log's newest entry is "
                          f"{newest['sha'][:12]}. The CI job moved the branch without "
                          f"appending to pointers.json, so this move is unauditable.")

        # --- 2. the tip is actually on the branch it claims to track -------
        if tracks:
            st, err = compare(args.repo, tracks, tip, token)
            if err:
                fails.append(f"{name}: the reachability check could not run ({err}). "
                             "A pointer advertising a commit that is not on the branch "
                             "it claims to track is the failure mode this catches, and "
                             "nothing else would.")
            elif st not in ("behind", "identical"):
                fails.append(f"{name}: tip {tip[:12]} is not reachable from '{tracks}' "
                              f"(compare says '{st}'). The pointer is advertising a commit "
                              f"that is not on the branch it claims to track.")

    print(f"pointers checked: {len(declared)}   log entries: {len(log)}   repo: {args.repo}"
          + ("   [offline]" if args.offline else ""))
    for n in notes:
        print(f"  note: {n}")
    if fails:
        print(f"\nFAILED ({len(fails)}):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
