# Himalayas

**EVerest federated testing.** Build and test results reported by EVerest integrators
against the floating `testing/` pointer branches of
[EVerest](https://github.com/EVerest/EVerest), collected by pull request and shown on one
status page.

**[Status page](https://everest.github.io/Himalayas/)**: who has reported what, against
which pointer, and how recently.

All EVerest documentation and issue tracking lives in the
[main EVerest repository](https://github.com/EVerest/everest).

> ### These results are self-reported
>
> Each submission is produced by the integrator named on it, on their own hardware and in
> their own CI. **The EVerest project does not run, reproduce or verify them.** Integrators
> may also report results from tests that are not public, so **a passing entry does not mean
> a fixed, comparable suite was run.**

## Submit your first test report in 5 steps

1. **Register.** Open a PR adding your id and the GitHub accounts that will submit to
   [`integrators/allowlist.yaml`](integrators/allowlist.yaml), and wait for a code owner to
   merge it. ([details](CONTRIBUTING.md#1-register-once))
2. **Pick a pointer and record the exact SHA.** Fetch `testing/main` (or a stable pointer)
   from `EVerest/EVerest`, note the full 40-character commit with `git rev-parse`, and build
   and test at it. ([details](CONTRIBUTING.md#2-pick-a-pointer-and-record-the-commit))
3. **Produce [CTRF](https://ctrf.io) or [OpenHTF](https://github.com/google/openhtf) with the required fields:** commit, pointer, SIL or HIL, real
   start and stop times, a passed build with its module list, and your integrator id. Tag
   each test. ([minimal example](CONTRIBUTING.md#31-a-minimal-working-ctrf-record),
   [field table](CONTRIBUTING.md#32-field-by-field-ctrf))
4. **Validate locally** in a checkout of this repository:

   ```bash
   python3 -m pip install -r scripts/requirements.txt
   python3 scripts/validate_submission.py results/your-id/testing/main/20260909.json
   ```

   ([details](CONTRIBUTING.md#5-validate-locally))
5. **Open a PR** from your fork that adds only that file. The gate comments accept or
   reject with the reason; an accepted PR auto-merges. ([details](CONTRIBUTING.md#6-open-the-pull-request))

[CONTRIBUTING.md](CONTRIBUTING.md) is the full walkthrough, including a fully automated
submission from any CI. [SCHEMA.md](SCHEMA.md) is the contract.

### Add your logo

Optional, for a public integrator: add `integrators/logos/<your-id>.svg`, check it with
`python3 scripts/build_status.py --check-logos`, and open a PR. The site shows it beside your
name. See [Adding a logo](CONTRIBUTING.md#adding-a-logo) for the rules and an example.

## What the gate checks

About the *result*:

1. The record validates against the schemas for its format.
2. The commit is a **full 40-character SHA** that exists in `EVerest/EVerest`.
3. That commit **appears in `pointers.json`** for the pointer claimed, so it really was an
   agreed base.
4. The reported build **passed**.

About the *submission*: the file is named `results/<id>/<pointer>/YYYYMMDD.json`, the
integrator id in the file matches the directory, that id is in the allowlist, and the
GitHub account opening the PR is one that entry authorizes. `results/` is **append-only**:
a PR that removes, edits or renames an existing record is refused.

**Beyond the build, no test is mandatory.** Which tests you run is your choice, and they may
be closed source. That lowers the bar to participating, at the cost of results not being
directly comparable between integrators. The rest of the record is not optional:
[SCHEMA.md](SCHEMA.md) lists the fields a record is rejected for omitting.

The full list of rejection codes is the `REJECTS` block at the top of
[`scripts/validate_submission.py`](scripts/validate_submission.py), which is the authority.

## Pointer branches

EVerest maintains **floating pointer branches** that a CI job force-pushes forward:

| Pointer | Tracks | Shown on the status site as |
|---|---|---|
| `testing/main` | `main` | main (development) |
| `testing/stable-2026.02` | `stable/2026.02` | 2026.02 (stable, stops moving soon) |
| `testing/stable-2026.10` | `stable/2026.10` | 2026.10 (stable) |

The site's names come from each pointer's `label`, `kind` (`development` or `stable`) and
optional `note` in [`pointers.json`](pointers.json).

Fetch one to get the agreed base to test against. They are **not releases**: no support
promise, no backports, and they are **ungated**, so a pointer can point at a broken `main`.
The point is a fast signal about whether something broke. They deliberately do not live
under `stable/`, because `stable/2026.02` is already a real protected branch that receives
backports.

**A force-pushed branch keeps no history**, so [`pointers.json`](pointers.json) is an
append-only log of every commit each pointer has pointed at. That log is what makes a
submission auditable, and the gate verifies a submitted commit appears in it. To follow
moves, watch `pointers.json` or subscribe to
`https://github.com/EVerest/EVerest/commits/testing/main.atom`.

EVerest's supported releases are separate, on a six-month cadence, and are not what this
repository tracks.

## Staleness

A result is marked stale once the pointer has **moved off** the commit it tested **and** the
result is more than **14 days** old. Both must hold: the pointers move often, so a newer
base alone means little. This is why run timestamps must be real; a result without one can
never be aged.

## Layout

| Path | What it is |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to submit, step by step |
| [`SCHEMA.md`](SCHEMA.md) | The submission contract, and why `extra` has to exist |
| `schema/ctrf.schema.json` | The normative CTRF schema, vendored so validation is reproducible |
| `schema/everest-extra.schema.json` | EVerest's contract for CTRF `results.extra` |
| `schema/everest-profile.schema.json` | A stricter overlay on CTRF for the freeform fields it leaves open |
| `schema/everest-htf-profile.schema.json` | The structural profile for a normalized OpenHTF record |
| `schema/everest-htf-metadata.schema.json` | EVerest's contract for OpenHTF `metadata` |
| `schema/vocabulary.json` | The controlled vocabulary for test tags and build targets |
| `schema/everest-modules.txt` | EVerest module names, used to warn on typos. Never hand-edit |
| `examples/` | The smallest legal record and a worked HIL record, for each format |
| `integrators/allowlist.yaml` | Who may submit, under which id, from which accounts. Logos go in `integrators/logos/<id>.svg` |
| `pointers.json` | Append-only log of where each pointer has pointed |
| `results/<id>/<pointer>/<YYYYMMDD>.json` | Submissions |
| `testdata/results/` | Fixture submissions used by the self-test |
| `testdata/allowlist.yaml` | The example integrator ids, allowlisted only inside the self-test |
| `scripts/validate_submission.py` | The intake gate. Run it locally before you push |
| `scripts/normalise.py` | Detects CTRF or OpenHTF and normalizes to OpenHTF |
| `scripts/classify_pr_files.py` | Decides what a PR is, and refuses any change to an existing record |
| `scripts/verify_pointers.py` | Checks the pointer branches and the log agree |
| `scripts/record_pointer_move.py` | Appends one pointer move to the log. Called by a workflow, never by hand |
| `scripts/refresh_module_names.py` | Regenerates `schema/everest-modules.txt` from EVerest's module manifests. Run by hand when the list goes stale |
| `scripts/archive.py` | Strips traces and attachments off records older than 6 months. Run by hand |
| `scripts/selftest.py` | 159 checks, almost all negative. Run after touching `scripts/` or `schema/` |
| `scripts/build_status.py` | Generates the status site: an overview, detailed and coverage page per pointer, a page per setup, a contribute page generated from this README and CONTRIBUTING.md, and `summary.json` and `regressions.json` |
| `status/` | Generated, and deployed to Pages |
| `docs/design/` | Design records |
| `docs/images/everest_horizontal-color.svg` | EVerest's logo, shown in the status site header. See [`THIRD_PARTY.md`](THIRD_PARTY.md) |

## Running it locally

```bash
python3 -m pip install -r scripts/requirements.txt

# the gate CI runs; the path is relative to the repository root.
# --skip-commit-check works offline
python3 scripts/validate_submission.py results/your-id/testing/main/20260909.json

# rebuild the site from results/ and pointers.json; no network needed
python3 scripts/build_status.py --grace-days 14
open status/index.html

# check the pointer branches and the log agree
python3 scripts/verify_pointers.py

# assert the gate still rejects what it should; all 159 checks must pass
python3 scripts/selftest.py
```

## Machine-readable output

[`status/summary.json`](status/summary.json) carries the latest result per pointer, per
integrator and configuration: commit, moves behind the tip, freshness, environment,
submission count, outcome counts per standard, tag coverage, failing measurements, and
the path of the record. It also lists records whose commit is not in the pointer's log.
[`status/regressions.json`](status/regressions.json) lists each regression with its last
good and first bad commit. Read these rather than scraping the pages.
