# Sprint S-043 Summary — M3: Risk controls foundation — order-layer refusal tests

**Sprint:** S-043 | **Milestone:** M3 — Risk controls foundation
**Type:** auto-claude (roadmap) | **Date:** 2026-05-06
**Status:** CLOSED ✅

---

## Outcome

M3 (Risk controls foundation) formally closed. The order-layer refusal-path
gap is closed: every refusal site on the order route is now pinned to a
stable status/reason token via 28 new gap-closer tests. The risk engine,
kill switch, and risk caps were already implemented under S-010 / S-012 /
S-021; S-043 is the validation layer that prevents downstream contracts
from drifting silently.

---

## What was done

### T0 — Session open
- `docs/claude/milestone-state.md` updated: M3 status → IN PROGRESS, S-043 active.
- Sprint-start ping appended to `docs/claude/pending-pings.jsonl`.

### T1 — Verify-before-trusting-done: refusal-path map + gap list

Mapped every refusal path in the order layer and identified test coverage:

**`safe_place_order` (`src/runtime/orders.py`):**

| # | Path | Reason | Status | Pre-S-043 coverage |
|---|---|---|---|---|
| 1 | non-dict order input | "order must be a dictionary" | failed_validation | ❌ GAP |
| 2 | empty/missing/whitespace-only symbol | "symbol is required" | failed_validation | ❌ GAP |
| 3 | invalid side | "side must be …" | failed_validation | ✅ test_runtime_orders |
| 4 | non-numeric qty | "invalid qty" | failed_validation | ✅ test_runtime_orders |
| 5 | qty ≤ 0 | "qty must be > 0" | failed_validation | ✅ test_runtime_orders |
| 6 | halt flag active | "halt_flag_active" | halted | ✅ test_order_refusal |
| 7 | MAX_POSITION_USD exceeded | (raises ValueError) | — | ✅ test_order_refusal |
| 8 | daily loss reached MAX_DAILY_LOSS_USD | (raises ValueError) | — | ✅ test_order_refusal |
| 9 | open positions reached MAX_OPEN_POSITIONS | (raises ValueError) | — | ✅ test_order_refusal |
| 10 | per-strategy open positions cap | "MAX_POS_PER_STRATEGY" | refused | ✅ test_per_strategy_risk |
| 11 | per-strategy daily loss cap | "MAX_DAILY_LOSS_PER_STRATEGY_USD" | refused | ✅ test_per_strategy_risk |
| 12 | qty > MAX_QTY | "qty exceeds MAX_QTY" | failed_validation | ✅ test_runtime_orders |
| 13 | exchange exception | (exception message) | failed_exchange | ✅ test_runtime_orders |

**`RiskManager.evaluate` (`src/units/accounts/risk.py`):**

| # | Path | Reason token | Pre-S-043 coverage |
|---|---|---|---|
| 1 | smoke-test order bypass | (allow=True, reason=None) | ⚠️ approve() bool only |
| 2 | dry_run=True | "account_mode_dry_run" | ⚠️ no direct unit test |
| 3 | daily_pnl < -max_daily_loss_usd | "DAILY_LOSS_CAP" | ⚠️ approve() bool only |
| 4 | estimated_value > max_pos_size_usd | "POSITION_SIZE_CAP" | ⚠️ approve() bool only |
| 5 | intraday drawdown ≥ max_dd_pct | "INTRADAY_DRAWDOWN" | ⚠️ approve() bool only |

The reason tokens are stable contract surface consumed by:
- `src/units/accounts/execute.py` — writes `reason` into `trade_journal.entry_reason`.
- `src/ui/processor.py` — renders "REJECTED: <reason>" rows in `/last5` etc.
- `tests/test_packages_command.py` — asserts the literal tokens.
- `tests/test_execute_journal_rejections.py` — round-trips them through the journal.

If any token drifts (e.g. `DAILY_LOSS_CAP` → `daily_loss_cap`) the rejection
renderer breaks silently and the journal becomes unreadable. Pre-S-043 the
existing tests exercised `approve()` (bool only), leaving the tokens unpinned.

### T2 — Wrote `tests/test_s043_order_refusal_paths.py`

28 new tests across five concerns:

