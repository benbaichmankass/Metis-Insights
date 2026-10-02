# Bracket-faithful re-grade of the two prop legs (RQ-20261002-050)

Closes PI-20261001-S8DMYMUN-0001. Rule `RULE-PROP-BRACKET-REGRADE-STAGE0-B6V2` was committed in
`research/queue/RQ-20261002-050.yaml` (commit 2a0a3468) **before** any run. Exit model: one static
SL+TP bracket, no trail / stale stop / decay, no time exit (what `breakout_1` actually gets).
Costs: harness venue-aware full stack for `net_r`; `prop_ev_sim --costs breakout` for B6.

## Verdicts

| leg | verdict window | n (static) | Stage-0 net_r (full cost) | B6 V2, 5 seeds | **verdict** |
|---|---|---|---|---|---|
| `trend_donchian_eth_prop` | 365d (n=100 ≥ 88) | 100 | +21.28 R | INDETERMINATE ×5 (p5 −23…−11, EV +114…+130 $/life) | **PASS** |
| `trend_donchian_sol_prop` | 730d (365d n=53 < 88, escalated per the rule) | 105 | +13.37 R | INDETERMINATE ×5 (p5 −35…−30, EV +54…+66 $/life) | **PASS** |

Population: Binance Vision 1h candles to 2026-09-30; trend harness on the leg's declared params with the
trail/stale/decay flags stripped. B6 arm: fresh $5,000 account, room sizing k=0.33, min risk $10, seeds 1–5.
INDETERMINATE is not a FAIL under the registered rule: the declared-lever baseline for both legs was already
INDETERMINATE on every seed (`docs/research/b6-prop-ev/cost-headroom-2026-09-27.md` §5).

## Both windows (static arm / declared-lever control)

| leg | window | static n | static net_r | base n | base net_r |
|---|---|---|---|---|---|
| ETH | 365d | 100 | +21.28 | 168 | +10.63 |
| ETH | 730d | 206 | +28.17 | 355 | +13.38 |
| SOL | 365d | 53 | **−0.49** | 65 | +3.69 |
| SOL | 730d | 105 | +13.37 | 131 | +10.20 |

The 365d base arms reproduce the earlier measurement exactly (ETH +10.63, SOL +3.69), so the harness and candles
are unchanged.

## What the SOL PASS does and does not say (read this before acting on it)

- The registered rule gives PASS: n=105 ≥ 88, net_r > 0, V2 not FAIL.
- **The PASS is carried by the earlier year.** The most recent 365d on the bracket is −0.49 R (n=53, which is
  below the 88 floor on its own and so cannot be graded alone); the prior 365d is ≈ +13.86 R (n≈52, derived by
  subtraction, not a separate run). Quarterly static net_r on the recent year: −5.51, +0.42, +0.05, +4.54.
- So the earlier worry stands in a narrower form: SOL's edge on the real bracket is not shown by the latest year.
  It is shown by the two-year pool. That is a regime/stationarity question the rule did not register, and it is
  not a FAIL.
- The original SOL Stage-0 record (n=65) was itself below the 88 floor; this re-grade is the first one that
  clears it.
- B6 EV for SOL on the bracket (+$54…+66 per life) is about half the declared-lever figure in the
  cost-headroom run (+$122…+136), and both are INDETERMINATE.

## Tier-3 change

None is triggered: neither leg FAILS. For the manager's judgement only, not proposed by this unit: if the
manager weighs the recent-year −0.49 R above the registered two-year pool, the exact change would be
`config/strategies.yaml::trend_donchian_sol_prop.execution: shadow` (then the roster/mirror consequences for
`breakout_1`). This lane applies nothing, and the $75 flat ticket risk is untouched.

## Records

- `bracket-regrade-trend_donchian_eth_prop-2026-10-02.json`, `bracket-regrade-trend_donchian_sol_prop-2026-10-02.json`
  (both windows, per-seed V2, verdict)
- `regrade-trades/*.jsonl` (the static trades the B6 arm consumed)
- Reproduce: `python3 scripts/research/prop_bracket_exit_model.py --leg <leg> --days <365|730> --workdir <dir>`, then
  `python3 scripts/research/prop_ev_sim.py --trades <leg>=<dir>/static.jsonl --costs breakout --sizing room --room-frac 0.33 --min-risk 10 --modes realized,path,stop --seed N`
- Not done here: the committed `comms/strategy_evidence/<leg>.json` records are unchanged (still graded on
  declared levers, with `venue_bracket_arm` beside them); a roster or execution change is the manager's call.
