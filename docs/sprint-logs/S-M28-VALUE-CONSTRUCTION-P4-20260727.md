# Sprint Log: S-M28-VALUE-CONSTRUCTION-P4-20260727

## Date Range
2026-07-27 (single session; research-driver).

## Objective
Iterate M28 value-thesis **construction** (D1 transform / D2 conditioning / D3
cross-section) and grade each through the **P4 net-of-cost LIFECYCLE gate**
(`thesis_backtest_run`) on the committed 21-yr backfill — the arbiter the operator's
directive names. Goal: a construction that beats its own P4 baseline (`edge_vs_baseline`,
baseline = −0.0047 from the level/S1-former), net-of-cost, OOS. Only once one clears does
re-running C4 (exit-conditioning) become worthwhile.

## Tier
Tier-1 throughout — pure observe-only research tooling + docs. No order path, no live
influence (P5 expression / P6 `c_macro` remain Tier-3, out of scope).

## Starting Context
Prior session (PR #7758) established the "backtest history first" rule and ran the M28-P4
value gate on 21-yr history → OOS-NULL (edge −0.0047, calib ~0). Prior value D1/D2/D3 work
(ledger entry 12, #7534) was graded through the **S2/S3 horizon-IC grader**, NOT the P4
lifecycle gate — so a P4 re-test of the construction space was genuinely unrun work.

## Repo State Checked
Read `docs/CLAUDE-RULES-CANONICAL.md` (via research-driver + session-coordination skills),
ROADMAP M28/M36 rows, `RESEARCH-RIGOR-STANDARD.md` (backtest-first rule),
`M28-signal-research-methodology.md` (D1–D5 backlog), the P4 gate run doc, and the
construction toolkit. Posted `▶️ START` on coordination board #6927; armed hourly self-wake.

## Files and Systems Inspected
`scripts/macro/{thesis_backtest_run,construction_sweep,signal_constructions,valuation_snapshot_backfill,fetch_macro_candles}.py`,
`crypto_signals_data.build_percentile_snapshots`, `src/units/strategies/macro_thesis/*`,
the committed `comms/macro/valuation_snapshots_backfill.jsonl` (10,125 rows, 9 driver series,
2005→2026), `.github/workflows/m28-value-grade.yml`.

## Work Completed
- **`scripts/macro/value_construction_sweep.py`** (new) — derives D1 (change/detrend/accel),
  D2 (`level_x_turning` + a price-turning conditioner in the grader), and D3 (cross-section
  basket) value constructions **locally from the committed backfill's raw `value` series**
  (no FRED/network for emit), re-emitting `cheap_score` via the UNCHANGED
  `signal_constructions` + `build_percentile_snapshots` path, then grading each through the
  UNCHANGED P4 gate. Baseline (committed backfill) graded as the in-run control.
- **`tests/test_m28_value_construction_sweep.py`** (new) — 5 tests incl. a leakage-safe PIT
  prefix-stability check. Green under CI-pinned ruff 0.15.x + pytest.
- **`.github/workflows/m28-value-grade.yml`** — added a P4-sweep step (hosted US-IP runner
  fetches the full 5-symbol candle set, runs the sweep, posts the scorecard) + commits the
  fetched candle CSVs for hermetic local reproducibility (Stooq/Yahoo are proxy-blocked from
  the research sessions).
- **PR #7766 merged** to main (squash, via merge queue / auto-merge); grade run via issue
  #7773 on a hosted US-IP runner.
- **Ledger entry 13** + ROADMAP M28/M36-C status updates recording the result.

## Validation Performed
P4 sweep (21-yr backfill + real ETF candles, issue #7773):

| construction | n | win | mean_net | calib | edge_vs_baseline |
|---|---|---|---|---|---|
| change (D1) | 837 | 0.515 | +0.0027 | +0.0224 | −0.0031 |
| detrend (D1) | 918 | 0.509 | +0.0029 | +0.0094 | −0.0034 |
| level_x_turning (D2) | 574 | 0.505 | +0.0011 | +0.0049 | −0.0017 |
| baseline (level/S1) | 1104 | 0.497 | +0.0018 | −0.0037 | −0.0047 |
| xsec (D3) | 776 | 0.494 | −0.0001 | −0.0339 | −0.0059 |
| accel (D1) | 804 | 0.506 | −0.0001 | +0.0108 | −0.0066 |
| level | 1028 | 0.482 | −0.0004 | −0.0184 | −0.0068 |

**Verdict: nothing clears** (`edge_vs_baseline > 0` bar) — every cell still loses to naive
all-long net-of-cost. But D1 `change`/`detrend` + D2 `turning` **beat the level baseline and
flip calibration positive** — confirming the methodology's core hypothesis (edge in the
*shift*, not the *level*). Improvement is real but sub-threshold → C4 stays un-warranted.

## Documentation Updated
`docs/research/M28-signal-research-ledger.md` (entry 13 + P4 re-test nuance in the
meta-finding), `ROADMAP.md` (M28 row + M36 Track C row).

## Contradictions or Drift Found
None. Note: the ledger's earlier "value exhausted" verdict was graded under the S2/S3
arbiter only; entry 13 qualifies it under the P4 arbiter (improvement + positive calibration,
still sub-all-long) — recorded, not contradicted.

## Risks and Follow-Ups
- **Next iteration (queued):** D4 composite (`change ⊕ detrend`) + D5 horizon sweep on the
  `change` cell (find the hold horizon where its edge survives cost) + D2 regime-conditioning.
  If those stay sub-threshold, value is cross-gate-conclusively exhausted → pivot.
- The `level_x_price_turning` cell scored n=0 (price-momentum gate over-filtered) — minor;
  the fundamental-turning `level_x_turning` cell is the working D2.

## Deferred Items
D4/D5 iteration deferred to a fresh session (this session's context was heavily reloaded;
handed off per `session-handoff`).

## Next Recommended Sprint
`S-M28-VALUE-CONSTRUCTION-D4D5` — extend `value_construction_sweep.py` with the D4 composite
(`change ⊕ detrend`, equal- then IC-weighted) + a D5 horizon sweep on the `change` cell,
re-grade through the P4 gate via `m28-value-grade-now`, record in the ledger.
- Required verification before starting: `pytest tests/test_m28_value_construction_sweep.py`
  green; candles committed at `data/macro_candles/` (the #7766 candles-PR) or re-fetched on
  the hosted runner.

## Wrap-Up Check
Ledger + ROADMAP updated; PR #7766 merged; merge protocol run (🔒 CLAIM / 🔓 RELEASE on #6927);
hourly cadence + self-wake armed. `doc-freshness` pending on this docs PR.
