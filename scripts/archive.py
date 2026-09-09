#!/usr/bin/env python3
"""Archive old submissions, stripping traces and attachments.

Full records are kept for N months, then archived with traces and attachments
stripped. N is the --months option, defaulting to 6 - which
matches EVerest's own stable release cadence, so an archived record is one from
before the current supported line. Change the default if that reasoning is wrong.

Why this exists at all: the pointers move DAILY, so at 5-15 integrators the repo
takes 1,825-5,475 submissions a year. Retention is "keep the historical record",
not "keep every byte" - so what gets dropped is the bulk, and what is kept is
everything the status page and any later analysis actually read.

STRIPPED
  phases[].attachments            base64 blobs; OpenHTF inlines these by default
  phases[].outcome_details        entries with code == "trace"
  phases[].codeinfo               including sourcecode, if the submitter left it in

KEPT
  every phase, its name, outcome, timings, tags, suite and MEASUREMENTS,
  the full metadata block, and outcome_details entries that are messages
  rather than traces - so "which test failed, when, on what commit, and by
  how much" all survive.

Dry by default: pass --apply to actually move anything.
"""
import argparse, datetime, glob, json, os, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalise import detect, ctrf_to_openhtf                  # noqa: E402


def run_millis(doc):
    fmt = detect(doc)
    if fmt == "openhtf":
        return doc.get("end_time_millis") or doc.get("start_time_millis") or 0
    summ = ((doc.get("results") or {}).get("summary") or {})
    return summ.get("stop") or summ.get("start") or 0


def strip(doc):
    """Normalise, then drop the bulk. Archiving also normalises, so an archived
    record is always OpenHTF regardless of how it arrived."""
    fmt = detect(doc)
    rec = doc if fmt == "openhtf" else ctrf_to_openhtf(doc)
    dropped = {"attachments": 0, "traces": 0, "codeinfo": 0}
    for ph in rec.get("phases") or []:
        if ph.get("attachments"):
            dropped["attachments"] += len(ph["attachments"])
            ph["attachments"] = {}
        if ph.pop("codeinfo", None):
            dropped["codeinfo"] += 1
        od = ph.get("outcome_details")
        if od:
            kept = [d for d in od if d.get("code") != "trace"]
            dropped["traces"] += len(od) - len(kept)
            if kept:
                ph["outcome_details"] = kept
            else:
                ph.pop("outcome_details", None)
    rec.setdefault("metadata", {}).setdefault("source", {})["archived"] = True
    return rec, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6,
                    help="keep full records younger than this many months (default 6)")
    ap.add_argument("--apply", action="store_true", help="actually move files")
    args = ap.parse_args()

    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=args.months * 30.44)).timestamp() * 1000
    moved = kept = 0
    totals = {"attachments": 0, "traces": 0, "codeinfo": 0}
    saved = 0

    for p in sorted(glob.glob(os.path.join(ROOT, "results", "*", "*", "*", "*.json"))):
        try:
            with open(p) as f:
                doc = json.load(f)
        except Exception:                                      # noqa: BLE001
            continue
        when = run_millis(doc)
        if not when or when >= cutoff:
            kept += 1
            continue

        rel = os.path.relpath(p, os.path.join(ROOT, "results"))
        dst = os.path.join(ROOT, "archive", rel)
        rec, dropped = strip(doc)
        before = os.path.getsize(p)
        payload = json.dumps(rec, indent=2) + "\n"
        after = len(payload.encode())
        saved += before - after
        for k in totals:
            totals[k] += dropped[k]
        moved += 1

        if args.apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w") as f:
                f.write(payload)
            os.remove(p)
        print(f"{'archived' if args.apply else 'would archive'}  {rel}  "
              f"{before:,} -> {after:,} bytes")

    print(f"\n{'moved' if args.apply else 'would move'}: {moved}   kept: {kept}   "
          f"cutoff: {args.months} months")
    print(f"dropped: {totals['attachments']} attachment(s), {totals['traces']} trace(s), "
          f"{totals['codeinfo']} codeinfo block(s)   bytes saved: {saved:,}")
    if not args.apply and moved:
        print("\ndry run - pass --apply to move them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
