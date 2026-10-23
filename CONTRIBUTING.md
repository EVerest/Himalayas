# Submitting test results to Himalayas

A submission is one JSON file, [CTRF](https://ctrf.io) or
[OpenHTF](https://github.com/google/openhtf), added by pull request under
`results/<your-id>/<pointer>/`. This guide walks through it in order. The contract itself
is [SCHEMA.md](SCHEMA.md), and the authority on what is rejected is the `REJECTS` block at
the top of [`scripts/validate_submission.py`](scripts/validate_submission.py).

Two things to know first:

1. **Your CI does not have to be GitHub Actions.** A pull request can be opened entirely
   over the REST API from GitLab, Jenkins, Buildkite, a cron job or a shell script.
   [Section 7](#7-fully-automated-from-any-ci) is a working example.
2. **A pull request from your own fork needs no write access to this repository.** You hold
   no credential for anything of ours. That is why intake is a PR and not a token-based API.

Contents:

1. [Register once](#1-register-once)
2. [Pick a pointer and record the commit](#2-pick-a-pointer-and-record-the-commit)
3. [Write the record](#3-write-the-record)
4. [Tag, skip and link](#4-tag-skip-and-link)
5. [Validate locally](#5-validate-locally)
6. [Open the pull request](#6-open-the-pull-request)
7. [Fully automated, from any CI](#7-fully-automated-from-any-ci)
8. [Common rejections and how to fix them](#8-common-rejections-and-how-to-fix-them)
9. [Pre-push checklist](#9-pre-push-checklist)
10. [Reporting conventions](#10-reporting-conventions)

---

## 1. Register once

Open a PR adding yourself to [`integrators/allowlist.yaml`](integrators/allowlist.yaml):

```yaml
  - id: your-id                 # lowercase letters, digits, dashes; 1-40 chars
    display_name: Your Org      # omit this line to stay anonymous
    contact: ci@your-org.example
    github_users:               # required, and must list at least one account
      - your-ci-bot             # the account that will open your pull requests
```

Your `id` is also your `results/<id>/` directory, which is what makes it unique. It may not
start or end with a dash. Additions need approval from the repository's code owners, so this
is not instant.

**`github_users` is mandatory.** It lists the accounts allowed to publish as your id. A
submission opened by any other account is rejected with `not-authorised`, and an entry with
no list authorizes nobody. Without this binding any GitHub account could publish an
auto-merged result in your name. List every account your CI might open pull requests from:
a change to this file needs a code owner, so it is not something to fix in a hurry later.

Keep it in its **own PR**. A results PR that also changes anything outside `results/` is
refused. The allowlist PR gets a green `Validate submission` check, because it is not a
submission; it waits on a code owner's review, not on the gate.

### Staying anonymous

The repository is public, so there is no private submission and no per-submission privacy
flag. To stay unnamed, register a pseudonymous `id`, omit `display_name` from your
allowlist entry, and omit `extra.integrator.displayName` from your records. The status page
takes the name from the record, so a record that carries `displayName` names you whatever
the allowlist says. Without it the page shows `your-id (anonymous)`.

### Adding a logo

A public integrator may add its logo, which the status site shows beside its name on the
overview and on its setup pages. Add it as `integrators/logos/<your-id>.svg`, in the
allowlist PR or a later PR of its own; either way a code owner reviews it. By adding it you
confirm your organization may have it shown there.

- **SVG only**, at most 32 KiB, starting with `<svg` (an `<?xml?>` declaration and comments
  before it are fine).
- **Static shapes only.** Paths, basic shapes, groups, gradients, clip paths, masks and a
  `<style>` for fills. No `<script>`, event handlers, links (`<a>`), `<image>`, `<text>`,
  animation or `<foreignObject>`, and no `href`, `url()` or `@import` that points outside the
  file. Convert text to paths, and remove the link wrappers some design tools export.
  The site publishes the logo as re-written from these parts, not your file byte for byte.
- **Readable on white.** It is shown about 20 px tall on a small white plate, in light and
  dark mode alike, so use the version made for light backgrounds.
- **Not for anonymous ids.** A logo would name you, so an id without `display_name` gets
  none.

A logo that breaks a rule is left off the site, and the self-test names it.

---

## 2. Pick a pointer and record the commit

EVerest force-pushes three pointer branches forward daily: `testing/main`,
`testing/stable-2026.02` and `testing/stable-2026.10` (see the [README](README.md#pointer-branches)).
Pick one, then note the **exact commit** it points at. The pointers are branches of
`EVerest/EVerest`, so this runs in a checkout of that, not of this repository:

```bash
git fetch origin +refs/heads/testing/main:refs/remotes/origin/testing/main
git rev-parse origin/testing/main     # this full SHA goes in your submission
```

The `+` matters: pointers are force-pushed, so a plain fetch of a checked-out pointer branch
refuses to fast-forward.

**Record the commit, not the pointer name.** By the time anyone reads your submission the
pointer will have moved on. The full 40-character SHA is the only durable identifier your
record carries, and the gate checks it against [`pointers.json`](pointers.json) to confirm
it really was a tip of that pointer.

Then build and test at that commit.

---

## 3. Write the record

Either format is accepted, and both are normalized to **OpenHTF** internally. Both accept
both SIL and HIL runs. Pick the one your tooling already emits:

| | Strengths | The catch |
|---|---|---|
| **CTRF** | EVerest's own `run-tests.sh` emits JUnit XML, which converts in one command | No device field, so a **HIL** submission must set `extra.hardware.board`. No measurements |
| **OpenHTF** | Native `dut_id`, `station_id`, and **measurements with limits**, which the status page shows and CTRF cannot express | Nothing about EVerest: commit, build and integrator live in `metadata` |

### 3.1 A minimal working CTRF record

Start from this and edit. It is a SIL run with one passed and one skipped test, a link to
the CI run and a link to a human-readable report. With `your-id` replaced by an allowlisted
id it passes the gate as is:

```json
{
  "reportFormat": "CTRF",
  "specVersion": "1.0.0",
  "results": {
    "tool": { "name": "your-harness", "version": "1.4.0" },
    "summary": {
      "tests": 2, "passed": 1, "failed": 0, "skipped": 1, "pending": 0, "other": 0,
      "start": 1788944942564, "stop": 1788945589177
    },
    "environment": {
      "appName": "EVerest",
      "appVersion": "testing/main",
      "commit": "fdf2ac5415425e0a6af7d30caacf46f2bf9316ee",
      "branchName": "main",
      "testEnvironment": "SIL",
      "osPlatform": "linux",
      "healthy": true,
      "buildUrl": "https://ci.your-org.example/runs/1234"
    },
    "extra": {
      "build": { "status": "passed", "modules": ["EvseManager", "EvseSlac"] },
      "integrator": { "id": "your-id" },
      "reportUrl": "https://ci.your-org.example/runs/1234/report.pdf"
    },
    "tests": [
      {
        "name": "dc_session_start_stop",
        "status": "passed",
        "duration": 10990,
        "suite": ["sil", "DIN70121"],
        "tags": ["DC", "DIN70121", "smoke"]
      },
      {
        "name": "dc_bpt_discharge",
        "status": "skipped",
        "duration": 0,
        "suite": ["sil", "ISO15118-20"],
        "tags": ["DC", "ISO15118-20", "V2X"],
        "message": "Skipped: the simulated EV has no bidirectional mode"
      }
    ]
  }
}
```

The smallest legal records, with zero tests, are
[`examples/minimal-ctrf.json`](examples/minimal-ctrf.json) and
[`examples/minimal-openhtf.json`](examples/minimal-openhtf.json). Worked HIL records are
[`examples/ctrf-hil-dc.json`](examples/ctrf-hil-dc.json) and
[`examples/openhtf-hil-dc.json`](examples/openhtf-hil-dc.json). Copy one of these rather
than the example on [ctrf.io](https://ctrf.io), which omits the required `reportFormat` and
`specVersion` and does not validate.

The examples publish as `example-integrator` (Example Integrator GmbH) and
`example-anonymous`. Those ids are allowlisted only inside the self-test, so a copied example
is rejected with `not-allowlisted` until you replace the integrator `id`, and `displayName`
if present, with your own. The `example.com` URLs are placeholders for your CI's.

### 3.2 Field by field (CTRF)

**Required** means the gate rejects the record without it. **Encouraged** means it is
optional, but missing it costs you something on the status page or draws a warning.
**Optional** means nothing happens if it is absent.

| Field | | Rule |
|---|---|---|
| `reportFormat` | Required | `"CTRF"` |
| `specVersion` | Required | SemVer, e.g. `"1.0.0"` |
| `results.tool.name` | Required | the harness that produced the run. `tool.version` is optional |
| `results.summary.tests`, `passed`, `failed`, `skipped`, `pending`, `other` | Required | integers, consistent with `tests[]`. A nonzero `failed` marks the run failed even if no test in `tests[]` did |
| `results.summary.start`, `stop` | Required | real epoch milliseconds, **at least 1**. `junit-to-ctrf` writes `0`, which is rejected |
| `environment.commit` | Required | full 40-character lowercase SHA, present in `pointers.json` for that pointer, and present in `EVerest/EVerest` |
| `environment.appVersion` | Required | the pointer name: `testing/main`, `testing/stable-2026.02` or `testing/stable-2026.10`. Must match the pointer in the file path |
| `environment.testEnvironment` | Required | `SIL` or `HIL`, exactly |
| `extra.build.status` | Required | must be `passed`; a failed build is rejected |
| `extra.build.modules` | Required | non-empty list of unique PascalCase EVerest module names. A name not in [`schema/everest-modules.txt`](schema/everest-modules.txt) warns |
| `extra.integrator.id` | Required | equals your `results/<id>/` directory and is in the allowlist |
| `extra.hardware.board` | Required for HIL | the board model. SIL runs do not need it |
| `tests[].name` | Required | at most 400 characters. Keep it stable across runs ([section 10](#10-reporting-conventions)) |
| `tests[].status` | Required | `passed`, `failed`, `skipped`, `pending` or `other` |
| `tests[].duration` | Required | integer milliseconds; `0` is fine for a skip |
| `environment.buildUrl` | Encouraged | `https://` only. Missing it warns `no-build-url` |
| `extra.reportUrl` | Encouraged | `https://` only. Shown as **report** on the status page |
| `tests[].tags` | Encouraged | see [section 4.1](#41-tag-your-tests). Without tags a test counts toward no protocol |
| `tests[].suite` | Encouraged | non-empty list of non-empty strings, e.g. recovered from the JUnit `classname` |
| `tests[].message` | Encouraged | for failures and skips. Keep it short |
| `extra.configuration` | Encouraged | needed when you submit several setups per pointer. `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, e.g. `dc-full-ocpp`. Each configuration gets its own card; without it your newest submission hides the others |
| `environment.branchName` | Optional | `main` or `stable/yyyy.mm` |
| `environment.appName` | Optional | if present, `EVerest` |
| `environment.osPlatform` | Optional | `linux`, `darwin` or `windows` (lowercase) |
| `environment.healthy` | Optional | a boolean mirror of the build status, for generic CTRF readers |
| `extra.build.targets[]` | Optional | `{target, status, toolchain}`: a triple like `aarch64-linux-gnu`, `passed`/`failed`/`skipped`, and `<compiler> <version>` like `gcc 13.2.0`. A target EVerest does not build warns |
| `extra.integrator.displayName` | Optional | omit to stay anonymous |
| `extra.hardware.bsp`, `chargeController` | Optional | HIL detail |
| `tests[].trace` | Optional | truncate it ([section 4.3](#43-link-the-report-and-keep-the-file-small)) |
| `tests[].rawStatus`, `flaky`, `device`, `extra` | Optional | accepted. Of these only `flaky` is carried into the normalized record today |

`results.environment`, `results.tool` and each test are closed by the CTRF schema, and
`results.extra` is closed by [`schema/everest-extra.schema.json`](schema/everest-extra.schema.json)
at every level. **An unknown key in any of them is a rejection**, not ignored data. If you
need a field that does not exist, open an issue.

### 3.3 Producing CTRF from JUnit XML

EVerest already writes JUnit XML from `tests/run-tests.sh`, so start there:

```bash
npx junit-to-ctrf@latest result.xml -o ctrf.json -u false \
  -e appName=EVerest \
     appVersion=testing/main \
     commit="$EVEREST_SHA" \
     branchName=main \
     testEnvironment=SIL \
     osPlatform=linux \
     buildUrl="$CI_JOB_URL"
```

**Then post-process. The converter has two gaps and both matter here:**

| Gap | Why it matters | Fix |
|---|---|---|
| `summary.start` / `stop` come out **`0`**, though the JUnit XML carries a `timestamp` | Rejected: a result with no timestamp can never be aged, so it could never be marked stale | Inject real epoch-millisecond values |
| **`classname` is dropped**, so every test lands as `suite: ["pytest"]` | Nobody can tell an AC result from a DC one | Recover it into `suite`, and add `tags` like `["DC","ISO15118-2"]` |

Finally add `results.extra` with `build` (including `modules`) and `integrator`, and
`hardware.board` for a HIL run.

### 3.4 If you are submitting OpenHTF

Export the `TestRecord` as JSON via `openhtf.output.callbacks.json_factory`, then put the
EVerest fields in `metadata` per [SCHEMA.md](SCHEMA.md): `metadata.everest` (`commit`,
`pointer`, `testEnvironment`, and optionally `tracks`, `buildUrl`, `reportUrl`,
`configuration`), `metadata.build` and `metadata.integrator`. `dut_id`, `station_id`,
`start_time_millis` and `end_time_millis` are required. Two defaults to change:

- **`inline_attachments=False`.** OpenHTF base64-inlines attachments by default; the gate
  caps inline bulk at 256 KB and this repository keeps submissions indefinitely.
- **Consider stripping `codeinfo`.** OpenHTF includes each phase's source code by default,
  and **this repository is public.** The gate warns rather than rejects; if those tests are
  not meant to be published, strip it. A CTRF `snippet` triggers the same warning.

---

## 4. Tag, skip and link

### 4.1 Tag your tests

Tags are what put a test into a per-protocol view. They come from a controlled vocabulary,
[`schema/vocabulary.json`](schema/vocabulary.json):

| Group | Values |
|---|---|
| Power | `AC` `DC` |
| High-level comms | `ISO15118-2` `ISO15118-3` `ISO15118-20` `DIN70121` |
| Basic comms | `IEC61851` |
| OCPP | `OCPP1.6` `OCPP2.0.1` `OCPP2.1` |
| Features | `TLS` `PnC` `SmartCharging` `Eichrecht` `Reservation` `EEBUS` `V2X` |
| Lifecycle | `mandatory` `smoke` `compliance` `regression` |

Give each test its power type, its protocol, and any feature it exercises, e.g.
`["DC", "ISO15118-20", "TLS"]`.

**Anything of your own must be prefixed `x-`**, then lowercase letters, digits and dashes,
e.g. `x-derating-ramp`. That keeps private tags from colliding with future standard ones and
keeps them out of the per-protocol rollups. Expect `x-` tags to be common: closed-source
tests are allowed.

What the gate does with a tag:

| Tag | Example | Result |
|---|---|---|
| In the vocabulary | `ISO15118-2` | accepted |
| `x-` prefixed | `x-derating-ramp` | accepted |
| Well-formed but not in the vocabulary | `ISO15118-4` | accepted, warns `tag-vocabulary` |
| Free text, spaces, or `x-` with capitals | `ISO 15118-2 over TLS`, `x-Ramp` | **rejected** (`ctrf-profile`) |

"Well-formed" means PascalCase with optional dash-separated parts (`OCPP2.0.1`), all
lowercase (`smoke`), or `x-` prefixed.

The status page counts a tag as covered only if a test carrying it **passed**. If none
passed and at least one failed, the tag shows as failed; if every test with it was skipped,
it shows as not run.

### 4.2 Mark skips

A test that did not run is `"status": "skipped"`, with the reason in `message`:

```json
{ "name": "dc_bpt_discharge", "status": "skipped", "duration": 0,
  "tags": ["DC", "ISO15118-20", "V2X"],
  "message": "Skipped: the simulated EV has no bidirectional mode" }
```

Keep the protocol tags on a skipped test: the page then shows that protocol as not run
rather than leaving it out. `pending` is treated the same as `skipped`. Do not use `skipped`
for a test that ran and failed, and see [section 10](#10-reporting-conventions) for
deviations, inconclusive results and rig limitations.

### 4.3 Link the report and keep the file small

Two links, both optional, both `https://` only:

| Link | CTRF | OpenHTF | Shown as |
|---|---|---|---|
| The CI run | `environment.buildUrl` | `metadata.everest.buildUrl` | the run link |
| A human-readable report (PDF or HTML) | `extra.reportUrl` | `metadata.everest.reportUrl` | **report**, next to the run link |

Use `reportUrl` especially when the CI run itself is not public. The status page never
fetches either link.

**Truncate `trace`.** A few hundred characters is plenty. On a real EVerest run a failed
test is about 4,000 bytes of CTRF against about 123 for a passed one, so failures dominate
the file, and this repository keeps submissions indefinitely. The caps:

| Cap | Limit | Over it |
|---|---|---|
| Whole file | 1 MB | `too-big` |
| Inline bulk: `message`, `trace`, `stdout`, `stderr`, inline attachments | 256 KB | `too-big`; over half warns `attachments-large` |

Full stack traces belong behind `buildUrl`. Records older than six months may later be
archived with traces and attachments stripped (`scripts/archive.py`, run by hand).

---

## 5. Validate locally

The gate CI runs is a script in this repository. Run it in a checkout of Himalayas with
your file at its final path:

```bash
python3 -m pip install -r scripts/requirements.txt
mkdir -p results/your-id/testing/main
cp ctrf.json results/your-id/testing/main/20260909.json
python3 scripts/validate_submission.py results/your-id/testing/main/20260909.json
```

Things to know:

- **Pass the path as `results/...`.** The gate resolves it against the repository root and
  checks its shape, so an absolute path or a file elsewhere is rejected with `bad-path`.
- **`--skip-commit-check` works offline.** Without it the gate asks the GitHub API whether
  the commit exists, and an unauthenticated call can be rate-limited; set `GITHUB_TOKEN` to
  avoid that. An unanswered check only warns (`commit-uncheckable`). The `pointers.json`
  check runs either way.
- **The author binding is not checked locally** unless you pass `--author <github-login>`.
  CI always passes the pull request author.
- **Before your allowlist PR merges**, the gate rejects your id with `not-allowlisted`.
  Everything else it reports is still accurate.
- Exit code `0` is accepted, `1` rejected, `2` a tool error (the gate could not reach a
  verdict).

A local pass with `--author` and without `--skip-commit-check` is a pass of the same gate CI runs.

If you change anything under `scripts/` or `schema/`, also run the gate's own suite, which
is almost entirely negative cases asserting the specific rejection code:

```bash
python3 scripts/selftest.py        # 150 checks, all of which must pass
```

---

## 6. Open the pull request

Add one file per run at `results/<your-id>/<pointer>/<YYYYMMDD>.json`, e.g.
`results/your-id/testing/main/20260909.json`. For a second run on the same pointer the same
day, add a numeric suffix: `20260909-2.json`, then `-3` and so on. **`-1` is rejected**: the
first submission of a day carries no suffix, so `-1` would be a second legal name for the
same thing. One PR may add several files, for example one per configuration.

The gate comments the verdict, with every reason at once. On rejection, push a fix to the
same branch and it re-runs. On acceptance auto-merge lands it, and the status page
redeploys within about 20 minutes. On a tool error the PR is closed; open a new one, or
raise an issue if the file looks correct.

**`results/` is append-only.** A PR may only *add* files there. A PR that deletes, edits or
renames an existing record is refused, including one that changes your own: a correction is
a new file, never an edit. Removing a record needs write access and a human.

---

## 7. Fully automated, from any CI

No GitHub Actions, no `git`. Fork this repository once by hand, then per run. The script
assumes the record is already written to `ctrf.json` in the working directory; produce it
first, or change the filename on the `b64` line.

`UPSTREAM` below is `EVerest/Himalayas`, agreed with the EVerest TSC on 2026-09-09.

```bash
#!/usr/bin/env bash
set -euo pipefail

FORK=your-org/Himalayas             # your fork
UPSTREAM=EVerest/Himalayas
ID=your-id
POINTER=testing/main
FILE="results/$ID/$POINTER/$(date -u +%Y%m%d).json"
BRANCH="submit-${POINTER//\//-}-$(date -u +%Y%m%d)"
API="https://api.github.com"
H=(-H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json")

# base64 -w0 is GNU coreutils. On macOS and the BSDs base64 does not wrap and
# rejects -w, so pick whichever this machine has.
if base64 -w0 </dev/null >/dev/null 2>&1; then b64() { base64 -w0 "$1"; }
else b64() { base64 "$1" | tr -d '\n'; }
fi

# 1. branch off your fork's default branch
BASE=$(curl -fsSL "${H[@]}" "$API/repos/$FORK/git/ref/heads/main" | jq -r .object.sha)
curl -fsSL -X POST "${H[@]}" "$API/repos/$FORK/git/refs" \
  -d "$(jq -n --arg r "refs/heads/$BRANCH" --arg s "$BASE" '{ref:$r,sha:$s}')" >/dev/null

# 2. commit the submission
curl -fsSL -X PUT "${H[@]}" "$API/repos/$FORK/contents/$FILE" \
  -d "$(jq -n --arg m "results: $ID on $POINTER" --arg b "$(b64 ctrf.json)" \
              --arg br "$BRANCH" '{message:$m,content:$b,branch:$br}')" >/dev/null

# 3. open the PR against upstream
curl -fsSL -X POST "${H[@]}" "$API/repos/$UPSTREAM/pulls" \
  -d "$(jq -n --arg t "results: $ID on $POINTER" --arg h "${FORK%%/*}:$BRANCH" \
              '{title:$t, head:$h, base:"main", maintainer_can_modify:true}')" \
  | jq -r '"PR opened: \(.html_url)"'
```

The script names the file by today's date with no suffix, so a second run on the same day
needs `-2` added to `FILE` and `BRANCH`.

`$GH_TOKEN` needs write access to **your fork only**: `public_repo` on a classic PAT, or a
fine-grained PAT scoped to your fork with *Contents: write* and *Pull requests: write*. It
needs **nothing** on `EVerest/Himalayas`.

---

## 8. Common rejections and how to fix them

The gate lists every failed check at once. The codes, roughly in the order people hit them:

| Code | Cause | Fix |
|---|---|---|
| `ctrf-profile` | A constrained field is malformed: `start`/`stop` of `0`, a short SHA, `sil` instead of `SIL`, `Linux` instead of `linux`, a free-text tag, an `http://` `buildUrl` | Fix the field named in the message; [section 3.2](#32-field-by-field-ctrf) has every rule |
| `ctrf-schema` | Not valid CTRF: a test without `duration`, no `results.tool`, a missing summary count, an unknown key under `environment` or a test | Add the missing field or remove the unknown key |
| `extra-contract` | `results.extra` has an unknown key or a malformed value, e.g. no `modules` | Use only the keys in [SCHEMA.md](SCHEMA.md#resultsextra) |
| `htf-profile` / `htf-metadata` | The normalized record breaks the OpenHTF profile or the metadata contract. On the CTRF path this usually repeats a CTRF error | Fix the CTRF error first; on the OpenHTF path, fix `metadata` |
| `pointer-mismatch` | The pointer in the path and `appVersion` (or `metadata.everest.pointer`) disagree | Make them the same pointer name |
| `commit-not-pointed` | The commit was never a tip of that pointer, per `pointers.json` | Test a commit the pointer actually pointed at. Often caused by a wrong `appVersion`, which also fails `ctrf-profile` and `pointer-mismatch` |
| `commit-malformed` | Not a full 40-character lowercase SHA | Use `git rev-parse` output, not a short SHA |
| `commit-unknown` | The commit is not in `EVerest/EVerest` | Check you fetched from the right repository |
| `commit-missing` | No commit at all | Set `environment.commit` |
| `build-missing` / `build-failed` | No build reported, or its status is not `passed` | Only passing builds are accepted |
| `hil-needs-board` | A HIL run submitted as CTRF names no board | Set `extra.hardware.board` |
| `id-missing` / `id-mismatch` | No integrator id, or it differs from the `results/<id>/` directory | Make them equal |
| `not-allowlisted` | The id is not in `integrators/allowlist.yaml` | Register first ([section 1](#1-register-once)) |
| `not-authorised` | The PR author is not in that entry's `github_users`, or the entry has none | Open the PR from a listed account, or have an owner add the account |
| `pointer-unknown` | The pointer in the path is not declared in `pointers.json` | Use a declared pointer |
| `bad-path` | Not `results/<id>/<pointer>/YYYYMMDD.json`, or a `-1` suffix | Rename the file |
| `too-big` | Over 1 MB, or over 256 KB of inline bulk | Truncate traces, export OpenHTF with `inline_attachments=False` |
| `not-json` / `unknown-format` | Unparseable, or neither CTRF (`reportFormat`, `specVersion`) nor OpenHTF (`dut_id`, `phases`) | Start from an example |

Before the gate runs, the PR itself is refused if it **removes, edits or renames** anything
under `results/` (append-only), or if it adds results **and** changes anything outside
`results/`.

**Warnings never block:** `no-build-url`, `tag-vocabulary` (a tag outside the vocabulary),
`module-names` (a module name not in EVerest), `target-vocabulary` (a build target EVerest
does not build), `attachments-large`, `sourcecode` (embedded test source), and
`commit-uncheckable` (the GitHub API did not answer).

---

## 9. Pre-push checklist

- [ ] My id is in `integrators/allowlist.yaml`, and the account opening the PR is in its
      `github_users`.
- [ ] The file is at `results/<my-id>/<pointer>/YYYYMMDD.json` (`-2`, `-3`... for a second
      run the same day, never `-1`), and the PR changes nothing outside `results/`.
- [ ] `commit` is the full SHA that `git rev-parse` printed for the pointer I tested.
- [ ] `appVersion` is that pointer name, and matches the path.
- [ ] `summary.start` and `stop` are real epoch milliseconds, not `0`.
- [ ] `extra.build.status` is `passed` and `extra.build.modules` lists the modules.
- [ ] A HIL run sets `extra.hardware.board`.
- [ ] Every test has a power and protocol tag, and private tags start with `x-`.
- [ ] Skipped tests are `skipped` with a reason in `message`.
- [ ] Traces are truncated, and `buildUrl` (and `reportUrl` if I have a report) is set.
- [ ] Several setups on one pointer each carry their own `extra.configuration`.
- [ ] For anonymity, no `displayName` in the record.
- [ ] `python3 scripts/validate_submission.py results/...` exits `0`.

---

## 10. Reporting conventions

These conventions are agreed for a redesigned status page that is not built yet. Adopting
them now keeps your history comparable once it is. Each says what the gate and the page do
**today**. The design record is [docs/design/status-page.md](docs/design/status-page.md).

### Stable test names

The test name is the test's identity: comparisons, history and regression detection will
match tests by name across runs. Keep each name the same from run to run, keep run-specific
values (timestamps, ports, serial numbers) out of it, keep names unique within a record, and
use one naming scheme within a suite, e.g. not a pytest node id like
`tests/test_x.py::test_y[dut]` among `TC_...` names.

*Planned: the gate does not check this yet.* It will warn on duplicate names, on a suite
mixing naming schemes, and on a test with no tag from the vocabulary.

### Conformance tags

A conformance test will be any test carrying a conformance-spec tag, and each spec maps to
the standard it tests:

| Tag | Standard |
|---|---|
| `ISO15118-4` | ISO 15118-2 |
| `ISO15118-5` | ISO 15118-3 |
| `ISO15118-21` | ISO 15118-20 |
| `DIN70122` | DIN 70121 |
| `OCPP1.6-TestCases`, `OCPP2.0.1-Part6`, `OCPP2.1-Part6` | that OCPP version |

A test derived from a requirement of the standard can carry the requirement id as a tag,
e.g. `V2G3-M06-08`, `V2G2-...`, `V2G20-...`, `V2G-DC-...`, `IEC61851-1-...`,
`OCPP2.0.1-...`. Keep the protocol tag (`ISO15118-2` and so on) as well.

Today these tags are well-formed and accepted, and they warn `tag-vocabulary` until the
vocabulary lists them. *Planned:* the `compliance` lifecycle tag will be deprecated with a
warning, so prefer a spec tag for new tests.

### Accepted deviations

A test that fails on purpose, because you accept a deviation from a clause, keeps
`"status": "failed"` and states the deviation in `tests[].extra.acceptedDeviation`:

```json
{ "name": "...", "status": "failed", "duration": 16827,
  "message": "...",
  "extra": { "acceptedDeviation": {
    "reason": "Kept for compatibility with deployed EVs",
    "clause": "V2G3-M06-08",
    "since": "2026-09-01" } } }
```

Today the gate accepts `tests[].extra` (CTRF leaves it open and no EVerest schema constrains
it), but the normalizer drops it, so **the status page shows such a test as failed.**
*Planned:* the page shows it as accepted, never as a pass. Do not report a deviation as
`skipped`. The bare tag `x-accepted-deviation` on a skipped test is accepted today;
*planned:* it will warn and show as "accepted (clause not declared)" for a transition
period.

### Inconclusive results

A test whose verdict is neither pass nor fail, for example because a precondition could not
be established, is `"status": "other"` with `tests[].extra.inconclusive`:

```json
{ "name": "...", "status": "other", "duration": 15847,
  "message": "Precondition not established: ...",
  "extra": { "inconclusive": { "reason": "Precondition not established" } } }
```

Today the gate accepts this, but `other` is normalized to an OpenHTF `ERROR`, so **the page
counts it as a failure and marks the run failed.** *Planned:* inconclusive becomes its own
outcome, never a pass or a failure. Do not report it as `failed` with an `x-inconclusive`
tag or as `skipped`.

### Rig limitations

A test your rig cannot exercise is `"status": "skipped"` with the tag `x-rig-limitation` and
the reason in `message`. This works today as an ordinary skip. *Planned:* the page labels it
"not exercised on this rig", and a passed test carrying the tag stays a pass with a
footnote.

### Failure keys

Put the stable cause of a failure on the **first line** of `message`, and volatile detail
after it. *Planned:* failures will be grouped by that first line with hex addresses,
decimals, ISO timestamps and UUIDs blanked (integers are kept). To choose the group
yourself, set `tests[].extra.failureKey` to a short string. It is accepted today and
ignored.

### Evidence hashes and test systems

*Planned, and rejected today:* `extra.reportSha256` (a SHA-256 of the file at `reportUrl`)
and a `testSystem` (`{name, version}`) for runs made with a reference test system.
`results.extra` is closed, so the gate rejects each of them with `extra-contract`. A
Vendor Declaration of Conformance (VDoC) is not a separate field: link it as the report. **Do not emit them** until the
schema adds them. Use `extra.reportUrl` for the report link today.
