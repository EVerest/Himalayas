# The Himalayas submission contract

**Two formats are accepted and both are normalised to OpenHTF internally.**

| Submitting | Validated against | Where the EVerest fields go |
|---|---|---|
| **OpenHTF** `TestRecord` | [`everest-htf-profile`](schema/everest-htf-profile.schema.json) + [`everest-htf-metadata`](schema/everest-htf-metadata.schema.json) | `metadata.everest`, `metadata.build`, `metadata.integrator` |
| **CTRF** report | [`ctrf.schema.json`](schema/ctrf.schema.json) + [`everest-profile`](schema/everest-profile.schema.json), then converted | `results.environment`, `results.extra` |

## Why OpenHTF is the internal format

It is the richer of the two. `measurements` with limits and per-device identity
(`dut_id`, `station_id`) are **native**, and its outcome enum is a superset of CTRF's — it has
`ERROR`, `TIMEOUT` and `ABORTED`, which CTRF cannot express.

**What that direction costs:** OpenHTF models a device under test. It has no concept of a
source commit, a build, or a test suite. So everything identifying *what was tested* lives in
`TestRecord.metadata`, an untyped object — which is the same closed-schema problem CTRF had
with `results.extra`, relocated rather than removed.
[`everest-htf-metadata.schema.json`](schema/everest-htf-metadata.schema.json) is what pins it
down, and it is the real specification work in this repository.

**A converted CTRF submission has no measurements.** CTRF has no concept of one, so that
asymmetry is expected and is not an error — it just means the status page shows a measurements
table for OpenHTF submissions and not for CTRF ones.

## What is mandatory — both paths side by side

Reviewed and settled 2026-09-09. Everything here is **rejected** if absent.

| What EVerest needs | CTRF path | OpenHTF path |
|---|---|---|
| EVerest commit, **full 40 chars** | `environment.commit` | `metadata.everest.commit` |
| Which pointer was tracked | `environment.appVersion` | `metadata.everest.pointer` |
| SIL vs HIL | `environment.testEnvironment` | `metadata.everest.testEnvironment` |
| Build status | `extra.build.status` | `metadata.build.status` |
| **Modules built**, non-empty | `extra.build.modules` | `metadata.build.modules` |
| Integrator id | `extra.integrator.id` | `metadata.integrator.id` |
| Run start **and** end, both ≥ 1 | `summary.start` / `stop` | `start_time_millis` / `end_time_millis` |
| Per-test name and outcome | `tests[].name` / `status` | `phases[].name` / `outcome` |
| Device and station | *(does not exist)* | `dut_id`, `station_id` |
| **Board, when `testEnvironment` is HIL** | `extra.hardware.board` | *(covered by `dut_id`)* |

**HIL is supported on both paths.** A hardware run can be submitted as CTRF or as OpenHTF.
The difference is only what identifies the device: OpenHTF has `dut_id` natively, so CTRF must
supply `extra.hardware.board` instead. Nothing about HIL is OpenHTF-only.

Two asymmetries are deliberate rather than oversights:

- **`dut_id` / `station_id`** are required for OpenHTF and do not exist in CTRF. So a **HIL**
  submission on the CTRF path must instead name `extra.hardware.board` — otherwise the
  normaliser would fall back to `unspecified-dut` and two chargers from one integrator would be
  indistinguishable. SIL is exempt: there is no device.
- **Per-test `duration`** is required by upstream CTRF and optional for an OpenHTF phase. That
  is inherited from CTRF, not chosen, and is accepted as-is.

**Why modules is mandatory:** without it a submission cannot say which parts of EVerest it
actually exercised, so two greens covering entirely different module sets look identical.

## The CTRF path

A submission arriving as CTRF is a **CTRF** document plus an `extra` block that EVerest
defines, converted on intake.

Validate before you push:

```bash
python3 scripts/validate_submission.py results/<id>/<pointer>/YYYYMMDD.json
```

---

## Why `extra` has to exist at all

CTRF models test results well and models nothing else. Two facts from the normative schema
shaped this whole repository:

- **There is no build-status object**, and the schema is *closed*: `results.environment` and
  `results.tool` both declare `"additionalProperties": false`. Build status, hardware
  identity and integrator identity **cannot** be added as native fields.
