# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-2026-05-19.md`  
**Last updated:** 2026-05-19 (Sprint 2 complete)

---

## STATUS: BLOCKED_PM — SPRINT 3

**SPRINT:** S-VWAP-LIVE-PARAM-UPDATE  
**WAITING_FOR_BEN:** Review and approve the Tier-3 draft PR proposing `SL_STD_MULT_DEFAULT 0.5 → 0.3` in `src/units/strategies/vwap.py`. The 12-combo ENTRY×SL sweep shows ENTRY=1.0/SL=0.3 at `mean_total_r=+4.88` vs current config at `-0.46` (rank 9/12). This is a Tier-3 live-strategy change — Claude must not merge without Ben.

**LAST_COMPLETED:** Sprint 2 (S-VWAP-SWEEP-DISPATCH, 2026-05-19) — dispatched 12-combo param sweep via `vwap-backtest.yml` issue #1569, collected ranked results. Sweep ran 22 min (16 windows × 14 days, 365d 5m BTCUSDT, seed=42). Sprint log: `docs/sprint-logs/S-VWAP-SWEEP-DISPATCH-2026-05-19.md`.

**READY_TO_CONTINUE:** Once Ben approves the Tier-3 draft PR: Claude merges it, then fires `pull-and-deploy` operator action. Then fires `vm-diag-snapshot` to confirm live SHA matches the merged commit. If Ben asks for anchor experiment first, run Sprint 2 of roadmap (S-VWAP-ANCHOR-EXPERIMENT) — see below.

---

## What was done in this session (Sprint 2 — S-VWAP-SWEEP-DISPATCH)

- Diagnosed `operator-actions.yml` label-trigger unreliability: issues #1563, #1565, #1567, #1455 all failed to trigger the workflow. Root cause: label-payload timing in `issues.opened` + `issues.labeled` events
- Added `--param-sweep` support to `vwap-backtest.yml` (PR #1568, merged): `param_sweep: true` issue body field, mutex with compare/threshold-sweep, `param_sweep_window` result table in issue comment
- Dispatched 12-combo sweep via issue #1569; sweep completed in 22 min; issue closed `completed`
- Collected sweep results (see sprint log for full table)
- Opened Tier-3 draft PR: `SL_STD_MULT_DEFAULT = 0.3` in `vwap.py` + updated inline comments

## Sweep results summary

| ENTRY σ | SL σ | Mean Total R | L R | S R | Pos/16 |
|---------|------|-------------|-----|-----|--------|
| **1.0** | **0.3** | **+4.88** | -0.58 | +5.45 | 9/16 |
| 0.8 | 0.3 | +4.82 | -1.35 | +6.17 | 8/16 |
| 1.5 | 0.3 | +3.13 | -2.24 | +5.37 | 8/16 |
| 1.2 | 0.3 | +3.07 | -1.35 | +4.42 | 9/16 |
| ... | ... | ... | ... | ... | ... |
| **1.0** | **0.5** | **-0.46** | -1.34 | +0.89 | 9/16 ← CURRENT LIVE |

Full ranked table: `docs/sprint-logs/S-VWAP-SWEEP-DISPATCH-2026-05-19.md` § Sweep Results.

## What to do next (Sprint 3 first actions — after Ben approval)

1. **Wait for Ben to review the Tier-3 draft PR** (see PR linked in sprint log)
2. **Once approved: merge the PR** (Claude merges, no self-merge)
3. **Fire `pull-and-deploy` operator action** to deploy the new `SL_STD_MULT_DEFAULT = 0.3`
4. **Fire `vm-diag-snapshot`** to confirm the live VM is running the new SHA
5. **Monitor:** next `/health-review` should surface VWAP SL=0.3σ in the trade stats. Watch for any anomalous SL placements

## Optional parallel sprint (no Ben approval needed)

Roadmap Sprint 2 (S-VWAP-ANCHOR-EXPERIMENT) can run concurrently:
- Add `--vwap-anchor rolling|session` CLI flag to `run_backtest_vwap.py`
- Comparison run: same 16 windows, ENTRY=1.0/SL=0.3 (sweep winner), two anchor variants
- Dispatch via `vwap-backtest.yml` issue
- Sprint is fully autonomous (Tier-1 backtest only)

## Key context for Sprint 3

- Tier-3 change: `SL_STD_MULT_DEFAULT = 0.5 → 0.3` in `src/units/strategies/vwap.py`
- R:R implication: 2:1 → 3.33:1 (reward:risk at entry boundary with ENTRY=1.0)
- The 2026-05-03 directive was to preserve 2:1 R:R — this change intentionally relaxes that
- Live constants currently: `ENTRY_STD_THRESHOLD = 1.0σ`, `SL_STD_MULT_DEFAULT = 0.5σ`
- Proposed: keep ENTRY at 1.0σ, reduce SL to 0.3σ
- Deploy path: `pull-and-deploy` operator action after merge
- Long-side issue NOT fully resolved by this change (L R = -0.58 at best) — anchor experiment and policy gate are the follow-on levers

## Open follow-up items

From `comms/follow_ups.json`:

| FU ID | Summary | Blocking? |
|---|---|---|
| FU-20260518-001 | VWAP performance tracking | Updated with sweep results; monitor after deploy |
| FU-20260518-003 | Operator-action completion-comment race | No — title-prefix path is reliable alternative |
| FU-20260519-001 | regime-classifier-baseline-v0 f1_trend=0.0 | No — Sprint 5 handles this |
| FU-20260519-002 | prop_velotrade_1 at $0 balance → degenerate ML labels | No |
| FU-20260519-003 | test_reload_invalidates_cache flake | No — Sprint 6 handles this |

## Waiting for Ben

**Tier-3 draft PR:** `SL_STD_MULT_DEFAULT 0.5 → 0.3` in `src/units/strategies/vwap.py`  
Evidence: ENTRY=1.0/SL=0.3 = mean_total_r +4.88 vs current +(-0.46) across 16 windows × 14 days.  
Action needed: Review, approve, and confirm merge or request further experiments first.
