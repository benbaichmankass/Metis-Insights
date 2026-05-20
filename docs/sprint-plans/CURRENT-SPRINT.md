# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-2026-05-19.md`  
**Last updated:** 2026-05-19 (Sprint 4 in progress)

---

## STATUS: WAITING FOR BEN — SPRINT 4 TIER-3 PR OPEN

**SPRINT:** S-VWAP-POLICY-LIVE-WIRE  
**PR:** Draft Tier-3 PR — `policy_for_candles` wired into `build_vwap_signal` in `src/units/strategies/vwap.py`

**LAST_COMPLETED:**
- Sprint 3 (S-VWAP-LIVE-PARAM-UPDATE, 2026-05-19) — PR #1571 merged, SL=0.3 live
- Sprint 2 (S-VWAP-ANCHOR-EXPERIMENT, 2026-05-19) — PR #1576 merged; anchor comparison run via issue #1577 concluded: **session anchor wins** (+4.88 vs +1.75 rolling). No Tier-3 flip.

**READY_TO_CONTINUE:** Once Ben approves and merges the Sprint 4 Tier-3 PR:
1. `ict-git-sync.timer` auto-deploys to live VM (or Claude fires `pull-and-deploy` if needed)
2. Monitor `/health-review` for regime skip events (weak-up/low, sideways/low trades suppressed; strong-up/low signals at 2.0σ threshold)
3. Check FU-20260518-001 for impact on long-side R — first live data with policy gate active
4. Proceed to Sprint 5 (S-REGIME-CLASSIFIER-BASELINE) or Sprint 6 (S-FLAKE-RELOAD-CACHE) depending on priority

---

## What was done in this session (Sprint 4 — S-VWAP-POLICY-LIVE-WIRE)

### Anchor experiment results collected (issue #1577)
- Session anchor wins: +4.88 overall vs +1.75 rolling
- Rolling destroys short-side R (-2.32 vs +5.45); long-side gain (+4.07 vs -0.58) doesn't compensate
- No Tier-3 flip. Long-side problem is a regime/policy problem → Sprint 4

### Policy gate implemented (`src/units/strategies/vwap.py`)
- Added top-level import: `from src.units.strategies.vwap_policy import policy_for_candles`
- In `build_vwap_signal`: after computing deviation, calls `policy_for_candles(candles_df)`
  - `allow=False` → return `side="none"` with `reason="regime_policy_skip: regime=<regime>"`
  - `threshold=N` → use N as `effective_threshold` instead of `ENTRY_STD_THRESHOLD`
  - `threshold=None` → use `ENTRY_STD_THRESHOLD` unchanged
- `confidence` updated to use `effective_threshold`
- `base_meta` always includes `policy_regime`, `policy_allow`, `policy_threshold`

### Tests added (`tests/test_vwap_strategy.py`)
- `TestPolicyGate` (7 tests): skip suppresses buy/sell, skip meta auditable, 2.0σ override raises entry bar, deep signals pass override, unknown regime falls through to module constant, policy meta on every signal
- Fixed 7 pre-existing test failures (DRY_RUN/MODE checks removed by 2026-05-03 directive)
- **77/77 tests passing, 0 regressions**

### Sprint logs written
- `docs/sprint-logs/S-VWAP-ANCHOR-EXPERIMENT-2026-05-19.md`
- `docs/sprint-logs/S-VWAP-POLICY-LIVE-WIRE-2026-05-19.md`

## Sprint 4 key context

### Policy table live after merge
| Regime | Effect |
|--------|--------|
| `weak-up/low` | skip — strategy loses at all thresholds in this regime |
| `sideways/low` | skip — no consistent edge at any threshold |
| `strong-up/low` | threshold override → 2.0σ (vs 1.0σ default) |
| all others | fall through to ENTRY_STD_THRESHOLD=1.0σ |

### What this changes live
- Some signals that would have fired (weak-up/low, sideways/low regimes) will be suppressed
- Strong-up/low signals require 2.0σ deviation instead of 1.0σ
- All other regimes: no change
- SL/TP/exit paths: unchanged
- Cadence will decrease in the skip regimes; this is intentional

### What this does NOT change
- ENTRY_STD_THRESHOLD constant (still 1.0σ)
- SL_STD_MULT_DEFAULT constant (still 0.3σ)
- HTF gate
- Monitor/exit logic

## Open follow-up items

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking | Updated with anchor results; watch after policy gate deploys |
| FU-20260518-003 | Operator-action completion-comment race | No — title-prefix path is reliable |
| FU-20260519-001 | regime-classifier-baseline-v0 f1_trend=0.0 | No — Sprint 5 |
| FU-20260519-002 | prop_velotrade_1 at $0 balance → degenerate ML labels | No |
| FU-20260519-003 | test_reload_invalidates_cache flake | No — Sprint 6 |

## Waiting for Ben

**Tier-3 draft PR:** Policy gate wired into `build_vwap_signal` (weak-up/low + sideways/low skip; strong-up/low → 2.0σ override)  
Evidence: vwap_policy.py policy table backed by issue #1536 24-window adaptive backtest (n=3-6 per regime, n≥3 + positive mean_R required for each entry).  
Action needed: Review, approve, and confirm merge.
