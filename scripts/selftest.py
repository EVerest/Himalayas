#!/usr/bin/env python3
"""Self-test the gate and the tooling by asserting what they REJECT.

WHY THIS EXISTS
A rewrite of validate_submission.py once silently disabled five CTRF checks.
Nothing went red: every legitimate example and submission still passed, because
clean data validates the same either way. Only a deliberately malformed input showed it.

So this suite is almost entirely NEGATIVE cases, and each one asserts the specific error
code, not merely that something failed - a case that starts failing for the wrong reason
is a check that has quietly stopped working.

It runs the real command-line tools against a throwaway copy of the repository, so it
tests what CI and a contributor actually run, not internals.

    python3 scripts/selftest.py            # all suites
    python3 scripts/selftest.py -v         # show every case
    python3 scripts/selftest.py --only gate

Each suite's check count is asserted against EXPECT at the bottom of this file,
and the total is asserted again in .github/workflows/selftest.yml. Without that,
deleting cases is invisible: a suite cut from 70 checks to 3 still exits
non-zero, which is all the CI guard used to assert.
"""
import argparse, base64, copy, json, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
FAILS = []
RUN = 0


def report(name, ok, detail=""):
    global RUN
    RUN += 1
    if not ok:
        FAILS.append((name, detail))
    return ok


def sh(work, *args):
    return subprocess.run([PY, os.path.join(work, "scripts", args[0]), *args[1:]],
                          capture_output=True, text=True, cwd=work)


def codes(out):
    return sorted(set(re.findall(r"^\| `([a-z-]+)` \|", out, re.M)))