| Class | Test count | What it pins |
|---|---|---|
| `TestPayloadValidationRefusals` | 6 | non-dict order, missing/empty/whitespace symbol → "failed_validation" with the exact reason substring |
| `TestHaltFlagPrecedence` | 3 | halt flag wins over MAX_POSITION_USD, MAX_QTY, MAX_OPEN_POSITIONS — order of checks pinned |
| `TestRiskManagerEvaluateReasons` | 7 | direct (allow, reason) tuple for clean / DAILY_LOSS_CAP / POSITION_SIZE_CAP / INTRADAY_DRAWDOWN, plus boundary pins (exact-cap accept) and missing-estimated_value path |
| `TestEvaluateAccountModeDryRun` | 3 | "account_mode_dry_run" token, precedence over other caps, live-default contract |
| `TestSmokeTestBypass` | 4 | smoke-test orders bypass every gate including dry_run; `is_test=False` and absent flag both treated as real orders |
| `TestExchangeNotCalledOnRefusal` | 5 | every refusal short-circuits before `client.place_order` (no-call invariant) |

All 28 new tests pass.

### T3 — Sprint close

- `docs/claude/milestone-state.md`: M3 CLOSED → M4 queued (active milestone replaced).
- This summary doc filed.
- Sprint-complete ping appended to `docs/claude/pending-pings.jsonl`.
- Closing checkpoint `CP-2026-05-06-15-s043-complete` appended to `CHECKPOINT_LOG.md`.

---

## Validation

| Check | Result |
|---|---|
| `pytest tests/test_s043_order_refusal_paths.py` | ✅ 28 passed |
| `pytest tests/test_runtime_orders.py tests/test_order_refusal.py tests/test_per_strategy_risk.py tests/test_smoke_test_pipeline.py` (regression sweep) | ✅ No new failures (10 pre-existing failures verified to predate this branch — DRY_RUN/ALLOW_LIVE_TRADING legacy tests + MagicMock numpy isolation issues; tracked separately) |
| `scripts/secret_scan.py` | ✅ Clean |
| `scripts/check_dry_run_in_diff.py` | ✅ Clean |
| Gap list produced at T1 | ✅ Above |
| All identified gaps covered at T2 | ✅ 28 new tests across 6 classes |

---

## Files changed

- `tests/test_s043_order_refusal_paths.py` — new file (28 tests, 5 helper classes).
- `docs/claude/milestone-state.md` — M3 CLOSED, M4 active, M0..M10 status table refreshed, queued list advanced.
- `docs/claude/pending-pings.jsonl` — sprint-start + sprint-complete pings.
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — `CP-2026-05-06-15-s043-complete` entry.
- `docs/sprint-summaries/sprint-043-summary.md` — this file.

No source files in `src/` were modified — S-043 is a tests-only sprint.

---

## Live-mode check

✅ No live-trading code touched. Tests-only PR. `scripts/check_dry_run_in_diff.py`
clean. `config/accounts.yaml` not modified. No changes to `src/runtime/orders.py`,
`src/runtime/pipeline.py`, `src/runtime/trading_mode.py`, or `src/units/accounts/*`.

---

## Deferred / not in scope

- **Pre-existing test failures** (10 tests in `test_runtime_orders.py`,
  `test_per_strategy_risk.py`, `test_smoke_test_pipeline.py`): all reference
  the removed `DRY_RUN` / `ALLOW_LIVE_TRADING` env vars (operator directive
  2026-05-03, BUG-039) or hit a MagicMock-numpy test-isolation issue. These
  predate the S-043 branch — verified by running the suite at HEAD~ and
  observing the same failures. Cleaning them belongs in a M4 (repo hygiene)
  Janitor sprint, not here.
- **5m/1h timeframe enforcement** (Tier 3 — operator hold, unchanged).
- **S-015 pause/continue** (Tier 2 — operator hold, unchanged).
- **BUG-057** — awaiting VM `journalctl` diag lines (unchanged).

---

## Lessons learned

1. **Reason tokens are contract surface.** The previous suite exercised
   `RiskManager.approve()` (bool) but never the reason vocabulary —
   exactly the strings that downstream code assumes. Pin tokens directly,
   not just the boolean shape.
2. **Order-of-checks invariants need explicit tests.** "Halt wins over
   risk caps" is an architectural rule (kill switch is the first gate
   after payload validation) but had no positive test. The
   `TestHaltFlagPrecedence` class makes it a tested invariant rather
   than a comment in the code.
3. **No-call exchange invariant is worth its own class.** Every refusal
   path returning the right status string is one thing; proving the
   exchange wasn't called is a separate, equally important assertion
   that catches refactor mistakes (e.g. moving a check below
   `client.place_order`).

---

## Next sprint

**M4 — Repo hygiene + CI.** Workplan order: full Janitor audits,
canonical path enforcement across units, complete GitHub Actions
suite. S-003 / S-021 / S-035 already covered foundational pieces.
The pre-existing test failures noted above (legacy DRY_RUN tests,
MagicMock isolation) are good first targets for the hygiene work.
