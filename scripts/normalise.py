#!/usr/bin/env python3
"""Normalise a submission to OpenHTF, this repository's internal format.

Accepts either a CTRF report or an OpenHTF TestRecord and emits an OpenHTF
record carrying EVerest metadata. OpenHTF was chosen as the normal form because
it is the richer of the two: measurements and per-DUT identity are native, and
its outcome enum is a superset of CTRF's.

WHAT THE DIRECTION COSTS, ONCE
------------------------------
OpenHTF models a device under test. It has no concept of a source commit, a
build, or a test suite. So everything identifying WHAT was tested moves into
TestRecord.metadata, an untyped object - which is the same closed-schema problem
CTRF had with results.extra, relocated rather than removed. That is what
schema/everest-htf-metadata.schema.json exists to pin down.

CTRF -> OpenHTF mapping
  results.tests[]                  -> phases[]
  test.name                        -> phase.name
  test.status                      -> phase.outcome        (see STATUS_MAP)
  test.start/stop                  -> phase.start/end_time_millis, when present
  test.suite, test.tags            -> phase.suite, phase.tags   (non-native, kept)
  test.message + test.trace        -> phase.outcome_details
  test.snippet                     -> phase.codeinfo.sourcecode
  test.stdout/stderr               -> phase.outcome_details
  summary.start/stop               -> start/end_time_millis
  environment.testEnvironment      -> metadata.everest.testEnvironment,
                                      and dut_id for a SIL run
  environment.commit/branchName    -> metadata.everest.*
  extra.build/integrator/hardware  -> metadata.*

WHAT IS LOST, AND WHAT IS INVENTED
----------------------------------
The earlier version of this note said "Lossy in this direction: nothing
material", which was not true. Stated properly:

LOST. `test.device` (per-test hardware, which OpenHTF models per RECORD, not per
phase), `test.retries` / `retryAttempts`, `test.filePath` / `line`, `test.steps`,
`test.insights`, `test.parameters`, `test.extra`. None is read by anything
downstream today, which is why the direction is still the right one - but "not
read" is not "not material", and a later reader should know they were dropped
here rather than never sent.

SYNTHESISED. OpenHTF requires fields CTRF has no equivalent for, so three values
are manufactured, and every one of them is listed in
`metadata.source.synthesised` on the record - because a synthesised value that
looks like a measured one is worse than a missing one:

  dut_id      "sil" for a SIL run, or extra.hardware.board for HIL. A board is a
              MODEL, not a rig, so two rigs with the same board are
              indistinguishable. That is a real limit of the CTRF path.
  station_id  the integrator id, so every CTRF submission from one integrator
              reports the same station.

Phase timings are NOT synthesised any more. They used to be derived from
`summary.start` plus `duration` when a test carried no `start`, which made every
phase of a run appear to have started at the same instant - fabricated data
indistinguishable from measured data. A phase with no timings now carries none.
"""
import argparse, json, sys

# CTRF status -> OpenHTF PhaseOutcome. OpenHTF has no 'pending', so a pending
# CTRF test becomes SKIP: it did not run, which is the closest true statement.
STATUS_MAP = {
    "passed": "PASS",
    "failed": "FAIL",
    "skipped": "SKIP",
    "pending": "SKIP",
    "other": "ERROR",
}


def detect(doc):
    """Which format is this? Decided on required keys, not on guesswork."""
    if isinstance(doc, dict):
        if doc.get("reportFormat") == "CTRF" or ("results" in doc and "specVersion" in doc):
            return "ctrf"
        if "phases" in doc and "dut_id" in doc:
            return "openhtf"
    return None


