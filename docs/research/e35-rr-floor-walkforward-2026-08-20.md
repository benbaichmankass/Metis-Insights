# M31 P5 precondition 3b — the `rr_floor` walk-forward

**Date:** 2026-08-20 · **Tier-1, observe-only.** The operator pre-approved
**running** this; the scope of that approval is the run, **not** the gate and
**not** shipping. Declaring any `rr_floor` in `config/strategies.yaml` remains
**Tier-3**.

**Tool:** `scripts/research/m20_fleet_exit_sweep.py --levers rr_floor` — the
canonical M20 machinery, so the IS/OOS Path A/B gate and the yearly walk-forward
are the fleet's own, not a bespoke comparison.
**Artifacts:** `runtime_logs/m20_rrfloor/2026-08-20/`.

**POPULATION: 19 legs × 3 cells = 57 (leg × cell) rows.** Config-exact base
args, `--tp-cap-pct 0.099`, `--split-mode oos-trades --split-target-oos 50`,
walk-forward folds 2021–2026, **net of fees**. Candle data fetched this session
from `data.binance.vision` (free lane; **the trainer VM was not used**).
⚠️ SOL/XRP/ADA/AVAX each miss 5 days of the 1,830-day span (upstream archive
gap, 0.27%); BTC/ETH none.

---

## 1. The tally

| verdict | n |
|---|---|
| `is_oos_fail` | 43 |
| `wf_fail` | 4 |
| `path_b_wf_fail` | 3 |
| **`PASS` (Path A)** | **2** |
| `path_b_wf_pass` | 2 |
| `error` → now `INERT` | 3 |

**Only `rrfloor0.5` ever passes.** `rrfloor0.75` and `rrfloor1.0` pass on no leg,
under either path. That is a coherent shape rather than a scatter: the floor has
to sit below the leg's own `rr_min` distribution to fire at all, and 0.5 is the
only grid point that does on any leg.

### The 3 `error` rows were not errors

All three are `squeeze_breakout_4h`, one per cell, and **`backtest_squeeze.py`
declares no `--rr-floor` flag** (measured by grepping each harness's parser:
`backtest_trend.py` 3 occurrences, `backtest_pullback.py` 2, and
`backtest_squeeze.py` / `backtest_fvg_range.py` / `backtest_ict_scalp.py` **0
each**). `cells_for` gated rr_floor on whether the lever is *semantically*
applicable — is the family in `LIVE_TP_CAPPED_FAMILIES`, so `rr_from_here` has a
target — and never on whether the harness implements the flag. Two independent
conditions, one test. Fixed and pinned; full account in
`BL-20260820-SWEEP-EMITS-CELLS-FOR-FLAGS-THE-HARNESS-DOES-NOT-IMPLEMENT`.

---

## 2. The four passing cells, WITH the drawdown exchange rate

⚠️ **`path_b_wf_pass` is never reportable without this column** (root `CLAUDE.md`
§ `beats`). It was **not** recorded on the cell in this run's `verdicts.json`, so
it is **re-derived** here from the IS/OOS payloads in `results.jsonl` using
`m20_fleet_exit_sweep.drawdown_exchange_rate` — the module's own function, not a
restatement. All four cells are `rrfloor0.5`.

| leg | verdict | win | Δnet_R | Δmax_dd | base n | rate | note |
|---|---|---|---|---|---|---|---|
| `trend_donchian` | PASS (A) | IS | **+4.408** | −1.684 | 289 | ok (grant 0.124) | |
| | | OOS | +0.938 | −0.938 | 49 | **null** | ⚠️ `base_unprofitable` |
| `trend_donchian_sol_prop` | PASS (A) | IS | **+2.643** | −0.199 | 269 | ok (grant 0.068) | |
| | | OOS | +0.244 | −0.618 | 49 | ok (grant 0.887) | |
| `ada_pullback_2h` | path_b_wf_pass | IS | +6.544 | −1.250 | 178 | ok (grant 0.669) | |
| | | OOS | +3.792 | **+0.189** | 50 | ok (grant 1.295) | ⚠️ **`grant_capped: true`** |
| `trend_donchian_eth` | path_b_wf_pass | IS | +9.743 | −4.194 | 535 | ok (grant 0.366) | |
| | | OOS | +1.208 | **+1.718** | 49 | ok (grant 0.692) | |

All four are **4/6 folds, `wins_effective` 4/6, `inert_wins` 0** — so no fold was
counted as a win for a lever that never fired
(`BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`).

**Both `path_b_wf_pass` rows clear the rate in both windows.** That is worth
stating with its denominator, because the standing precedent is the opposite:
the 2026-08-16 pullback run was **0 for 3** on the rate. This run is **2 of 2**.
It does not make Path B shippable — both Path B thresholds remain UNSET and the
rate is *reported, not enforced* — but the reason those three were dismissed does
not apply to these two.

### ⚠️ The headline PASS is an improvement on a losing book

