# Status page redesign

Design record for the next version of the status page and the reporting conventions it
depends on. Every decision below has the status **Decided, not implemented**: none of it is
in `scripts/build_status.py`, `scripts/validate_submission.py` or the schemas yet. The
contributor-facing summary is
[CONTRIBUTING.md, section 10](../../CONTRIBUTING.md#10-reporting-conventions).

## Architecture

| Decision | Rationale | Status |
|---|---|---|
| Python renders a no-JS HTML baseline plus versioned JSON indexes (`summary.json`, and `regressions.json` per pointer). A small client layer adds filters, URL state, charts and diffs. | The page stays readable without JavaScript, and the JSON is a stable interface for other consumers. | Decided, not implemented |
| No feed and no hosted service. | Pages hosting and a scheduled build are enough; nothing new to operate. | Decided, not implemented |

## Views

| Decision | Rationale | Status |
|---|---|---|
| One site with a default view and a detailed view, not split by audience. | Integrators and EVerest maintainers ask the same questions at different depths. | Decided, not implemented |
| The default view opens on `testing/main` and has a pointer selector. | `testing/main` is where most results land. | Decided, not implemented |
| Add a coverage view and a per-setup detail page. | Coverage gaps and the history of one setup do not fit in a row. | Decided, not implemented |

### Default view

| Decision | Rationale | Status |
|---|---|---|
| Setups are grouped by integrator, and groups where everything passes are collapsed. | Attention goes to what is failing. | Decided, not implemented |
| A summary bar whose segments filter the list; filter state lives in the URL. | A filtered view can be linked. | Decided, not implemented |
| Each row shows the verdict, freshness, one conformance cell per standard (verdict plus counts), and other tests in their own column. | Conformance results and an integrator's own tests are different claims. | Decided, not implemented |
| A ▲ badge marks a confirmed regression. | Regressions are the main signal the pointers exist to produce. | Decided, not implemented |

### Detail page

Sections, in order:

1. Header: verdict, freshness, commit and moves behind the pointer tip, environment,
   provenance, evidence, build, modules.
2. Conformance cells.
3. Failure clusters, newest first, each with "failing since"; regression clusters first.
4. Accepted and waived.
5. Skipped.
6. Passing, collapsed by standard.
7. History chart: failed tests per pointer move, with test-count changes annotated.
8. Runs table, with report and record links.

| Decision | Rationale | Status |
|---|---|---|
| The section order above. | What needs action comes before what does not. | Decided, not implemented |

## Verdicts and freshness

| Decision | Rationale | Status |
|---|---|---|
| Row verdicts, in precedence order: `build-failed` > `failing` > `passing-partial` > `passing` > `not-exercised` > `build-only`, plus `not-reported`. | One ordering resolves every mix of outcomes the same way. | Decided, not implemented |
| A run in which every test was skipped is `not-exercised`. | Skips are not passes. | Decided, not implemented |
| The row verdict counts all tests, not only conformance tests. | An integrator's own failing test is still a failure. | Decided, not implemented |
| Freshness is `current`, `stale` or `unknown`, with a grace of 14 days. The page also shows how many moves the result is behind the pointer tip. | "Stale" alone hides whether the base is one move or forty behind. | Decided, not implemented |

## Test outcomes

| Decision | Rationale | Status |
|---|---|---|
| Outcomes are `passed`, `failed`, `skipped`, `accepted` (deviation), `inconclusive`, and `waived` (by EVerest). | Each answers a different question and is shown differently. | Decided, not implemented |

### Accepted deviations

| Decision | Rationale | Status |
|---|---|---|
| `tests[].extra.acceptedDeviation {reason, clause, since}` is the source of truth. The test keeps `status: failed`. | The clause is structured data, and a deviation is not a pass. | Decided, not implemented |
| An accepted deviation is never shown as a pass. | The test did not meet the clause. | Decided, not implemented |
| The bare tag `x-accepted-deviation` draws a gate warning and is shown as "accepted (clause not declared)" for a transition period. This is today's Pionix encoding: `status: skipped`, `rawStatus: "FAIL EXPECTED"`, clause only in the message text. | Existing records keep rendering while producers move to the structured form. | Decided, not implemented |

### Inconclusive

| Decision | Rationale | Status |
|---|---|---|
| Inconclusive is its own outcome: CTRF `status: "other"` plus `tests[].extra.inconclusive {reason}`. | Today the same harness verdict is emitted as `failed` (tag `x-inconclusive`) or as `skipped` (tag `x-rig-limitation`), inconsistently. | Decided, not implemented |
| Never a pass or a failure. Shown amber; makes the row `passing-partial` at most. | No verdict was reached. | Decided, not implemented |
| `passed` to `inconclusive` is not a regression. It is listed as "inconclusive since" on the detail page. | Nothing is known to have broken. | Decided, not implemented |

### Rig limitations

| Decision | Rationale | Status |
|---|---|---|
| `x-rig-limitation` is a skip reason: not exercised on this rig. | It describes the rig, not the software under test. | Decided, not implemented |
| A passed test carrying the tag stays a pass, with a footnote. | Its outcome was reported as a pass. | Decided, not implemented |

### Waivers

| Decision | Rationale | Status |
|---|---|---|
| A reviewed `waivers.yaml` with entries `W-nnn`, matched by requirement or test, each with `reason`, `decided_in`, `scope` and `review_by`. | A waiver is an EVerest decision and needs a reviewed record. | Decided, not implemented |
| Counted like `accepted`, labeled "waived by EVerest". | The reader must see who decided. | Decided, not implemented |
| A waiver stops applying after `review_by`, with a warning 30 days ahead. | Waivers expire rather than accumulate. | Decided, not implemented |

## Conformance

| Decision | Rationale | Status |
|---|---|---|
| A conformance test is a test carrying a conformance-spec tag. | Explicit tagging, never inference from names. | Decided, not implemented |
| A vocabulary group `conformanceSpecs` maps test spec to standard: `ISO15118-4` to ISO 15118-2, `ISO15118-5` to ISO 15118-3, `ISO15118-21` to ISO 15118-20, `DIN70122` to DIN 70121. | The cell belongs to the standard, the tag names the test specification. | Decided, not implemented |
| OCPP tags `OCPP1.6-TestCases`, `OCPP2.0.1-Part6`, `OCPP2.1-Part6`, one cell per version. | OCPP versions are separate standards. | Decided, not implemented |
| The `compliance` lifecycle tag is deprecated, with a warning. | It says nothing about which specification. | Decided, not implemented |
| Wording for every standard: "Conformance test cases, self-run". Never "certified" or "compliant", and no logos. | Results are self-reported; certification is a separate process. | Decided, not implemented |

### Requirement IDs

| Decision | Rationale | Status |
|---|---|---|
| Requirement-derived conformance tests are allowed for any standard. | Not every standard has a published test specification. | Decided, not implemented |
| Requirement IDs become tags (`V2G2-`, `V2G3-`, `V2G20-`, `V2G-DC-`, `IEC61851-1-`, `OCPP2.0.1-`...), mapped to a standard by prefix in a `requirementIds` vocabulary group. | Prefix mapping needs no list of every requirement. | Decided, not implemented |
| If a test has both, the spec tag wins and the IDs are kept. Cells split into spec cases and requirement cases. | Spec cases and requirement-derived cases are different claims. | Decided, not implemented |

### Tag aliases

| Decision | Rationale | Status |
|---|---|---|
| Per-integrator `tag_aliases` in `integrators/allowlist.yaml` (CODEOWNERS-protected), applied at build time. Stored records are never rewritten. | Existing private tags map to standard ones without touching the append-only record. | Decided, not implemented |

## Provenance and evidence

| Decision | Rationale | Status |
|---|---|---|
| Provenance is self-declared at three levels: own harness (`results.tool.name`); reference test system (`testSystem {name, version}` from a vocabulary, plus an attached report or VDoC, the Vendor Declaration of Conformance PDF that OCTT generates); certified (only a link to OCA's Certified Products List). | The reader can tell what kind of claim a result is. | Decided, not implemented |
| Optional `extra.reportSha256` next to `reportUrl`, and `extra.vdoc {url, sha256}`. | A hash pins the document a link pointed at. | Decided, not implemented |
| Evidence is labeled "linked by the integrator"; a link without a hash shows "unverified link", muted. | Linking is not verification. | Decided, not implemented |
| The builder never fetches evidence, and reports are not mirrored into the repository. | No third-party content is hosted or vouched for. | Decided, not implemented |

## Attribution and comparison

| Decision | Rationale | Status |
|---|---|---|
| A result counts for every pointer whose log in `pointers.json` contains its exact commit SHA, labeled "reported under ...". Freshness is computed against each pointer's own tip. | The same commit is often the tip of more than one pointer. | Decided, not implemented |
| Comparison is a toggle in the detailed view: both verdicts plus a test diff (fails only on A, only on B, on both). | A diff answers "what differs" directly. | Decided, not implemented |
| A test that ran on one side only is "not run", never "fixed". | Absence is not a pass. | Decided, not implemented |
| Test identity is the test name, with a warning on duplicate names. | Names are the only identifier every record has. | Decided, not implemented |

## History, regressions and flakiness

### History

| Decision | Rationale | Status |
|---|---|---|
| A commit x setup matrix in the detailed view, one column or row per pointer move; untested moves are dashed. | Gaps in testing stay visible. | Decided, not implemented |
| Repeated runs show the latest, with a ≠ marker when they disagree. | One cell per move, without hiding disagreement. | Decided, not implemented |
| The detail page has a chart of failed tests per move, with test-count changes annotated. | A drop in failures from fewer tests is not an improvement. | Decided, not implemented |

### Regressions

| Decision | Rationale | Status |
|---|---|---|
| A regression is a test that passed at the previous tested commit and fails at the next, reported as a range across any untested moves, and listed until the test passes again. | The range is all the data supports. | Decided, not implemented |
| Two states. **Suspected**: one failing run; shown on the detail page, in the detailed panel, and in `regressions.json` with `confirmed: false`; no badge. **Confirmed**: a repeat failure, at the same commit or the next tested commit; gets the ▲ badge; the range is kept from the first failure. | A single failure is often noise. | Decided, not implemented |
| The rule is uniform for all tests. | No per-test exceptions to maintain. | Decided, not implemented |

### Flaky tests

| Decision | Rationale | Status |
|---|---|---|
| Window: the last 10 runs, every run counted including repeats. Flaky means two or more flips, or disagreement at one commit, within the window. | Simple and computable from the records alone. | Decided, not implemented |
| The current streak decides first: if every run in the current failing streak failed and it spans two or more tested commits, it is a steady failure (regression or long-standing), tagged "previously flaky". | A test that is now failing consistently needs attention whatever its past. | Decided, not implemented |
| Flaky tests that pass now go in a collapsed "Flaky (passing now)" list with no verdict effect, and leave it after 10 consistent runs. | Visible without moving the verdict. | Decided, not implemented |

### Failure clustering

| Decision | Rationale | Status |
|---|---|---|
| The cluster key is the first line of the message with only volatile tokens blanked (hex addresses, decimals, ISO timestamps, UUIDs); integers are kept. | Integers often carry meaning, such as a response code or a step number. | Decided, not implemented |
| An optional integrator-supplied `tests[].extra.failureKey` wins when present. | The producer knows its failure modes best. | Decided, not implemented |
| The cluster title is the key; the raw message is behind a disclosure. | Titles stay short and comparable. | Decided, not implemented |

## Coverage

| Decision | Rationale | Status |
|---|---|---|
| One row per vocabulary entry: setups exercising it, setups passing it, integrators. Gaps are listed explicitly. | What nobody tests is the useful answer. | Decided, not implemented |
| Coverage counts test tags only, plus build target and test environment. Loaded modules are shown raw on the detail page and never counted. | Every setup loads the OCPP and ISO 15118 modules, even when it does not test them. | Decided, not implemented |

## Test identity hygiene

| Decision | Rationale | Status |
|---|---|---|
| The gate warns on a test with no tag from the vocabulary groups. | An untagged test cannot appear in any per-standard view. | Decided, not implemented |
| The gate warns on a record mixing naming schemes within one suite, for example a pytest node id `path.py::test_x[dut]` among `TC_` names. | Mixed schemes break identity across runs. Real case: Pionix's skipped DIN `ChargeParameterDiscovery_008` is emitted as a pytest node id without spec tags. | Decided, not implemented |
| The builder puts untagged tests under "Other tests", marked untagged, and never infers a standard from the suite or name. | Inference would make claims the integrator did not make. | Decided, not implemented |

## Not reported

| Decision | Rationale | Status |
|---|---|---|
| An optional `setups:` list per integrator in `integrators/allowlist.yaml`: `{config, environment, pointers: [...]}`. | A missing result is only meaningful against a declared intent. | Decided, not implemented |
| Only a declared setup without a result on a listed pointer gets a "not reported" row. An integrator with nothing declared does not appear until its first result. | No rows for setups nobody promised. | Decided, not implemented |

## Fixtures

| Decision | Rationale | Status |
|---|---|---|
| The fixture integrator ids `deadbeef-charge` and `anon-7f3a` move out of the production allowlist into `testdata/allowlist.yaml`, loaded only by the self-test. The gate tests and the references in `examples/` are updated with it. | Fixture ids should not be able to publish real results. | Decided, not implemented |

## Open questions

These need someone outside the design work to answer.

| Question | Until answered |
|---|---|
| Do Pionix's `TC_SECC_DC_VTB_Din*` cases come from DIN 70122, or are they Pionix's own? | DIN results show under "Other tests" with a dagger. |
| Does IEC 61851-1 or IEC 61851-23 have a separate test specification? | Not decided. |
| What does a passed test tagged `x-rig-limitation` mean in Pionix's harness? | It stays a pass with a footnote. |

Also unresolved, found while documenting the conventions:

| Question | Note |
|---|---|
| Where does `testSystem` live in a record? | `results.extra` is closed, so the extra contract must add it. |
| What format does `acceptedDeviation.since` take: a date, a commit, or either? | Not fixed by the decision. |
| How do accepted deviations, inconclusive results and failure keys map onto the OpenHTF path? | The decisions name CTRF fields only. OpenHTF phases accept extra keys today. |
| What does the gate warning for the bare `x-accepted-deviation` tag become after the transition period? | The length of the period is not set. |