def ctrf_to_openhtf(doc):
    r = doc.get("results") or {}
    env = r.get("environment") or {}
    extra = r.get("extra") or {}
    summ = r.get("summary") or {}
    tests = r.get("tests") or []

    phases = []
    for t in tests:
        details = []
        if t.get("message"):
            details.append({"code": "message", "description": t["message"]})
        if t.get("trace"):
            details.append({"code": "trace", "description": t["trace"]})
        # stdout/stderr are inline bulk on the CTRF path, and they were dropped
        # silently. Carried as traces so the size cap and archive.py's stripping
        # both see them: the cap exists because this repository keeps files
        # indefinitely, and it cannot bound what it cannot see.
        for stream in ("stdout", "stderr"):
            lines = t.get(stream)
            if isinstance(lines, list) and lines:
                details.append({"code": "trace",
                                "description": stream + ":\n" + "\n".join(
                                    str(x) for x in lines)})

        ph = {
            "name": t.get("name"),
            "outcome": STATUS_MAP.get(t.get("status"), "ERROR"),
            "measurements": {},          # CTRF has none, by construction
            "attachments": {},
        }
        # Only real timings. These used to be derived from summary.start plus
        # duration when a test carried no start, so every phase of a run
        # appeared to begin at the same instant - invented data that read
        # exactly like measured data. A phase with no timings now carries none.
        if t.get("start") is not None:
            ph["start_time_millis"] = int(t["start"])
            if t.get("stop") is not None:
                ph["end_time_millis"] = int(t["stop"])
            elif t.get("duration") is not None:
                ph["end_time_millis"] = int(t["start"]) + int(t["duration"])
        if t.get("suite"):
            ph["suite"] = t["suite"] if isinstance(t["suite"], list) else [t["suite"]]
        if t.get("tags"):
            ph["tags"] = t["tags"]
        if details:
            ph["outcome_details"] = details
        if t.get("flaky") is not None:
            ph["marginal"] = bool(t["flaky"])
        # CTRF's snippet is source code, exactly like OpenHTF's
        # codeinfo.sourcecode. Carried across so the gate's sourcecode privacy
        # warning fires on both paths: it was OpenHTF-only, so a closed-source
        # integrator's snippet reached a public repository with no warning at
        # all, and a closed-source test suite is explicitly allowed here.
        if t.get("snippet"):
            ph["codeinfo"] = {"name": t.get("name") or "", "sourcecode": t["snippet"]}
        # CTRF attachments are references, not inline blobs, so they carry no
        # bytes here - but they are the only place a reader could look for the
        # detail that the cap keeps out, so keep the reference.
        if t.get("attachments"):
            ph["attachments"] = {
                str((a or {}).get("name") or i): {"path": (a or {}).get("path"),
                                                 "contentType": (a or {}).get("contentType")}
                for i, a in enumerate(t["attachments"]) if isinstance(a, dict)}
        phases.append(ph)

    # The run outcome came from summary.failed alone, so a report claiming
    # failed: 0 with a failed test in tests[] was accepted as a PASS. Both are
    # self-reported, so the honest reading is the pessimistic one: if either
    # says something failed, the run failed.
    failed = summ.get("failed", 0) or 0
    any_phase_failed = any(ph["outcome"] in ("FAIL", "ERROR") for ph in phases)
    outcome = "FAIL" if failed or any_phase_failed else "PASS"

    testenv = env.get("testEnvironment")
    # OpenHTF requires a device under test. A SIL run has no device, so give it
    # a stable synthetic id rather than inventing a fake serial number. For HIL
    # it is the board, which is a MODEL - two rigs with the same board are
    # indistinguishable, and that is recorded as synthesis below rather than
    # left to look like device identity.
    dut = "sil" if testenv == "SIL" else (
        (extra.get("hardware") or {}).get("board") or "unspecified-dut")
    station = (extra.get("integrator") or {}).get("id") or "unspecified-station"

    md = {
        "everest": {k: v for k, v in {
            "commit": env.get("commit"),
            "pointer": env.get("appVersion"),
            "tracks": env.get("branchName"),
            "testEnvironment": testenv,
            "buildUrl": env.get("buildUrl"),
        }.items() if v is not None},
        "source": {k: v for k, v in {
            "format": "ctrf",
            "tool": (r.get("tool") or {}).get("name"),
            "specVersion": doc.get("specVersion"),
            "convertedBy": "everest-himalayas/normalise.py 0.1.0",
            # Which of this record's values were manufactured here rather than
            # reported. A synthesised value that looks measured is worse than a
            # missing one, and CTRF has no field for either of these.
            "synthesised": ["dut_id", "station_id"],
        }.items() if v is not None},
    }
    for key in ("build", "integrator", "hardware"):
        if extra.get(key):
            md[key] = extra[key]

    rec = {
        "dut_id": dut,
        "station_id": station,
        "outcome": outcome,
        "start_time_millis": int(summ.get("start") or 0),
        "metadata": md,
        "phases": phases,
    }
    if summ.get("stop"):
        rec["end_time_millis"] = int(summ["stop"])
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="a CTRF report or an OpenHTF TestRecord")
    ap.add_argument("-o", "--output", help="write here instead of stdout")
    ap.add_argument("--print-format", action="store_true",
                    help="print the detected format and exit")
    args = ap.parse_args()

    try:
        with open(args.path) as f:
            doc = json.load(f)
    except Exception as e:                                     # noqa: BLE001
        print(f"error: {args.path}: {e}", file=sys.stderr)
        return 2

    fmt = detect(doc)
    if args.print_format:
        print(fmt or "unknown")
        return 0 if fmt else 1
    if fmt is None:
        print("error: not recognisable as CTRF or OpenHTF. A CTRF report needs "
              "reportFormat and specVersion; an OpenHTF record needs dut_id and phases.",
              file=sys.stderr)
        return 1

    rec = doc if fmt == "openhtf" else ctrf_to_openhtf(doc)
    if fmt == "openhtf":
        rec.setdefault("metadata", {}).setdefault("source", {"format": "openhtf"})

    out = json.dumps(rec, indent=2) + "\n"
    if args.output:
        with open(args.output, "w") as f:
            f.write(out)
        print(f"{fmt} -> openhtf: {args.output} "
              f"({len(rec.get('phases') or [])} phase(s))", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
