#!/usr/bin/env python3
"""Validate a Himalayas submission.

Accepts a CTRF report or an OpenHTF TestRecord, normalises to OpenHTF (the
internal format), and gates it.

REJECTS
  bad-path            not results/<id>/<pointer>/<YYYYMMDD>.json
  not-json            unparseable
  unknown-format      neither CTRF nor OpenHTF
  htf-profile         the normalised record breaks the structural profile
  htf-metadata        metadata breaks the EVerest contract
  ctrf-schema         a CTRF submission is not valid CTRF
  ctrf-profile        a CTRF submission breaks the EVerest CTRF profile, which
                      includes the rule that a HIL run must name its board
  extra-contract      results.extra breaks the EVerest extra contract
  build-missing       no metadata.build
  build-failed        metadata.build.status is not passed
  id-missing          no metadata.integrator.id at all
  id-mismatch         integrator id does not match the results/<id>/ directory
  pointer-mismatch    the pointer in the path does not match the one in the record
  pointer-unknown     the pointer in the path is not declared in pointers.json
  hil-needs-board     a HIL run submitted as CTRF did not name its board
  not-allowlisted     integrator not in integrators/allowlist.yaml
  commit-malformed    not a full 40-character SHA
  commit-unknown      that commit is not in the EVerest repository
  commit-not-pointed  that commit was never a tip of the pointer, per pointers.json
  commit-missing      no metadata.everest.commit at all
  not-authorised      the submitting GitHub account is not listed for that
                      integrator id in integrators/allowlist.yaml
  too-big             the submission, or its inline attachments, exceed the caps

TOOL ERRORS - exit 2, and the workflow closes the pull request
  the submission file cannot be read, integrators/allowlist.yaml cannot be read,
  pointers.json cannot be read, or the gate breaks on a document no schema
  rejected. All four mean the gate cannot say whether the submission is valid,
  which is a different statement from "this submission is wrong".

WARNS - never blocks
  sourcecode          OpenHTF embeds test source by default and this repo is
                      public; warn-only by design, so it warns loudly
  attachments-large   inline attachments, traces and messages are getting big
  tag-vocabulary      a tag outside schema/vocabulary.json and not x- prefixed
  target-vocabulary   a build target EVerest does not itself build
  module-names        a module name not found in EVerest
  no-build-url        no link back to the run

Exit 0 accepted, 1 rejected, 2 tool error. The distinction is load-bearing: the
workflow closes the pull request on a tool error, because the tool cannot say
whether the submission is good, and leaves it open on a rejection, because the
submitter can push a fix. So nothing here may crash out with exit 1.

Two things keep that true. Schema validation runs on the document AS SUBMITTED
and before normalisation, so a document malformed enough to break the reader has
already been rejected with a reason by the time the reader sees it - which is how
`metadata: "hello"` used to raise AttributeError and exit 1, indistinguishable
from a legitimate rejection. And normalisation and every check after it run
inside a guard: a break with a schema error already recorded is reported as that
rejection, and a break with NOTHING recorded is our bug, so it becomes exit 2.
"""
import argparse, json, os, re, sys, urllib.request, urllib.error

try:
    import jsonschema
except ImportError:
    print("error: pip install -r scripts/requirements.txt", file=sys.stderr)
    sys.exit(2)
try:
    import yaml
except ImportError:
    yaml = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalise import detect, ctrf_to_openhtf                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# results/<integrator>/<pointer>/<YYYYMMDD>[-N].json - the pointer keeps its
# slash, so testing/main becomes two path segments. The optional -N suffix exists
# because the pointers move daily: a re-run after a flake, or a SIL and a HIL run
# on the same day, would otherwise overwrite each other.
PATH_RE = re.compile(
    r"^results/(?P<who>[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?)/"
    r"(?P<pointer>testing/(?:main|stable-\d{4}\.\d{2}))/"
    r"(?P<date>\d{8})(?:-(?P<seq>[2-9]|[1-9]\d+))?\.json$")


class ToolError(Exception):
    """The gate could not reach a verdict. Exit 2, and the workflow closes the PR."""