def gate(work, doc, relpath, extra=()):
    full = os.path.join(work, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        json.dump(doc, f, indent=2)
    r = sh(work, "validate_submission.py", relpath, "--skip-commit-check", *extra)
    os.remove(full)
    return r.returncode, codes(r.stdout), r.stdout


# ----------------------------------------------------------------- suites
def suite_gate(work, verbose):
    ctrf = json.load(open(os.path.join(work, "examples", "ctrf-hil-dc.json")))
    htf = json.load(open(os.path.join(work, "examples", "openhtf-hil-dc.json")))
    mini = json.load(open(os.path.join(work, "examples", "minimal-ctrf.json")))
    P = "results/deadbeef-charge/testing/main/20260901.json"

    # (label, base, mutation, expected code(s), path). A tuple means EVERY code
    # listed must appear - which is how the hand-written branches get pinned: each
    # of them fires alongside a schema rule that says the same thing less well, so
    # asserting only one code lets the other be deleted with the suite still green.
    cases = [
        ("path: wrong filename", ctrf, None, "bad-path", "results/deadbeef-charge/testing/main/nope.json"),
        ("path: missing pointer dir", ctrf, None, "bad-path", "results/deadbeef-charge/20260901.json"),
        ("path: -1 suffix", ctrf, None, "bad-path", "results/deadbeef-charge/testing/main/20260901-1.json"),
        ("format: not CTRF or OpenHTF", {"hello": "world"}, None, "unknown-format", P),
        ("ctrf: invalid CTRF", ctrf, lambda d: d["results"]["summary"].pop("passed"), "ctrf-schema", P),
        ("ctrf: missing specVersion", ctrf, lambda d: d.pop("specVersion"), "ctrf-schema", P),
        ("ctrf: per-test duration missing", ctrf, lambda d: d["results"]["tests"][0].pop("duration"), "ctrf-schema", P),
        ("profile: appName not EVerest", ctrf, lambda d: d["results"]["environment"].update(appName="fork"), "ctrf-profile", P),
        ("profile: osPlatform Linux", ctrf, lambda d: d["results"]["environment"].update(osPlatform="Linux"), "ctrf-profile", P),
        ("profile: branchName feature/x", ctrf, lambda d: d["results"]["environment"].update(branchName="feature/x"), "ctrf-profile", P),
        ("hil: HIL via CTRF, no hardware block", ctrf, lambda d: d["results"]["extra"].pop("hardware"),
         ("hil-needs-board", "ctrf-profile"), P),
        ("hil: HIL via CTRF, hardware but no board", ctrf, lambda d: d["results"]["extra"]["hardware"].pop("board"), "hil-needs-board", P),
        ("commit: short SHA", ctrf, lambda d: d["results"]["environment"].update(commit="30150a8"),
         ("commit-malformed", "ctrf-profile"), P),
        ("commit: never a pointer tip", ctrf, lambda d: d["results"]["environment"].update(
            commit="c4090db808cedad1c38b41d1fad1afc4b91970f7"), "commit-not-pointed", P),
        ("pointer: not in the enum", ctrf, lambda d: d["results"]["environment"].update(appVersion="testing/nope"), "pointer-mismatch", P),
        ("pointer: path/record mismatch", ctrf, lambda d: d["results"]["environment"].update(
            appVersion="testing/stable-2026.02"), "pointer-mismatch", P),
        ("build: missing", ctrf, lambda d: d["results"]["extra"].pop("build"),
         ("build-missing", "extra-contract"), P),
        ("build: failed", ctrf, lambda d: d["results"]["extra"]["build"].update(status="failed"), "build-failed", P),
        ("modules: missing (ctrf)", ctrf, lambda d: d["results"]["extra"]["build"].pop("modules"),
         ("extra-contract", "htf-metadata"), P),
        ("modules: empty (ctrf)", ctrf, lambda d: d["results"]["extra"]["build"].update(modules=[]),
         ("extra-contract", "htf-metadata"), P),
        ("modules: missing (openhtf)", htf, lambda d: d["metadata"]["build"].pop("modules"), "htf-metadata", P),
        ("modules: empty (openhtf)", htf, lambda d: d["metadata"]["build"].update(modules=[]), "htf-metadata", P),
        ("identity: id mismatch", ctrf, lambda d: d["results"]["extra"]["integrator"].update(id="someone-else"), "id-mismatch", P),
        ("identity: not allowlisted", ctrf, lambda d: d["results"]["extra"]["integrator"].update(id="rogue-co"),
         "not-allowlisted", "results/rogue-co/testing/main/20260901.json"),
        ("time: summary.start = 0", ctrf, lambda d: d["results"]["summary"].update(start=0),
         ("ctrf-profile", "htf-profile"), P),
        ("tags: free text", ctrf, lambda d: d["results"]["tests"][0].update(tags=["ISO 15118-2 over TLS"]),
         ("ctrf-profile", "htf-profile"), P),
        ("openhtf: testEnvironment 'sil'", htf, lambda d: d["metadata"]["everest"].update(testEnvironment="sil"), "htf-metadata", P),
        ("openhtf: bad outcome", htf, lambda d: d.update(outcome="GREEN"), "htf-profile", P),
        ("openhtf: end time missing", htf, lambda d: d.pop("end_time_millis"), "htf-profile", P),
        # No integrator id at all: the allowlist check and the author binding both
        # sat behind `if claimed`, so this bypassed identity entirely.
        ("identity: no id (ctrf)", ctrf, lambda d: d["results"]["extra"]["integrator"].pop("id"),
         ("id-missing", "extra-contract"), P),
        ("identity: no id (openhtf)", htf, lambda d: d["metadata"]["integrator"].pop("id"),
         ("id-missing", "htf-metadata"), P),
        # No commit at all. Every commit check sits behind `if sha`, so this used
        # to satisfy all four of them by having nothing for them to look at.
        ("commit: absent (ctrf)", ctrf, lambda d: d["results"]["environment"].pop("commit"),
         ("commit-missing", "ctrf-profile"), P),
        ("commit: absent (openhtf)", htf, lambda d: d["metadata"]["everest"].pop("commit"),
         ("commit-missing", "htf-metadata"), P),
        # extra.build.status is not a free string.
        ("extra: build status not in the enum", ctrf,
         lambda d: d["results"]["extra"]["build"].update(status="green"),
         ("extra-contract", "build-failed"), P),
        # A document malformed enough to break the reader must still be a
        # REJECTION with a reason, never a crash. These three were exit 1 by
        # AttributeError, which is indistinguishable from a real rejection.
        ("crash: metadata is a string", htf, lambda d: d.update(metadata="hello"),
         "htf-profile", P),
        ("crash: phases is a list of strings", htf, lambda d: d.update(phases=["a"]),
         "htf-profile", P),
        ("crash: build is a string", htf, lambda d: d["metadata"].update(build="passed"),
         "htf-metadata", P),
    ]
    for label, base, mut, want, path in cases:
        d = copy.deepcopy(base)
        if mut:
            mut(d)
        rc, cs, _ = gate(work, d, path)
        wants = want if isinstance(want, tuple) else (want,)
        ok = rc == 1 and all(w in cs for w in wants)
        report(f"reject {label}", ok, f"rc={rc} codes={cs} wanted={want}")
        if verbose or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'} reject {label:36} rc={rc} {','.join(cs)}")

    # --- the pointer set is restated in four places ---------------------------
    # pointers.json declares it, everest-profile constrains appVersion,
    # everest-htf-metadata constrains metadata.everest.pointer, and PATH_RE
    # matches the path shape. PATH_RE now defers to pointers.json; the two enums
    # still restate the set, so assert they agree rather than discovering it when
    # a maintenance line is added and only three of the four get edited.
    with open(os.path.join(work, "pointers.json")) as f:
        declared = set(json.load(f).get("pointers") or {})
    with open(os.path.join(work, "schema", "everest-htf-metadata.schema.json")) as f:
        htf_enum = set(json.load(f)["properties"]["everest"]["properties"]
                       ["pointer"]["enum"])
    with open(os.path.join(work, "schema", "everest-profile.schema.json")) as f:
        ctrf_enum = set(json.load(f)["properties"]["results"]["properties"]
                        ["environment"]["properties"]["appVersion"]["enum"])
    ok = declared == ctrf_enum == htf_enum and bool(declared)
    report("the declared pointer set matches both schema enums", ok,
           f"pointers.json={sorted(declared)} ctrf={sorted(ctrf_enum)} "
           f"openhtf={sorted(htf_enum)}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} pointer set agrees in three places")

    # And a path naming a pointer nobody declared is rejected.
    d = copy.deepcopy(ctrf)
    d["results"]["environment"]["appVersion"] = "testing/main"
    rc, cs, _ = gate(work, d, "results/deadbeef-charge/testing/stable-1999.01/20260901.json")
    ok = rc == 1 and "pointer-unknown" in cs
    report("reject a path naming an undeclared pointer", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} reject undeclared pointer path        rc={rc} {','.join(cs)}")

    # --- the docs have to describe a submission the gate accepts -------------
    # CONTRIBUTING.md and SCHEMA.md documented `appVersion` as a `test/YYYY.MM.N`
    # tag long after it became the pointer branch name, and the normaliser derives
    # metadata.everest.pointer from it - so the documented recipe was rejected four
    # ways, and it is the first thing a new integrator runs.
    with open(os.path.join(work, "schema", "everest-profile.schema.json")) as f:
        prof = json.load(f)
    allowed = set(prof["properties"]["results"]["properties"]["environment"]
                  ["properties"]["appVersion"]["enum"])
    documented = set()
    for doc in ("CONTRIBUTING.md", "SCHEMA.md", "README.md"):
        with open(os.path.join(work, doc)) as f:
            documented |= set(re.findall(r"appVersion[= ]+`?([A-Za-z0-9./-]+)`?", f.read()))
    documented -= {"and", "is", "to", "the", "a"}
    bad = sorted(documented - allowed)
    ok = bool(documented) and not bad
    report("docs: every documented appVersion is one the schema accepts", ok,
           f"documented={sorted(documented)} allowed={sorted(allowed)} bad={bad}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} docs: documented appVersion values    {bad or 'all valid'}")

    # --- the author-to-integrator binding -----------------------------------
    # An integrator id lists the GitHub accounts allowed to publish as it.
    # Without this the page's premise - attributed self-reporting - is unenforced:
    # any GitHub account could publish an auto-merged result as any integrator.
    authorised = "deadbeef-charge-ci"
    rc, cs, _ = gate(work, copy.deepcopy(ctrf), P, extra=("--author", authorised))
    ok = rc == 0
    report("accept a submission from an authorised account", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept authorised author              rc={rc}")

    rc, cs, _ = gate(work, copy.deepcopy(ctrf), P, extra=("--author", "DEADBEEF-Charge-CI"))
    ok = rc == 0
    report("account match is case-insensitive", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} authorised author, other case         rc={rc}")

    rc, cs, _ = gate(work, copy.deepcopy(ctrf), P, extra=("--author", "drive-by-account"))
    ok = rc == 1 and "not-authorised" in cs
    report("reject a submission from an unlisted account", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} reject unlisted author                rc={rc} {','.join(cs)}")

    # The author reaches the gate through the environment, because the workflow
    # must not interpolate it into a shell command.
    env = dict(os.environ, SUBMISSION_AUTHOR="drive-by-account")
    full = os.path.join(work, P)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        json.dump(ctrf, f)
    r = subprocess.run([PY, os.path.join(work, "scripts", "validate_submission.py"),
                        P, "--skip-commit-check"], capture_output=True, text=True,
                       cwd=work, env=env)
    os.remove(full)
    ok = r.returncode == 1 and "not-authorised" in codes(r.stdout)
    report("author is read from SUBMISSION_AUTHOR", ok, f"rc={r.returncode}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} author via SUBMISSION_AUTHOR          rc={r.returncode}")

    # An allowlist entry with no github_users authorises nobody. Written into the
    # throwaway copy, because the shipped allowlist must not carry a fixture.
    al = os.path.join(work, "integrators", "allowlist.yaml")
    with open(al) as f:
        original = f.read()
    with open(al, "a") as f:
        f.write("  - id: no-accounts\n    display_name: Fixture, no github_users\n")
    d = copy.deepcopy(ctrf)
    d["results"]["extra"]["integrator"]["id"] = "no-accounts"
    rc, cs, _ = gate(work, d, "results/no-accounts/testing/main/20260901.json",
                     extra=("--author", authorised))
    with open(al, "w") as f:
        f.write(original)
    ok = rc == 1 and "not-authorised" in cs
    report("reject an id whose entry lists no accounts", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} reject id with no github_users        rc={rc} {','.join(cs)}")

    # --- exit 2 is reserved for "the gate could not tell" --------------------
    # The workflow closes the pull request on exit 2 and leaves it open on exit 1,
    # so the two must never be confusable. Nothing above may exit 2, and a real
    # tool failure must not exit 1.
    # Both files the gate cannot proceed without. Each used to warn and skip its
    # check, which accepts a submission whose identity or whose agreed base could
    # not be verified - a green that means nothing.
    for missing in ("integrators/allowlist.yaml", "pointers.json"):
        path = os.path.join(work, missing)
        os.rename(path, path + ".hidden")
        rc, cs, out = gate(work, copy.deepcopy(ctrf), P)
        os.rename(path + ".hidden", path)
        ok = rc == 2 and "could not be processed" in out
        report(f"tool failure exits 2 with a verdict: no {missing}", ok,
               f"rc={rc} codes={cs}")
        if verbose or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'} exit 2 when {missing:34} is missing rc={rc}")

    # not-json and the whole-file cap, neither of which anything pinned
    full = os.path.join(work, P)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write("{ this is not json")
    r = sh(work, "validate_submission.py", P, "--skip-commit-check")
    os.remove(full)
    ok = r.returncode == 1 and "not-json" in codes(r.stdout)
    report("reject a file that is not JSON", ok, f"rc={r.returncode} {codes(r.stdout)}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} reject not-json                       rc={r.returncode}")

    # The whole-file cap on its own, with the inline cap raised out of the way.
    d = copy.deepcopy(htf)
    d["phases"][0].setdefault("outcome_details", []).append(
        {"code": "message", "description": "x" * 1_200_000})
    rc, cs, _ = gate(work, d, P, extra=("--max-attachment-bytes", "4000000"))
    ok = rc == 1 and "too-big" in cs
    report("reject a submission over the whole-file cap", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} reject whole-file too-big             rc={rc} {','.join(cs)}")

    # The inline cap on the CTRF path, which did not exist. normalise.py sets
    # attachments to a reference map with no bytes in it and CTRF's bulk lives in
    # trace, stdout and stderr, so 350 KB of trace plus 350 KB of stdout was
    # accepted with zero warnings and only the 1 MB whole-file cap could bite.
    d = copy.deepcopy(ctrf)
    d["results"]["tests"][0]["trace"] = "T" * 350_000
    d["results"]["tests"][0]["stdout"] = ["O" * 350_000]
    rc, cs, _ = gate(work, d, P, extra=("--max-file-bytes", "4000000"))
    ok = rc == 1 and "too-big" in cs
    report("reject CTRF inline bulk over the cap", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} reject CTRF inline bulk               rc={rc} {','.join(cs)}")

    # And a CTRF snippet is source code, so it must warn like OpenHTF's
    # codeinfo.sourcecode. It was dropped silently, so a closed-source
    # integrator's snippet reached a public repository with no warning at all.
    d = copy.deepcopy(ctrf)
    d["results"]["tests"][0]["snippet"] = "def secret_limit(): return 32.0"
    rc, cs, out = gate(work, d, P)
    ok = rc == 0 and "sourcecode" in out
    report("accept + warn on a CTRF snippet", ok, f"rc={rc} warned={'sourcecode' in out}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept+warn CTRF snippet              rc={rc}")

    # The run outcome must not come from summary.failed alone: a report claiming
    # failed: 0 with a failed test in tests[] was normalised to PASS.
    d = copy.deepcopy(ctrf)
    for t in d["results"]["tests"]:
        t["status"] = "passed"
    d["results"]["tests"][0]["status"] = "failed"
    d["results"]["summary"].update(failed=0, passed=len(d["results"]["tests"]))
    tmp = os.path.join(work, "_outcome.json")
    with open(tmp, "w") as f:
        json.dump(d, f)
    r = sh(work, "normalise.py", "_outcome.json")
    got = json.loads(r.stdout).get("outcome") if r.returncode == 0 else None
    os.remove(tmp)
    ok = got == "FAIL"
    report("run outcome is cross-checked against tests[]", ok,
           f"outcome={got}, summary.failed was 0 with one failed test")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} outcome cross-checked                 {got}")

    # Phase timings must not be invented. They were derived from summary.start
    # plus duration, so every phase of a run looked concurrent.
    d = copy.deepcopy(ctrf)
    for t in d["results"]["tests"]:
        t.pop("start", None)
        t.pop("stop", None)
    tmp = os.path.join(work, "_timings.json")
    with open(tmp, "w") as f:
        json.dump(d, f)
    r = sh(work, "normalise.py", "_timings.json")
    phases = json.loads(r.stdout)["phases"] if r.returncode == 0 else []
    os.remove(tmp)
    ok = bool(phases) and not any("start_time_millis" in ph for ph in phases)
    report("no phase timings are invented when CTRF gives none", ok,
           f"{sum('start_time_millis' in ph for ph in phases)} of {len(phases)} phases timed")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} no invented phase timings")

    # Synthesised values are declared as such on the record.
    r = sh(work, "normalise.py", os.path.join("examples", "ctrf-hil-dc.json"))
    syn = (((json.loads(r.stdout).get("metadata") or {}).get("source") or {})
           .get("synthesised") if r.returncode == 0 else None)
    ok = syn == ["dut_id", "station_id"]
    report("a converted record declares which values were synthesised", ok, f"got {syn}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} synthesis declared                    {syn}")

    # attachments over the cap must reject
    d = copy.deepcopy(htf)
    d["phases"][0]["attachments"] = {"a.png": {"mimetype": "image/png", "sha1": "x",
                                               "size": 400000,
                                               "data": base64.b64encode(b"x" * 400000).decode()}}
    rc, cs, _ = gate(work, d, P)
    ok = rc == 1 and "too-big" in cs
    report("reject attachments over cap", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} reject attachments over cap          rc={rc} {','.join(cs)}")

    # sourcecode WARNS and is accepted - warn-only by design
    d = copy.deepcopy(htf)
    d["phases"][0]["codeinfo"] = {"name": "p", "docstring": "d", "sourcecode": "def p(): pass"}
    rc, cs, out = gate(work, d, P)
    ok = rc == 0 and "sourcecode" in out
    report("accept + warn on sourcecode", ok, f"rc={rc} warned={'sourcecode' in out}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept+warn sourcecode                rc={rc}")

    # HIL via CTRF, WITH a board, must be ACCEPTED. HIL is supported on both paths;
    # the board is the price CTRF pays for having no dut_id.
    rc, cs, _ = gate(work, copy.deepcopy(ctrf), P)
    ok = rc == 0
    report("accept HIL via CTRF with a board", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept HIL via CTRF with a board      rc={rc}")

    # SIL with no hardware must be ACCEPTED - a board applies to HIL only
    d = copy.deepcopy(ctrf)
    d["results"]["environment"]["testEnvironment"] = "SIL"
    d["results"]["extra"].pop("hardware")
    rc, cs, _ = gate(work, d, P)
    ok = rc == 0
    report("accept SIL without hardware", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept SIL without hardware           rc={rc}")

    # every example and every real submission must be accepted. The path has to match
    # the example's OWN integrator id, or id-mismatch fires - correctly - and the test
    # is measuring the harness rather than the gate.
    for name in sorted(os.listdir(os.path.join(work, "examples"))):
        if not name.endswith(".json"):
            continue
        d = json.load(open(os.path.join(work, "examples", name)))
        who = ((d.get("metadata") or d.get("results") or {}).get("integrator")
               or ((d.get("results") or {}).get("extra") or {}).get("integrator") or {}).get("id")
        assert who, f"{name} has no integrator id - the examples must carry one"
        rc, cs, _ = gate(work, d, f"results/{who}/testing/main/20260901.json")
        ok = rc == 0
        report(f"accept example {name}", ok, f"rc={rc} codes={cs}")
        if verbose or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'} accept example {name:28} rc={rc}")

    for dirpath, _, files in os.walk(os.path.join(work, "results")):
        for fn in sorted(files):
            if not fn.endswith(".json"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), work).replace(os.sep, "/")
            r = sh(work, "validate_submission.py", rel, "--skip-commit-check")
            ok = r.returncode == 0
            report(f"accept committed {rel}", ok, f"rc={r.returncode} {codes(r.stdout)}")
            if verbose or not ok:
                print(f"  {'ok  ' if ok else 'FAIL'} accept committed {rel:44} rc={r.returncode}")


def suite_intake(work, verbose):
    """The pull-request classifier: what a pull request is allowed to be.

    This is the suite for the defect that destroyed data. A deletion-only fork
    pull request produced an empty change list, every loop after it iterated zero
    times, and auto-merge was armed on a vacuous pass - so any GitHub account
    could have deleted every integrator's records with no write access and no
    human involved. results/ is append-only, so the refusal is structural.
    """
    def kinds(files, want_kind, want_reason=None):
        out = os.path.join(work, "_gh_out")
        added = os.path.join(work, "_added.txt")
        verdict = os.path.join(work, "_verdict.md")
        for f in (out, added, verdict):
            if os.path.exists(f):
                os.remove(f)
        r = subprocess.run(
            [PY, os.path.join(work, "scripts", "classify_pr_files.py"),
             "--github-output", out, "--added", added, "--verdict", verdict],
            input=json.dumps(files), capture_output=True, text=True, cwd=work)
        got = {}
        if os.path.exists(out):
            for ln in open(out):
                if "=" in ln:
                    k, v = ln.strip().split("=", 1)
                    got[k] = v
        accepted = ([ln.strip() for ln in open(added) if ln.strip()]
                    if os.path.exists(added) else [])
        return r.returncode, got, accepted, (open(verdict).read()
                                             if os.path.exists(verdict) else "")

    SUB = "results/deadbeef-charge/testing/main/20260909.json"
    OTHER = "results/anon-7f3a/testing/main/20260909.json"
    cases = [
        # (label, files, expected kind, expected reason, must accept nothing)
        ("deletion-only pull request",
         [{"status": "removed", "filename": SUB}], "refuse", "append-only"),
        ("deletion of every record",
         [{"status": "removed", "filename": SUB},
          {"status": "removed", "filename": OTHER}], "refuse", "append-only"),
        ("edit of an existing record",
         [{"status": "modified", "filename": SUB}], "refuse", "append-only"),
        ("rename that re-dates a record",
         [{"status": "renamed", "filename": "results/deadbeef-charge/testing/main/20260910.json",
           "previous_filename": OTHER}], "refuse", "append-only"),
        ("rename out of results/",
         [{"status": "renamed", "filename": "docs/x.json",
           "previous_filename": SUB}], "refuse", "append-only"),
        ("an add alongside a deletion",
         [{"status": "added", "filename": "results/deadbeef-charge/testing/main/20260910.json"},
          {"status": "removed", "filename": OTHER}], "refuse", "append-only"),
        ("a submission that also changes code",
         [{"status": "added", "filename": "results/deadbeef-charge/testing/main/20260910.json"},
          {"status": "modified", "filename": "scripts/validate_submission.py"}],
         "refuse", "mixed"),
        ("a path that escapes results/",
         [{"status": "added", "filename": "results/../../etc/cron.d/x.json"}],
         "refuse", "bad-path"),
        ("a filename the gate would not accept",
         [{"status": "added", "filename": "results/deadbeef-charge/testing/main/nope.json"}],
         "refuse", "bad-path"),
        # An allowlist pull request must not be refused and must not deadlock: the
        # required check has to report on a pull request that is not a submission.
        ("an allowlist-only pull request",
         [{"status": "modified", "filename": "integrators/allowlist.yaml"}],
         "not-a-submission", ""),
        ("a pull request that changes nothing", [], "not-a-submission", ""),
        ("a genuine submission",
         [{"status": "added", "filename": "results/deadbeef-charge/testing/main/20260910.json"}],
         "submission", ""),
    ]
    for label, files, want_kind, want_reason in cases:
        rc, got, accepted, verdict = kinds(files, want_kind, want_reason)
        ok = (rc == 0 and got.get("kind") == want_kind
              and got.get("reason", "") == want_reason)
        # Nothing may be fetched for a pull request that is not a submission.
        if want_kind == "submission":
            ok = ok and accepted == [f["filename"] for f in files]
        else:
            ok = ok and accepted == []
        # A refusal has to say why, or the submitter gets a red check and silence.
        if want_kind == "refuse":
            ok = ok and "rejected" in verdict
        report(f"intake: {label} -> {want_kind}", ok,
               f"rc={rc} got={got} added={accepted}")
        if verbose or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'} intake: {label:38} "
                  f"{got.get('kind')}/{got.get('reason', '')}")

    # A filename is attacker-controlled and was interpolated raw into the comment,
    # so a backtick in one broke out of its code span.
    rc, got, accepted, verdict = kinds(
        [{"status": "removed", "filename": "results/a/testing/main/`id`.json"}],
        "refuse", "append-only")
    ok = got.get("kind") == "refuse" and "`id`" not in verdict
    report("intake: a backtick in a filename cannot break out of its code span",
           ok, verdict[:200])
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} intake: backtick in filename is neutralised")

    # A malformed API response is a tool error, not a silent pass.
    r = subprocess.run([PY, os.path.join(work, "scripts", "classify_pr_files.py")],
                       input="not json", capture_output=True, text=True, cwd=work)
    ok = r.returncode == 2
    report("intake: a malformed files API response is a tool error", ok,
           f"rc={r.returncode}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} intake: malformed API response rc={r.returncode}")


