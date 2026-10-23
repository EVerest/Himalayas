# Status page redesign

Design record for the next version of the status page and the reporting conventions it
depends on. A decision marked **Implemented** is in `scripts/build_status.py`. One marked
**Decided, not implemented** needs a change to `scripts/validate_submission.py`, the
schemas or `integrators/allowlist.yaml` first; the page renders what records carry today.
The contributor-facing summary is
[CONTRIBUTING.md, section 10](../../CONTRIBUTING.md#10-reporting-conventions).

## Architecture

| Decision | Rationale | Status |
|---|---|---|
| Python renders a no-JS HTML baseline plus versioned JSON indexes (`summary.json`, and `regressions.json` per pointer). A small client layer adds filters, URL state, charts and diffs. | The page stays readable without JavaScript, and the JSON is a stable interface for other consumers. | Implemented |
| No feed and no hosted service. | Pages hosting and a scheduled build are enough; nothing new to operate. | Implemented |

## Views

| Decision | Rationale | Status |
|---|---|---|
| One site with a default view and a detailed view, not split by audience. | Integrators and EVerest maintainers ask the same questions at different depths. | Implemented |
| The default view opens on `testing/main` and has a pointer selector. | `testing/main` is where most results land. | Implemented |
| Add a coverage view and a per-setup detail page. | Coverage gaps and the history of one setup do not fit in a row. | Implemented |
| A Contribute page, generated at build time from the README's five-step quick start and the whole of CONTRIBUTING.md, linked from the header, the overview and the footer. | A reader who wants to report results should not have to find the repository first, and a generated page cannot drift from the guide. | Implemented |
| Every commit sha shown as text links to that commit in EVerest/EVerest, including the chart's axis labels. Tooltips stay plain text. | A reader chasing a regression range wants the diff one click away. | Implemented |
| EVerest's logo in the header beside the site name, linking to the EVerest repository. The build turns its navy fills white. | The site belongs to the project, and the navy wordmark would vanish on the navy header. A white plate around it crowded the header onto two rows. | Implemented |
| An "All integrators" card at the top of each pointer's overview: one sentence counting setups by verdict and saying how close they are to the tip, then one chip per standard saying on how many of the setups that judged it it fails. | Answers "how good is this pointer right now" before the per-company detail. Setups are the unit, never tests, and there are no percentages: a test count is dominated by the biggest suite and moves when tests are added or dropped. | Implemented |
| Stale setups are left out of the summary and the verdict buttons, and offered under their own "stale, left out" button. | A setup that stopped reporting should not make today look better or worse. | Implemented |
| A daily chart under the summary: setups by verdict per day over the last 90 days, each day replaying the summary as it would have read then, with a table view. | The last day always equals the summary, so the chart and the sentence cannot disagree. A day can mix commits; per-commit history stays on the setup page. | Implemented |
| No overview that mixes pointers, and no flat table mixing every company's setups. | Mixing pointers mixes results from different EVerest commits. Readers look for a company first, and the verdict buttons already slice across companies. | Rejected |

### Default view

| Decision | Rationale | Status |
|---|---|---|
| Setups are grouped by integrator, and groups where everything passes are collapsed. | Attention goes to what is failing. | Implemented |
| A summary bar whose segments filter the list; filter state lives in the URL. | A filtered view can be linked. | Implemented |
| Each row shows the verdict, then one Tested column (the commit with moves behind the tip, and the test date under it), the environment, and three result columns: Vehicle communication (IEC 61851, ISO 15118, DIN 70121), Backend (OCPP) and Other tests. A result column holds one chip per standard the setup reported, in a fixed order. | One column per standard left most cells empty, and three OCPP versions would have added three more. Grouping keeps OCPP in a predictable place. Conformance results and an integrator's own tests are different claims. | Implemented |
| Within a group, passing setups are listed before failing ones; the summary bar follows the same order. | The page reads as what works first, then what does not. | Implemented |
| A standard's chip names its counts: "✕ 1 failed, 1 passed", with accepted, inconclusive and skipped tests on a second line, never inside the count. | "1 of 2" was read as including the accepted test; one long line widened every column. | Implemented |
| A standard without a test-spec mapping (DIN 70121, IEC 61851 today) is shown like any other: no marker on its chip. The missing mapping is noted on the coverage and detail pages only. | A marker on every cell was noise without telling the reader what to do. | Implemented, except the note on the coverage and detail pages |
| A ▲ badge marks a confirmed regression, on its own line under the verdict. | Regressions are the main signal the pointers exist to produce. | Implemented |
| A build-only verdict takes the build's color: green when it passed, red when it failed. | A dashed pill read as "nothing happened", when the build is the one thing that was reported. | Implemented |

### Detailed view

| Decision | Rationale | Status |
|---|---|---|
| Integrators appear under their display name, as in the default view. | The id is an internal key. | Implemented |
| The build column is a ✓ or ✕, with the status in its tooltip. | It is nearly always "passed"; a mark saves the width. | Implemented |
| Sections: regressions panel, setups table, comparison. | The history matrix was dropped (see History). | Implemented |
| The regressions panel is collapsed by default. Within each state, every integrator is its own collapsed section with a test count, holding that integrator's table. | An integrator heading row read as part of the column titles, and the panel has to scale to many integrators. | Implemented |
| The setups table has one results column: passed and failed on the first line, total tests and any accepted, inconclusive or skipped on the second. | Six count columns took most of the width. | Implemented |
| Each row shows the last 14 runs as bricks, oldest first: green for no failed tests, red for one or more, with a legend under the table. | A line chart and a third color for partial verdicts were harder to read than two colors. | Implemented |

### Detail page

Sections, in order:

1. Header: verdict, test date, commit and moves behind the pointer tip, environment,
   provenance, evidence, build, and the full module list (always shown, not folded).
2. Capability map for this setup (see Coverage), collapsed by default. Selecting a
   tile filters the test results to that tag.
3. History chart: failed tests per pointer move, with test-count changes annotated.
4. Test results: one collapsible group per status (failed, inconclusive, accepted and
   waived, skipped, passed), all collapsed by default. Each test is itself collapsible
   and shows everything its record says: message, raw status, tags, file, and its
   regression, flaky or long-standing marker with "failing since". The reader can
   regroup by status, standard or failure message, and order by newest failure or name.
5. Runs table, with report and record links.

| Decision | Rationale | Status |
|---|---|---|
| The section order above. | The chart answers "is it getting better" before the reader opens individual tests. | Implemented |
| The detail page shows the test date, not a freshness label. | The date is unambiguous; freshness belongs in the overview. | Implemented |
| The integrator field shows the name only, without "self-reported". | The site header already says every result is self-reported. | Implemented |

## Verdicts and freshness

| Decision | Rationale | Status |
|---|---|---|
| Row verdicts, in precedence order: `build-failed` > `failing` > `passing-partial` > `passing` > `not-exercised` > `build-only`, plus `not-reported`. | One ordering resolves every mix of outcomes the same way. | Implemented, except `not-reported`, which waits on the setups list (see Not reported) |
| A run in which every test was skipped is `not-exercised`. | Skips are not passes. | Implemented |
| The row verdict counts all tests, not only conformance tests. | An integrator's own failing test is still a failure. | Implemented |
| Freshness is `current`, `stale` or `unknown`, with a grace of 14 days. The page also shows how many moves the result is behind the pointer tip. | "Stale" alone hides whether the base is one move or forty behind. | Implemented |

## Test outcomes

| Decision | Rationale | Status |
|---|---|---|
| Outcomes are `passed`, `failed`, `skipped`, `accepted` (deviation), `inconclusive`, and `waived` (by EVerest). | Each answers a different question and is shown differently. | Implemented, except `waived`, which waits on `waivers.yaml` |

### Accepted deviations

| Decision | Rationale | Status |
|---|---|---|
| `tests[].extra.acceptedDeviation {reason, clause, since}` is the source of truth. The test keeps `status: failed`. | The clause is structured data, and a deviation is not a pass. | Rendered by the builder; not yet in the schema or the gate |
| An accepted deviation is never shown as a pass. | The test did not meet the clause. | Implemented |
| The bare tag `x-accepted-deviation` draws a gate warning and is shown as "accepted (clause not declared)" for a transition period. This is today's Pionix encoding: `status: skipped`, `rawStatus: "FAIL EXPECTED"`, clause only in the message text. | Existing records keep rendering while producers move to the structured form. | Rendered as "clause not declared"; the gate warning is not implemented |

### Inconclusive

| Decision | Rationale | Status |
|---|---|---|
| Inconclusive is its own outcome: CTRF `status: "other"` plus `tests[].extra.inconclusive {reason}`. | Today the same harness verdict is emitted as `failed` (tag `x-inconclusive`) or as `skipped` (tag `x-rig-limitation`), inconsistently. | Rendered by the builder; not yet in the schema or the gate |
| Never a pass or a failure. Shown amber; makes the row `passing-partial` at most. | No verdict was reached. | Implemented |
| `passed` to `inconclusive` is not a regression. It is listed as "inconclusive since" on the detail page. | Nothing is known to have broken. | Implemented |

### Rig limitations

| Decision | Rationale | Status |
|---|---|---|
| `x-rig-limitation` is a skip reason: not exercised on this rig. | It describes the rig, not the software under test. | Implemented |
| A passed test carrying the tag stays a pass, with a footnote. | Its outcome was reported as a pass. | Implemented |

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
| OCPP tags `OCPP1.6-TestCases`, `OCPP2.0.1-Part6`, `OCPP2.1-Part6`, one chip per version. | OCPP versions are separate standards. | Decided, not implemented |
| The `compliance` lifecycle tag is deprecated, with a warning. | It says nothing about which specification. | Decided, not implemented |
| Wording for every standard: "Conformance test cases, self-run". Never "certified" or "compliant", and no logos. | Results are self-reported; certification is a separate process. | Implemented |

### Requirement IDs

| Decision | Rationale | Status |
|---|---|---|
| Requirement-derived conformance tests are allowed for any standard. | Not every standard has a published test specification. | Decided, not implemented |
| Requirement IDs become tags (`V2G2-`, `V2G3-`, `V2G20-`, `V2G-DC-`, `IEC61851-1-`, `OCPP2.0.1-`...), mapped to a standard by prefix in a `requirementIds` vocabulary group. | Prefix mapping needs no list of every requirement. | Decided, not implemented |
| If a test has both, the spec tag wins and the IDs are kept. Chips split into spec cases and requirement cases. | Spec cases and requirement-derived cases are different claims. | Decided, not implemented |

### Tag aliases

| Decision | Rationale | Status |
|---|---|---|
| Per-integrator `tag_aliases` in `integrators/allowlist.yaml` (CODEOWNERS-protected), applied at build time. Stored records are never rewritten. | Existing private tags map to standard ones without touching the append-only record. | Decided, not implemented |

## Provenance and evidence

| Decision | Rationale | Status |
|---|---|---|
| Provenance is self-declared at three levels: own harness (`results.tool.name`); reference test system (`testSystem {name, version}` from a vocabulary, plus an attached report); certified (only a link to OCA's Certified Products List). | The reader can tell what kind of claim a result is. | Implemented for own harness and reference test system; the certified level is not |
| Optional `extra.reportSha256` next to `reportUrl`. | A hash pins the document a link pointed at. | Decided, not implemented |
| A VDoC (the Vendor Declaration of Conformance PDF that OCTT generates) is submitted as the report. There is no separate VDoC field. | It is evidence like any other report; a second field adds nothing. | Decided, not implemented |
| Evidence is labeled "linked by the integrator"; a link without a hash shows "unverified link", muted. | Linking is not verification. | Implemented; every link shows as unverified until `reportSha256` exists |
| The builder never fetches evidence, and reports are not mirrored into the repository. | No third-party content is hosted or vouched for. | Implemented |

## Attribution and comparison

| Decision | Rationale | Status |
|---|---|---|
| A result counts for every pointer whose log in `pointers.json` contains its exact commit SHA, labeled "reported under ...". Freshness is computed against each pointer's own tip. | The same commit is often the tip of more than one pointer. | Implemented, except the "reported under ..." label |
| Comparison is a toggle in the detailed view: both verdicts plus a test diff (fails only on A, only on B, on both). | A diff answers "what differs" directly. | Implemented |
| A test that ran on one side only is "not run", never "fixed". | Absence is not a pass. | Implemented |
| Test identity is the test name, with a warning on duplicate names. | Names are the only identifier every record has. | Implemented |

## History, regressions and flakiness

### History

| Decision | Rationale | Status |
|---|---|---|
| The detailed view has no history matrix. Each setup row carries its last 14 runs as bricks instead (see Detailed view). | The per-row strip shows the trend without a second table. | Implemented |
| The detail page has a chart of failed tests per move, with test-count changes annotated. | A drop in failures from fewer tests is not an improvement. | Implemented |

### Regressions

| Decision | Rationale | Status |
|---|---|---|
| The detailed view's regressions panel is grouped by state (confirmed, suspected, flaky and failing now), then by integrator under its display name. | The reader triages by certainty first, then by who has to act. | Implemented |
| A regression is a test that passed at the previous tested commit and fails at the next, reported as a range across any untested moves, and listed until the test passes again. | The range is all the data supports. | Implemented |
| Two states. **Suspected**: one failing run; shown on the detail page, in the detailed panel, and in `regressions.json` with `confirmed: false`; no badge. **Confirmed**: a repeat failure, at the same commit or the next tested commit; gets the ▲ badge; the range is kept from the first failure. | A single failure is often noise. | Implemented |
| The rule is uniform for all tests. | No per-test exceptions to maintain. | Implemented |

### Flaky tests

| Decision | Rationale | Status |
|---|---|---|
| Window: the last 10 runs, every run counted including repeats. Flaky means two or more flips, or disagreement at one commit, within the window. | Simple and computable from the records alone. | Implemented |
| The current streak decides first: if every run in the current failing streak failed and it spans two or more tested commits, it is a steady failure (regression or long-standing), tagged "previously flaky". | A test that is now failing consistently needs attention whatever its past. | Implemented |
| Flaky tests that pass now go in a collapsed "Flaky (passing now)" list with no verdict effect, and leave it after 10 consistent runs. | Visible without moving the verdict. | Implemented |

### Failure clustering

| Decision | Rationale | Status |
|---|---|---|
| The cluster key is the first line of the message with only volatile tokens blanked (hex addresses, decimals, ISO timestamps, UUIDs); integers are kept. | Integers often carry meaning, such as a response code or a step number. | Implemented |
| An optional integrator-supplied `tests[].extra.failureKey` wins when present. | The producer knows its failure modes best. | Implemented |
| The cluster title is the key; the raw message is behind a disclosure. | Titles stay short and comparable. | Implemented |

## Coverage

The coverage view answers which kinds of charging, which protocols and which features
reported tests show working. It is a capability map: tiles in four groups, each tile
one vocabulary tag.

- Charging: AC, DC, bidirectional (V2X).
- Vehicle communication: IEC 61851, ISO 15118-3, ISO 15118-2, ISO 15118-20, DIN 70121.
- Backend and energy management: OCPP 1.6, OCPP 2.0.1, OCPP 2.1, EEBUS.
- Features: Plug & Charge, TLS, smart charging, reservation, Eichrecht.

| Decision | Rationale | Status |
|---|---|---|
| "Supported" means evidenced by reported tests. EVerest's own feature claims and the modules a setup loads are not counted. | Every setup loads the OCPP and ISO 15118 modules, even when it does not test them, and a claims list would drift from the README. | Implemented |
| A capability map of tiles replaces the table with one row per vocabulary entry. Tiles with no reports stay on the map, dashed. | The question is high level; gaps are as useful as coverage. | Implemented |
| A feature tile lists its carriers as chips. A chip fills once a test carries both the feature tag and the carrier tag. Carriers: PnC, TLS and smart charging over ISO 15118-2, ISO 15118-20 and OCPP; reservation and Eichrecht over OCPP; V2X over AC and DC. | A feature only means something through a protocol: PnC over ISO 15118-2 is a different claim from PnC over OCPP. | Implemented |
| The carrier list is fixed in `vocabulary.json`, next to the tags. | Tags are flat; only a fixed list can show a carrier as "possible, not reported". | Implemented |
| Across setups, the best setup wins: a tile is green when any setup passes its tests. The caption keeps the failing count ("passing on 1 · 2 failing"). | The page answers whether EVerest supports a capability somewhere; the caption and the setup list keep failures visible. | Implemented |
| Per setup, a tile passes when no test with its tag failed and at least one passed. Passing with no `compliance` test shows as "own tests only". | Accepted, inconclusive and skipped tests do not decide a tile, as in the conformance chips. | Implemented |
| An All / SIL / HIL filter above the map. Build target is not on the map. | A HIL pass is a stronger claim; with best-setup-wins a SIL pass would otherwise speak for a HIL failure. A capability rarely passes on one target and fails on another. | Implemented |
| Selecting a tile opens a list of the setups behind it, with each setup's counts for that tag only and its environment. Each setup links to its detail page, filtered to the tag. | A best-setup tile hides disagreement by design, so the selection has to show it. | Implemented |
| The detail page carries the same map for its one setup, colored by that setup's result. | One setup needs no aggregation rule. | Implemented |

## Test identity hygiene

| Decision | Rationale | Status |
|---|---|---|
| The gate warns on a test with no tag from the vocabulary groups. | An untagged test cannot appear in any per-standard view. | Decided, not implemented |
| The gate warns on a record mixing naming schemes within one suite, for example a pytest node id `path.py::test_x[dut]` among `TC_` names. | Mixed schemes break identity across runs. Real case: Pionix's skipped DIN `ChargeParameterDiscovery_008` is emitted as a pytest node id without spec tags. | Decided, not implemented |
| The builder puts untagged tests under "Other tests", marked untagged, and never infers a standard from the suite or name. | Inference would make claims the integrator did not make. | Implemented |

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
| Do Pionix's `TC_SECC_DC_VTB_Din*` cases come from DIN 70122, or are they Pionix's own? | Until answered, DIN results show in their own DIN 70121 column without a test-spec mapping. |
| Does IEC 61851-1 or IEC 61851-23 have a separate test specification? | Not decided. |
| What does a passed test tagged `x-rig-limitation` mean in Pionix's harness? | It stays a pass with a footnote. |

Also unresolved, found while documenting the conventions:

| Question | Note |
|---|---|
| Where does `testSystem` live in a record? | `results.extra` is closed, so the extra contract must add it. |
| What format does `acceptedDeviation.since` take: a date, a commit, or either? | Not fixed by the decision. |
| How do accepted deviations, inconclusive results and failure keys map onto the OpenHTF path? | The decisions name CTRF fields only. OpenHTF phases accept extra keys today. |
| What does the gate warning for the bare `x-accepted-deviation` tag become after the transition period? | The length of the period is not set. |
