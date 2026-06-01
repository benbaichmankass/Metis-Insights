# Session handoff — 2026-06-01 (night — regime router phases 1 + 2 live)

> **Continues** `docs/research/session-handoff-2026-06-01-evening.md`. This
> session shipped the matrix's final coverage gap (vwap live-gated) **and**
> the first two phases of the regime router that the matrix was built to
> drive. The detector + observability are wired into the live tick; the
> shadow log accumulates phase-3's evidence going forward.

## TL;DR

Live bot is unchanged at `1e886c6`. Branch-head deploys via the next
`pull-and-deploy` will pick up phases 1 + 2 of the regime router (PRs
#2582 + #2583). The aggregator's decision is **bit-identical** to
pre-phase-2 — phase 2 is log-only. Phase 3 (operator-approved hard gates)
sits in the backlog as `PERF-20260601-006` gated on ≥ 7 days of live
shadow data.

## 1. What shipped this session

| PR | What | Tier | State |
|---|---|---|---|
| **#2574** | `scripts/backtest_pullback.py` + htf_pullback regime row | 1 | **merged** |
| **#2580** | `src/backtest/run_backtest_vwap.py` exit-side gates + vwap live-gated regime row | 1 | **merged** |
| **#2581** | Evening session-end handoff + `vwap-viability-verdict` citation cleanup | 1 | **merged** |
| **#2582** | **Regime router phase 1** — `RegimeDetector` + per-tick eval stamping | 1/2 | **merged** |
| **#2583** | **Regime router phase 2** — shadow the policy table (log-only) | 2 | **merged** |

Six PRs in one session (counting morning's #2572 which was already merged
at session start).

## 2. Initiative status — regime-aware routing (`PERF-20260601-002`)

| Step | Phase | Status |
|---|---|---|
| 1 — evidence matrix | the regime × strategy × direction grid | **complete** (matrix is decision-grade for every roster strategy after PRs #2574 + #2580) |
| 2 — design proposal | `docs/research/regime-router-design-2026-06-01.md` | **complete** (operator implicitly approved by greenlighting the build) |
| 3 — phase-1 build | `RegimeDetector` + per-tick eval stamping | **shipped** (#2582) |
| 4 — phase-2 build | shadow the policy table | **shipped** (#2583) |
| 5 — phase-3 build | turn OFF cells into hard gates behind a flag | **backlog** (`PERF-20260601-006`) — gated on ≥ 7 days shadow data |
| 6 — phase-4 build | soft weights + classifier-v0 detector | **backlog** (`PERF-20260601-007`) — gated on phase 3 proving lift |

## 3. What runs differently on the live VM after the next pull-and-deploy

**Every per-strategy `*_eval` audit row** (turtle, ict_scalp, vwap,
trend_donchian, trend_donchian_1h, fade_breakout_4h, squeeze_breakout_4h,
fvg_range_15m, htf_pullback_trend_2h, mes_trend_long_1d) carries three
new fields:

- `regime` — `chop` / `transitional` / `trending` / `unknown`
- `adx_14` — float (rounded 4dp) or null
- `regime_source` — `"adx-14"` (phase 4 will swap to `"classifier-v0"`
  if the shadow model validates)

**A new audit event** `regime_shadow_gate` fires for every aggregated
intent whose `(strategy, side, regime)` cell is OFF in
`config/regime_policy.yaml`. Payload: `{strategy, symbol, side, regime,
adx_14, gated, cell, reason, enforced}`. **`enforced: false`** —
phase 2 logs only; the aggregator's downstream decision is unchanged.

**Nothing else changes**: no order path touched, no risk caps moved, no
new gates between signal and order, no behaviour change to any existing
strategy.

## 4. Backlog state after this session

### New `performance-review-backlog.json` items (this session)

| id | Tier | Status | Trigger |
|---|---|---|---|
| `PERF-20260601-006` | 3 | open | Phase 3 (hard gates) — after ≥ 7 days of live shadow data |
| `PERF-20260601-007` | 3 | open | Phase 4 (soft weights + classifier-v0) — after phase 3 proves lift |
| `PERF-20260601-008` | 1 | open | Verify phase-1+2 deploy on live data — base-rate match + shadow row volume (24-48h post-deploy) |

### New `health-review-backlog.json` items (this session)

| id | Tier | Status | Trigger |
|---|---|---|---|
| `BL-20260601-003` | 3 | open | Consolidate `fade_breakout_4h._adx` + `fvg_range_15m._adx` onto `src.runtime.regime.wilder_adx` — pre-existing duplication that the new central detector makes redundant |

### Still-open items from earlier today

| id | Tier | Trigger |
|---|---|---|
| `PERF-20260601-005` | 3 | squeeze re-promotion — gated on post-#2548 multi-day window (~7h elapsed as of session close) |
| `BL-20260601-002` | 3 | `config/strategies.yaml:318` cites a non-existent `vwap-viability-verdict-2026-05-23.md` — Tier-3, operator-only |

## 5. EXACT next steps for the next session

In priority order:

1. **Deploy the branch.** Phases 1+2 are on `main` but not yet on the live
   VM. Fire `pull-and-deploy` via the `system-action` operator workflow
   (Tier-2 ack required in chat). Verify the post-deploy state:
   - `/api/diag/services` → `ict-trader-live` is `active`
   - `/api/diag/audit?limit=50` → recent `*_eval` rows now carry `regime`
     and `adx_14` fields

2. **`PERF-20260601-008` — verify live regime stream matches matrix
   base rates.** 24-48h after the deploy, pull
   `/api/diag/audit?limit=2000` and group by strategy + regime. Confirm:
   - chop ~30% / transitional ~19% / trending ~51% on 1h-strategy rows
     (trend_donchian, trend_donchian_1h, htf_pullback_trend_2h, fade,
     squeeze)
   - chop ~35% / transitional ~20% / trending ~45% on 5m-strategy rows
     (vwap, ict_scalp_5m)
   If any strategy diverges by >5 percentage points in any bucket,
   investigate (candle-fetch mismatch / detector bug / regime shift).
   This **gates** `PERF-20260601-006` (phase 3) — the cross-check must
   pass before the hard-gate PR opens.

3. **`PERF-20260601-005` — squeeze re-promotion (operator-pre-approved).**
   Still gated on multi-day post-#2548 window. Pull orphan / intent_noop /
   re-entry rate via diag once the window has accrued.

4. **Backlog drain** if (1)-(3) are not yet ready.

## 6. Open questions still on operator review

The regime-router design doc (`docs/research/regime-router-design-2026-06-01.md`
§ 6) had four open questions. Q1 (detector TF) was decided as
**per-strategy** in this session. Three remain for the phase-3 / phase-4 PR:

2. **Gate vs weight first** — start phase 3 with hard gates (the design's
   default), then graduate to weights in phase 4? Or skip hard gates and
   ship phase 3 as soft weights directly? Recommend hard gates first
   (mechanical, auditable, one-flag rollback).
3. **Keep / retire the strategy-level `long_only` flag** once the table
   covers `trend_donchian`'s short cells? Recommend retire on the phase-3
   PR (the table now subsumes it).
4. **Boundary hysteresis** — ADX hovering at 19.5/20 / 24.5/25 will flip
   regimes tick-to-tick; add a hysteresis band or dwell-time so the
   router doesn't thrash? Recommend wait for phase-3 data — if the
   hard-gate frequency at the boundary is benign, no hysteresis needed.

These are operator-input items the next session should ask before
opening the phase-3 PR.

## 7. How to operate (recurring reminders)

Unchanged from the evening handoff:

- **VM access is relay-only.** Trainer = `trainer-vm-diag-request` labelled
  issue. Live VM reads = `vm-diag-request`. Live VM mutations/deploys =
  `system-action`. **One relay at a time.**
- **For long-running tasks (≥ ~5 min), use the detached runner + done-marker
  pattern.** This session's #2575 demonstrated the full pattern after
  #2576's SSH-pipe drop.
- **Base64-encode long bash inside trainer-vm-diag bodies** (#2575 used
  this).
- **Tier-3** (`config/strategies.yaml`, `config/accounts.yaml`,
  `risk_caps`, order code) needs explicit operator approval.

## 8. Relay trail for this session

- `#2573` — htf_pullback synchronous trainer relay (matrix tag)
- `#2575` — vwap gated detached kick-off
- `#2576` — vwap poll relay (killed by SSH-pipe drop at ~6 min)
- `#2577` — vwap status ping (#1)
- `#2578` — vwap status ping (#2)
- `#2579` — vwap status ping (#3, caught DONE marker + result)
