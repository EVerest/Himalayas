# Himalayas

**EVerest federated testing.** Build and test results reported by EVerest integrators,
against the floating `testing/` pointer branches of
[EVerest](https://github.com/EVerest/EVerest).

All EVerest documentation and issue tracking lives in the
[main EVerest repository](https://github.com/EVerest/everest).

**→ [Status page](https://everest.github.io/Himalayas/)** - who has reported what,
against which pointer, and how recently.

Submissions are accepted as **CTRF** or **OpenHTF** and normalised to OpenHTF internally.
**Both formats accept both SIL and HIL runs** — see [SCHEMA.md](SCHEMA.md).

> ### These results are self-reported
>
> Each submission is produced by the integrator named on it, on their own hardware and in
> their own CI. **The EVerest project does not run, reproduce or verify them.** Integrators
> may also report results from tests that are not public, so **a passing entry does not mean
> a fixed, comparable suite was run.**

## What EVerest actually checks

Four things about the *result*:

1. The record validates against the schemas for its format.
2. The commit is a **full 40-character SHA** that exists in `EVerest/EVerest`.
3. That commit **appears in `pointers.json`** for the pointer claimed — i.e. it really was
   an agreed base at some point.
4. The reported build **passed**.

And four about the *submission itself*: the file is named
`results/<id>/<pointer>/YYYYMMDD.json`, the integrator id in the file matches the directory,
that id is on [`integrators/allowlist.yaml`](integrators/allowlist.yaml), and the GitHub
account opening the pull request is one that entry authorises. `results/` is **append-only**,
so a pull request that removes, edits or renames an existing record is refused outright.

The full list of rejection codes, each with its reason, is the `REJECTS` block at the top of
[`scripts/validate_submission.py`](scripts/validate_submission.py). That file is the
authority; this section is the summary.

**Beyond the build, no test is mandatory.** The build is the only *test outcome* the gate
requires — which tests you run is your choice, and they may be closed source. That is a
deliberate trade: it lowers the bar to participating, at the cost of results not being
directly comparable between integrators. It does not mean the rest of a submission is
optional: [SCHEMA.md](SCHEMA.md) lists the fields a record is rejected for omitting, and the
module list is among them.

## Submitting

Open a pull request adding one file at `results/<your-id>/<pointer>/YYYYMMDD.json` — for
example `results/deadbeef-charge/testing/main/20260909.json`. For a second run on the same
pointer the same day, add a numeric suffix: `20260909-2.json`, then `-3` and so on.
**`-1` is not a legal name**, deliberately: the first submission of a day has no suffix, so
allowing `-1` would give two legal names for the same thing.

Only *added* files are accepted. `results/` is an append-only record: a correction is a new
file, never an edit, and a removal needs write access and a human.

A bot validates the submission and comments accept or reject **with the reason**; accepted
submissions auto-merge and the status page redeploys within 20 minutes.

Your CI does not have to be GitHub Actions, and a PR from your own fork needs **no write
access here** — see [CONTRIBUTING.md](CONTRIBUTING.md), which includes a working
REST-API-only example.

## What a pointer branch is

EVerest maintains **floating pointer branches** that a CI job force-pushes forward daily:

| Pointer | Tracks |
|---|---|
| `testing/main` | `main` |
| `testing/stable-2026.02` | `stable/2026.02` |

Fetch one to get the agreed base to test against. They are **not releases**: no support
promise, no backports, and they are **ungated** — a pointer can point at a broken `main`. That
is deliberate: the point is a fast signal about whether something broke.

Note the names. They deliberately do **not** live under `stable/`, because `stable/2026.02` is
already a real protected branch that receives backports.

**A force-pushed branch keeps no history**, so [`pointers.json`](pointers.json) is an
append-only log of every commit each pointer has pointed at. That log is what makes a
submission auditable: without it, "I tested `testing/main`" would be uncheckable a day later.
The gate verifies a submitted commit appears in it.

Discovery: subscribe to `https://github.com/EVerest/EVerest/commits/testing/main.atom`, or
watch `pointers.json`.

EVerest's supported releases are separate, on a six-month cadence, and are not what this
repository tracks.

## Layout

| Path | What it is |
|---|---|
| [`SCHEMA.md`](SCHEMA.md) | The submission contract, and why `extra` has to exist |
| `schema/ctrf.schema.json` | The normative CTRF schema, vendored so validation is reproducible |
| `schema/everest-extra.schema.json` | **EVerest's own contract** for `results.extra` |
| `schema/everest-profile.schema.json` | A **stricter overlay on CTRF**, constraining the freeform string fields CTRF leaves open |
| `schema/everest-htf-profile.schema.json` | The structural profile for a normalised OpenHTF record |
| `schema/everest-htf-metadata.schema.json` | **EVerest's contract** for OpenHTF `metadata` — commit, pointer, build, integrator |
| `schema/vocabulary.json` | The controlled vocabulary for `test.tags` and build targets |
| `schema/everest-modules.txt` | EVerest module names, used to warn on typos |
| `examples/minimal-ctrf.json` | The smallest legal CTRF submission |
| `examples/ctrf-hil-dc.json` | A worked **HIL** submission in CTRF: build matrix, per-test tags, a private test |
| `examples/minimal-openhtf.json` | The smallest legal OpenHTF submission |
| `examples/openhtf-hil-dc.json` | A worked **HIL** submission in OpenHTF, with measurements and their limits |
| `integrators/allowlist.yaml` | Who may submit, under which id |
| `pointers.json` | **Append-only log** of where each pointer branch has pointed |
| `results/<id>/<pointer>/<YYYYMMDD>.json` | Submissions, grouped by pointer |
| `scripts/normalise.py` | Detects CTRF or OpenHTF and normalises to OpenHTF |
| `scripts/validate_submission.py` | The intake gate. Run it locally before you push |
| `scripts/verify_pointers.py` | Checks the pointer branches and the log agree |
| `scripts/record_pointer_move.py` | Appends one pointer move to the log. Called by `record-pointer-move.yml`, never by hand |
| `scripts/classify_pr_files.py` | Decides what a pull request is, and refuses any change to an existing record |
| `scripts/archive.py` | Strips traces and attachments off records older than 6 months. **Run by hand; no workflow invokes it** |
| `scripts/selftest.py` | **134 checks, almost all negative.** Run this after touching anything in `scripts/` or `schema/` |
| `scripts/build_status.py` | Generates `status/index.html` and `status/summary.json` |
| `status/` | Generated, and deployed to Pages |

## Running it locally

```bash
python3 -m pip install -r scripts/requirements.txt

# the same gate CI runs; --skip-commit-check works offline
python3 scripts/validate_submission.py results/deadbeef-charge/testing/main/20260909.json

# rebuild the page
python3 scripts/build_status.py --grace-days 14
open status/index.html

# check the pointer branches and the log agree
python3 scripts/verify_pointers.py

# assert the gate still REJECTS what it should - run this after any change
python3 scripts/selftest.py
```

## Staleness

A result is marked stale once the pointer branch has **moved off** the commit it tested **and**
the result is more than **14 days** old. Both conditions must hold: the pointers move daily, so
a newer base alone means very little.

This is why the run timestamps must be real. `junit-to-ctrf` leaves them at `0`, and a result
with no timestamp can never be aged — see
[CONTRIBUTING.md §4](CONTRIBUTING.md#4-producing-the-file).

## Machine-readable output

[`status/summary.json`](status/summary.json) carries the current state per integrator:
pointer, commit, environment, build status, pass/fail counts, age in days, and the stale
flag. It is the file to read from, rather than scraping the page.
