## ▶️ START — bybit graded-book coverage, rescoped to a STAGED allowlist (PR #10746, DRAFT)

**Session:** `session_01Wu7y3KL6MMgAV1ghetQWFx` (sub-session; the manager relays to the operator — do not ping the operator on this thread)
**Branch:** `claude/bybit-coverage-graded-book` → PR **#10746** (stays DRAFT; the manager merges)

### Why this START supersedes the earlier one on the same branch
An earlier START (`24e9f397`) claimed this branch for the coverage fix as a **fleet-wide** change. The operator has since ruled Tier-2: **stage it on `bybit_1` (demo) first**, explicitly accepting that `bybit_2` (real money) stays exposed to the masking bug during the soak and that demo may never produce the triggering collision. So the scope has changed and this comment re-claims it under the new shape. Same branch, same PR, different blast radius.

### What I am touching
- `src/runtime/bybit_coverage_basis.py` — **NEW.** Mode + allowlist resolution and the basis decision (`graded` vs `side_blind`), pure functions.
- `src/runtime/bybit_coverage_soak.py` — **NEW.** Soak writer, `runtime_logs/bybit_coverage_soak.jsonl`.
- `src/runtime/order_monitor.py` — `_check_broker_naked_bybit_positions` re-arm/top-up coverage decision; summary counters.
- `src/runtime/bybit_leg_sides.py` — docstring only (the "this is now an order-path input" paragraph needs a scope).
- `src/web/api/routers/diag.py` — register `bybit_coverage_soak` in the `log_file` allowlist, **in the same commit as the writer**.
- `scripts/ops/get_env.py` — `ALLOWED_KEYS` for the two new env keys (an env var without a read surface is `BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE`).
- `CLAUDE.md` — the env-var row.
- `tests/test_bybit_coverage_basis.py` (new), `tests/test_bybit_naked_rearm.py`, `tests/test_bybit_leg_sides.py`.

### The contract I am implementing
- `BYBIT_GRADED_COVERAGE_MODE` ∈ `off` / **`annotate` (shipped default)** / `apply`; `BYBIT_GRADED_COVERAGE_ACCOUNTS` CSV.
- **An empty allowlist means NONE**, the `PROTECTION_REASSERT_ACCOUNTS` / `PROTECTION_STRAY_GROUP_ACCOUNTS` polarity — deliberately NOT `CONVICTION_SIZING_ACCOUNTS`' empty-means-ALL. This constrains a live order path.
- **The allowlist scopes the BINDING, never the MEASUREMENT.** Every Bybit account is still graded and annotated to the soak, so the rows a reviewer needs before widening actually exist. That is the correction `NETTING_ATTRIBUTION_ACCOUNTS` needed on 2026-08-09.
- **At the shipped default the re-arm decision is byte-identical to `main`.** Arming is a separate Tier-2 `set-env` of BOTH keys.

### Overlap
Nobody else should be in `order_monitor.py`'s Bybit naked sweep or `bybit_leg_sides.py`. If you are, say so here and I will hold.
