# Submitting results to Himalayas

Two things to know before anything else:

1. **Your CI does not have to be GitHub Actions.** A pull request can be opened entirely
   over the REST API from GitLab, Jenkins, Buildkite, a cron job, or a shell script.
   [§3](#3-fully-automated-from-any-ci) is a working example.
2. **A pull request from your own fork needs no write access to this repository.** You hold
   no credential for anything of ours. That is why intake is a PR and not a token-based API.

---

## 1. Once, before your first submission

Open a PR adding yourself to [`integrators/allowlist.yaml`](integrators/allowlist.yaml):

```yaml
  - id: your-id                 # lowercase letters, digits, dashes; 1-40 chars
    display_name: Your Org      # OMIT this line to stay anonymous
    contact: ci@your-org.example
    github_users:               # REQUIRED, and must list at least one account
      - your-ci-bot             # the account that will open your pull requests
```

Your `id` is also your `results/<id>/` directory, which is what makes it unique. Additions
need approval from the repository's code owners, so this is not instant.

**`github_users` is the accounts allowed to publish as your id, and it is mandatory.** A
submission opened by any other account is rejected with `not-authorised`, and an entry with
no list authorises nobody. That is what makes an attributed result mean something: without
it, any GitHub account could publish an auto-merged result in your name. List every account
your CI might open pull requests from — a change to this file needs a code owner, so it is
not something to fix in a hurry later.

Keep it in its **own PR** — the gate rejects a results PR that changes anything outside
`results/`. That PR gets a green `Validate submission` check, because it is not a
submission; it waits on a code owner's review, not on the gate.

### Staying anonymous

The repository is public, so there is no such thing as a private submission, and there is no
per-submission privacy flag. If you do not want to be named, register a pseudonymous `id`
and omit `display_name`. Nothing else is required. The status page then shows
`your-id (anonymous)`.

---

## 2. Every run, by hand

1. Fetch a pointer branch and note the **exact commit** it points at. The pointer branches
   are branches of `EVerest/EVerest`, so this runs in a checkout of that, not of this
   repository:

   ```bash
   git fetch origin +refs/heads/testing/main:refs/remotes/origin/testing/main
   git rev-parse origin/testing/main     # this full SHA goes in your submission
   ```

   The `+` matters: pointers are force-pushed, so a plain fetch of a checked-out pointer
   branch will refuse to fast-forward.
2. Build and test at that commit.
3. Produce the record ([§4](#4-producing-the-file)) — **CTRF or OpenHTF**, either is accepted.
4. Open a PR **adding one file**: `results/<your-id>/<pointer>/<YYYYMMDD>.json`, e.g.
   `results/deadbeef-charge/testing/main/20260909.json`. For a second run on the same pointer
   the same day, add a numeric suffix — `20260909-2.json`, then `-3` and so on. **`-1` is
   rejected**: the first submission of a day carries no suffix, so `-1` would be a second
   legal name for the same thing.
5. The gate comments the verdict. On rejection, push a fix to the same branch — it re-runs.
   On acceptance, auto-merge lands it.

**`results/` is append-only.** Your PR may only *add* files. A PR that deletes, edits or
renames an existing record is refused outright, including one that changes your own: a
correction is a new file with a new name, never an edit. Removing a record needs write
access and a human.

**Record the commit, not the pointer name.** A pointer branch is force-pushed, so it will have
moved on by the time anyone reads your submission. The full 40-character SHA is the only
durable identifier your record carries, and the gate checks it against
[`pointers.json`](pointers.json) to confirm it really was an agreed base.

---

## 3. Fully automated, from any CI

No GitHub Actions, no `git`. Fork this repository once by hand, then per run. The script
assumes the record from [§4](#4-producing-the-file) is already written to `ctrf.json` in the
working directory — produce it first, or change the filename on the `b64` line.

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

`$GH_TOKEN` needs write access to **your fork only** — `public_repo` on a classic PAT, or a
fine-grained PAT scoped to your fork with *Contents: write* and *Pull requests: write*. It
needs **nothing** on `EVerest/Himalayas`.

---

## 4. Producing the file

Either format is accepted and both are normalised to **OpenHTF** internally.

**Both formats accept both SIL and HIL runs.** Pick on what your tooling already emits:

| | Strengths | The catch |
|---|---|---|
| **OpenHTF** | Native `dut_id`, `station_id`, and **measurements with limits** — measurements appear on the status page, and CTRF cannot express them at all | Nothing about EVerest: no commit, build or suite fields, so those live in `metadata` |
| **CTRF** | EVerest's own `run-tests.sh` already emits JUnit XML, which converts in one command | No device field, so a **HIL** submission must set `extra.hardware.board` |

There is a worked HIL example for each: [`examples/ctrf-hil-dc.json`](examples/ctrf-hil-dc.json)
and [`examples/openhtf-hil-dc.json`](examples/openhtf-hil-dc.json).

### If you are submitting OpenHTF

Export the `TestRecord` as JSON via `openhtf.output.callbacks.json_factory`, then put the
EVerest fields in `metadata` per [SCHEMA.md](SCHEMA.md). Two defaults to change:

- **`inline_attachments=False`.** OpenHTF base64-inlines attachments by default; the gate caps
  them at 256 KB and this repository keeps submissions indefinitely.
- **Consider stripping `codeinfo`.** OpenHTF includes each phase's `sourcecode` in the record
  by default. **This repository is public.** The gate warns rather than rejects — it is your
  file and your choice — but if those tests are not meant to be published, strip it.

### If you are submitting CTRF

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
| `summary.start` / `stop` come out **`0`**, though the JUnit XML carries a `timestamp` | A result with no timestamp **can never be aged**, so it can never be marked stale — and the gate warns about it | Inject real epoch-millisecond values |
| **`classname` is dropped** — every test lands as `suite: ["pytest"]` | Nobody can tell an AC result from a DC one, so per-protocol views are impossible | Recover it into `suite`, and add `tags` like `["DC","ISO15118-2"]` |

Finally add `results.extra` per [SCHEMA.md](SCHEMA.md). `build` and `integrator` are required,
and inside `build` both `status` **and a non-empty `modules` list** are required — a submission
that does not say which modules it exercised cannot be compared with anyone else's.

**If `testEnvironment` is `HIL`, you must also give `extra.hardware.board`.** CTRF has no device
field, so without it the record cannot identify which charger produced the result. SIL
submissions do not need it.

### Fill the standardised fields correctly

CTRF leaves `commit`, `appVersion`, `testEnvironment`, `osPlatform` and `test.tags` as
freeform strings, which is enough to make the aggregate meaningless. They are constrained by
[`schema/everest-profile.schema.json`](schema/everest-profile.schema.json) and the gate
**rejects** violations. The ones people get wrong:

| Field | Must be | Common mistake |
|---|---|---|
| `commit` | **full 40-char** SHA | a short SHA |
| `appVersion` | the **pointer branch name**: `testing/main` or `testing/stable-2026.02` | a tag like `test/2026.09.2`, or a CalVer release like `2026.02.1`. This one field poisons four checks at once: the normaliser derives `metadata.everest.pointer` from it, so a wrong value fails `ctrf-profile`, `htf-metadata`, `pointer-mismatch` **and** `commit-not-pointed` - the last reporting a real commit as never having been an agreed base |
| `testEnvironment` | `SIL` or `HIL` | `sil`, `hardware`, `simulation` |
| `osPlatform` | `linux` | `Linux` |
| `summary.start`/`stop` | real epoch millis | `0`, straight from `junit-to-ctrf` |
| `buildUrl` | an `https://` URL | an internal `http://` host |

And tags come from a [controlled vocabulary](SCHEMA.md#the-tag-vocabulary) — `DC`,
`ISO15118-2`, `OCPP2.0.1`, `TLS` and so on. **Anything of your own must be prefixed
`x-`**, e.g. `x-derating-ramp`. Free text like `"ISO 15118-2 over TLS"` is rejected: it is
exactly what breaks the per-protocol view.

### Truncate `trace`

A few hundred characters is plenty. Measured on a real EVerest run, a **failed** test is
~4,000 bytes of CTRF against ~123 for a passed one, so failures dominate the file, and this
repository keeps submissions indefinitely. Full stack traces belong behind
`environment.buildUrl`, which is why that field is strongly encouraged.

---

## 5. What gets rejected

Start from a minimal example and edit rather than writing one from scratch:
[`examples/minimal-ctrf.json`](examples/minimal-ctrf.json) for CTRF and
[`examples/minimal-openhtf.json`](examples/minimal-openhtf.json) for OpenHTF. Both are the
*smallest legal submission* — a passing build, one module, an integrator id, and zero tests.

| Code | Meaning |
|---|---|
| `bad-path` | Not `results/<id>/<pointer>/YYYYMMDD.json` |
| `unknown-format` | Neither CTRF nor OpenHTF |
| `commit-not-pointed` | That commit never appears in `pointers.json` for that pointer, so it cannot be shown to have been an agreed base |
| `pointer-mismatch` | The pointer in the path and the pointer in the record disagree |
| `too-big` | The submission, or its inline attachments, exceed the caps |
| `not-json` | Not valid JSON |
| `htf-profile` / `htf-metadata` | The normalised record breaks the structural profile or the EVerest metadata contract. Unknown keys are rejected, not ignored |
| `ctrf-schema` | A CTRF submission is not valid CTRF |
| `ctrf-profile` | A CTRF submission breaks the EVerest CTRF profile — including the rule that a HIL run must name its board |
| `extra-contract` | `results.extra` breaks the EVerest extra contract |
| `build-missing` / `build-failed` | No build reported, or its status is not `passed` |
| `id-mismatch` | The integrator id does not match the `results/<id>/` directory |
| `not-allowlisted` | That id is not in `integrators/allowlist.yaml` |
| `commit-malformed` / `commit-unknown` | The EVerest commit is not a full 40-character SHA, or not in the repository |

**Warnings never block**: a missing `buildUrl`, an unknown tag or module name, inline
attachments approaching the cap, or `codeinfo.sourcecode` being present.

## 6. Check before you push

```bash
python3 -m pip install -r scripts/requirements.txt
python3 scripts/validate_submission.py results/your-id/testing/main/20260909.json
```

Identical to what CI runs, so a local pass is a CI pass. Add `--skip-commit-check` to work
without network.

The gate has a suite of its own, and it is almost entirely negative cases, each asserting
the specific rejection code rather than merely that something failed:

```bash
python3 scripts/selftest.py        # 135 checks, all of which must pass
```
