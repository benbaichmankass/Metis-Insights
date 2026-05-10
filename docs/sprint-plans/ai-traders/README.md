# AI Traders — Sprint Plans

Sprint plans that implement the master plan in
[`../../AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md).

Each workstream gets one sprint plan file. Workstreams may decompose into
multiple `S-NNN` sprints when scheduled — record the mapping inside the
workstream file.

## Index

| WS | Title | M | Plan |
|---|---|---|---|
| WS1 | Architecture baseline | M9 | [ws1-architecture-baseline.md](ws1-architecture-baseline.md) |
| WS2 | Canonical trade pipeline | M9 | [ws2-canonical-pipeline.md](ws2-canonical-pipeline.md) |
| WS3 | Data foundation | M10 | [ws3-data-foundation.md](ws3-data-foundation.md) |
| WS4 | Training center | M9 | [ws4-training-center.md](ws4-training-center.md) |
| WS5 | Baseline models | M9 | [ws5-baseline-models.md](ws5-baseline-models.md) |
| WS6 | Open-source model layer | M9 | [ws6-open-source-models.md](ws6-open-source-models.md) |
| WS7 | Deployment tiers | M9 | [ws7-deployment-tiers.md](ws7-deployment-tiers.md) |
| WS8 | Monitoring and feedback loops | M9 | [ws8-monitoring-feedback.md](ws8-monitoring-feedback.md) |
| WS9 | Oracle / Hugging Face runtime split | M10 | [ws9-runtime-split.md](ws9-runtime-split.md) |
| WS10 | Architecture-doc enforcement | M9 | [ws10-arch-doc-enforcement.md](ws10-arch-doc-enforcement.md) |

## Implementation order

WS1 → WS2 → WS3 → WS4 → first baseline (subset of WS5) → model registry +
promotion (WS4 / WS7) → shadow mode (WS7) → rest of WS5 → WS6 → WS8 + WS10.

WS9 (runtime split) is a continuous discipline rather than a single-shot
sprint and is enforced from WS3 onwards.

## Non-negotiable rules (apply to every WS sprint)

- Do not weaken the live trading safety posture.
- Do not run heavy training jobs on the Oracle live VM.
- Do not introduce a model into live strategy logic without staged
  promotion and explicit operator approval.
- Do not let AI output bypass risk caps, broker validation, or
  mission-aware account restrictions.
- Architecture-changing code must update the architecture docs in the same
  PR.