class Verdict:
    def __init__(self):
        self.errors, self.warnings, self.notes = [], [], []

    def err(self, code, msg):
        self.errors.append((code, msg))

    def warn(self, code, msg):
        self.warnings.append((code, msg))

    def note(self, msg):
        self.notes.append(msg)

    @property
    def accepted(self):
        return not self.errors

    def markdown(self, relpath, fmt=None):
        L = []
        if self.accepted:
            L.append(f"### Submission accepted &mdash; `{relpath}`\n")
            L.append(f"Format detected: **{fmt or 'unknown'}**"
                     + (", normalised to OpenHTF.\n" if fmt == "ctrf" else ".\n"))
        else:
            L.append(f"### Submission rejected &mdash; `{relpath}`\n")
            L.append(f"{len(self.errors)} check(s) failed. Fix and push to this branch; "
                     "the gate re-runs automatically.\n")
            L.append("| Check | Why it failed |")
            L.append("|---|---|")
            for code, msg in self.errors:
                L.append(f"| `{code}` | {msg} |")
            L.append("")
        if self.warnings:
            L.append(f"<details><summary>{len(self.warnings)} warning(s) &mdash; "
                     "not blocking</summary>\n")
            for code, msg in self.warnings:
                L.append(f"- `{code}`: {msg}")
            L.append("\n</details>\n")
        for n in self.notes:
            L.append(n)
        L.append("\n---\n*Results in this repository are **self-reported** by integrators "
                 "and are not reproduced or verified by the EVerest project.*")
        return "\n".join(L)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def allowlist_entries():
    """id -> entry, or None if the file cannot be read.

    Returning the whole entry rather than the id is what makes the author binding
    possible: the accounts authorised to publish as an id live on the entry, and
    the file is CODEOWNERS-protected, so adding one is an owner-approved change.
    """
    p = os.path.join(ROOT, "integrators", "allowlist.yaml")
    if not os.path.exists(p) or yaml is None:
        return None
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    return {e["id"]: e for e in (data.get("integrators") or [])
            if isinstance(e, dict) and "id" in e}


def pointer_log():
    try:
        with open(os.path.join(ROOT, "pointers.json")) as f:
            return json.load(f)
    except Exception:                                          # noqa: BLE001
        return None


def load_vocabulary():
    try:
        with open(os.path.join(ROOT, "schema", "vocabulary.json")) as f:
            return json.load(f)
    except Exception:                                          # noqa: BLE001
        return None