- CTRF types `extra` as nothing more than `{"type": "object"}`.

So **everything that gives a submission meaning beyond pass/fail counts is defined by
[`schema/everest-extra.schema.json`](schema/everest-extra.schema.json)**, not by CTRF. That
file is the actual specification here.

### Build status is not a test

It lives in `extra.build`, **not** as a synthetic test named `build`. A fake entry in
`tests[]` would inflate the test count and make "1 failed" ambiguous between a build failure
and a failed charging session. The cost of doing it properly: no off-the-shelf CTRF viewer
will show your build status, because they all read `tests[]`. The status page renders it
instead.

---

## Use a native field wherever one exists

Do not put something in `extra` that CTRF already models. The gate rejects unknown `extra`
keys, so this is enforced, not advisory.

| What | Native field | Notes |
|---|---|---|
| EVerest commit | `results.environment.commit` | **Required.** Checked against the real repository |
| pointer tracked | `results.environment.appVersion` | `testing/main` or `testing/stable-2026.02` |
| branch / release line | `results.environment.branchName` | usually `main` |
| SIL vs hardware | `results.environment.testEnvironment` | `SIL` or `HIL` |
| build health as a boolean | `results.environment.healthy` | mirror of `extra.build.status`, so generic CTRF readers see it |
| link back to your CI run | `results.environment.buildUrl` | optional, **strongly encouraged** — with traces truncated it is the only route to full detail |
| OS / arch | `results.environment.osPlatform`, `osVersion`, `osRelease` | |
| run start and end | `results.summary.start`, `stop` | **Required by CTRF, and must not be `0`.** Staleness is computed from these |
| protocol variants, per test | `test.tags` | e.g. `["DC","ISO15118-2"]`. This is what makes per-protocol views possible |
| hardware, per test | `test.device` | |
| suite or file a test came from | `test.suite` | recover this from JUnit `classname` |
| free-form label for the run | `results.environment.reportName` | |

---

## `results.extra`

| Key | Required | Contents |
|---|:---:|---|
| `build` | **yes** | `status`: `passed` \| `failed`. **Required `modules[]`**, unique non-empty strings. Optional `targets[]` of `{target, status, toolchain}` |
| `integrator` | **yes** | `id` (**required**, must equal your `results/<id>/` directory). Optional `displayName` — omit to stay anonymous |
| `hardware` | no | `bsp`, `board`, `chargeController`. Only meaningful when `testEnvironment` is `HIL` |

`additionalProperties: false` at **every** level. This is a contract: an unrecognised key is
a rejected submission, not silently ignored data. If you need a field that does not exist,
open an issue rather than inventing one — a field only half the integrators emit is worse
than no field.

### `build.targets` is plural for a reason

EVerest cross-builds four embedded targets. A single boolean cannot express "builds for
x86 and aarch64 but not armv7", which is exactly the interesting case. The status page shows
each target, and the overall `status` is your call — the gate only reads `status`.

---

## Two fields that deliberately do not exist

- **No `selfReported` flag.** Every submission here is self-reported, so a per-file boolean
  carries no information. The caveat lives on the status page and in the README, where a
  reader will actually see it.
- **No `publication` or visibility flag.** The repository is public; there is no private
  submission to opt out of. Anonymity is a pseudonymous `id` with `displayName` omitted.

---

## Standardised fields — the freeform-string problem

CTRF leaves the fields this project most depends on as **freeform strings**: `commit`,
`appVersion`, `testEnvironment`, `osPlatform`, and `test.tags`. Nothing in CTRF stops one
integrator writing `SIL` and another `sil`, or `ISO15118-2` and `ISO 15118-2 over TLS`. Once
that happens the per-protocol view is meaningless and the aggregate cannot be trusted.

CTRF's own schema is closed and cannot be tightened, so a **stricter overlay** is applied in
addition to it: [`schema/everest-profile.schema.json`](schema/everest-profile.schema.json).
It constrains only the fields it names; everything else stays governed by CTRF.

### Enforced — the gate rejects these

