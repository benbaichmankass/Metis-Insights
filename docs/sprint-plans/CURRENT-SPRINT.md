# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-2026-05-19.md`  
**Last updated:** 2026-05-19 (Sprint 1 complete)

---

## STATUS: READY — SPRINT 2

**Active sprint:** S-VWAP-SWEEP-DISPATCH  
**Sprint type:** auto-claude (Tier-1 operator-action dispatch)

## What was done in this session (Sprint 1 — S-VWAP-PARAM-SWEEP)

- Added `PARAM_SWEEP_ENTRY = [0.8, 1.0, 1.2, 1.5]` and `PARAM_SWEEP_SL = [0.3, 0.5, 0.7]` constants
- Added `sl_std_mult: float | None = None` to `run_single` and `run_windows`
- Added long/short trade split throughout: `total_r_long`, `total_r_short`, `wins_long`, `wins_short`, `trades_long`, `trades_short` in `run_single` return dict
- Added `mean_total_r_long`, `mean_total_r_short` to `run_windows` aggregate and `per_regime_stats`
- Added `--param-sweep` CLI flag (4 × 3 = 12 combinations, monkey-patches ENTRY, passes sl_std_mult directly)
- Added `--entry-threshold SIGMA` and `--sl-mult SIGMA` standalone CLI overrides
- Updated `_print_regime_coverage` to show L/S split per config and handle `param_sweep_window` key
- 87 tests pass; pre-existing unrelated failure unchanged
- Sprint log: `docs/sprint-logs/S-VWAP-PARAM-SWEEP-2026-05-19.md`

## What to do next (Sprint 2 first actions)

1. **Read the sprint definition:** `ROADMAP-2026-05-19.md` → Sprint 2 (S-VWAP-SWEEP-DISPATCH)
2. **Verify the operator-action script:** read `scripts/ops/vwap_backtest_sweep_action.sh` to confirm it can pass `--param-sweep --windows 24 --window-days 14`
3. **Check `operator-actions.yml`:** confirm `bt_mode: compare` dispatch key at line 231
4. **Dispatch action:** open issue labelled `operator-action` with `action: vwap-backtest-sweep` and body containing `bt_mode: param-sweep` (or whatever the script expects) plus `--windows 24 --window-days 14`
5. **Collect results:** read output JSON via `mcp__github__issue_read` once the action completes
6. **Analyse:** identify the winning (ENTRY, SL) pair; produce a short table of `mean_total_r`, `mean_total_r_long`, `mean_total_r_short` per combo
7. **Propose Tier-3 PR:** if a clear winner emerges, open a draft PR that updates `ENTRY_STD_THRESHOLD` and `SL_STD_MULT_DEFAULT` in `vwap.py` — ping Ben for approval

## Key context for Sprint 2

- Sweep grid: `PARAM_SWEEP_ENTRY = [0.8, 1.0, 1.2, 1.5]` × `PARAM_SWEEP_SL = [0.3, 0.5, 0.7]` = 12 combinations
- Runner call: `python -m src.backtest.run_backtest_vwap --param-sweep --windows 24 --window-days 14 --seed 42`
- Output key in JSON: `param_sweep_window`
- The dispatch key is `bt_mode:` (NOT `mode:`) — see `operator-actions.yml:231`
- Live constants NOT changed yet: `ENTRY_STD_THRESHOLD = 1.0σ`, `SL_STD_MULT_DEFAULT = 0.5σ`
- Changing live constants is Tier-3 — requires Ben approval before merge

## Open follow-up items to be aware of

From `comms/follow_ups.json`:

| FU ID | Summary | Blocking Sprint 2? |
|---|---|---|
| FU-20260519-001 | regime-classifier-baseline-v0 f1_trend=0.0 | No (Sprint 5 handles this) |
| FU-20260519-002 | prop_velotrade_1 at $0 balance → degenerate ML labels | No (operator action needed) |
| FU-20260519-003 | test_reload_invalidates_cache flake | No (Sprint 6 handles this) |
| FU-20260518-001 | VWAP performance tracking | Add Sprint 2 sweep results here when collected |
| FU-20260518-003 | Operator-action completion-comment race | No (Sprint 8 handles this) |

## Waiting for Ben

Nothing currently blocked on Ben for Sprint 2 dispatch. Tier-3 approval needed only if a clear (ENTRY, SL) winner emerges and a `vwap.py` PR is proposed.
