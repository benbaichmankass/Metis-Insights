# Sprint Log: S-M19-FC-SLTP-GEOMETRY-2026-07-05

## Date Range
2026-07-05 (M19 T0.4 Phase-2 — "extend the fc win": fc-informed SL/TP geometry
offline backtest).

## Objective
Test the highest-value non-data-constrained extension of the T0.4 `fc` forecast
head: instead of feeding `fc_*` as a vol-regime classifier feature (its validated
use), **size the stop/target from the forecast's own predicted range** — and see,
over historical trades, whether that geometry would have improved net-R / win-rate /
maxDD vs the SL/TP the bot actually placed. Report honestly, with the same skepticism
that voided the v1 strawman.

## Tier
Tier 1 (offline research + docs). Read-only over the synced `trade_journal.db` +
`datasets-out/{forecasts,market_raw}` side-streams; writes nothing; no `src/` /
`config/` / order-path change. GPU spend: **$0**.

## Starting Context
The fc head is at shadow, soaking across BTC+ETH toward the Tier-3 money gate. The
representation frontier (T0.1/T1.1/T1.2) is three-for-three negative; fc's classifier
use is the milestone's durable win. Phase 2 was the reprioritized active lever (the
cost-data Phase 1 and T1.3 ranker Phase 3 are data-constrained/deferred). v1 of the
backtest produced a "spectacular" result that I diagnosed as **confounded** (an
absolute-15m-quantile barrier is a tight coin-flip vs the actual multi-hour SL/TP) and
refused to report; this sprint built + ran the confound-removed v2.

## Work Completed
**v2 three-arm confound-removed backtest** (`scripts/ml/fc_sltp_geometry_backtest.py`,
PR #5587, merged). Each arm holds the trade's real direction + R:R fixed so the only
variable is stop/target **distance**:
- `real-realized` = `pnl / risk_$` (ground-truth realized R — **the anchor**).
- `fixed-resim` = the actual SL/TP re-simulated forward (triple-barrier, entry bar
  excluded via `bisect_right`, mark-to-close at `--max-hold`).
- `fc-vol-scaled` = actual R:R, distance × `clamp(fc_range_rel/median, [0.5, 2.0])`,
  same engine (scaling both barriers preserves r_tp, so R:R is identical).

Added `--vol-clamp-lo/-hi`; local validation (`ast.parse` + unit checks of `_epoch`,
`_asof`, `_simulate`, `_agg`, argparse). Ran on the trainer (relay #5588),
`--max-hold 96`, full fc coverage (`no_fc_cover=0`).

## Validation Performed
BTCUSDT (n=552 sim / 458 real; ETH n=20, too thin):

| arm | n | win_rate | mean_R | sum_R | maxDD_R |
|---|---|---|---|---|---|
| real-realized | 458 | 0.264 | −0.675 | −309.0 | −346.9 |
| fixed-resim | 552 | 0.254 | −0.058 | −31.8 | −101.5 |
| fc-vol-scaled | 552 | 0.390 | +0.416 | +229.8 | −44.4 |

## Contradictions or Drift Found
The result **looks** like a win (fc-vol-scaled +0.42R vs fixed-resim −0.06R) and is
**not** one. The anchor exposes the confound: `real-realized` (−0.68R) vs
`fixed-resim` (−0.06R) diverge by ~0.6R — the forward triple-barrier engine does not
reproduce live outcomes (live trades close on fees/monitor/flip/reconciler exits, not
clean barriers; the re-sim is a rosier hold-to-barrier policy the system doesn't run).
Since `fc-vol-scaled` uses the same engine, its edge is an **in-simulator artifact**
over a rosy-broken baseline, driven by how scaled barrier distance meets the 24h
mark-to-close cap — not a real-money result. **VERDICT: INCONCLUSIVE — do not
graduate an fc→SL/TP-geometry change on this evidence.**

## Documentation Updated
- NEW `docs/research/T0.4-fc-sltp-geometry-evidence-2026-07-05.md` (full 3-arm table,
  the reality-calibration diagnosis, reproduce steps).
- `ROADMAP.md` T0.4 row — Phase-2 inconclusive result recorded.
- `docs/claude/ml-review-backlog.json` — `MB-20260705-FC-SLTP-GEOMETRY`.
- This log.

## Risks and Follow-Ups
- **A faithful test is a live observe-only fc-geometry shadow-soak** (the
  `exit_ladder_soak` shape: log the fc-scaled SL/TP alongside the placed SL/TP per
  opening order, compare realized outcomes under the account rulesets) — NOT another
  offline backtest (proven unfaithful). That is a deliberate build, backlog-tracked.
- fc's validated use (vol-regime classifier feature, at shadow) is unchanged; the
  shadow→advisory promotion stays the Tier-3 money gate (operator + a powered RG4 +
  head-pinned money-gate walk-forward).

## Deferred Items
- Live fc-geometry shadow-soak (the real Phase-2 path).
- Phase 1 broker-truth cost writer (Tier-2, deferred); Phase 3 T1.3 ranker (data-walled).

## Next Recommended Sprint
With Phase 2 assessed (inconclusive, needs a live soak not a backtest) and Phases 1/3
deferred as data-constrained, M19's active offline exploration is substantially
complete. Rather than pick the next lever by default, the **next session should be a
deep-research pass** that prioritizes the candidate directions (D1 live fc-geometry
soak · D2 break the label wall · D3 task-matched corpus-embedding head · D4 mature
fc→advisory) against current data reality and returns an evidence-backed recommendation
— brief: [`M19-next-direction-deep-research-brief-2026-07-05`](../research/M19-next-direction-deep-research-brief-2026-07-05.md);
directions table in the ROADMAP M19 "Next research directions" block. In parallel,
soak maturation continues (let the BTC+ETH fc shadow soak accrue volatile episodes,
then re-run a powered fresh-mirror RG4 before any fc→advisory proposal —
`MB-20260705-FC-ADVISORY-READINESS`).

## Wrap-Up Check
- [x] Objective met (v2 built, ran, decided — honestly inconclusive).
- [x] Verified reality reported (real trainer numbers; anchor-driven skepticism, no overclaim).
- [x] No live-path / config / risk change; nothing promoted.
- [x] GPU ledger untouched ($0).
- [x] Findings preserved durably (evidence doc + ROADMAP + backlog + this log).