def load_module_names():
    try:
        with open(os.path.join(ROOT, "schema", "everest-modules.txt")) as f:
            return {ln.strip() for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")}
    except Exception:                                          # noqa: BLE001
        return None


def commit_exists(sha, repo, token):
    url = f"https://api.github.com/repos/{repo}/commits/{sha}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "everest-himalayas-gate"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200, None
    except urllib.error.HTTPError as e:
        # GitHub answers 422 for a well-formed SHA that does not exist, and 404
        # for a malformed ref. Both mean absent. 403/429 and 5xx mean unknown.
        if e.code in (404, 422):
            return False, f"HTTP {e.code}"
        return None, f"HTTP {e.code}"
    except Exception as e:                                     # noqa: BLE001
        return None, str(e)


def schema_errors(v, checks):
    """Record up to six errors per (code, schema, target) triple."""
    for name, schema_file, target in checks:
        with open(os.path.join(ROOT, "schema", schema_file)) as f:
            schema = json.load(f)
        for e in sorted(jsonschema.Draft7Validator(schema).iter_errors(target),
                        key=lambda x: list(x.path))[:6]:
            loc = "/".join(str(x) for x in e.path) or "(root)"
            v.err(name, f"`{loc}`: {e.message}")


def inline_bytes(rec):
    """Inline bulk: base64 attachments, plus traces and messages.

    Attachments alone did not bound the CTRF path at all. `normalise.py` sets
    `attachments` to a reference map with no bytes in it, and CTRF's bulk lives
    in `trace`, `stdout` and `stderr`, which become outcome_details - so a CTRF
    submission with 350 KB of trace and 350 KB of stdout was accepted with no
    warning, and only the 1 MB whole-file cap bit. The cap exists because this
    repository keeps every submission indefinitely, so it has to measure what is
    actually inline, whichever format it arrived in.
    """
    total = 0
    for ph in rec.get("phases") or []:
        for a in (ph.get("attachments") or {}).values():
            if isinstance(a, dict):
                d = a.get("data")
                if isinstance(d, str):
                    total += len(d)
                elif isinstance(a.get("size"), int):
                    total += a["size"]
            elif isinstance(a, str):
                total += len(a)
        for d in (ph.get("outcome_details") or []):
            if isinstance(d, dict) and isinstance(d.get("description"), str):
                total += len(d["description"])
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--report")
    ap.add_argument("--everest-repo", default="EVerest/EVerest")
    ap.add_argument("--skip-commit-check", action="store_true")
    ap.add_argument("--author", default=os.environ.get("SUBMISSION_AUTHOR") or None,
                    help="GitHub login that opened the pull request. An "
                         "integrator id lists the accounts allowed to publish as it, "
                         "and a submission from any other account is rejected. Reads "
                         "SUBMISSION_AUTHOR if not given, so the workflow can pass it "
                         "through env: rather than interpolating it into a shell command.")
    ap.add_argument("--max-file-bytes", type=int, default=1_048_576,
                    help="whole submission cap; this repository keeps files indefinitely")
    ap.add_argument("--max-attachment-bytes", type=int, default=262_144,
                    help="inline bulk cap - attachments, traces and messages. OpenHTF "
                         "base64-inlines attachments by default, and a CTRF trace is "
                         "inline by construction")
    args = ap.parse_args()

    v = Verdict()
    rel = args.path.replace(os.sep, "/")
    abspath = args.path if os.path.isabs(args.path) else os.path.join(ROOT, rel)

    m = PATH_RE.match(rel)
    if not m:
        v.err("bad-path",
              "Path must be `results/&lt;integrator&gt;/&lt;pointer&gt;/YYYYMMDD.json`, with an "
              "optional `-2`, `-3`&hellip; suffix for a second run the same day. "
              "e.g. `results/acme-charge/testing/main/20260909-2.json`. "
              f"Got `{rel}`.")
    who = m.group("who") if m else None
    path_ptr = m.group("pointer") if m else None

    # --- size, before anything else ------------------------------------
    try:
        size = os.path.getsize(abspath)
        if size > args.max_file_bytes:
            v.err("too-big", f"{size:,} bytes exceeds the {args.max_file_bytes:,}-byte cap. "
                             "Truncate traces and export with `inline_attachments=False`.")
    except OSError as e:
        # The gate is handed a path by the workflow. If it is not there, the fetch
        # failed - not the submitter's doing, so not a rejection.
        raise ToolError(f"{rel} could not be read: {e}")

    try:
        doc = load_json(abspath)
    except json.JSONDecodeError as e:
        v.err("not-json", f"Not valid JSON: {e}")
        return finish(v, rel, None, args)

    fmt = detect(doc)
    if fmt is None:
        v.err("unknown-format",
              "Not recognisable as CTRF or OpenHTF. A CTRF report needs `reportFormat` and "
              "`specVersion`; an OpenHTF record needs `dut_id` and `phases`.")
        return finish(v, rel, None, args)

    # --- schema validation, ON THE DOCUMENT AS SUBMITTED -----------------
    # This runs BEFORE normalisation and bails if it fails, so normalise.py is
    # never handed a document no schema has vetted. It used to run after, and
    # `metadata: "hello"` therefore reached md.get() and raised AttributeError -
    # exit 1, indistinguishable from a legitimate rejection. Ordering is what
    # makes that unreachable; a try/except would only have hidden it.
    #
    # A submission that ARRIVED as CTRF is checked against the CTRF schemas here
    # and against the normalised ones below, because normalisation is lossy in the
    # direction that matters: it discards CTRF-only structure (specVersion,
    # per-test duration) and CTRF-only fields (appName, osPlatform), and it cannot
    # express a rule that spans results.environment and results.extra - which the
    # HIL-needs-a-board rule does. Checking only the normalised form silently
    # disabled five CTRF checks once before; do not remove either half.
    if fmt == "ctrf":
        submitted = [("ctrf-schema", "ctrf.schema.json", doc),
                     ("ctrf-profile", "everest-profile.schema.json", doc),
                     ("extra-contract", "everest-extra.schema.json",
                      (doc.get("results") or {}).get("extra") or {})]
    else:
        submitted = [("htf-profile", "everest-htf-profile.schema.json", doc),
                     ("htf-metadata", "everest-htf-metadata.schema.json",
                      doc.get("metadata"))]
    schema_errors(v, submitted)

    # Normalisation is attempted only AFTER the submitted document has been
    # schema-checked, and everything downstream of it runs inside the guard
    # below. That is what keeps the exit codes meaning what they say.
    #
    # The failure this replaces: the gate normalised first, so `metadata: "hello"`
    # reached md.get() and raised AttributeError - exit 1, which the workflow
    # reads as "the integrator's file is wrong" and which is also what a
    # legitimate rejection looks like. Now a document malformed enough to break
    # the reader has already been rejected by a schema, and the guard reports
    # that reason. If nothing has gone red and the gate still breaks, that is our
    # bug, not the submitter's: it becomes a ToolError, exit 2, and the workflow
    # closes the pull request rather than blaming the file.
    #
    # Errors are NOT bailed on before the policy checks. A submitter should see
    # every reason at once, and the hand-written checks below exist precisely to
    # explain what a bare "'hardware' is a required property" does not.
    try:
        rec = doc if fmt == "openhtf" else ctrf_to_openhtf(doc)
        raw_md = rec.get("metadata")
        md = raw_md if isinstance(raw_md, dict) else {}

        if fmt == "ctrf":
            schema_errors(v, [("htf-profile", "everest-htf-profile.schema.json", rec),
                              ("htf-metadata", "everest-htf-metadata.schema.json", md)])

        ev = md.get("everest") if isinstance(md.get("everest"), dict) else {}
        build = md.get("build") if isinstance(md.get("build"), dict) else {}
        integ = md.get("integrator") if isinstance(md.get("integrator"), dict) else {}

        # --- the minimal set IS the build ----------------------------------
        if not build:
            v.err("build-missing", "`metadata.build` is absent. The build is the one mandatory "
                                   "item in a submission.")
        elif build.get("status") != "passed":
            v.err("build-failed", f"`metadata.build.status` is `{build.get('status')}`. Only "
                                  "submissions whose build passed are accepted.")

        # --- identity ------------------------------------------------------
        claimed = integ.get("id")
        # Same shape as commit-missing: every identity check below is behind
        # `if claimed`, so a submission with no integrator id skipped the
        # allowlist AND the author binding, and was left to a schema rule.
        if not claimed:
            v.err("id-missing",
                  "`metadata.integrator.id` is absent. Without it a submission cannot be "
                  "attributed, which is the whole point of this repository, and neither "
                  "the allowlist nor the author check can run.")
        if who and claimed and claimed != who:
            v.err("id-mismatch", f"`metadata.integrator.id` is `{claimed}` but the file is under "
                                 f"`results/{who}/`. They must match.")
        allow = allowlist_entries()
        if allow is None:
            # The allowlist is the whole of the identity story, so an unreadable one
            # is a tool error rather than a check to skip with a warning.
            raise ToolError("integrators/allowlist.yaml could not be read"
                            + ("" if yaml else " (PyYAML is not installed)"))
        if claimed and claimed not in allow:
            v.err("not-allowlisted", f"`{claimed}` is not in `integrators/allowlist.yaml`. "
                                     "Open a separate PR adding your entry first.")
        elif claimed:
            # An integrator id carries a list of the GitHub accounts allowed to
            # publish as it. Without this the page's entire premise - attributed
            # self-reporting - is unenforced: any account can publish as any id.
            #
            # The rejection is deliberately quiet. It names no authorised account, and
            # nothing notifies the integrator's registered contact, so an
            # outsider cannot use the gate to enumerate an
            # integrator's accounts or to generate mail to them.
            authorised = allow[claimed].get("github_users")
            if not isinstance(authorised, list) or not authorised:
                v.err("not-authorised",
                      f"`{claimed}` has no `github_users` list in "
                      "`integrators/allowlist.yaml`, so no account is authorised to publish "
                      "as it. An owner must add the list before submissions are accepted.")
            elif args.author is None:
                # Local runs have no author. Say so rather than passing silently: the
                # check that binds a submission to an account was not performed.
                v.note("*Author binding not checked: no `--author` was given. In CI the "
                       "workflow always passes the pull request author.*")
            elif args.author.lower() not in {str(a).lower() for a in authorised}:
                v.err("not-authorised",
                      f"The account that opened this pull request is not authorised to "
                      f"publish as `{claimed}`. If it should be, an owner must add it to "
                      f"that entry's `github_users` in `integrators/allowlist.yaml`.")

        # --- HIL on the CTRF path must name its board -----------------------
        # The schema enforces this too, but a bare "'hardware' is a required property"
        # does not say why, and the why matters: HIL via CTRF IS supported, it just has
        # to identify the device, because CTRF has no dut_id for the normaliser to use.
        if fmt == "ctrf" and ev.get("testEnvironment") == "HIL":
            board = (md.get("hardware") or {}).get("board")
            if not board:
                v.err("hil-needs-board",
                      "This is a **HIL** run submitted as CTRF, which is supported &mdash; but "
                      "CTRF has no device field, so it must set `results.extra.hardware.board` "
                      "to say which charger produced the result. Without it the record would be "
                      "indistinguishable from any other rig you own. SIL submissions do not need "
                      "this.")

        # --- pointer consistency -------------------------------------------
        rec_ptr = ev.get("pointer")
        if path_ptr and rec_ptr and path_ptr != rec_ptr:
            v.err("pointer-mismatch", f"The path says `{path_ptr}` but "
                                      f"`metadata.everest.pointer` says `{rec_ptr}`.")
        # PATH_RE matches the SHAPE of a pointer path, because a regex cannot
        # know which stable lines exist. pointers.json does, and it is the one
        # place the set is declared rather than restated - so the path is checked
        # against it here. The two schema enums still restate it; the self-test
        # asserts all three agree.
        declared_ptrs = set((pointer_log() or {}).get("pointers") or {})
        if path_ptr and declared_ptrs and path_ptr not in declared_ptrs:
            v.err("pointer-unknown",
                  f"`{path_ptr}` is not a declared pointer. `pointers.json` declares "
                  + ", ".join(f"`{x}`" for x in sorted(declared_ptrs))
                  + ". A submission against an undeclared pointer has no agreed base to "
                    "be checked against.")

        # --- the commit ----------------------------------------------------
        sha = ev.get("commit")
        # Every commit check below sits behind `if sha`, so a submission with no
        # commit at all once sailed through all four of them. The schemas require it,
        # but a schema rule that is the only thing standing between an unpinned
        # submission and acceptance is one edit from being gone.
        if not sha:
            v.err("commit-missing",
                  "`metadata.everest.commit` is absent. A submission that names no commit "
                  "cannot be shown to have tested any agreed base, so none of the commit "
                  "checks can run.")
        elif not re.fullmatch(r"[0-9a-f]{40}", str(sha)):
            v.err("commit-malformed", f"`metadata.everest.commit` must be a full 40-character "
                                      f"SHA; got `{sha}` ({len(str(sha))} chars). With tags "
                                      "replaced by force-pushed pointers the SHA is the only "
                                      "durable identifier.")
        else:
            # --- was it ever a pointer tip? the gate verifies the log ---
            # An unreadable log used to warn and skip. This is the strongest claim
            # the gate makes, so skipping it silently accepts a submission whose
            # base cannot be shown to have been agreed - a tool error, not a
            # warning.
            log = pointer_log()
            if log is None:
                raise ToolError("pointers.json could not be read, so the commit could not "
                                "be checked against the pointer log")
            else:
                seen = {e.get("sha") for e in (log.get("log") or [])
                        if not rec_ptr or e.get("pointer") == rec_ptr}
                if sha not in seen:
                    v.err("commit-not-pointed",
                          f"`{sha[:12]}` does not appear in `pointers.json` for "
                          f"`{rec_ptr or 'any pointer'}`. A force-pushed branch keeps no history, "
                          "so the log is the only record of what the agreed base ever was &mdash; "
                          "a commit that was never a pointer tip cannot be shown to be one.")
            if not args.skip_commit_check:
                ok, why = commit_exists(sha, args.everest_repo, os.environ.get("GITHUB_TOKEN"))
                if ok is False:
                    v.err("commit-unknown", f"Commit `{sha[:12]}` was not found in "
                                            f"`{args.everest_repo}`.")
                elif ok is None:
                    v.warn("commit-uncheckable", f"Could not verify `{sha[:12]}` ({why}).")
            else:
                v.note(f"*Commit reachability check skipped (offline). Claimed `{sha[:12]}`.*")

        # --- attachments and source code -----------------------------------
        ab = inline_bytes(rec)
        if ab > args.max_attachment_bytes:
            v.err("too-big", f"inline attachments, traces and messages total {ab:,} bytes, "
                             f"over the {args.max_attachment_bytes:,}-byte cap. Export with "
                             "`inline_attachments=False`, truncate traces, and link the full "
                             "detail from `buildUrl`.")
        elif ab > args.max_attachment_bytes // 2:
            v.warn("attachments-large", f"inline attachments, traces and messages total "
                                        f"{ab:,} bytes, over half the cap.")

        leaked = [ph.get("name") for ph in (rec.get("phases") or [])
                  if (ph.get("codeinfo") or {}).get("sourcecode")]
        if leaked:
            v.warn("sourcecode",
                   f"**{len(leaked)} phase(s) carry `codeinfo.sourcecode`** &mdash; OpenHTF "
                   "includes test source in its record by default, and this repository is "
                   f"public. First: `{leaked[0]}`. If those tests are not meant to be published, "
                   "strip `codeinfo` before submitting. Accepted either way; your call.")

        # --- vocabulary: warn only, the lists drift with EVerest ------------
        vocab = load_vocabulary()
        if vocab:
            known = set()
            for vals in (vocab.get("testTags") or {}).values():
                if isinstance(vals, list):
                    known.update(vals)
            seen = {t for ph in (rec.get("phases") or []) for t in (ph.get("tags") or [])}
            odd = sorted(t for t in seen if t not in known and not t.startswith("x-"))
            if odd:
                v.warn("tag-vocabulary", "not in `schema/vocabulary.json`: "
                       + ", ".join(f"`{t}`" for t in odd[:8])
                       + ". Use a standard tag, or prefix a private one with `x-`.")
            tgts = {t.get("target") for t in (build.get("targets") or [])}
            odd_t = sorted(t for t in tgts if t and t not in set(vocab.get("buildTargets") or []))
            if odd_t:
                v.warn("target-vocabulary", "target(s) EVerest does not itself build: "
                       + ", ".join(f"`{t}`" for t in odd_t[:5])
                       + ". Fine, but not directly comparable with upstream.")
        mods = load_module_names()
        if mods:
            unknown = sorted(m for m in (build.get("modules") or []) if m not in mods)
            if unknown:
                v.warn("module-names", "not EVerest module name(s): "
                       + ", ".join(f"`{m}`" for m in unknown[:8])
                       + ". Check the spelling against `modules/**/` in EVerest.")

        if not ev.get("buildUrl"):
            v.warn("no-build-url", "`metadata.everest.buildUrl` is absent. Optional but strongly "
                                   "encouraged: with attachments capped it is the only route to "
                                   "full detail.")

    except ToolError:
        raise
    except Exception as e:                                     # noqa: BLE001
        if v.accepted:
            raise ToolError(f"the gate broke on a document no schema rejected: "
                            f"{type(e).__name__}: {e}")
        v.note("*Some checks could not run: this document is malformed in a way that "
               "stops the gate reading it. The failures above are the reason.*")

    return finish(v, rel, fmt, args)


def finish(v, rel, fmt, args):
    out = v.markdown(rel, fmt)
    write_report(args, out)
    print(out)
    return 0 if v.accepted else 1


def write_report(args, text):
    if getattr(args, "report", None):
        with open(args.report, "w") as f:
            f.write(text + "\n")


def tool_error(args, rel, why):
    """Exit 2 WITH a verdict. A tool error that writes nothing leaves the
    workflow with a red check and no reason on the pull request."""
    out = (f"### Submission could not be processed &mdash; `{rel}`\n\n"
           f"The intake gate failed while checking this file, so it cannot say whether "
           f"the submission is valid.\n\n```\n{why}\n```\n\n"
           "This pull request is being closed. Please open a new one once the "
           "submission is fixed; if the file looks correct, raise an issue &mdash; the "
           "fault may be ours.")
    write_report(args, out)
    print(out)
    print(f"tool error: {why}", file=sys.stderr)
    return 2


def cli():
    """Parse enough to know where the report goes, then run main() guarded.

    Any unexpected exception has to become exit 2, never exit 1: the workflow
    reads exit 1 as "the integrator's file is wrong" and exit 2 as "we could not
    tell", and it closes the pull request only on the second.
    """
    rel = " ".join(a for a in sys.argv[1:] if not a.startswith("-")) or "(unknown)"
    shim = argparse.Namespace(report=None)
    if "--report" in sys.argv:
        i = sys.argv.index("--report")
        if i + 1 < len(sys.argv):
            shim.report = sys.argv[i + 1]
    try:
        return main()
    except SystemExit:
        raise
    except ToolError as e:
        return tool_error(shim, rel, str(e))
    except Exception as e:                                     # noqa: BLE001
        import traceback
        return tool_error(shim, rel,
                          f"{type(e).__name__}: {e}\n\n"
                          + "".join(traceback.format_exc()).strip())


if __name__ == "__main__":
    sys.exit(cli())
