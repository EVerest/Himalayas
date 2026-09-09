#!/usr/bin/env python3
"""Append one pointer move to pointers.json.

Called by the workflow that receives the pointer-moved dispatch from EVerest.
Append-only and idempotent: re-running with the same sha for the same pointer is
a no-op rather than a duplicate row, because a redelivered webhook must not
corrupt the log the gate depends on.

Refuses to append a move for a pointer that is not declared, whose tracked branch
disagrees with the declaration, or whose sha already appears earlier in that
pointer's log.

WHAT THIS SCRIPT DOES *NOT* CHECK, deliberately: whether the new sha is a
descendant of the previous one. That needs the commit graph, which means network
access to EVerest, and doing it here would duplicate verify_pointers.py check 4.
The workflow that calls this runs verify_pointers.py immediately afterwards, and
that is what enforces forward-only movement. If you reuse this script outside
that workflow, run the verifier too, or a genuine rewind to a commit that was
never logged will be accepted.
"""
import argparse, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pointer", required=True)
    ap.add_argument("--tracks", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--moved-at", required=True)
    ap.add_argument("--file", default=os.path.join(ROOT, "pointers.json"))
    args = ap.parse_args()

    if not SHA_RE.match(args.sha):
        print(f"error: --sha must be a full 40-character SHA, got {args.sha!r}", file=sys.stderr)
        return 2
    if not TS_RE.match(args.moved_at):
        print(f"error: --moved-at must be YYYY-MM-DDTHH:MM:SSZ, got {args.moved_at!r}",
              file=sys.stderr)
        return 2

    with open(args.file) as f:
        doc = json.load(f)

    declared = doc.get("pointers") or {}
    if args.pointer not in declared:
        print(f"error: {args.pointer} is not declared in pointers.json. Add it there first - "
              "the log must not carry moves for pointers nobody agreed to.", file=sys.stderr)
        return 1
    if declared[args.pointer].get("tracks") != args.tracks:
        print(f"error: {args.pointer} is declared as tracking "
              f"{declared[args.pointer].get('tracks')!r}, not {args.tracks!r}.", file=sys.stderr)
        return 1

    log = doc.setdefault("log", [])
    mine = [e for e in log if e.get("pointer") == args.pointer]
    if mine and mine[-1].get("sha") == args.sha:
        print(f"no-op: {args.pointer} is already logged at {args.sha[:12]}")
        return 0
    if any(e.get("sha") == args.sha for e in mine):
        print(f"error: {args.sha[:12]} already appears earlier in the log for "
              f"{args.pointer}, so this move would revisit a commit the pointer has "
              "already left. Pointers only move forward.", file=sys.stderr)
        return 1

    log.append({"pointer": args.pointer, "tracks": args.tracks,
                "sha": args.sha, "moved_at": args.moved_at})
    with open(args.file, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"appended: {args.pointer} -> {args.sha[:12]} at {args.moved_at} "
          f"({len(log)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
