### ▶️ START — exit-head / M20-evidence backlog drain

- **Session:** `session_012Nk5tVpHfSHvYfRyN7BC5S` (sub-session, dispatched by the manager)
- **Branch:** `claude/exit-head-evidence-drain-9k2p1x`
- **Scope:** the Tier-1 `high` rows of the exit-head / M20-evidence class in `docs/claude/health-review-backlog.json` — `BL-20260813-EXIT-HEAD-LIVE-ARM-DROPPED-ON-NO-CANDLES`, `-HARNESS-PASS-DOES-NOT-SURVIVE-THE-LIVE-BOOK`, `-EDGE-SMALL-AND-INCONSISTENT`, `BL-20260813-SHIPPED-DONCHIAN-1H-HEAD-RESTS-ON-BESTARM`, `BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER`, `BL-20260815-SCALP-EXIT-HEAD-MATRIX-DISAGREES-WITH-THE-4-ARM-SCREEN`, `BL-20260816-EXIT-HEAD-LEVER-HAS-NO-CONSUMER-IN-ICT-SCALP`, `BL-20260810-EXIT-LEVER-SPACE-UNDER-ENUMERATED`.

**Files/subsystems I am about to touch** (nothing on the order path, nothing on either VM):

- `scripts/ops/exit_mechanism_coverage.py` + `tests/` — the orphaned-exit-lever-declare detector.
- `docs/claude/health-review-backlog.json` — surgical per-row edits only, via `scripts/ops/backlog_append.py`. **This file is contended**; I will resolve any conflict row-by-row, never by taking one side wholesale.
- possibly `docs/research/exit-refinement-coverage.json` (matrix cell notes) and `docs/research/m20-exit-head-rounds.jsonl`.

**Heads-up for anyone else in M20 tooling:** I am NOT touching `scripts/ml/train_exit_head.py`'s `--total-sort` default, `scripts/research/m20_fleet_exit_sweep.py`, or any `config/strategies.yaml` value. PR will be opened as a DRAFT; the manager merges.
