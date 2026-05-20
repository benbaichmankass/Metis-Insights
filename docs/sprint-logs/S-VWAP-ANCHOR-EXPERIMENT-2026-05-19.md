# Sprint Log: S-VWAP-ANCHOR-EXPERIMENT

## Date Range
- Start: 2026-05-19
- End: 2026-05-19

## Objective
- Primary: Implement `--vwap-anchor session|rolling|compare` in `run_backtest_vwap.py` and `vwap-backtest.yml`; run anchor comparison via issue #1577; determine whether rolling VWAP anchor outperforms session anchor
- Secondary: Collect anchor comparison results and update FU-20260518-001; if rolling wins, open Tier-3 PR to flip live default

## Tier
- Tier 1 (backtest CLI + workflow changes, self-merge)
- No Tier-3 PR needed: session anchor won → no live default flip

## Starting Context
- Active roadmap items: Sprint 2 of ROADMAP-2026-05-19.md (S-VWAP-ANCHOR-EXPERIMENT), running concurrent to Sprint 3 (SL=0.3 deploy)
- Prior sprint reference: `S-VWAP-SWEEP-DISPATCH-2026-05-19.md` — SL=0.3 identified as optimal, deployed in PR #1571
- Live VWAP constants at start: ENTRY_STD_THRESHOLD=1.0σ, SL_STD_MULT_DEFAULT=0.3σ (post-#1571)

## Repo State Checked
- Branch: `claude/setup-coding-session-qCToW`
- Deployment state: SL=0.3 live (PR #1571 merged and deployed)
- Branch divergence: `claude/` branch was behind `origin/main` by several commits (missing f0c6de5 SL=0.3 change). Resolved via `git merge origin/main`.

## Files and Systems Inspected
- `src/backtest/run_backtest_vwap.py` — added `--vwap-anchor` CLI flag
- `.github/workflows/vwap-backtest.yml` — added `anchor_compare: true` parse + JS renderer
- `src/units/strategies/vwap.py` — `_session_anchor_slice` interface studied
- Issue #1577 — anchor comparison run results

## What Was Done

### Code changes (PR #1576)
1. **`run_backtest_vwap.py`**: Added `--vwap-anchor session|rolling|compare` flag
   - Rolling anchor implemented by dropping `timestamp` column before `build_vwap_signal` call — forces `_session_anchor_slice` fallback to full rolling window
   - `anchor_compare` mode runs both anchors on the same N windows, outputs `{"anchor_window_comparison": results}`
   - Fixed `_simulate_trade` NameError bug: `vwap_anchor` was referenced as a free variable without being passed as parameter

2. **`vwap-backtest.yml`**: Added `anchor_compare: true` issue body field
   - Parsed in "Parse issue body" step; mutually exclusive with other modes
   - CLI dispatches `--vwap-anchor compare --no-htf` when set
   - JS comment renderer adds `anchor_window_comparison` table
   - Also restored `param_sweep_window` renderer (was missing from branch due to divergence from main)

3. **`test_vwap_strategy.py`**: Updated two SL pin tests
   - `test_sl_default_pinned_to_current_value`: `SL_STD_MULT_DEFAULT == 0.3` (was 0.5)
   - `test_risk_reward_at_entry_boundary`: boundary_rr = 1.0/0.3; realized R:R ≥ 1.0 (ATR floor widens SL in synthetic fixtures)

### PRs opened and merged
- PR #1576 (anchor experiment + test fixes) — 10/10 CI checks, merged 2026-05-19
- PR #1578 (docs: sprint 2 dispatched CURRENT-SPRINT.md update) — merged 2026-05-19

### Anchor comparison run
- Issue #1577 dispatched: `anchor_compare: true`, 16 windows × 14 days, seed=42, 365d pool, no HTF gate, ENTRY=1.0σ/SL=0.3σ (module defaults)
- Completed in ~22 min; results posted as issue comment; issue closed `completed`

## Anchor Comparison Results

| Anchor  | Mean Total R | L R   | S R   | Mean Win% | Pos/16 |
|---------|-------------|-------|-------|-----------|--------|
| session | **+4.88**   | -0.58 | +5.45 | —         | 9/16   |
| rolling | +1.75       | +4.07 | -2.32 | —         | 8/16   |

### Analysis
- Rolling anchor improves long-side R dramatically (+4.07 vs -0.58) — validating that removing the session UTC-midnight anchor helps mean-reversion longs in the first hours of a session
- Rolling anchor catastrophically destroys short-side R (-2.32 vs +5.45) — a 7.77R swing on shorts that more than offsets the long-side gain
- Net: session anchor wins by +3.13R overall
- Root cause of rolling's short-side collapse: not investigated in depth, but likely the rolling window VWAP drifts with the trend on the short side, moving TP away from current price and widening deviation in the wrong direction
- Long-side problem (-0.58R at session anchor) is NOT an anchor issue — it persists with session anchor and requires the policy gate (Sprint 4)

## Decision
- **No Tier-3 flip**: session anchor remains the live default. No live code change triggered by this sprint.
- FU-20260518-001 updated: session anchor confirmed correct; long-side problem is a regime/policy problem, not an anchor problem.

## Follow-up Items Created
- Sprint 4 (S-VWAP-POLICY-LIVE-WIRE): wire `vwap_policy.policy_for_candles` into `build_vwap_signal` — the mechanism for fixing long-side under `weak-up/low` and `sideways/low` regimes by skipping those signals

## What Was NOT Done
- Rolling anchor Tier-3 PR: not needed (session wins)
- Anchor-specific tests (anchor comparison is tested via backtest infrastructure, not unit tests)