def suite_normalise(work, verbose):
    for name, want in (("minimal-ctrf.json", "ctrf"), ("ctrf-hil-dc.json", "ctrf"),
                       ("minimal-openhtf.json", "openhtf"), ("openhtf-hil-dc.json", "openhtf")):
        r = sh(work, "normalise.py", os.path.join("examples", name), "--print-format")
        got = r.stdout.strip()
        ok = got == want
        report(f"detect {name} as {want}", ok, f"got {got}")
        if verbose or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'} detect {name:26} -> {got}")

    # CTRF status mapping, including pending -> SKIP which OpenHTF has no word for
    src = json.load(open(os.path.join(work, "examples", "ctrf-hil-dc.json")))
    src["results"]["tests"] = [
        {"name": "a", "status": "passed", "duration": 1},
        {"name": "b", "status": "failed", "duration": 1},
        {"name": "c", "status": "skipped", "duration": 1},
        {"name": "d", "status": "pending", "duration": 1},
        {"name": "e", "status": "other", "duration": 1},
    ]
    tmp = os.path.join(work, "_map.json")
    with open(tmp, "w") as f:
        json.dump(src, f)
    r = sh(work, "normalise.py", "_map.json")
    got = [p["outcome"] for p in json.loads(r.stdout)["phases"]]
    want = ["PASS", "FAIL", "SKIP", "SKIP", "ERROR"]
    os.remove(tmp)
    ok = got == want
    report("ctrf status -> openhtf outcome mapping", ok, f"got {got} want {want}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} status mapping {got}")

    # measurements survive an OpenHTF passthrough
    r = sh(work, "normalise.py", os.path.join("examples", "openhtf-hil-dc.json"))
    rec = json.loads(r.stdout)
    n = sum(len(p.get("measurements") or {}) for p in rec["phases"])
    ok = n == 3
    report("openhtf measurements survive normalisation", ok, f"got {n}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} measurements survive: {n}")


