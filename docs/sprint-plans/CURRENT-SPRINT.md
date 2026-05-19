# Current Sprint Handoff

**Roadmap:** `docs/sprint-plans/ROADMAP-2026-05-19.md`  
**Last updated:** 2026-05-19 (Sprint 0 — planning session)

---

## STATUS: READY — SPRINT 1

**Active sprint:** S-VWAP-PARAM-SWEEP  
**Sprint type:** auto-claude (Tier-1 backtest only)

## What was done in this session (Sprint 0)

- Inspected full repo: strategy files, backtest harness, ML layer, config, CI/CD, sprint logs
- Read `S-VWAP-POLICY-INVESTIGATION-2026-05-19.md` to understand what the last session concluded
- Confirmed: policy tuning is exhausted (+0.36 R / +0.13 R across 24 × 14d windows ≈ flat)
- Produced the sprint roadmap (`docs/sprint-plans/ROADMAP-2026-05-19.md`)
- Documented 9 sprints with tier, deliverables, DoD, and merge authority for each

## What to do next (Sprint 1 first actions)

1. **Read the sprint definition:** `ROADMAP-2026-05-19.md` → Sprint 1 (S-VWAP-PARAM-SWEEP)
2. **Read the prior context:** `docs/sprint-logs/S-VWAP-POLICY-INVESTIGATION-2026-05-19.md`
3. **First code action:** Add `total_r_long`, `total_r_short`, `wins_long`, `wins_short` to the per-window aggregate in `src/backtest/run_backtest_vwap.py`
4. **Second code action:** Add `--entry-threshold` and `--sl-mult` CLI override flags
5. **Operator-action:** Dispatch 4 × 3 = 12 parameter combinations via `vwap-backtest-sweep` with `bt_mode: compare`

## Key context for Sprint 1

- The backtest runner lives at `src/backtest/run_backtest_vwap.py` (38KB)
- The operator-action wrapper is `scripts/ops/vwap_backtest_sweep_action.sh`
- The dispatch key is `bt_mode:` (NOT `mode:`) — see `operator-actions.yml:231`
- Current live constants: `ENTRY_STD_THRESHOLD = 1.0σ`, `SL_STD_MULT_DEFAULT = 0.5σ`
- Policy table from #1537 is on main and should be left unchanged during Sprint 1
- **Do not touch `vwap.py` live constants** — Sprint 1 is backtest-only, Tier-1

## Open follow-up items to be aware of

From `comms/follow_ups.json`:

| FU ID | Summary | Blocking Sprint 1? |
|---|---|---|
| FU-20260519-001 | regime-classifier-baseline-v0 f1_trend=0.0 | No (Sprint 5 handles this) |
| FU-20260519-002 | prop_velotrade_1 at $0 balance → degenerate ML labels | No (operator action needed) |
| FU-20260519-003 | test_reload_invalidates_cache flake | No (Sprint 6 handles this) |
| FU-20260518-001 | VWAP performance tracking | No — Sprint 1 results should be added here when done |
| FU-20260518-003 | Operator-action completion-comment race | No (Sprint 8 handles this) |

## Waiting for Ben

Nothing currently blocked on Ben. All Wave 1 sprints are autonomous.
