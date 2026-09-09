<!-- Submitting test results? Keep this PR to results/ only. -->

## Submission

- Integrator id:
- Pointer tested: `testing/main` or `testing/stable-2026.02`
- EVerest commit (full 40-char SHA):
- Environment: SIL / HIL

## Checklist

- [ ] This PR changes files under `results/` only
- [ ] My integrator id is in `integrators/allowlist.yaml` (add it in a separate PR first)
- [ ] The file is named `results/<my-id>/<pointer>/YYYYMMDD.json`, e.g.
      `results/deadbeef-charge/testing/main/20260909.json` (add `-2`, `-3`... for a
      second run the same day; `-1` is not a legal name)
- [ ] `results.extra.build.status` is `passed`
- [ ] `summary.start` / `summary.stop` are real timestamps, not `0`
- [ ] Traces are truncated; full detail is reachable via `environment.buildUrl`

The gate runs automatically and comments the result. If it rejects the submission it says
why; push a fix to this branch and it re-runs.

**These results are published as self-reported.** EVerest does not reproduce or verify them.