| Field | Rule | Why |
|---|---|---|
| `environment.appName` | must be `EVerest` | this repository tracks nothing else |
| `environment.appVersion` | one of `testing/main`, `testing/stable-2026.02` | it names the floating pointer that was tracked, and the normaliser derives `metadata.everest.pointer` from it. A tag or a CalVer value here is a different claim, and it fails four checks rather than one |
| `environment.commit` | **full 40-char** lowercase SHA | short SHAs are ambiguous; a result whose base cannot be pinned exactly is not reproducible even in principle |
| `environment.branchName` | `main` or `stable/yyyy.mm` | |
| `environment.testEnvironment` | **`SIL`** or **`HIL`** exactly | these are displayed separately, so case and spelling matter |
| `environment.osPlatform` | `linux` \| `darwin` \| `windows` | |
| `environment.buildUrl` | must start `https://` | |
| `summary.start`, `summary.stop` | integer **≥ 1** | `junit-to-ctrf` emits `0`. A result with no timestamp **can never be aged**, so the staleness rule could never govern it. This is why it is an error and not a warning |
| `test.tags[]` | `PascalCase`, `lowercase`, or `x-`-prefixed | free text here is exactly what breaks per-protocol rollups |
| `build.targets[].target` | a well-formed triple | |
| `build.targets[].toolchain` | `<compiler> <version>`, e.g. `gcc 13.2.0` | comparable across integrators without hand-parsing |
| `build.modules[]` | PascalCase identifier | |

### Warned — a likely typo, but not fatal

These come from [`schema/vocabulary.json`](schema/vocabulary.json) and
[`schema/everest-modules.txt`](schema/everest-modules.txt), which **drift with
EVerest**. Rejecting on them would break a submission made against a newer
EVerest than this repository was last updated against, so they only warn.

| Check | What it catches |
|---|---|
| `tag-vocabulary` | a tag that is neither standard nor `x-`-prefixed |
| `target-vocabulary` | a target EVerest does not itself build — fine, just not directly comparable with upstream |
| `module-names` | a module name not found in EVerest, i.e. almost always a typo |

### The tag vocabulary

| Group | Values |
|---|---|
| Power | `AC` `DC` |
| High-level comms | `ISO15118-2` `ISO15118-3` `ISO15118-20` `DIN70121` |
| Basic comms | `IEC61851` |
| OCPP | `OCPP1.6` `OCPP2.0.1` `OCPP2.1` |
| Features | `TLS` `PnC` `SmartCharging` `Eichrecht` `Reservation` `EEBUS` `V2X` |
| Lifecycle | `mandatory` `smoke` `compliance` `regression` |

**Anything else must be prefixed `x-`** — e.g. `x-derating-ramp`. That keeps private tags
from colliding with future standard ones, and keeps them out of the public per-protocol
rollups where they would be noise. Since integrator tests may be closed source, expect
`x-` tags to be common; that is the design, not a failure.

### Build targets

`x86_64-linux-gnu`, `aarch64-linux-gnu`, `aarch64-linux-musl`, `armv7-linux-gnueabihf`,
`armv7-linux-musleabihf`.

The four non-x86 entries are exactly what EVerest cross-builds in its own CI, so an
integrator reporting one of those is **directly comparable with upstream**. Other triples
are accepted with a warning.

---

## Worked examples

| File | Format | What it shows |
|---|---|---|
| [`examples/minimal-ctrf.json`](examples/minimal-ctrf.json) | CTRF | The floor: a passing build, one module, an id, `tests: []`. Legal, and renders as *"build only · no tests reported"* |
| [`examples/ctrf-hil-dc.json`](examples/ctrf-hil-dc.json) | CTRF | A three-target build matrix with one failing target, per-test `tags` and `device`, and a closed-source integrator test |
| [`examples/minimal-openhtf.json`](examples/minimal-openhtf.json) | OpenHTF | The same floor on the OpenHTF path: `phases: []`, and the EVerest fields in `metadata` |
| [`examples/openhtf-hil-dc.json`](examples/openhtf-hil-dc.json) | OpenHTF | A HIL run with **measurements and their limits**, including one outside its limits. This is the shape CTRF cannot express |

Every one of them is accepted by the gate, and the self-test asserts that on each run. Copy
the one matching your format and edit it, rather than starting from the
[ctrf.io](https://ctrf.io) docs — the example on that site omits the required top-level
`reportFormat` and `specVersion` keys and does not validate.
