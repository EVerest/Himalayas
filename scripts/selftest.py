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

That guard only works while the count is a property of the test code, so no
check here may be generated per stored submission under results/. Confirming
that the submissions already published still validate is a data check, not a
negative test: it lives in .github/workflows/revalidate-results.yml, which
has no count guard because growing is what it does. Fixtures the repository
owns - examples/ - are covered here, since they change only when someone
edits them and that is exactly what the count guard exists to surface.
"""
import argparse, base64, contextlib, copy, io, json, os, re, shutil, subprocess, sys, tempfile
import urllib.parse

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


def sh(work, *args, env=None):
    return subprocess.run([PY, os.path.join(work, "scripts", args[0]), *args[1:]],
                          capture_output=True, text=True, cwd=work, env=env)


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
    P = "results/example-integrator/testing/main/20260901.json"

    # (label, base, mutation, expected code(s), path). A tuple means EVERY code
    # listed must appear - which is how the hand-written branches get pinned: each
    # of them fires alongside a schema rule that says the same thing less well, so
    # asserting only one code lets the other be deleted with the suite still green.
    cases = [
        ("path: wrong filename", ctrf, None, "bad-path", "results/example-integrator/testing/main/nope.json"),
        ("path: missing pointer dir", ctrf, None, "bad-path", "results/example-integrator/20260901.json"),
        ("path: -1 suffix", ctrf, None, "bad-path", "results/example-integrator/testing/main/20260901-1.json"),
        ("path: label then -1", ctrf, None, "bad-path", "results/example-integrator/testing/main/20260901-nightly-1.json"),
        ("path: label segment starts with a digit", ctrf, None, "bad-path",
         "results/example-integrator/testing/main/20260901-nightly-201ocpp.json"),
        ("path: uppercase label", ctrf, None, "bad-path", "results/example-integrator/testing/main/20260901-NIGHTLY.json"),
        ("path: label of five segments", ctrf, None, "bad-path",
         "results/example-integrator/testing/main/20260901-a-b-c-d-e.json"),
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
        ("healthy: true on a failed build", ctrf, lambda d: d["results"]["extra"]["build"].update(status="failed"),
         "healthy-mismatch", P),
        ("healthy: false on a passed build", ctrf, lambda d: d["results"]["environment"].update(healthy=False),
         "healthy-mismatch", P),
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
         "extra-contract", P),
        # reportUrl is a link a reader follows from the status page: https only,
        # like buildUrl, on both submission paths.
        ("extra: reportUrl not https", ctrf,
         lambda d: d["results"]["extra"].update(reportUrl="http://ci.example/report.pdf"),
         "extra-contract", P),
        ("htf: reportUrl not https", htf,
         lambda d: d["metadata"]["everest"].update(reportUrl="http://ci.example/report.pdf"),
         "htf-metadata", P),
        # reportSha256 pins the file reportUrl names, so it needs one to pin.
        ("extra: reportSha256 not hex", ctrf,
         lambda d: d["results"]["extra"].update(reportSha256="ABC123"), "extra-contract", P),
        ("extra: reportSha256 without reportUrl", ctrf,
         lambda d: (d["results"]["extra"].pop("reportUrl"), d["results"]["extra"].update(reportSha256="0" * 64)),
         "extra-contract", P),
        ("htf: reportSha256 without reportUrl", htf,
         lambda d: (d["metadata"]["everest"].pop("reportUrl"), d["metadata"]["everest"].update(reportSha256="0" * 64)),
         "htf-metadata", P),
        ("extra: testSystem without a name", ctrf,
         lambda d: d["results"]["extra"].update(testSystem={"version": "1.0"}), "extra-contract", P),
        ("htf: testSystem without a name", htf,
         lambda d: d["metadata"].update(testSystem={"version": "1.0"}), "htf-metadata", P),
        # Section 10: a deviation stays failed, an inconclusive result is other.
        ("tests: accepted deviation reported as skipped", ctrf,
         lambda d: d["results"]["tests"][0].update(status="skipped", extra={"acceptedDeviation": {
             "reason": "Kept for deployed EVs", "clause": "V2G3-M06-08"}}), "ctrf-profile", P),
        ("tests: accepted deviation without a clause", ctrf,
         lambda d: d["results"]["tests"][0].update(status="failed", extra={"acceptedDeviation": {
             "reason": "Kept for deployed EVs"}}), "ctrf-profile", P),
        ("tests: inconclusive reported as failed", ctrf,
         lambda d: d["results"]["tests"][0].update(status="failed", extra={"inconclusive": {
             "reason": "Precondition not established"}}), "ctrf-profile", P),
        ("tests: failureKey empty", ctrf,
         lambda d: d["results"]["tests"][0].update(status="failed", extra={"failureKey": ""}), "ctrf-profile", P),
        # description is a one-line title, not a second message.
        ("tests: description over two lines", ctrf,
         lambda d: d["results"]["tests"][0].update(extra={"description": "Title\nand more"}), "ctrf-profile", P),
        ("tests: description too long", ctrf,
         lambda d: d["results"]["tests"][0].update(extra={"description": "x" * 501}), "ctrf-profile", P),
        ("tests: logUrl not https", ctrf,
         lambda d: d["results"]["tests"][0].update(extra={"logUrl": "http://ci.example/t1.log"}), "ctrf-profile", P),
        # configuration is a card title and a summary key, not prose.
        ("extra: configuration is prose", ctrf,
         lambda d: d["results"]["extra"].update(configuration="AC and DC <b>"),
         "extra-contract", P),
        ("htf: configuration is prose", htf,
         lambda d: d["metadata"]["everest"].update(configuration="AC and DC <b>"),
         "htf-metadata", P),
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
    # Invented, nonfunctional credentials, assembled here so no scanner flags this file.
    fake_gh = "gh" + "p_" + "Zq7" * 12
    fake_key = "-----BEGIN " + "PRIVATE KEY-----\nMIIBVAIBADANBg\n-----END " + "PRIVATE KEY-----"
    cases += [
        ("secret: token in a CTRF trace", ctrf,
         lambda d: d["results"]["tests"][0].update(status="failed", trace=f"auth failed for {fake_gh}"),
         "possible-secret", P),
        ("secret: token in an OpenHTF buildUrl", htf,
         lambda d: d["metadata"]["build"].update(buildUrl="https://ci.example/run/7?access_token=" + "Zq7" * 6),
         "possible-secret", P),
        ("secret: key in an OpenHTF attachment", htf,
         lambda d: d["phases"][0]["attachments"].update(
             {"key.pem": {"data": base64.b64encode(fake_key.encode()).decode(), "mimetype": "text/plain"}}),
         "possible-secret", P),
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

    # The verdict is posted as a public comment, so it must not repeat the value,
    # even when the same value also fails a schema rule that quotes what it got.
    leak = copy.deepcopy(ctrf)
    leak["results"]["environment"]["commit"] = fake_gh
    rc, cs, out = gate(work, leak, P)
    ok = rc == 1 and "possible-secret" in cs and fake_gh not in out and "Zq7Zq7" not in out
    report("a flagged credential is not repeated in the verdict", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} a flagged credential is not repeated in the verdict")

    # A valid record the builder cannot handle is refused as our fault, before it
    # merges and stops the site updating. Plant a builder that breaks on a marker.
    bpath = os.path.join(work, "scripts", "build_status.py")
    orig = open(bpath).read()
    marker = "def build_site(recs, declared, logs, allow, vocab, grace_days=GRACE_DAYS, docs=None):\n"
    assert marker in orig, "build_site signature moved; update this case"
    open(bpath, "w").write(orig.replace(marker, marker + "    if any(r['config'] == 'selftest-crash' for r in recs):\n"
                                                        "        raise RuntimeError('selftest-crash')\n", 1))
    crash = copy.deepcopy(ctrf)
    crash["results"]["extra"]["configuration"] = "selftest-crash"
    try:
        rc, cs, out = gate(work, crash, P)
    finally:
        open(bpath, "w").write(orig)
    report("a record that breaks the site build is refused as our fault",
           rc == 2 and "status site build fails" in out and "build_site()" in out, f"rc={rc} codes={cs}")

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
    rc, cs, _ = gate(work, d, "results/example-integrator/testing/stable-1999.01/20260901.json")
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
    authorised = "example-integrator-ci"
    rc, cs, _ = gate(work, copy.deepcopy(ctrf), P, extra=("--author", authorised))
    ok = rc == 0
    report("accept a submission from an authorised account", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept authorised author              rc={rc}")

    rc, cs, _ = gate(work, copy.deepcopy(ctrf), P, extra=("--author", "Example-Integrator-CI"))
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

    # --- the source binding: records the intake copied from a bound repository ---
    # The gate re-reads the source rather than trusting the intake, so these run
    # against a fixture directory standing in for GitHub.
    import yaml
    with open(al) as f:
        original = f.read()
    with open(al, "a") as f:
        f.write("  - id: example-sourced\n    github_users: [example-sourced-ci]\n"
                "    sources:\n      - repo: example/records\n        repo_id: 4242\n"
                "        ref: results\n        path: records/\n        label: nightly\n")
    fix = os.path.join(work, "_sources")
    sd = copy.deepcopy(ctrf)
    sd["results"]["extra"]["integrator"]["id"] = "example-sourced"
    SP = "results/example-sourced/testing/main/20260901-nightly.json"

    def publish(doc, name="20260901-nightly.json"):
        d = os.path.join(fix, "4242", "results", "records", "testing", "main")
        shutil.rmtree(fix, ignore_errors=True)
        os.makedirs(d)
        with open(os.path.join(d, name), "w") as f:
            json.dump(doc, f, indent=2)

    bot = ("--author", "himalayas-accessor[bot]", "--author-id", "332461223",
           "--source-fixtures", fix)
    publish(sd)
    altered = copy.deepcopy(sd)
    altered["results"]["tests"][0]["name"] += " (edited after publishing)"
    source_cases = [
        ("accept an intake record byte-identical to its bound source",
         lambda: gate(work, sd, SP, extra=bot), 0, None),
        ("reject an intake record that differs from its source",
         lambda: gate(work, altered, SP, extra=bot), 1, "source-mismatch"),
        ("reject an intake record its source does not hold",
         lambda: (publish(sd, "20260902-nightly.json"), gate(work, sd, SP, extra=bot))[1],
         1, "source-mismatch"),
        ("reject an intake record without its source's declared label",
         lambda: (publish(sd, "20260901.json"),
                  gate(work, sd, "results/example-sourced/testing/main/20260901.json",
                       extra=bot))[1], 1, "source-mismatch"),
        ("the bot is known by id: its login alone authorizes nothing",
         lambda: (publish(sd), gate(work, sd, SP,
                                    extra=("--author", "himalayas-accessor[bot]",
                                           "--source-fixtures", fix)))[1],
         1, "not-authorised"),
        ("reject a pull request that uses a label reserved for a source",
         lambda: gate(work, sd, SP, extra=("--author", "example-sourced-ci")), 1, "source-label"),
        ("accept a pull request under a sourced id with another label",
         lambda: gate(work, sd, "results/example-sourced/testing/main/20260901-manual.json",
                      extra=("--author", "example-sourced-ci")), 0, None),
        ("accept a pull request under a sourced id with no label",
         lambda: gate(work, sd, "results/example-sourced/testing/main/20260901.json",
                      extra=("--author", "example-sourced-ci")), 0, None),
        ("reject an intake record under an id its source is not bound to",
         lambda: gate(work, ctrf, "results/example-integrator/testing/main/20260901-nightly.json",
                      extra=bot), 1, "source-mismatch"),
    ]
    for label, run, want_rc, want in source_cases:
        rc, cs, _ = run()
        ok = rc == want_rc and (want is None or want in cs)
        report(label, ok, f"rc={rc} codes={cs}")
        if verbose or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'} {label:60} rc={rc} {','.join(cs)}")

    # A source the gate cannot read is ours to fix, and nobody can resubmit an
    # intake record, so the verdict must not tell anyone to.
    shutil.rmtree(os.path.join(fix, "4242"))
    with open(os.path.join(fix, "4242"), "w"):
        pass
    rc, cs, out = gate(work, sd, SP, extra=bot)
    ok = rc == 2 and "could not be read" in out and "stays open" in out
    report("an unreachable source exits 2 and keeps the intake PR open", ok, f"rc={rc}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} unreachable source exits 2, PR stays open   rc={rc}")

    # The intake bot's id reaches the gate through the environment, as the login does.
    publish(sd)
    full = os.path.join(work, SP)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        json.dump(altered, f, indent=2)
    r = subprocess.run([PY, os.path.join(work, "scripts", "validate_submission.py"), SP,
                        "--skip-commit-check", "--source-fixtures", fix],
                       capture_output=True, text=True, cwd=work,
                       env=dict(os.environ, SUBMISSION_AUTHOR="himalayas-accessor[bot]",
                                SUBMISSION_AUTHOR_ID="332461223"))
    os.remove(full)
    ok = r.returncode == 1 and "source-mismatch" in codes(r.stdout)
    report("intake bot id is read from SUBMISSION_AUTHOR_ID", ok, f"rc={r.returncode}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} bot id via SUBMISSION_AUTHOR_ID       rc={r.returncode}")

    # A malformed source is the allowlist's fault, not the submitter's: exit 2.
    with open(al) as f:
        bound = f.read()
    with open(al, "w") as f:
        f.write(bound.replace("repo_id: 4242", "repo_id: example"))
    rc, cs, out = gate(work, sd, SP, extra=bot)
    ok = rc == 2 and "malformed source" in out
    report("a malformed source in the allowlist exits 2", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} malformed allowlist source exits 2   rc={rc}")
    rc, cs, out = gate(work, sd, SP, extra=("--author", "example-sourced-ci"))
    ok = rc == 2 and "being closed" in out
    report("a tool error on a submitter's PR says it is being closed", ok, f"rc={rc}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} tool error closes a submitter's PR   rc={rc}")
    with open(al, "w") as f:
        f.write(original)
    shutil.rmtree(fix, ignore_errors=True)

    # validate-submission.yml closes the bot's rejected pull requests by user id,
    # and leaves its unprocessable ones open, so its literal must be the gate's.
    with open(os.path.join(work, ".github", "workflows", "validate-submission.yml")) as f:
        wf = yaml.safe_load(f)
    sys.path.insert(0, os.path.join(work, "scripts"))
    import sources as src_mod
    close = next((st.get("if", "") for st in wf["jobs"]["validate"]["steps"]
                  if st.get("name", "").startswith("Close the PR")), "")
    me = f"github.event.pull_request.user.id == {src_mod.INTAKE_BOT_ID}"
    ok = (f"(steps.gate.outputs.rc == '1' && {me})" in close
          and f"(steps.gate.outputs.rc == '2' && {me.replace('==', '!=')})" in close)
    report("validate-submission.yml names the gate's intake bot id", ok, close)
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} workflow and gate agree on the bot id")

    # The shipped allowlist must bind no malformed source: the gate would exit 2
    # on every record from that id, and the intake would refuse to run at all.
    with open(os.path.join(ROOT, "integrators", "allowlist.yaml")) as f:
        shipped = {e["id"]: e for e in (yaml.safe_load(f) or {}).get("integrators") or []}
    bad = src_mod.allowlist_problems(shipped)
    report("the shipped allowlist binds no malformed source", not bad, "; ".join(bad))
    if verbose or not bad == []:
        print(f"  {'ok  ' if not bad else 'FAIL'} shipped allowlist sources are well formed")

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

    # A test system and a report hash are optional record fields on both paths.
    d = copy.deepcopy(ctrf)
    d["results"]["extra"].update(testSystem={"name": "Conformance tool", "version": "1.2.0"}, reportSha256="ab" * 32)
    rc_c, cs_c, _ = gate(work, d, P)
    d = copy.deepcopy(htf)
    d["metadata"]["testSystem"] = {"name": "Conformance tool", "version": "1.2.0"}
    d["metadata"]["everest"]["reportSha256"] = "ab" * 32
    rc_h, cs_h, _ = gate(work, d, P)
    ok = rc_c == 0 and rc_h == 0
    report("accept testSystem and reportSha256 (ctrf and openhtf)", ok, f"ctrf rc={rc_c} {cs_c} openhtf rc={rc_h} {cs_h}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept testSystem and reportSha256    rc={rc_c},{rc_h}")

    # The section 10 forms are accepted, and the bare tag from before them warns.
    d = copy.deepcopy(ctrf)
    d["results"]["tests"][0].update(status="failed", extra={"acceptedDeviation": {
        "reason": "Kept for deployed EVs", "clause": "V2G3-M06-08", "since": "2026-09-01"}, "failureKey": "deviation"})
    d["results"]["tests"][1].update(status="other", extra={"inconclusive": {"reason": "Precondition not established"}})
    d["results"]["summary"].update(passed=sum(t["status"] == "passed" for t in d["results"]["tests"]),
                                   failed=sum(t["status"] == "failed" for t in d["results"]["tests"]),
                                   other=sum(t["status"] == "other" for t in d["results"]["tests"]))
    rc, cs, out = gate(work, d, P)
    ok = rc == 0 and "deviation-undeclared" not in out
    report("accept a declared deviation and an inconclusive result", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept deviation and inconclusive     rc={rc}")
    d = copy.deepcopy(ctrf)
    d["results"]["tests"][0].update(status="skipped", tags=["DC", "x-accepted-deviation"])
    d["results"]["summary"].update(passed=sum(t["status"] == "passed" for t in d["results"]["tests"]),
                                   failed=sum(t["status"] == "failed" for t in d["results"]["tests"]),
                                   skipped=sum(t["status"] == "skipped" for t in d["results"]["tests"]))
    rc, cs, out = gate(work, d, P)
    ok = rc == 0 and "deviation-undeclared" in out
    report("accept + warn on a bare x-accepted-deviation tag", ok, f"rc={rc} warned={'deviation-undeclared' in out}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept+warn bare deviation tag        rc={rc}")

    # A label lets two runs of one day coexist without racing for the next -N.
    for name in ("20260901-ocpp201-sil.json", "20260901-ocpp201-sil-2.json"):
        rc, cs, _ = gate(work, copy.deepcopy(ctrf), f"results/example-integrator/testing/main/{name}")
        ok = rc == 0
        report(f"accept a labelled path {name}", ok, f"rc={rc} codes={cs}")
        if verbose or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'} accept {name:36} rc={rc}")

    # A conformance test carries its OCPP version and the test specification it comes from.
    d = copy.deepcopy(ctrf)
    d["results"]["tests"][0]["tags"] = ["OCPP1.6", "OCPP1.6-TestCases"]
    d["results"]["tests"][1]["tags"] = ["OCPP2.0.1", "OCPP2.0.1-Part6"]
    rc, cs, out = gate(work, d, P)
    ok = rc == 0 and "tag-vocabulary" not in out
    report("accept OCPP conformance tags without a vocabulary warning", ok, f"rc={rc} codes={cs}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept OCPP conformance tags          rc={rc}")

    # A failed build is published, with no modules to list and nothing tested.
    d = copy.deepcopy(ctrf)
    d["results"]["extra"]["build"] = {"status": "failed"}
    d["results"]["environment"]["healthy"] = False
    d["results"]["tests"] = []
    d["results"]["summary"].update(tests=0, passed=0, failed=0, skipped=0, pending=0, other=0)
    rc_c, cs_c, out_c = gate(work, d, P)
    d = copy.deepcopy(htf)
    d["metadata"]["build"] = {"status": "failed"}
    rc_h, cs_h, out_h = gate(work, d, P)
    ok = rc_c == 0 and rc_h == 0 and "unknown-module" not in out_c + out_h
    report("accept a failed build with no modules (ctrf, no tests, and openhtf)", ok,
           f"ctrf rc={rc_c} {cs_c} openhtf rc={rc_h} {cs_h}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} accept a failed build                 rc={rc_c},{rc_h}")

    # Every example must be accepted. The path has to match the example's OWN
    # integrator id, or id-mismatch fires - correctly - and the test is measuring
    # the harness rather than the gate.
    #
    # Only examples/, deliberately. Sweeping the stored submissions under results/
    # belongs to revalidate-results.yml: its count grows every time an integrator
    # publishes, and a check count that moves on its own cannot guard anything.
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

    SUB = "results/example-integrator/testing/main/20260909.json"
    OTHER = "results/example-unnamed/testing/main/20260909.json"
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
         [{"status": "renamed", "filename": "results/example-integrator/testing/main/20260910.json",
           "previous_filename": OTHER}], "refuse", "append-only"),
        ("rename out of results/",
         [{"status": "renamed", "filename": "docs/x.json",
           "previous_filename": SUB}], "refuse", "append-only"),
        ("an add alongside a deletion",
         [{"status": "added", "filename": "results/example-integrator/testing/main/20260910.json"},
          {"status": "removed", "filename": OTHER}], "refuse", "append-only"),
        ("a submission that also changes code",
         [{"status": "added", "filename": "results/example-integrator/testing/main/20260910.json"},
          {"status": "modified", "filename": "scripts/validate_submission.py"}],
         "refuse", "mixed"),
        ("a path that escapes results/",
         [{"status": "added", "filename": "results/../../etc/cron.d/x.json"}],
         "refuse", "bad-path"),
        ("a filename the gate would not accept",
         [{"status": "added", "filename": "results/example-integrator/testing/main/nope.json"}],
         "refuse", "bad-path"),
        # An allowlist pull request must not be refused and must not deadlock: the
        # required check has to report on a pull request that is not a submission.
        ("an allowlist-only pull request",
         [{"status": "modified", "filename": "integrators/allowlist.yaml"}],
         "not-a-submission", ""),
        ("a pull request that changes nothing", [], "not-a-submission", ""),
        ("a genuine submission",
         [{"status": "added", "filename": "results/example-integrator/testing/main/20260910.json"}],
         "submission", ""),
        ("a labelled submission",
         [{"status": "added", "filename": "results/example-integrator/testing/main/20260910-ocpp16-sil.json"}],
         "submission", ""),
        ("a label the gate would not accept",
         [{"status": "added", "filename": "results/example-integrator/testing/main/20260910-nightly-1.json"}],
         "refuse", "bad-path"),
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

    # CTRF has no report-link field, so extra.reportUrl must be carried into
    # metadata.everest, where the status page reads it for both formats.
    r = sh(work, "normalise.py", os.path.join("examples", "ctrf-hil-dc.json"))
    got = (json.loads(r.stdout).get("metadata") or {}).get("everest", {}).get("reportUrl")
    want = json.load(open(os.path.join(work, "examples", "ctrf-hil-dc.json")))["results"]["extra"]["reportUrl"]
    ok = got == want
    report("ctrf extra.reportUrl reaches metadata.everest", ok, f"got {got}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} reportUrl carried: {got}")

    # extra.configuration too: the status page groups cards by it.
    got = (json.loads(r.stdout).get("metadata") or {}).get("everest", {}).get("configuration")
    want = json.load(open(os.path.join(work, "examples", "ctrf-hil-dc.json")))["results"]["extra"]["configuration"]
    ok = got == want
    report("ctrf extra.configuration reaches metadata.everest", ok, f"got {got}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} configuration carried: {got}")


def newest_moved_at(work):
    """The newest moved_at in the log, as a datetime."""
    import datetime
    with open(os.path.join(work, "pointers.json")) as f:
        log = json.load(f).get("log") or []
    stamps = [datetime.datetime.fromisoformat(e["moved_at"].replace("Z", "+00:00"))
              for e in log if e.get("moved_at")]
    return max(stamps)


class FakeGitHub:
    """A local stand-in for api.github.com: path -> (status, body), 404 otherwise."""

    def __init__(self):
        import http.server, threading
        routes = self.routes = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = routes.get(self.path, (404, {"message": "Not Found"}))
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        self.env["VERIFY_POINTERS_API"] = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def pending_cases(work, verbose, fresh, moved_at):
    """tracks_pending skips check 2 only while the tracked branch is missing."""
    sha = "0" * 39 + "b"
    fixture = os.path.join(work, "pointers-pending.json")

    def write(spec):
        with open(fixture, "w") as f:
            json.dump({"pointers": {"testing/next": spec},
                       "log": [{"pointer": "testing/next", "tracks": spec.get("tracks"),
                                "sha": sha, "moved_at": moved_at}]}, f)

    gh = FakeGitHub()
    gh.routes["/repos/EVerest/EVerest/branches/testing%2Fnext"] = (200, {"commit": {"sha": sha}})
    tracked = "/repos/EVerest/EVerest/branches/stable%2Fnext"
    pending = {"tracks": "stable/next", "tracks_pending": True}
    cases = [
        # label, spec, tracked branch response, extra args, want rc, want in stdout
        ("passes while the tracked branch is missing", pending, (404, {}), (),
         0, "note: testing/next: tracks stable/next, not created yet; reachability not checked"),
        ("fails once the tracked branch exists", pending, (200, {"commit": {"sha": sha}}), (),
         1, "Remove tracks_pending from pointers.json"),
        ("fails when the branch lookup errors", pending, (500, {}), (),
         1, "could not look up pending branch 'stable/next' (HTTP 500)"),
        ("unset still fails a missing tracked branch", {"tracks": "stable/next"}, (404, {}), (),
         1, "the reachability check could not run (HTTP 404)"),
        ("must be a boolean", {"tracks": "stable/next", "tracks_pending": "yes"}, (404, {}),
         ("--offline",), 1, "tracks_pending must be true or false"),
        ("needs tracks", {"tracks_pending": True}, (404, {}),
         ("--offline",), 1, "tracks_pending is set but the pointer tracks nothing"),
    ]
    try:
        for label, spec, resp, extra, want_rc, want_says in cases:
            write(spec)
            gh.routes[tracked] = resp
            r = sh(work, "verify_pointers.py", "--pointers", fixture, "--now", fresh,
                   *extra, env=gh.env)
            ok = r.returncode == want_rc and want_says in r.stdout and "Traceback" not in r.stderr
            report(f"tracks_pending {label}", ok,
                   f"rc={r.returncode} want {want_rc} {r.stdout[-200:]} {r.stderr[-200:]}")
            if verbose or not ok:
                print(f"  {'ok  ' if ok else 'FAIL'} tracks_pending {label:44} rc={r.returncode}")
    finally:
        gh.close()
        os.remove(fixture)


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

    # The recorder scopes the check to the pointer it just logged, so a sibling
    # pointer's in-flight move cannot fail it.
    r = sh(work, "verify_pointers.py", "--offline", "--now", fresh, "--pointer", "testing/main")
    ok = r.returncode == 0 and "pointers checked: 1 " in r.stdout
    report("verifier scopes to --pointer", ok, f"rc={r.returncode} {r.stdout[-160:]}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} verifier scopes to --pointer rc={r.returncode}")

    r = sh(work, "verify_pointers.py", "--offline", "--now", fresh, "--pointer", "testing/rogue")
    ok = r.returncode == 2
    report("verifier refuses an undeclared --pointer", ok, f"rc={r.returncode}")
    if verbose or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} verifier refuses undeclared --pointer rc={r.returncode}")

    pending_cases(work, verbose, fresh, base.strftime("%Y-%m-%dT%H:%M:%SZ"))

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
    rel = "results/example-integrator/testing/main/20200913.json"
    full = os.path.join(work, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        json.dump(src, f, indent=2)

    r = sh(work, "archive.py", "--months", "6", "--apply")
    dst = os.path.join(work, "archive", "example-integrator", "testing", "main", "20200913.json")
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

    # The pointer log is committed through GitHub's commit API, which is what
    # makes the commit signed and the append a compare-and-swap. A `git push`
    # anywhere in a workflow is either that write reintroduced unsigned - main
    # requires signatures, so it is rejected and the pointer silently never gets
    # logged - or a second, unreviewed write path into this repository.
    pushers = []
    for fn in sorted(os.listdir(wfdir)):
        with open(os.path.join(wfdir, fn)) as f:
            code = [ln for ln in f.read().splitlines()
                    if not ln.lstrip().startswith(("//", "#"))]
        if any(re.search(r"\bgit\s+push\b", ln) for ln in code):
            pushers.append(fn)
    check("docs: no workflow runs git push - the commit API is the only write",
          not pushers, f"git push in {pushers}")

    # The gate has to be able to act on its own verdict. An explicit permissions
    # block is exhaustive, so `contents: read` refuses the auto-merge mutation and
    # an accepted submission sits green and unmerged - which is exactly what
    # happened on the first real submission. A later tidy that narrows this back
    # reintroduces that defect, and nothing else in this repository would notice.
    with open(os.path.join(wfdir, "validate-submission.yml")) as f:
        vs = f.read()
    perms = vs.split("\npermissions:", 1)[-1].split("\n\n", 1)[0]
    check("docs: validate-submission.yml grants contents: write to arm auto-merge",
          "contents: write" in perms, f"permissions block is {perms.strip()!r}")

    # A known NESTED module. Every EVerest module lives below the top level of
    # modules/ - this one under modules/HardwareDrivers/PowerMeters/ - so a list
    # regenerated by anything that stopped recursing loses it, and the gate goes
    # back to telling integrators their real hardware modules are not EVerest
    # modules. The check is on a nested name rather than a count so it names the
    # cause instead of a number that drifts with upstream.
    with open(os.path.join(work, "schema", "everest-modules.txt")) as f:
        mods = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}
    check("docs: the module list keeps a nested module (recursive walk intact)",
          "CarloGavazzi_EM580" in mods, f"{len(mods)} names, nested one absent")

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
    """Cover the status site, not just the exit code of the generator.

    `render()` made to return an empty string once shipped a zero-byte page with
    the whole suite green, and the generator's honesty rules - freshness always
    rendered, a tag only claimed as covered when a test carrying it passed -
    could each be defeated with a one-line edit and nothing went red. So these
    assert what is ON the pages, and the later cases drive build_site() directly
    because the states that matter (stale AND failing at once) are not reachable
    from the committed data.
    """
    def check(label, cond, detail=""):
        report(label, cond, detail)
        if verbose or not cond:
            print(f"  {'ok  ' if cond else 'FAIL'} {label}"
                  + (f"   {detail}" if detail and not cond else ""))

    def rows_of(page):
        """Each overview row, as its own chunk of HTML."""
        return [r.split("</tr>")[0] for r in page.split("<tr data-v=")[1:]]

    stale = os.path.join(work, "status", "setup", "selftest-removed.html")
    os.makedirs(os.path.dirname(stale), exist_ok=True)
    open(stale, "w").write("a page whose record is gone")
    r = sh(work, "build_status.py")
    check("status page generates", r.returncode == 0, r.stdout[-200:])
    check("a rebuild removes output the build no longer makes", not os.path.exists(stale))

    out = os.path.join(work, "status")
    s = {}
    if os.path.exists(os.path.join(out, "summary.json")):
        s = json.load(open(os.path.join(out, "summary.json")))
    site = {}
    for dp, _, fns in os.walk(out):
        for fn in fns:
            if fn.endswith(".html"):
                site[os.path.relpath(os.path.join(dp, fn), out)] = open(os.path.join(dp, fn)).read()
    page = site.get("index.html", "")
    pointers = s.get("pointers") or {}
    setups = [m for p in pointers.values() for m in p.get("setups") or []]

    check("summary.json has the expected shape",
          s.get("version") == 2 and bool(pointers) and s.get("selfReported") is True)

    # A zero-byte page is the failure this suite exists for. Size alone is a weak
    # assertion, so the page must also carry its own headline and the caveat
    # that this page is the only home for.
    check("index.html is not empty", len(page) > 4_000, f"{len(page)} bytes")
    check("page carries its headline", 'class="brand">Himalayas<' in page
          and "EVerest test results, self-reported" in page)
    check("page carries the self-reported caveat", "self-reported" in page)
    check("the EVerest logo links to EVerest on GitHub and the name to the overview",
          '<a class="logo" href="https://github.com/EVerest">' in page and '<a class="home" href="index.html">' in page)
    # Links may leave the site; loaded resources may not, so a page view contacts no third party.
    loads = re.compile(r'(?:src=["\']?|url\(["\']?|<link[^>]*href=["\']?)((?:https?:)?//[^"\' )>]+)')
    remote = sorted({f"{k}: {u}" for k, v in site.items() for u in loads.findall(v)})
    fonts = {(os.path.dirname(k), u) for k, v in site.items() for u in re.findall(r"url\(([^)]+\.woff2)\)", v)}
    missing = sorted(u for d, u in fonts if not os.path.exists(os.path.join(out, d, u)))
    check("pages load nothing from another origin and every font they name is shipped",
          not remote and bool(fonts) and not missing, f"remote: {remote}; missing: {missing}")
    # results/ and schema/ are copied in by the deploy workflow, not by the generator.
    dead = sorted({f"{k}: {h}" for k, v in site.items()
                   for h in re.findall(r'(?:href|src)="([^"#:]+)(?:#[^"]*)?"', v)
                   if not h.split("/")[-2:][0] in ("results", "schema")
                   and not os.path.normpath(h).lstrip("./").startswith(("results/", "schema/"))
                   and not os.path.exists(os.path.normpath(os.path.join(out, os.path.dirname(k), h)))})
    check("every relative link and source on every page points at a shipped file", not dead, f"{dead[:5]}")
    urls = load_build_status(work)
    unlinked = [k for k in site if f'href="{"../" * k.count("/")}privacy.html"' not in site[k]
                or f'href="{urls.SECURITY_POLICY}"' not in site[k]]
    check("every page links the privacy page and the security policy, and the privacy page the LF policy",
          not unlinked and f'href="{urls.LF_PRIVACY}"' in site.get("privacy.html", ""), f"{unlinked[:5]}")
    unlicensed = [k for k in site if f'href="{"../" * k.count("/")}licenses.html"' not in site[k]]
    lic = site.get("licenses.html", "")
    check("every page links the licenses page, which names the project license and each notice",
          not unlicensed and 'href="licenses/Apache-2.0.txt"' in lic
          and all(f'href="licenses/{f}"' in lic for f in os.listdir(os.path.join(work, "licenses"))),
          f"unlinked: {unlicensed[:5]}")

    # Regression badges live on the detailed page, one per setup with a confirmed regression.
    regs = json.load(open(os.path.join(out, "regressions.json"))) if os.path.exists(os.path.join(out, "regressions.json")) else {}
    hit = {(r["integrator"], r["configuration"]) for r in (regs.get("pointers") or {}).get("testing/main", []) if r["confirmed"]}
    det = site.get("detailed.html", "")
    check("regression badges are on the detailed page, not the overview",
          'class="reg"' not in page and 'data-f="regressed"' not in page and det.count('class="reg"') == len(hit)
          and f'▲ {len(hit)} setup{"s" * (len(hit) != 1)} with confirmed regressions' in det,
          f"overview badges {page.count('class=\"reg\"')}, detailed {det.count('class=\"reg\"')}, confirmed setups {len(hit)}")

    # Every committed submission has to reach the site. load_records() made to
    # return nothing was green, with every setup replaced by an empty notice.
    files = [os.path.relpath(os.path.join(dp, fn), work).replace(os.sep, "/")
             for dp, _, fns in os.walk(os.path.join(work, "results"))
             for fn in fns if fn.endswith(".json")]
    counted = (sum(m["submissions"] for m in setups)
               + sum(len(p.get("unattributed") or []) for p in pointers.values()))
    check("summary.json counts every committed submission",
          bool(files) and counted == len(files), f"{counted} counted, {len(files)} on disk")
    # One check over all of them, not one per file: a per-file count grows with
    # the stored data, and EXPECT cannot guard a number that moves on its own.
    every = "".join(site.values())
    unlinked = [rel for rel in sorted(files) if f"../{rel}" not in every]
    check("the pages link every committed submission",
          bool(files) and not unlinked, f"{len(unlinked)} unlinked: {unlinked[:3]}")

    # Freshness is never absent by construction: a missing marker reads as fresh.
    rows = rows_of(page)
    unmarked = [r for r in rows if len(re.findall(r'data-fresh="(?:current|stale|unknown)"', r)) != 1]
    check("every overview row carries exactly one freshness marker",
          bool(rows) and not unmarked, f"{len(rows)} row(s), {len(unmarked)} unmarked")

    # A tag whose only tests were skipped must NOT be claimed as covered.
    judged = [(m, tag) for m in setups for tag, st in (m.get("coverage") or {}).items() if st == "none"]
    tiles = []
    for m, tag in judged:
        sp = next((v for k, v in site.items() if k.startswith("setup") and m["record"] in v), "")
        tiles += re.findall(rf'<button class="cap (\w+)"[^>]*data-tag="{re.escape(tag)}"[^>]*>.*?</span>', sp)
    check("a skipped-only tag reads as not judged",
          bool(judged) and bool(tiles) and all(t == "l1" for t in tiles)
          and "none judged" in every, f"{len(judged)} tag(s), tiles={tiles}")
    states = {st for m in setups for st in (m.get("coverage") or {}).values()}
    check("a failed tag survives the rollup",
          "fail" in states and 'class="cap l3"' in every, f"states={sorted(states)}")

    # Measurements are the reason OpenHTF is the internal format, and a
    # measurement outside its limits is the one a reader must not miss.
    mfail = sum(m.get("measurementsFailed") or 0 for m in setups)
    check("a failing measurement reaches the page",
          mfail >= 1 and 'class="meas-fail"' in every, f"{mfail} failing")

    # The contribute page is generated from CONTRIBUTING.md, so a section the
    # renderer drops would vanish from the site with nothing else going red.
    guide = open(os.path.join(work, "CONTRIBUTING.md")).read()
    guide = re.sub(r"^```.*?^```", "", guide, flags=re.M | re.S)
    seen = {}
    want = [load_build_status(work).md_slug(h, seen) for h in re.findall(r"^#{1,6} (.+)$", guide, re.M)]
    cpage = site.get("contribute.html", "")
    missing = [w for w in want if f'id="{w}"' not in cpage]
    check("the contribute page carries every CONTRIBUTING section, and the overview links it",
          bool(want) and not missing and 'href="contribute.html"' in page, f"missing {missing[:3]}")

    # The All integrators list is the index of who may report, so an allowlisted
    # integrator with nothing on the pointer still gets a row.
    card = page[page.find('class="card allint"'):].split("</details>")[0]
    names = [a["name"] if a["name"] != a["id"] else f'{a["id"]} (name withheld)'
             for a in load_build_status(work).load_allowlist(load_build_status(work).pathlib.Path(work))]
    absent = [n for n in names if f">{n}<" not in card]
    check("the All integrators list names every allowlisted integrator",
          bool(names) and not absent and "not reported yet" in card, f"missing {absent[:3]}")

    # An integrator's logo is shown beside its name wherever the name heads a
    # card, a list row or a setup page, and is published with the site.
    bs = load_build_status(work)
    root = bs.pathlib.Path(work)
    logos = root / bs.INTEG_LOGO_DIR
    logos.mkdir(parents=True, exist_ok=True)
    good = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path fill="#123" d="M0 0h10v10z"/></svg>'
    (logos / "example-integrator.svg").write_text(good)
    (logos / "example-unnamed.svg").write_text(good)
    allow_l = bs.load_allowlist(root)
    declared_l, logs_l = bs.load_pointers(root)
    site_l = bs.build_site(bs.load_records(root), declared_l, logs_l, allow_l,
                           json.load(open(os.path.join(work, "schema", "vocabulary.json"))))
    idx = site_l.get("index.html", "")
    img = 'src="logos/example-integrator.svg"'
    card_l = idx[idx.find('id="integ-example-integrator"'):].split("</summary>")[0]
    list_l = idx[idx.find('class="card allint"'):].split("</details>")[0]
    setup_l = next((v for k, v in site_l.items() if k.startswith("setup/example-integrator-")), "")
    check("an integrator's logo shows on its card, its list row and its setup pages",
          img in card_l and img in list_l and f'src="../logos/example-integrator.svg"' in setup_l
          and site_l.get("logos/example-integrator.svg") == bs.clean_logo(good.encode())[0])

    # An id that withholds its name has no name to put a logo beside, and a logo would name it.
    audit = dict(bs.logo_audit(root, allow_l))
    check("a logo for an id that withholds its name is refused",
          "example-unnamed.svg" not in str(list(site_l))
          and "withholds its name" in audit.get(f"{bs.INTEG_LOGO_DIR}/example-unnamed.svg", ""))
    bad_run = sh(work, "build_status.py", "--check-logos")
    for who in ("example-integrator", "example-unnamed"):
        (logos / f"{who}.svg").unlink()
    good_run = sh(work, "build_status.py", "--check-logos")
    check("--check-logos names a refused logo and passes the committed ones",
          bad_run.returncode == 1 and "example-unnamed.svg: belongs to an id that withholds its name" in bad_run.stdout
          and good_run.returncode == 0 and good_run.stdout.startswith("ok:"),
          f"rc={bad_run.returncode}/{good_run.returncode} {bad_run.stdout[:120]!r}")

    # Each kind of active or external content is refused on its own, in an
    # otherwise valid SVG, and what is published is the re-serialized tree.
    X = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
    unsafe = {
        "script": f'<svg {X}><script>alert(1)</script></svg>',
        "handler": f'<svg {X} onload="alert(1)"/>',
        "animate": f'<svg {X}><a><animate attributeName="href" to="javascript:alert(1)"/></a></svg>',
        "link": f'<svg {X}><a href="https://example.com"><path d="M0 0"/></a></svg>',
        "image": f'<svg {X}><image xlink:href="https://example.com/x.png"/></svg>',
        "use": f'<svg {X}><use href="data:image/svg+xml,&lt;svg/&gt;"/></svg>',
        "foreign": f'<svg {X}><foreignObject><p>x</p></foreignObject></svg>',
        "otherns": f'<svg {X}><h:style xmlns:h="http://www.w3.org/1999/xhtml">p{{}}</h:style></svg>',
        "dtd": f'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "y">]><svg {X}>&x;</svg>',
        "import": f'<svg {X}><style>@import "https://example.com/x.css";</style></svg>',
        "escape": f'<svg {X}><style>.a{{background:u\\72l(https://example.com)}}</style></svg>',
        "url": f'<svg {X}><rect style="fill:url(https://example.com/x)"/></svg>',
        "html": '<html><svg></svg></html>',
        "malformed": f'<svg {X}><path></svg>',
        "size": f'<svg {X}>' + " " * bs.INTEG_LOGO_MAX + '</svg>',
    }
    let_through = [k for k, v in unsafe.items() if bs.clean_logo(v.encode())[0] is not None]
    fine = f'<!-- art --><svg {X} viewBox="0 0 9 9"><defs><linearGradient id="g"><stop offset="0"/></linearGradient></defs>' \
           f'<style>.a{{fill:url(#g)}}</style><use xlink:href="#p"/><path id="p" class="a" d="M0 0h9v9z"/></svg>'
    cleaned, why = bs.clean_logo(fine.encode())
    check("a logo with active or external content is refused",
          not let_through and cleaned is not None and "art" not in cleaned,
          f"accepted {let_through}" if let_through else f"static logo refused: {why}")

    # The logos in the repository itself, which the cases above leave alone.
    committed = bs.logo_audit(bs.pathlib.Path(ROOT), bs.load_allowlist(bs.pathlib.Path(ROOT)))
    check("every committed logo is a safe SVG for a public integrator", not committed, f"{committed[:3]}")

    # A pass rate is passed over judged (passed plus failed) tests, and a
    # setup with any failure never reads 100%.
    cases = [((3, 1), "75%"), ((187, 3), "98.4%"), ((1999, 1), "99.9%"), ((15, 0), "100%")]
    wrong = [(c, bs.pct(bs.pass_rate(*c), c[1])) for c, want in cases if bs.pct(bs.pass_rate(*c), c[1]) != want]
    check("a pass rate is passed over judged tests, never 100% with a failure",
          not wrong and bs.pass_rate(0, 0) is None, f"{wrong}")

    # The pooled rate sums judged tests over the setups that have one, and
    # names the lowest so a large suite cannot hide a small broken one.
    def fake(cfg, p, f):
        return {"config": cfg, "integ_name": "Example", "passed_n": p, "failed_n": f, "rate": bs.pass_rate(p, f)}
    def plain(h):
        return re.sub(r"<[^>]+>", "", h)
    html_txt = bs.pooled_txt([fake("big", 466, 13), fake("small", 3, 1), fake("none", 0, 0)])
    txt = plain(html_txt)
    check("a pooled pass rate sums judged tests and names the lowest setup",
          txt.startswith("97.1% passed") and "lowest 75%" in txt and "Example / small" in html_txt
          and bs.pooled_txt([fake("none", 0, 0)]) == "" and plain(bs.pooled_txt([fake("one", 3, 1)])) == "75% passed", txt)

    # Each rate is colored by its own value, along the failing-to-passing pill
    # colors, so the shade and the number cannot disagree.
    shades = re.findall(r'--p:([0-9.]+)%">([0-9.]+)%<', html_txt + bs.pct_html(1.0, 0))
    check("a pass rate is shaded by its own value",
          shades == [("97.1", "97.1"), ("75", "75"), ("100", "100")]
          and "color-mix(in oklch,var(--ok) var(--p),var(--bad))" in page, f"{shades}")

    # The compare table shows each side's rate under its verdict.
    det = site.get("detailed.html", "")
    cmp_ = det[det.find('id="cmp"'):]
    check("the compare table shows pass rates under verdicts",
          'data-cmp=' in cmp_ and 'class="rate small"' in cmp_)

    # The run strip sizes a failing run by its failed count, scaled to the
    # worst run shown, so 12 failures and 3 do not look the same.
    def run(failed, passed=100, build="passed"):
        return {"commit": "0" * 40, "start": 0, "build": build,
                "tests": [{"outcome": "failed"}] * failed + [{"outcome": "passed"}] * passed}
    strip = bs.run_strip({"runs": [run(0), run(3), run(12), run(0, 0), run(1, build="failed")]})
    bars = re.findall(r'class="(s-[a-z-]+)" style="height:(\d+)px[;"]', strip)
    shades = re.findall(r'class="s-failing" style="height:\d+px;--p:([\d.]+%)"', strip)
    hs = {c: int(h) for c, h in bars}
    check("the run strip sizes and shades failing runs by their failed count",
          [c for c, _ in bars] == ["s-passing", "s-failing", "s-failing", "s-not-exercised", "s-build-failed"]
          and int(bars[1][1]) < int(bars[2][1]) == bs.STRIP_H and int(bars[0][1]) < int(bars[1][1])
          and shades == ["75%", "0%"] and 'title="97.1% passed · 3 failed of 103 judged · ' in strip, f"{bars} {shades}")

    # summary.json carries the unrounded rate for other consumers.
    rates = [(st.get("passRate", "missing"), st["counts"]) for st in setups]
    off = [r for r, c in rates
           if r != bs.pass_rate(sum(v.get("passed", 0) for v in c.values()), sum(v.get("failed", 0) for v in c.values()))]
    check("summary.json carries each setup's pass rate", bool(rates) and not off, f"{off[:3]}")

    # A pointer is named by the EVerest line it follows, and says which
    # EVerest branch it tracks.
    specs = json.load(open(os.path.join(work, "pointers.json")))["pointers"]
    bad_spec = [n for n, sp in specs.items()
                if not sp.get("label") or sp.get("kind") not in bs.POINTER_KINDS]
    labels = [sp.get("label") for sp in specs.values()]
    check("every declared pointer has a label and a known kind",
          not bad_spec and len(set(labels)) == len(labels), f"{bad_spec}")
    main_spec = specs.get(bs.DEFAULT_POINTER, {})
    opts = re.findall(r"<option [^>]*>([^<]*)</option>", page)
    check("pages name a pointer by its label and the branch it tracks",
          f'<h1><a class="ptitle" href="https://github.com/EVerest/EVerest/tree/{main_spec.get("tracks")}">{main_spec.get("label")}</a></h1>' in page
          and f"tracking <code>{main_spec.get('tracks')}</code>" in page
          and all(any(o.startswith(l) for o in opts) for l in labels)
          and f"<title>Himalayas · {main_spec.get('label')}" in page, f"options {opts}")

    # A pointer whose tracked branch does not exist yet is not linked to it.
    declared_p, logs_p = bs.load_pointers(root)
    pend = dict(declared_p, **{"testing/selftest-pending": {"label": "9999.99", "kind": "stable",
                                                          "tracks": "stable/9999.99", "tracks_pending": True}})
    pend_page = bs.build_site([], pend, logs_p, bs.load_allowlist(root),
                              json.load(open(os.path.join(work, "schema", "vocabulary.json")))).get(bs.pointer_file("index", "testing/selftest-pending"), "")
    check("a pointer whose tracked branch is not created yet is not linked",
          "<h1>9999.99</h1>" in pend_page and "tracking <code>stable/9999.99</code>, not created yet" in pend_page
          and "tree/stable/9999.99" not in pend_page)

    # Dates read YYYY-MM-DD or DD-MM, never month first.
    src = open(os.path.join(work, "scripts", "build_status.py")).read()
    bad_fmt = re.findall(r"(?<!%Y-)%m-%d|%m/%d", src)
    check("dates are written YYYY-MM-DD or DD-MM", not bad_fmt, f"{len(bad_fmt)} month-first format(s)")

    # A sha a reader sees opens that commit in EVerest. A call site that skips
    # commit_link renders a bare <code> sha and nothing else would notice. The
    # licenses page cites other repositories' commits.
    ours = "".join(v for k, v in site.items() if k != "licenses.html")
    bare = sorted({sha for sha in re.findall(r"(?<!/commit/[0-9a-f]{40}\">)<code>([0-9a-f]{7})</code>", ours)})
    check("every commit on the pages links to EVerest",
          'href="https://github.com/EVerest/EVerest/commit/' in every and not bare,
          f"{len(bare)} unlinked: {bare[:3]}")

    bs = load_build_status(work)
    root = bs.pathlib.Path(work)
    declared, logs = bs.load_pointers(root)
    allow = bs.load_allowlist(root)
    vocab = json.load(open(os.path.join(work, "schema", "vocabulary.json")))
    recs = bs.load_records(root)

    # A declared pointer nobody has tested is an answer, so it is rendered.
    empty = dict(declared, **{"testing/selftest-empty": {"tracks": "main"}})
    files_e = bs.build_site([], empty, logs, allow, vocab)
    check("a declared pointer with no results is rendered",
          'class="notice"' in files_e.get(bs.pointer_file("index", "testing/selftest-empty"), ""))

    # Stale and failing are orthogonal, and one must never suppress the other.
    # Not reachable from the committed data, so age a failing record at a commit
    # the pointer has since moved off.
    main = bs.DEFAULT_POINTER
    shas = [e["sha"] for e in logs.get(main, [])]
    rec = next((x for x in recs if bs.verdict(x) == "failing"), None)
    if rec is None or len(shas) < 2:
        check("sample data carries a failing submission to age", False,
              "no committed submission fails, or the pointer never moved")
        check("a stale row still shows its failing verdict", False, "skipped")
        check("a stale row still shows the stale marker", False, "skipped")
        check("a stale setup is left out of the verdict buttons and offered under stale", False, "skipped")
    else:
        rec = dict(copy.deepcopy(rec), pointer=main, commit=shas[0])
        rec["start"] = int(bs.NOW.timestamp() * 1000) - 60 * 86_400_000
        row = (rows_of(bs.build_site([rec], declared, logs, allow, vocab, 14)["index.html"]) or [""])[0]
        check("sample data carries a failing submission to age", True)
        check("a stale row still shows its failing verdict", row.startswith('"failing"'), row[:120])
        check("a stale row still shows the stale marker", 'data-fresh="stale"' in row, row[:200])
        # The verdict buttons count current setups only, so the aged failure must
        # leave them and be offered under its own button.
        aged = bs.build_site([rec], declared, logs, allow, vocab, 14)["index.html"]
        bar = aged[aged.find('class="summarybar"'):][:600]
        check("a stale setup is left out of the verdict buttons and offered under stale",
              'data-f="stale"' in bar and 'data-f="failing"' not in bar, bar[:300])

    base = next((x for x in recs if x["pointer"] == main and x["commit"] in shas), None)

    # A failed build has no modules and no tests: it renders as a failed build
    # for that move, not as a build-only row and not as a crash.
    d = json.load(open(os.path.join(work, "examples", "minimal-ctrf.json")))
    d["results"]["extra"]["build"] = {"status": "failed"}
    d["results"]["environment"].update(commit=shas[-1], healthy=False)
    d["results"]["tests"] = []
    d["results"]["summary"].update(tests=0, passed=0, failed=0, skipped=0, pending=0, other=0)
    rel = "results/example-unnamed/testing/main/20260901-selftest-failed.json"
    os.makedirs(os.path.dirname(os.path.join(work, rel)), exist_ok=True)
    with open(os.path.join(work, rel), "w") as f:
        json.dump(d, f)
    try:
        failed = [x for x in bs.load_records(root) if x["path"].endswith(rel)]
        files_f = bs.build_site(failed, declared, logs, allow, vocab) if failed else {}
    finally:
        os.remove(os.path.join(work, rel))
    row = (rows_of(files_f.get("index.html", "")) or [""])[0]
    setup = next((v for k, v in files_f.items() if k.startswith("setup/")), "")
    check("a failed build with no modules and no tests renders as a failed build",
          len(failed) == 1 and row.startswith('"build-failed"') and "✕ build failed" in row
          and "✕ build failed" in setup, row[:160])

    # A submitted report link is rendered on the setup page.
    if base is None:
        check("a setup page links the submitted report", False, "no submission on the default pointer")
        check("each configuration gets its own row", False, "no submission on the default pointer")
        return
    url = "https://reports.example/run-1/report.pdf"
    one = dict(copy.deepcopy(base), report=url)
    got = bs.build_site([one], declared, logs, allow, vocab)
    sp = next((v for k, v in got.items() if k.startswith("setup/")), "")
    check("a setup page links the submitted report", f'href="{url}">report</a>' in sp, sp[:160])

    # Two SUT configurations of one integrator are two rows, not one row
    # showing whichever was submitted last.
    a, b = copy.deepcopy(base), copy.deepcopy(base)
    a["config"], b["config"] = "ac-basic", "dc-full"
    b["start"] = (a["start"] or 0) + 1
    rows2 = rows_of(bs.build_site([a, b], declared, logs, allow, vocab)["index.html"])
    check("each configuration gets its own row",
          len(rows2) == 2 and any(">ac-basic<" in r for r in rows2)
          and any(">dc-full<" in r for r in rows2), f"{len(rows2)} row(s)")

    # Valid input must never stop the build: one record would take the site down
    # for every integrator.
    def builds(label, rs, then=lambda files: (True, "")):
        try:
            ok, why = then(bs.build_site(rs, declared, logs, allow, vocab))
        except Exception as e:                                 # noqa: BLE001
            ok, why = False, f"{type(e).__name__}: {e}"
        check(label, ok, why)

    def setup0(files):
        return json.loads(files["summary.json"])["pointers"][main]["setups"][0]

    if len(shas) < 2:
        for label in ("a failing rerun of an older commit keeps the newest commit as latest",
                      "an inconclusive rerun of an older commit does not break the build"):
            check(label, False, "the pointer never moved")
    else:
        t0 = dict(base["tests"][0], measurements=[])
        newer = dict(copy.deepcopy(base), commit=shas[-1], start=1_000,
                     tests=[dict(t0, outcome="passed", message="")])
        for outcome, label in (("failed", "a failing rerun of an older commit keeps the newest commit as latest"),
                               ("inconclusive", "an inconclusive rerun of an older commit does not break the build")):
            older = dict(copy.deepcopy(base), commit=shas[-2], start=2_000,
                         tests=[dict(t0, outcome=outcome, message="rerun")])
            builds(label, [newer, older], lambda f: (setup0(f)["commit"] == shas[-1]
                                                    and setup0(f)["verdict"] == "passing", str(setup0(f))[:160]))
    blank = dict(copy.deepcopy(base), tests=[dict(base["tests"][0], outcome="failed", message="  \n \t")])
    builds("a whitespace-only failure message does not break the build", [blank])
    twice = dict(copy.deepcopy(base), tests=[dict(base["tests"][0], outcome=o, message="") for o in ("failed", "passed")])
    builds("a run that repeats a test name does not break the build", [twice])

    htf = json.load(open(os.path.join(work, "examples", "openhtf-hil-dc.json")))
    htf["phases"][0]["measurements"] = {"v": {"name": "v", "measured_value": 400, "outcome": "PASS",
                                              "units": {"name": "volt", "code": "VLT", "suffix": "V"}}}
    units = [m["units"] for m in bs.htf_tests(htf)[0]["measurements"]]
    check("an OpenHTF unit object is read as its suffix", units == ["V"], str(units))

    # A tag is conformance-covered only by a passing test that is itself a compliance test.
    own = bs.tag_state([{"outcome": "passed", "tags": ["AC"]}, {"outcome": "skipped", "tags": ["AC", "compliance"]}])[0]
    conf = bs.tag_state([{"outcome": "passed", "tags": ["AC", "compliance"]}])[0]
    check("skipped compliance tests do not make a tag conformance-covered", (own, conf) == ("own", "pass"), f"{own}, {conf}")

    t1 = dict(base["tests"][0], outcome="failed", message="tagged", tags=["AC"])
    t2 = dict(base["tests"][0], outcome="failed", message="untagged", tags=["x-own"])
    sp = next((v for k, v in bs.build_site([dict(copy.deepcopy(base), tests=[t1, t2])], declared, logs, allow,
                                            vocab).items() if k.startswith("setup/")), "")
    check("only a test with no vocabulary tag is marked untagged", sp.count("<b>untagged</b>") == 1,
          f'{sp.count("<b>untagged</b>")} marker(s)')

    pinned = dict(copy.deepcopy(base), report="https://ci.example/r.pdf", report_sha="ab" * 32,
                  test_system={"name": "Conformance tool", "version": "1.2.0"})
    sp = next((v for k, v in bs.build_site([pinned], declared, logs, allow, vocab).items() if k.startswith("setup/")), "")
    check("a declared report hash is shown as declared, not as verified",
          "abababababab…</code> declared, not checked here" in sp and "Conformance tool 1.2.0" in sp, sp[sp.find("Evidence"):][:300])

    # A description titles the test where it is listed, escaped, and never replaces its name.
    title = "Update Charging Station Password <Accepted>"
    t0 = dict(base["tests"][0], desc=title)
    older = dict(copy.deepcopy(base), commit=shas[-2], start=1_000, tests=[dict(t0, outcome="passed")])
    newer = dict(copy.deepcopy(base), commit=shas[-1], start=2_000, tests=[dict(t0, outcome="failed", message="boom")])
    got = bs.build_site([older, newer], declared, logs, allow, vocab)
    sp = next((v for k, v in got.items() if k.startswith("setup/")), "")
    det = got.get(bs.pointer_file("detailed", main), "")
    want = "Update Charging Station Password &lt;Accepted&gt;"
    check("a test's description is shown on its setup page and in the regressions panel",
          f'<code>{bs.esc(t0["name"])}</code> {want}' in sp and want in det[det.find("Open regressions"):],
          f'setup={want in sp} panel={want in det}')

    logged = dict(copy.deepcopy(base), tests=[dict(base["tests"][0], log="https://ci.example/t1.log?a=1&b=2")])
    sp = next((v for k, v in bs.build_site([logged], declared, logs, allow, vocab).items() if k.startswith("setup/")), "")
    check("a test's log link is shown as unverified",
          '<a href="https://ci.example/t1.log?a=1&amp;b=2">log</a> · linked by the integrator · <span class="muted">unverified link</span>' in sp,
          sp[sp.find("log</a>") - 80:][:200])

    u, d = copy.deepcopy(base), copy.deepcopy(base)
    u["config"], d["config"] = "AC_1", "AC-1"
    pages = [k for k in bs.build_site([u, d], declared, logs, allow, vocab) if k.startswith("setup/")]
    check("configurations that slug alike get a page each", len(pages) == 2, str(pages))

    other = next((n for n in declared if n != main), None)
    nav = bs.build_site([], declared, logs, allow, vocab).get(bs.pointer_file("coverage", other or main), "")
    nav = nav[nav.find('aria-label="Views"'):][:400]
    check("the view links on a pointer's pages stay on that pointer",
          other is not None and all(f'href="{bs.pointer_file(v, other)}"' in nav for v in ("index", "detailed", "coverage")),
          nav[:300])

    tp = dict(base["tests"][0], outcome="passed", tags=["AC"])
    tile = bs.build_site([dict(copy.deepcopy(base), commit=shas[-1], start=int(bs.NOW.timestamp() * 1000),
                               tests=[tp, dict(tp, outcome="passed"), dict(tp, outcome="skipped")])],
                         declared, logs, allow, vocab)["index.html"]
    vcell = (re.findall(r'<td class="vcol">.*?</td>', tile) or [""])[0]
    tile = tile[tile.find('class="rtile"'):][:600]
    tf = dict(tp, outcome="failed", std="ISO15118-2")
    shaded = bs.build_site([dict(copy.deepcopy(base), tests=[tf] + [dict(tf, outcome="passed", name=f"p{i}") for i in range(3)])],
                           declared, logs, allow, vocab)
    chip = (shaded["index.html"].split('<span class="schip ')[1:] or [""])[0][:120]
    bar = shaded["detailed.html"].split('<span class="s-failing" style="height:')[1:2]
    check("failing chips are shaded by their pass rate, run bars by their failed count",
          chip.startswith(f'bad" style="--p:{bs.chip_shade(.75, 1)}"') and bar and ";--p:0%" in bar[0][:30], f"{chip} | {bar[:1]}")

    sp = next((v for k, v in shaded.items() if k.startswith("setup/")), "")
    sc = sp[sp.find('aria-label="share of judged tests failed'):]
    check("the setup chart plots the share of judged tests failed, labeled with the failed count",
          'fill="#C0392B" stroke="#C0392B" stroke-width="2"/><text' in sc and 'font-weight="700" fill="var(--soft)">1</text>' in sc
          and "\n25% failed\n1 of 4 judged\n0 not judged</title>" in sc and 'fill="var(--muted)">30%</text>' in sp
          and "#1E8F64" not in sc[:sc.find("</svg>")], sc[:300])
    check("chart axes step in round ticks from zero",
          [bs.ticks(v) for v in (1, 3, 9, 290)] == [[0, 1], [0, 1, 2, 3], [0, 5, 10], [0, 100, 200, 300]])

    check("the pass-rate tile counts every test and the ones not judged", "3 tests, 1 not judged</span>" in tile, tile[:400])
    check("an overview verdict counts its tests and the ones not judged under the rate",
          vcell.endswith('passed</span><span class="small muted" style="display:block">3 tests, 1 not judged</span></td>'), vcell)

    shared = dict(logs, **{other: list(logs.get(other, [])) + [dict(logs[main][-1], pointer=other)]}) if other else logs
    both = json.loads(bs.build_site([dict(copy.deepcopy(base), commit=shas[-1])], declared, shared, allow,
                                    vocab)["summary.json"])["pointers"]
    check("a record shows only under the pointer it was filed under, when two pointers share its commit",
          other is not None and len(both[main]["setups"]) == 1 and not both[other]["setups"],
          str({k: len(v["setups"]) for k, v in both.items()}))

    tot = bs.build_site([dict(copy.deepcopy(base), commit=shas[-1], tests=[dict(tp, name=f"t{i}", outcome=o) for i, o in
                                                                         enumerate(("passed", "passed", "failed", "skipped"))])],
                        declared, logs, allow, vocab)["index.html"]
    tot = tot[tot.find('class="card allint"'):tot.find("</details>", tot.find('class="card allint"'))]
    check("All integrators charts passed, failed and not judged tests per move, labeling the latest passed",
          f"\n2 passed\n1 failed\n1 not judged (1 skipped)\n1 setup</title>" in tot and tot.count("<circle") == 3
          and tot.count('font-weight="700" fill="var(--soft)">2</text>') == 1,
          tot[tot.find("<svg"):][:300])
    # A weekly setup must not drop out of the totals on the moves it skips,
    # until its last run goes stale.
    weekly = dict(copy.deepcopy(base), config="weekly", commit=shas[-2], start=int(bs.move_ms({"log": logs[main]}, len(shas) - 2)))
    daily = [dict(copy.deepcopy(base), config="daily", commit=c) for c in shas[-2:]]
    def tot_tip(recs_, grace=14):
        page_ = bs.build_site(recs_, declared, logs, allow, vocab, grace)["index.html"]
        return (re.findall(r"<title>" + shas[-1][:7] + r"[^\n]*\n(.*?)</title>", page_, re.S) or [""])[0]
    kept, gone = tot_tip(daily + [weekly]), tot_tip(daily + [weekly], grace=0)
    check("the totals carry a setup's last run forward until it goes stale",
          kept.endswith(f"2 setups: 1 tested here, 1 carried from {shas[-2][:7]}") and gone.endswith("\n1 setup"), f"{kept!r} | {gone!r}")
    at = bs.ms_to_dt(bs.move_ms({"log": logs[main]}, len(shas) - 1))
    check("chart moves are dated under their commit, and in full in the tooltip",
          f'fill="var(--muted)">{at:%d-%m}</text>' in tot and f"<title>{shas[-1][:7]} · moved {at:%Y-%m-%d}\n" in tot, str(at))
    old = {"shas": [str(i) for i in range(30)],
           "log": [{"at": (bs.NOW - bs.dt.timedelta(days=100 - (95 if i >= 25 else 0))).isoformat()} for i in range(30)]}
    new = dict(old, log=[{"at": bs.NOW.isoformat()}] * 30)
    check("charts show the moves of the last 60 days, and never fewer than 10",
          list(bs.chart_moves(old, 0)) == list(range(20, 30)) and list(bs.chart_moves(new, 3)) == list(range(3, 30))
          and list(bs.chart_moves(old, 27)) == [27, 28, 29])
    check("each chart move's summary becomes a tooltip box when scripts run",
          '<g class="mv" tabindex="0"><title>' in tot and "g.dataset.tip=t.textContent;t.remove()" in bs.JS
          and "addEventListener('focus'" in bs.JS)

    check("only a verdict filter in the URL filters the overview",
          "if(location.hash)apply" not in bs.JS and 'button[data-f="${CSS.escape(h)}"]' in bs.JS)


def suite_sources(work, verbose):
    """The source intake's planner and the allowlist rules it relies on.

    The planner decides what is new, and a mistake there either drops a record
    silently or opens the same pull request every four hours. GitHub is replaced
    by canned responses, so plan and apply run whole without a network.
    """
    sys.path.insert(0, os.path.join(work, "scripts"))
    import sources as S
    import intake_sources as I

    def check(label, cond, detail=""):
        report(label, cond, detail)
        if verbose or not cond:
            print(f"  {'ok  ' if cond else 'FAIL'} {label}"
                  + (f"   {detail}" if detail and not cond else ""))

    src = {"repo": "example/records", "repo_id": 7, "ref": "results",
           "path": "records/", "label": "nightly"}
    tree = ["README.md",
            "records/testing/main/20261027-nightly.json",
            "records/testing/main/20261028-nightly.json",
            "records/testing/stable-2026.10/20261027-nightly.json",
            "records/testing/main/notes.txt",
            "records/testing/main/20261029.json",
            "records/testing/feature/20261027.json",
            "other/testing/main/20261027.json"]
    got = I.candidates("example-sourced", src, tree)
    check("candidates are the valid record paths under the source path",
          [d for _, d in got] == [
              "results/example-sourced/testing/main/20261027-nightly.json",
              "results/example-sourced/testing/main/20261028-nightly.json",
              "results/example-sourced/testing/stable-2026.10/20261027-nightly.json"],
          str(got))
    on_main = {"results/example-sourced/testing/main/20261027-nightly.json"}
    branches = {"intake/example-sourced/testing/stable-2026.10/20261027-nightly.json"}
    new = [d for _, d in I.new_records("example-sourced", src, tree, on_main, branches)]
    check("candidates skip a file its source's label does not admit",
          not any(d.endswith("/20261029.json") for _, d in got), str(got))
    check("a record on main or with an intake branch is not new",
          new == ["results/example-sourced/testing/main/20261028-nightly.json"], str(new))
    check("the intake branch names the pointer, so dates on two pointers differ",
          I.branch_for("results/x/testing/main/20261027.json")
          == "intake/x/testing/main/20261027.json")
    try:
        I.open_one({"branch": "main", "dest": "results/x/testing/main/20261027.json"},
                   "EVerest/Himalayas", "0" * 40, "no-token")
        refused = False
    except ValueError:
        refused = True
    check("apply refuses to write a branch outside intake/", refused)

    check("a declared label admits itself and its extensions",
          S.label_admits(src, "nightly") and S.label_admits(src, "nightly-ocpp201"))
    check("a declared label does not admit a prefix-alike or no label",
          not S.label_admits(src, "nightlyx") and not S.label_admits(src, None))
    check("a source with no label admits any filename",
          S.label_admits(dict(src, label=None), None))

    good = {"repo": "a/b", "repo_id": 1, "ref": "main", "path": "records/"}
    for label, entry, want in [
        ("repo_id given as a name", dict(good, repo_id="a/b"), "repo_id"),
        ("path climbing out with ..", dict(good, path="../x/"), "path"),
        ("path without a trailing slash", dict(good, path="records"), "path"),
        ("label that is not a filename label", dict(good, label="Nightly"), "label"),
        ("unknown key", dict(good, branch="main"), "unknown key"),
        ("path that is the root as ./", dict(good, path="./"), "path"),
        ("ref that is a pull request head", dict(good, ref="refs/pull/1/head"), "ref"),
        ("ref that is a pull request merge", dict(good, ref="pull/1/merge"), "ref"),
        ("ref that is a commit", dict(good, ref="a" * 40), "ref"),
    ]:
        _, bad = S.entry_sources({"sources": [entry]})
        check(f"allowlist source rejected: {label}", any(want in b for b in bad), str(bad))
    _, bad = S.entry_sources({"sources": [dict(good, label="x"), dict(good, repo_id=2, label="x")]})
    check("allowlist source rejected: one label declared twice", any("twice" in b for b in bad), str(bad))
    probs = S.allowlist_problems({"a": {"sources": [good]}, "b": {"sources": [good]}})
    check("one repository cannot be bound to two ids", any("both" in p for p in probs), str(probs))
    check("a well-formed source has no problems", S.entry_sources({"sources": [good]})[1] == [])
    try:
        probs = S.allowlist_problems({"a": {"sources": [dict(good, repo_id=[1])]}})
        crashed = None
    except Exception as e:                                     # noqa: BLE001
        probs, crashed = [], e
    check("an unhashable repo_id is reported, not a crash",
          crashed is None and any("repo_id" in p for p in probs), f"{crashed!r} {probs}")

    # --- plan and apply against canned GitHub responses ---------------------
    big = 2_000_000
    blobs = {"records/testing/main/20261027-nightly.json": 10,
             "records/testing/main/20261028-nightly.json": 10,
             "records/testing/main/20261029-nightly.json": big,
             "records/testing/main/20261030-nightly.json": 10,
             "records/testing/main/20261031-nightly.json": 10}
    stranded = "intake/example-sourced/testing/main/20261027-nightly.json"
    taken = "intake/example-sourced/testing/main/20261028-nightly.json"
    s1 = dict(src, repo_id=7)
    s2 = dict(src, repo="example/mirror", repo_id=8, label=None)
    allow = {"example-sourced": {"sources": [s1, s2]}}

    def fake_json(path, token):
        if path.startswith("/repositories/") and path.count("/") == 2:
            return {"full_name": {"7": "example/records", "8": "example/mirror"}[path[14:]]}
        if "/branches/" in path:
            return {"commit": {"sha": "c" * 40}}
        if "/git/trees/" in path:
            return {"tree": [{"path": p, "type": "blob", "size": n} for p, n in blobs.items()]}
        if "/pulls?head=" in path:
            return [] if urllib.parse.quote(stranded, safe="") in path else [{"number": 1}]
        raise AssertionError(path)

    saved = (S.api_json, S.api_list, S.fetch_file, I.load_allowlist, I.MAX_PER_RUN)
    S.api_json = fake_json
    S.api_list = lambda path, token: [{"ref": f"refs/heads/{b}"} for b in (stranded, taken)]
    S.fetch_file = lambda s, p, token, fixtures=None: b"{}"
    I.load_allowlist = lambda root: allow
    I.MAX_PER_RUN = 2
    out = os.path.join(work, "_plan.json")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = I.plan(argparse.Namespace(repo="EVerest/Himalayas", out=out))
    finally:
        S.api_json, S.api_list, S.fetch_file, I.load_allowlist, I.MAX_PER_RUN = saved
    with open(out) as f:
        items = json.load(f)
    log = buf.getvalue()
    dests = [i["dest"].rsplit("/", 1)[1] for i in items]
    check("plan takes again a branch a dead run left without a pull request",
          [i["resume"] for i in items if i["branch"] == stranded] == [True], str(items))
    check("plan skips a branch that has a pull request",
          "20261028-nightly.json" not in dests, str(dests))
    check("plan refuses a file over the cap and fails the run",
          rc == 1 and "20261029-nightly.json is 2,000,000 bytes" in log, log)
    check("plan opens at most MAX_PER_RUN and defers the rest",
          len(items) == 2 and "1 more new record" in log, f"{dests} {log}")
    check("plan lists one file held by two sources of one id once",
          len(dests) == len(set(dests)) and "20261030-nightly.json" in dests, str(dests))

    # Rate limited at the second item's pull request: stop rather than fail
    # every later write, including the clean-up.
    calls = []

    def fake_call(method, path, token, body=None):
        calls.append((method, path))
        if method == "GET":
            return {"object": {"sha": "b" * 40}}
        if method == "POST" and path.endswith("/pulls"):
            raise S.ApiError(method, path, 403, "secondary rate limit")
        return None

    saved, I.call = I.call, fake_call
    os.environ["APP_TOKEN"] = "fake"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = I.apply(argparse.Namespace(repo="EVerest/Himalayas", plan=out))
    finally:
        I.call = saved
        del os.environ["APP_TOKEN"]
    first = [c for c in calls if c[0] != "GET"]
    check("apply deletes a stranded branch before taking it again",
          first[:2] and first[0][0] == "DELETE" and first[1] == ("POST", "/repos/EVerest/Himalayas/git/refs"),
          str(first))
    check("apply cleans up and stops at a rate limit",
          rc == 1 and first[-1][0] == "DELETE"
          and not any("20261030" in c[1] for c in calls), str(calls))


SUITES = {"gate": suite_gate, "intake": suite_intake, "normalise": suite_normalise,
          "pointers": suite_pointers, "archive": suite_archive,
          "staleness": suite_staleness, "build": suite_build,
          "docs": suite_docs, "sources": suite_sources}

# How many checks each suite must run. A suite whose count drops has had cases
# deleted, and deleting a case is the one failure this file cannot otherwise see:
# a suite cut from 70 checks to 3 exits non-zero just as happily. Six
# archive assertions also sat behind an `if ok:`, so breaking the move deleted
# them from the count instead of failing them.
#
# Raise a number when you add cases. If you are lowering one, say in the commit
# message which case you removed and why.
EXPECT = {"gate": 112, "intake": 16, "normalise": 8, "pointers": 17,
          "archive": 9, "staleness": 6, "build": 68, "docs": 10, "sources": 28}
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
        # The ids they publish under exist only here, so the shipped allowlist
        # cannot be used to publish as an example.
        with open(os.path.join(ROOT, "testdata", "allowlist.yaml")) as src, \
                open(os.path.join(work, "integrators", "allowlist.yaml"), "a") as dst:
            dst.write(src.read())
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
