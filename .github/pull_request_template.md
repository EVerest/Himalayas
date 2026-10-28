<!-- Submitting test results? Keep this PR to results/ only. -->

## Submission

- Integrator id:
- Pointer tested: `testing/main`, `testing/stable-2026.02` or `testing/stable-2026.10`
- EVerest commit (full 40-char SHA):
- Environment: SIL / HIL

## Checklist

- [ ] This PR changes files under `results/` only
- [ ] My integrator id is in `integrators/allowlist.yaml` (add it in a separate PR first)
- [ ] The file is named `results/<my-id>/<pointer>/YYYYMMDD[-label][-N].json`,
      e.g. `results/your-id/testing/main/20260909.json` (add a label such as
      `-ocpp201-sil` for a different run the same day, `-2`, `-3`... for a
      re-run; `-1` is not a legal name)
- [ ] `results.extra.build.status` is the real build outcome; a failed build is
      published as one
- [ ] `summary.start` / `summary.stop` are real timestamps, not `0`
- [ ] Traces are truncated; full detail is reachable via `environment.buildUrl`
- [ ] I may publish everything in the file, permanently: no credentials, no tokens in
      URLs, no personal data

The gate runs automatically and comments the result. If it rejects the submission it says
why; push a fix to this branch and it re-runs.

**These results are published as self-reported.** EVerest does not reproduce or verify them.