def newest_moved_at(work):
    """The newest moved_at in the log, as a datetime."""
    import datetime
    with open(os.path.join(work, "pointers.json")) as f:
        log = json.load(f).get("log") or []
    stamps = [datetime.datetime.fromisoformat(e["moved_at"].replace("Z", "+00:00"))
              for e in log if e.get("moved_at")]
    return max(stamps)


def suite_pointers(work, verbose):
    import datetime
    # The clock is injected. Without --now this check compared the committed log
    # against the real wall clock, so the suite went red on 2026-09-20 with no
    # code change - under a check named "pointer log is internally consistent",
    # which points at the log rather than at the calendar. That is how a suite
    # gets called flaky and then ignored.
    base = newest_moved_at(work)
    fresh = (base + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    aged = (base + datetime.timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")

    r = sh(work, "verify_pointers.py", "--offline", "--now", fresh)
    ok = r.returncode == 0
    report("pointer log is internally consistent", ok, r.stdout[-200:])
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} verify_pointers --offline rc={r.returncode}")

    # And the liveness check has to actually fire. It is the one that surfaces a
    # dead pointer job in a repository this one does not control.
    r = sh(work, "verify_pointers.py", "--offline", "--now", aged)
    ok = r.returncode == 1 and "Has the CI job stopped running?" in r.stdout
    report("verifier fails a pointer that has stopped moving", ok,
           f"rc={r.returncode} {r.stdout[-160:]}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} verifier fails a stalled pointer rc={r.returncode}")

    r = sh(work, "verify_pointers.py", "--offline", "--now", "the day before yesterday")
    ok = r.returncode == 2
    report("verifier refuses an unparseable --now", ok, f"rc={r.returncode}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} verifier refuses bad --now rc={r.returncode}")

    pj = os.path.join(work, "pointers.json")
    sha_a = "0" * 39 + "a"
    cases = [
        ("undeclared pointer", ["--pointer", "testing/rogue", "--tracks", "main",
                                "--sha", sha_a, "--moved-at", "2026-09-10T00:00:00Z"],
         1, "is not declared in pointers.json"),
        ("tracks disagrees with declaration", ["--pointer", "testing/main", "--tracks", "stable/2026.02",
                                               "--sha", sha_a, "--moved-at", "2026-09-10T00:00:00Z"],
         1, "is declared as tracking"),
        ("short sha", ["--pointer", "testing/main", "--tracks", "main",
                       "--sha", "30150a8", "--moved-at", "2026-09-10T00:00:00Z"],
         2, "must be a full 40-character SHA"),
        ("bad timestamp", ["--pointer", "testing/main", "--tracks", "main",
                           "--sha", sha_a, "--moved-at", "yesterday"],
         2, "must be YYYY-MM-DDTHH:MM:SSZ"),
    ]
    for label, args, want_rc, want_says in cases:
        r = sh(work, "record_pointer_move.py", *args, "--file", pj)
        # The exit code alone is not enough: a KeyError traceback and a deliberate
        # refusal both exit non-zero, and the suite read them as the same thing.
        # A refusal has to have decided to refuse, and say so.
        ok = (r.returncode == want_rc
              and want_says in r.stderr
              and "Traceback" not in r.stderr)
        report(f"recorder refuses {label}", ok,
               f"rc={r.returncode} want {want_rc} stderr={r.stderr[-160:]}")
        if verbose or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'} recorder refuses {label:36} rc={r.returncode}")

    # a genuinely new move appends; repeating it is a no-op
    r1 = sh(work, "record_pointer_move.py", "--pointer", "testing/main", "--tracks", "main",
            "--sha", sha_a, "--moved-at", "2026-09-10T00:00:00Z", "--file", pj)
    n1 = len(json.load(open(pj))["log"])
    r2 = sh(work, "record_pointer_move.py", "--pointer", "testing/main", "--tracks", "main",
            "--sha", sha_a, "--moved-at", "2026-09-10T00:00:00Z", "--file", pj)
    n2 = len(json.load(open(pj))["log"])
    ok = r1.returncode == 0 and r2.returncode == 0 and n2 == n1
    report("recorder append then idempotent repeat", ok, f"rc={r1.returncode}/{r2.returncode} {n1}->{n2}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} recorder append+idempotent  {n1} -> {n2}")

    # revisiting an earlier sha is refused
    first = next(e["sha"] for e in json.load(open(pj))["log"]
                 if e["pointer"] == "testing/main")
    r = sh(work, "record_pointer_move.py", "--pointer", "testing/main", "--tracks", "main",
           "--sha", first, "--moved-at", "2026-09-11T00:00:00Z", "--file", pj)
    ok = (r.returncode == 1 and "already appears earlier in the log" in r.stderr
          and "Traceback" not in r.stderr)
    report("recorder refuses revisiting an earlier sha", ok,
           f"rc={r.returncode} stderr={r.stderr[-160:]}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} recorder refuses revisit    rc={r.returncode}")


def suite_archive(work, verbose):
    src = json.load(open(os.path.join(work, "examples", "openhtf-hil-dc.json")))
    src["start_time_millis"] = 1_600_000_000_000
    src["end_time_millis"] = 1_600_000_600_000
    for ph in src["phases"]:
        ph["start_time_millis"] = 1_600_000_000_000
        ph["end_time_millis"] = 1_600_000_100_000
    src["phases"][0]["attachments"] = {"a.png": {"mimetype": "image/png", "sha1": "x",
                                                 "size": 1000, "data": base64.b64encode(b"y" * 1000).decode()}}
    src["phases"][0]["codeinfo"] = {"name": "p", "docstring": "d", "sourcecode": "secret"}
    src["phases"][1].setdefault("outcome_details", []).append({"code": "trace", "description": "T" * 500})
    rel = "results/deadbeef-charge/testing/main/20200913.json"
    full = os.path.join(work, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        json.dump(src, f, indent=2)

    r = sh(work, "archive.py", "--months", "6", "--apply")
    dst = os.path.join(work, "archive", "deadbeef-charge", "testing", "main", "20200913.json")
    ok = r.returncode == 0 and os.path.exists(dst) and not os.path.exists(full)
    report("archive moves an old record", ok, r.stdout[-200:])
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} archive moves an old record")

    # The content assertions run whether or not the move worked. They used to sit
    # behind `if ok:`, so breaking the move deleted six checks from the count
    # instead of failing them - one red where there should have been seven.
    a = json.load(open(dst)) if os.path.exists(dst) else {}
    phases = a.get("phases") or [{}, {}, {}]
    p0, p1 = phases[0], phases[1]
    checks = [
        ("attachments stripped", ok and not p0.get("attachments")),
        ("codeinfo stripped", ok and "codeinfo" not in p0),
        ("trace stripped", ok and all(d.get("code") != "trace"
                                      for d in p1.get("outcome_details") or [])),
        ("measurements kept", len(p0.get("measurements") or {}) == 2),
        ("phase outcomes kept",
         [ph.get("outcome") for ph in phases] == ["PASS", "FAIL", "SKIP"]),
        ("metadata kept",
         bool(((a.get("metadata") or {}).get("everest") or {}).get("commit"))),
        ("archived flag set",
         ((a.get("metadata") or {}).get("source") or {}).get("archived") is True),
    ]
    for label, cond in checks:
        report(f"archive: {label}", cond, f"archived record at {dst}")
        if verbose or not cond:
            print(f"  {'ok  ' if cond else 'FAIL'} archive: {label}")

    # An archived record must still satisfy the metadata contract. It did not:
    # archive.py writes metadata.source.archived into a subtree declared
    # additionalProperties: false, so every archived record was unvalidatable.
    import jsonschema
    with open(os.path.join(work, "schema", "everest-htf-metadata.schema.json")) as f:
        sch = json.load(f)
    errs = [e.message for e in
            jsonschema.Draft7Validator(sch).iter_errors(a.get("metadata") or {})]
    report("archive: archived record still validates", ok and not errs, "; ".join(errs[:3]))
    if verbose or errs:
        print(f"  {'ok  ' if not errs else 'FAIL'} archive: archived record still validates")
    shutil.rmtree(os.path.join(work, "archive"), ignore_errors=True)


def suite_staleness(work, verbose):
    sys.path.insert(0, os.path.join(work, "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("bs", os.path.join(work, "scripts", "build_status.py"))
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)
    NOW = 1_800_000_000_000
    tips = {"testing/main": "b" * 40}
    day = 86_400_000
    cases = [
        ("tip unchanged, old", {"pointer": "testing/main", "commit": "b" * 40, "ranAt": NOW - 40 * day}, False),
        ("moved, inside grace", {"pointer": "testing/main", "commit": "a" * 40, "ranAt": NOW - 3 * day}, False),
        ("moved, 13.9 days", {"pointer": "testing/main", "commit": "a" * 40, "ranAt": int(NOW - 13.9 * day)}, False),
        ("moved, 14.1 days", {"pointer": "testing/main", "commit": "a" * 40, "ranAt": int(NOW - 14.1 * day)}, True),
        ("no tip known", {"pointer": "testing/other", "commit": "a" * 40, "ranAt": NOW - 40 * day}, None),
        ("no timestamp", {"pointer": "testing/main", "commit": "a" * 40, "ranAt": 0}, None),
    ]
    for label, row, want in cases:
        got = bs.is_stale(row, tips, 14, NOW)
        ok = got is want
        report(f"staleness: {label}", ok, f"got {got} want {want}")
        if verbose or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'} staleness {label:24} -> {got}")


def load_build_status(work):
    """Import the copy of build_status.py under `work`, so ROOT is the copy."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bs_" + os.path.basename(work), os.path.join(work, "scripts", "build_status.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cards_of(page):
    """Each rendered submission card, as its own chunk of HTML.

    The page also renders freshness pills in its legend, so counting pills
    across the whole document would pass even with every card unmarked.
    """
    return [c.split("</article>")[0] for c in page.split("<article ")[1:]
            if c.startswith('class="card v-')]


def suite_docs(work, verbose):
    """Facts the documentation states about the tooling, checked against it.

    Every one of these was wrong at some point, and each is the kind of thing a
    reader trusts precisely because it looks specific: the documents stating
    the check count said "69 checks" against a 70-check suite, and
    CONTRIBUTING.md documented an `appVersion` value the gate rejects four ways.
    """
    def check(label, cond, detail=""):
        report(label, cond, detail)
        if verbose or not cond:
            print(f"  {'ok  ' if cond else 'FAIL'} {label}"
                  + (f"   {detail}" if detail and not cond else ""))

    # The documented check count, in the two documents that state it and in the
    # CI guard that asserts it. Four places, and they drifted.
    for doc in ("README.md", "CONTRIBUTING.md"):
        with open(os.path.join(work, doc)) as f:
            claimed = set(re.findall(r"(\d+) checks", f.read()))
        check(f"docs: {doc} states the real check count",
              claimed == {str(TOTAL)}, f"claims {sorted(claimed)}, suite is {TOTAL}")
    with open(os.path.join(work, ".github", "workflows", "selftest.yml")) as f:
        guard = set(re.findall(r"EXPECTED_CHECKS: '(\d+)'", f.read()))
    check("docs: the CI guard expects the real check count",
          guard == {str(TOTAL)}, f"guard says {sorted(guard)}, suite is {TOTAL}")

    # github-script v9 runs the script body as ESM, where require() does not
    # exist. Nothing else here executes those bodies, so a require() left behind
    # fails only in production - silently, because the steps that use them post
    # the gate's verdict and open the pointer issue rather than deciding a check.
    wfdir = os.path.join(work, ".github", "workflows")
    offenders = []
    for fn in sorted(os.listdir(wfdir)):
        with open(os.path.join(wfdir, fn)) as f:
            text = f.read()
        if "actions/github-script@" not in text:
            continue
        # A full-line comment may name it while explaining why it is gone; a call
        # may not. Only whole-line comments are stripped, so a trailing one still
        # counts - a commented-out call is a trap for whoever edits next.
        code = [ln for ln in text.splitlines()
                if not ln.lstrip().startswith(("//", "#"))]
        if any("require(" in ln for ln in code):
            offenders.append(fn)
    check("docs: no workflow calls require() in a github-script body",
          not offenders, f"require() in {offenders}")

    # Every file named in README.md's layout table has to exist, and every
    # script has to be named there. The table listed ctrf-hil-dc.json twice and
    # omitted openhtf-hil-dc.json entirely.
    with open(os.path.join(work, "README.md")) as f:
        readme = f.read()
    layout = readme.split("## Layout", 1)[-1].split("\n## ", 1)[0]
    listed = set(re.findall(r"^\| \[?`([^`]+)`\]?[^|]*\|", layout, re.M))
    ghosts = sorted(n for n in listed
                    if not any(c in n for c in "<>*")
                    and not os.path.exists(os.path.join(work, n)))
    check("docs: every path in README's layout table exists", not ghosts, f"missing {ghosts}")

    scripts = {f"scripts/{n}" for n in os.listdir(os.path.join(work, "scripts"))
               if n.endswith(".py")}
    check("docs: every script is in README's layout table",
          not (scripts - listed), f"unlisted {sorted(scripts - listed)}")

    # Every markdown link to a file in this repository has to resolve.
    broken = []
    for doc in sorted(n for n in os.listdir(work) if n.endswith(".md")):
        with open(os.path.join(work, doc)) as f:
            for target in re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", f.read()):
                if target.startswith(("http", "mailto")):
                    continue
                if not os.path.exists(os.path.join(work, target)):
                    broken.append(f"{doc} -> {target}")
    check("docs: every in-repo markdown link resolves", not broken, f"broken {broken}")


def suite_build(work, verbose):
    """Cover the status page, not just the exit code of the generator.

    `render()` made to return an empty string once shipped a zero-byte page with
    the whole suite green, and the generator's honesty rules - freshness always
    rendered, a tag only claimed as covered when a phase carrying it passed -
    could each be defeated with a one-line edit and nothing went red. So these
    assert what is ON the page, and the last case drives render() directly
    because the state that matters (stale AND failing at once) is not reachable
    from the committed data.
    """
    def check(label, cond, detail=""):
        report(label, cond, detail)
        if verbose or not cond:
            print(f"  {'ok  ' if cond else 'FAIL'} {label}"
                  + (f"   {detail}" if detail and not cond else ""))

    r = sh(work, "build_status.py", "--offline")
    check("status page generates", r.returncode == 0, r.stdout[-200:])

    summary_path = os.path.join(work, "status", "summary.json")
    page_path = os.path.join(work, "status", "index.html")
    s = json.load(open(summary_path)) if os.path.exists(summary_path) else {}
    page = open(page_path).read() if os.path.exists(page_path) else ""

    check("summary.json has the expected shape",
          "pointers" in s and s.get("internalFormat") == "openhtf")

    # A zero-byte page is the failure this suite exists for. Size alone is a weak
    # assertion, so the page must also carry its own headline and the caveat
    # that this page is the only home for.
    check("index.html is not empty", len(page) > 4_000, f"{len(page)} bytes")
    check("page carries its headline", "<h1>Himalayas</h1>" in page
          and "EVerest federated testing" in page)
    check("page carries the self-reported caveat", "self-reported" in page)

    # Every committed submission has to reach the page. load_results() made to
    # return nothing was green, with all four submissions replaced by "nobody has
    # reported" placeholders.
    files = [os.path.relpath(os.path.join(dp, fn), work).replace(os.sep, "/")
             for dp, _, fns in os.walk(os.path.join(work, "results"))
             for fn in fns if fn.endswith(".json")]
    counted = sum(i["submissions"] for p in (s.get("pointers") or {}).values()
                  for i in (p.get("integrators") or {}).values())
    check("summary.json counts every committed submission",
          bool(files) and counted == len(files), f"{counted} counted, {len(files)} on disk")
    for rel in sorted(files):
        check(f"page links {rel}", rel in page)

    # Freshness is never absent by construction: a missing marker reads as fresh.
    cards = cards_of(page)
    unmarked = [c for c in cards
                if len(re.findall(r'class="pill (?:fresh|stale|unknown)"', c)) != 1]
    check("every card carries exactly one freshness marker",
          bool(cards) and not unmarked, f"{len(cards)} card(s), {len(unmarked)} unmarked")

    # A tag whose only phase was skipped must NOT be claimed as covered.
    seen_states = {v for p in (s.get("pointers") or {}).values()
                   for i in (p.get("integrators") or {}).values()
                   for v in (i.get("coverage") or {}).values()}
    check("a notrun tag survives the rollup",
          "notrun" in seen_states and 'class="ptag notrun"' in page, f"states={sorted(seen_states)}")
    check("a failed tag survives the rollup",
          "failed" in seen_states and 'class="ptag failed"' in page, f"states={sorted(seen_states)}")

    # Measurements are the reason OpenHTF is the internal format, and a
    # measurement outside its limits is the one a reader must not miss.
    mfail = sum(i.get("measurementsFailed") or 0
                for p in (s.get("pointers") or {}).values()
                for i in (p.get("integrators") or {}).values())
    check("a failing measurement reaches the page",
          mfail >= 1 and 'class="mo no"' in page, f"{mfail} failing")

    # A declared pointer nobody has tested is an answer, so it is rendered.
    check("a declared pointer with no results is rendered",
          'class="card empty"' in page)

    # Stale and failing are orthogonal, and one must never suppress the other.
    # Not reachable from the committed data - offline runs know no pointer tips -
    # so drive render() with a moved tip and an aged row.
    bs = load_build_status(work)
    rows = bs.load_results()
    row = None
    for cand in rows:
        if cand["phaseFailed"] and cand["commit"]:
            row = cand
            break
    if row is None:
        check("sample data carries a failing submission to age", False,
              "no committed submission has a failed phase")
        check("a stale card still shows its failing verdict", False, "skipped")
        check("a stale card still shows the stale marker", False, "skipped")
    else:
        row = copy.deepcopy(row)
        row["ranAt"] = int(bs.datetime.datetime.now(bs.datetime.timezone.utc)
                           .timestamp() * 1000) - 60 * 86_400_000
        tips = {row["pointer"]: "f" * 40}          # pointer has moved off it
        page2 = bs.render({row["pointer"]: [row]}, tips, 14, "now",
                          {row["pointer"]: {"tracks": "main"}})
        card = (cards_of(page2) or [""])[0]
        check("sample data carries a failing submission to age", True)
        check("a stale card still shows its failing verdict",
              "phases failed" in card, card[:120])
        check("a stale card still shows the stale marker",
              'class="pill stale"' in card, card[:120])


SUITES = {"gate": suite_gate, "intake": suite_intake, "normalise": suite_normalise,
          "pointers": suite_pointers, "archive": suite_archive,
          "staleness": suite_staleness, "build": suite_build,
          "docs": suite_docs}

# How many checks each suite must run. A suite whose count drops has had cases
# deleted, and deleting a case is the one failure this file cannot otherwise see:
# a suite cut from 70 checks to 3 exits non-zero just as happily. Six
# archive assertions also sat behind an `if ok:`, so breaking the move deleted
# them from the count instead of failing them.
#
# Raise a number when you add cases. If you are lowering one, say in the commit
# message which case you removed and why.
EXPECT = {"gate": 66, "intake": 14, "normalise": 6, "pointers": 9,
          "archive": 9, "staleness": 6, "build": 18, "docs": 7}
TOTAL = sum(EXPECT.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--only", choices=sorted(SUITES), action="append")
    args = ap.parse_args()

    work = tempfile.mkdtemp(prefix="ft-selftest-")
    ran = args.only or sorted(SUITES)
    miscounted = []
    try:
        for item in os.listdir(ROOT):
            if item in (".git", "__pycache__"):
                continue
            src, dst = os.path.join(ROOT, item), os.path.join(work, item)
            (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
        # results/ is empty in the repository until a real integrator publishes.
        # The suites still need accepted submissions to work on - to validate, to
        # build a page from, and to age into a stale card - so the fixtures under
        # testdata/results/ are staged as if they had been accepted. They are
        # deliberately not committed under results/, where they would appear on
        # the public status page as though a real integrator had reported them.
        fixtures = os.path.join(ROOT, "testdata", "results")
        if os.path.isdir(fixtures):
            shutil.copytree(fixtures, os.path.join(work, "results"),
                            dirs_exist_ok=True)
        for name in ran:
            print(f"\n== {name} ==")
            before = RUN
            SUITES[name](work, args.verbose)
            got = RUN - before
            if got != EXPECT[name]:
                miscounted.append(f"suite '{name}' ran {got} checks, expected "
                                  f"{EXPECT[name]}. Cases were added or deleted; "
                                  f"update EXPECT in selftest.py if that was "
                                  f"deliberate.")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    expected = sum(EXPECT[n] for n in ran)
    print(f"\n{'-'*64}\n{RUN} checks, {len(FAILS)} failed"
          + (f", {len(miscounted)} suite(s) miscounted" if miscounted else ""))
    print(f"expected {expected} checks"
          + (" (all suites)" if len(ran) == len(SUITES) else f" from {', '.join(ran)}"))
    for name, detail in FAILS:
        print(f"  FAILED  {name}\n          {detail}")
    for m in miscounted:
        print(f"  MISCOUNT  {m}")
    return 1 if FAILS or miscounted else 0


if __name__ == "__main__":
    sys.exit(main())