`trend_donchian/rrfloor0.5` passes Path A, and its **OOS base net_R is −16.135**.
The cell moves it to −15.197. The rate returns `passes: null,
reason: "base_unprofitable"` — the function correctly declines to grade a
drawdown exchange on a book that made no money to exchange. `beats` (Path A)
requires only that net_R and maxDD are both no-worse; **it does not require the
base to be profitable**, so "PASS" here means *less bad*, not *good*.

`trend_donchian_sol_prop` is the same shape one order of magnitude smaller: OOS
base **+0.275 R** over 49 trades, cell **+0.518 R**. A +0.244 R OOS gain on a
book that made a quarter of an R.

**So the honest summary is: 2 of 57 cells pass Path A, both `rrfloor0.5`, both
with IS gains an order of magnitude larger than their OOS gains, and both OOS
windows are marginal — one deeply unprofitable, one within noise of zero.**
Nothing here is a Tier-3 proposal.

---

## 3. The dispersion test — BOTH PASSES ARE REFUSED

`m20_split_dispersion.py`, one `--emit-trades` run per arm, partitioned at five
candidate OOS targets. **`harness_agreement ok` on both legs** — the re-derived
`net_total_r` / `max_drawdown_r` / `total_trades` reproduce the harness's own
figures within the 0.001 R tolerance, so the band is not built on a metric that
fails to reproduce.

**`trend_donchian` / `rrfloor0.5`**

| target | split | OOS n | ΔIS | ΔOOS | base+Δ OOS | pass |
|---|---|---|---|---|---|---|
| 25 | 2026-04-06 | 25 | +5.346 | **0.0000** | −6.137 | **False** |
| 35 | 2026-02-09 | 35 | +4.408 | +0.938 | −10.064 | True |
| 50 | 2025-12-05 | 50 | +4.408 | +0.938 | −14.409 | True |
| 75 | 2025-06-20 | 75 | +4.408 | +0.938 | −3.693 | True |
| 100 | 2025-01-31 | 100 | +5.016 | +0.331 | −3.544 | True |

**`trend_donchian_sol_prop` / `rrfloor0.5`**

| target | split | OOS n | ΔIS | ΔOOS | base+Δ OOS | pass |
|---|---|---|---|---|---|---|
| 25 | 2026-03-16 | 25 | +2.887 | **0.0000** | +1.103 | **False** |
| 35 | 2026-01-06 | 35 | +2.269 | +0.618 | +1.451 | True |
| 50 | 2025-10-21 | 50 | +2.643 | +0.244 | −0.524 | True |
| 75 | 2025-06-16 | 75 | +2.610 | +0.276 | +6.649 | True |
| 100 | 2025-01-16 | 100 | +2.610 | +0.276 | +3.535 | True |

**Both: `split_sensitive: true`, `pass_fraction` 0.8 (4 of 5 graded).**

Per `PROCESS.md` § E4 that is a **REFUSAL, not a caveat.** Neither cell proceeds.

⚠️ **But the DIAGNOSIS is different from the precedent, and the difference
matters for what to do next.** The failing target is **25 on both legs**, and on
both the reason is `ΔOOS = 0.0000` — **the lever did not fire at all** in a
25-trade OOS window, and a zero delta cannot clear `beats`, which requires a
strict improvement in net_R or maxDD. So this is *inertness at small n*, not a
verdict flipping on performance. Contrast the case that motivated the tool
(`sol_pullback_2h`, 2026-08-19): there ΔOOS swung **5.14× and changed sign**
(+1.1645 → −1.6908). Here the ΔOOS range is 0.000–0.938 and 0.000–0.618, and
every non-zero value has the same sign.

That is a milder failure, and it is still a refusal — a cell that is inert in
one of five plausible windows has not demonstrated it would have acted in live.
**It is also a testable prediction:** if inertness at n=25 is the whole cause,
the same cell graded on a longer OOS window should stay `pass_fraction 1.0`, and
`rr_min_p10` per leg says which floors are reachable at all. Neither is done here.

---

## 4. What this does and does not close

**Closes:** M31 P5 precondition **3b** — the walk-forward has been RUN, on real
data, through the canonical gate, with the results recorded. It was previously
blocked on candle data that turned out to be a fetch away
(`PB-20260817-RR-FLOOR-LEVER-UNEXERCISABLE-ON-COMMITTED-DATA`).

**Does not close:** precondition **2b** — the live final-MFE population at
**n = 1**. That is a soak-depth problem and no amount of historical candle data
touches it, exactly as the backlog rows predicted. The M31 P4 parity check stays
blocked on the live side.

**Does not license anything, and now for a measured reason rather than a
missing one.** The dispersion test WAS run (§ 3) and **both Path-A passes are
`split_sensitive: true` — a refusal.** A Tier-3 `rr_floor` declare would need, at
minimum: `split_sensitive: false`, an OOS window whose base is not unprofitable
(`trend_donchian`'s is −16.135), and the operator's approval against specific
numbers. None of those three holds today.
