# breakout_1 exit geometry: MFE evidence (lane E65)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Question (checklist row E65):** for breakout_1's two legs, `trend_donchian_eth_prop` and
`trend_donchian_sol_prop`, what is the MFE distribution (in R) of their trades, and what exit
geometry does it support, net of the full cost stack?

**Tier:** this document is Tier-1 evidence. The geometry it recommends is a **Tier-3 proposal**,
filed with `next_action: ask_operator`. `config/strategies.yaml` is not edited.

---

## §0 PRE-REGISTRATION: committed before any candidate geometry was run

This section was committed and pushed, and the PR opened, **before** any of the candidate runs
below executed. The results sections were added in later commits. The commit history of this
file is the registration record.

### 0.1 A fact that changes what "current geometry" means

`src/prop/breakout_ticket.py:18` tells the placer: *"Do not manage the exit: the broker-side
bracket is the exit."* The prop account is manually bridged. **Nothing trails the stop, and
nothing applies the stale-exit or trail-decay levers on breakout_1.** The design doc says the same
(`prop-dynamic-exits-faster-banking-DESIGN.md` §3: *"it's frozen at the entry SL unless a human
trails it"*).

The committed evidence records (E55, `comms/strategy_evidence/trend_donchian_*_prop.json`) model
`trail_mult 3.5` and, for ETH, `stale_exit_bars 12` and trail decay. **That is how the Bybit
server-monitor would manage the trade, not how the prop account executes it.** So this lane
evaluates the prop legs **as executed**: a static bracket.

### 0.2 Geometries (fixed now, not chosen after seeing results)

All runs use `scripts/backtest_trend.py` on the leg's own YAML entry params (donchian 20, atr 14,
atr_stop_mult 2.5, min_confidence, long_only for SOL), the default full cost stack (fee 7.5 bps
round trip, slippage 5.0 bps, funding 1.0 bps per window), and **one candle file per leg**: 365
days of 1h Binance-vision candles fetched once and reused across every geometry.

| id | what | harness flags beyond the entry params |
|---|---|---|
| **T0** | the E55 record's geometry (trail and stale levers as the YAML declares). **Reference only: not executable on prop.** | exactly `regime_debt_matrix.build_harness_cmd` |
| **M**  | MFE probe: static SL, **no TP**, no trail | `--trail-mult 1000 --timeout-bars 720` |
| **B0** | **prop as executed today**: static SL + TP = min(entry ± 9.9%, 6R) | `--trail-mult 1000 --tp-cap-pct 0.099 --tp-r 6.0 --timeout-bars 720` |
| **B1** | static SL + TP 2R | as B0 with `--tp-r 2.0` |
| **B2** | static SL + TP 3R | as B0 with `--tp-r 3.0` |
| **B3** | the ExitPlan ladder shape: 50% banked at 1.5R, remainder TP 3R | as B2 plus `--bank-frac 0.5 --bank-at-r 1.5` |

B3 differs from B2 by exactly one lever (the rung), so it is judged against B2 as well as B0.
`--timeout-bars 720` (30 days) stands in for "no timeout", because prop has none. A timeout exit
is reported separately: it means the trade would still be open after 30 days.

### 0.3 What is reported

- **MFE** (from M, and separately from the committed E55 trades files): n; p25/p50/p75/p90; the
  fraction of trades reaching 1R, 1.5R, 2R, 3R and the B0 TP level **before the stop**; bars to
  MFE and bars to first reach each level. A level counts as reached on a bar only if that bar did
  not also take the stop (the harness's SL-first convention).
- **Per geometry and leg:** n, pooled net R (full cost stack), fee-only net R, maxDD in R, bars
  held p50/p90, `net_r_per_capital_day`, exit-reason counts, and net R in each of **4 calendar
  folds** (four equal calendar windows over the candle file, trades assigned by entry time).
- **Prop ruleset** (`config/prop_rulesets/breakout.yaml`: daily loss 3% = $150, static DD floor):
  both legs' trades under the same geometry, merged by exit time, at **$75 risk per trade**
  (E59 measured: 1.5% × nominal $5,000).
  - Daily loss: number of UTC days (reset shifted 00:30 UTC) whose realized loss is ≥ $150.
    Realized P&L only; the intraday equity excursion of an open trade is **not** modelled.
  - P(breach within N trades): iid bootstrap of the merged per-trade $ outcomes, 20,000 paths,
    seed 65, N ∈ {10, 25, 50}, starting cushion ∈ {**$94.76** (E59 measured distance to the floor),
    $300 (a fresh account)}. Breach = cumulative realized loss ≥ cushion at any point.

### 0.4 DECISION RULE (lane-registered)

This rule is registered by this lane for this question. It is **not** the M20 Path B floor, which
the operator has not set (exit-refinement skill), and it does not replace that gate.

A candidate **Bk ∈ {B1, B2, B3}** is ELIGIBLE to be proposed over **B0** only if all of these hold:

- **R0 (denominator).** Each leg has ≥ 25 trades under B0 (`MIN_OOS_TRADES`). A leg below that
  gets `insufficient_base`, which is not a pass.
- **R1 (Stage 0).** Pooled net R of Bk > 0 on each leg, net of the full cost stack.
- **R2 (return non-inferiority).** On each leg, net R(Bk) ≥ net R(B0), **or**
  net_r_per_capital_day(Bk) > net_r_per_capital_day(B0) **and** net R(Bk) ≥ 0.75 × net R(B0)
  (the 0.75 applies only when net R(B0) > 0; when net R(B0) ≤ 0, net R(Bk) ≥ net R(B0) is required).
- **R3 (time in market, the operator's stated reason).** p90 bars held under Bk < p90 bars held
  under B0, on each leg.
- **R4 (walk-forward).** On each leg, the number of folds with net R > 0 under Bk ≥ the number
  under B0.
- **R5 (prop survival).** Account-level P(breach within 25 trades | cushion $94.76) under Bk ≤ the
  figure under B0, and the count of days with a realized loss ≥ $150 under Bk ≤ the count under B0.

Among the ELIGIBLE candidates, the one with the highest account-level `net_r_per_capital_day`
is recommended. **If none is eligible, the recommendation is "no geometry change is supported"**,
and it is recorded as an honest negative. Separately, if **B0 itself fails R1**, that is reported
as its own finding: the prop legs would then have no Stage-0 evidence for the way they actually
execute.

*(Results follow in §1 onward, added after the runs.)*
