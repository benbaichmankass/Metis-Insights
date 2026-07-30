# Exit-capture deep-dive — MFE-vs-realized, 14-day live evidence (2026-07-30)

> **⚠️ ROOT-CAUSE PREMISE DISPROVEN (2026-07-30, concurrent-session finding —
> `BL-20260730-EXITCAPTURE-DEEPDIVE-WRONG-TPSL-PREMISE`).** This memo attributes
> the scalp exit leak to `BYBIT_TPSL_MODE=full` and treats `partial` as a fix
> "deployed today (05:37Z)." **That is wrong.** `BYBIT_TPSL_MODE=partial` was
> ALREADY live from ~2026-07-21 — verified three ways (`.env`, the unit's
> `EnvironmentFiles`, `/proc/<MainPID>/environ`) and corroborated by the journal
> (75 of 211 pre-"flip" opens already carried a non-NULL `sl_order_id`, a column
> written ONLY on the partial branch; the 05:37Z change was a **no-op
> re-assertion**, not an activation). So the pre/post-flip comparison below is
> **not an A/B** (both sides were partial mode) and the `full`-bracket-bug root
> cause is **unproven** — the scalps hold hours + close `reconciler_filled`
> *under partial mode*. The real cause is still open (see the concurrent
> session's items: the Bybit partial-leg desync/naked-bracket blindspot fixed in
> PR #8000 / `BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT`, plus
> `BL-20260730-CLOSED-TRADE-NULL-EXITPRICE-PNL` and
> `BL-20260730-TRADES-TIMESTAMP-FORMAT-MIXED`). **The MFE/giveback/round-tripper
> METRIC below (and the standing `execution_capture` review metric it seeded)
> stand; the TPSL-mode root-cause attribution does not.** Kept as-is below for
> the record; read it through this correction.

**Operator directive (2026-07-30):** "We're bleeding… trades get very close to
take-profit then snap back to the stop-loss instead… come up with a metric for
max unrealized PnL vs actual PnL and figure out what we're doing wrong to not
capture the value." This memo is the P1 evidence read of the
[`exit-refinement`](../../.claude/skills/exit-refinement/SKILL.md) pipeline.

Tool: `scripts/research/m20_exit_analysis.py --since-days 14` on the trainer VM
(the box with the `datasets-out/market_raw` candle store). Real / paper / prop
are **never blended**; reconciler/superseded/adopted-orphan artifact rows
excluded. R is multiplier-aware.

## The metric (what "capture" means here)

Per closed trade, over the trade's own bar path (entry→close):
- **MFE** = maximum favorable excursion in R (how close to TP it got).
- **giveback** = `MFE − realized_R` (value reached but not kept).
- **round-tripper** = went `MFE ≥ 1.0R` in favor then **closed negative** — the
  literal "near-TP then snap to SL" trade the operator described.
- **hold_h**, **chop_frac**, **time-to-MFE** = context.

## Headline (14 days) — DOLLARS are the truth, R is a diagnostic

**⚠️ Correction (2026-07-30, operator-flagged):** an earlier draft reported real
money as "+8.9R, positive." That was **wrong** — it used the *journal* R-metric,
which for `bybit_2` is unreliable (the `BL-20260713` netting / sub-account
under-recording), and was inflated by a **phantom** journal entry (trade 4076
`ict_scalp_5m` BTC logged +24.28R, while the Bybit exchange-fills wallet shows
BTC net **−$3.67**). R is risk-normalized and excludes fees and funding, so a
positive R-sum can sit on top of a losing dollar account. **Dollars are the
scoreboard; R is only for diagnosing exit mechanics.**

| book | n | sum R (diagnostic) | **actual $ (14d)** | source |
|---|---|---|---|---|
| **real money (`bybit_2`)** | 16 | +8.9R *(journal artifact)* | **−$28 to −$31** | exchange fills (BTC −3.67, ETH −24.77, XRP +0.32) + journal daily (−30.88) agree |
| **paper (full soak)** | 82 | −79.1R | **−$22.7k (7d)** | journal |

`bybit_2` lifetime (broker wallet-truth, to 2026-07-13): **−$262.52** (fees
−$147.81). The dominant real-money bleeder over the window is the **ETH short**
(`eth_pullback_2h`, −$24.77), not the scalps.

The exit leak the operator described is real and system-wide in the R-diagnostic:
**~1 in 5 trades round-trips**, average giveback 2.4–3.8R — that part stands. But
it explains the *shape* of the losses, not a positive account.

## Where the leak concentrates — the altcoin scalps

| leg (paper) | n | sum R | round-trippers | avg giveback | avg hold |
|---|---|---|---|---|---|
| `ict_scalp_avax_5m` | 6 | **−12.0R** | **50%** | 9.05R | 12.7h |
| `ict_scalp_xrp_5m` | 6 | **−19.0R** | **50%** | 5.13R | 5.8h |
| `ict_scalp_sol_5m` | 3 | +0.4R | 33% | 3.85R | 14.2h |
| **`ict_scalp_5m` (real, BTC)** | 3 | **+21.8R** | **0%** | 1.72R | 3.2h |

A **5-minute scalp held 6–14 hours** is the smoking gun: the TP/SL bracket is
not executing per-trade — the position sits open until a reconciler/time event
closes it, long after MFE has round-tripped.

## Root cause (high confidence): `BYBIT_TPSL_MODE=full` shared-bracket replacement

`BYBIT_TPSL_MODE` is at its default **`full`** on the live VM. Under `full`,
Bybit one-way netting gives the whole netted position **one** position-level
TP/SL, and **each new same-symbol open REPLACES it** (`BL-20260720-ICTSCALP-PASTSTOP-EXITS`).
On the soak account (`bybit_1`) the scalps fire constantly on the same symbol —
`xrp_5m`+`xrp_15m` share XRPUSDT, `sol_5m`+`sol_15m` share SOLUSDT, and even a
single leg firing repeatedly collides with itself — so older trades lose their
bracket, sit open, and give back their MFE.

**The control that proves it:** `ict_scalp_5m` on real-money `bybit_2` has **no
same-symbol sibling** (it's the only scalp on bybit_2). Its bracket survives —
**0% round-trippers, +21.8R, 3.2h hold.** Same strategy, clean exits, wins.

The fix — **`BYBIT_TPSL_MODE=partial`** (qty-scoped bracket per trade) — is
already built (`src/units/accounts/execute.py`) and Tier-3-gated on the
`validate-partial-tpsl` demo action. It is **not deployed** (default `full`).

## Counterfactual: a time-stop "recovers" +14.6R in paper…

A 4h flat time-stop recovers +14.6R in paper, concentrated in the leaking legs
(`avax_5m` +5.84R, `xrp_5m` +4.8R). **This is a symptom-cut, not the fix** — it
only helps because the trades are stuck open for hours. Fixing the bracket
(partial mode) removes the stuck-open state at the source; a time/stagnation
stop is a candidate *secondary* lever to sweep afterward (`MB-20260728`).

## Not an exit problem for the trend/pullback fleet

The M20 coverage matrix (`docs/research/exit-refinement-coverage.json`) already
records `honest_negative` for trail/stale/giveback/ladder on most trend and
pullback legs — mechanical exit levers there don't beat baseline. Their bleed is
**entry-selection**, not exits. Don't chase exit levers on that fleet.

## Plan (maximize capture)

1. **Deploy `BYBIT_TPSL_MODE=partial`** — highest leverage. `validate-partial-tpsl`
   on bybit_1 demo → operator flips `set-env BYBIT_TPSL_MODE=partial` (Tier-3).
   Expected: hold times collapse to minutes, round-trippers drop, scalps capture
   their MFE.
2. **Standing capture metric** — wire `roundtrippers% / mean_giveback / mean_hold_h`
   per strategy into `/performance-review` + `/system-review` (source:
   `m20_exit_analysis`). Watch capture continuously, not ad hoc.
3. **Re-soak & re-measure** the alt scalps on clean brackets, then graduate the
   legs that clear the ≥20–30-trade gate (`PB-20260721`). Operator chose
   **fix-exits-first** over graduating now (2026-07-30).
4. **Scalp exit-lever sweep** (`MB-20260728`, currently `blocked:no_harness_levers`)
   — partial-TP ladder / giveback-cap for the residual giveback.
5. *(Secondary, entries)* `slv_trend_1h` fired conf 0.06/0.15 "should-skip"
   losers → confidence floor / regime gate. Lower priority than exits.

## Corrected book-level picture (per-account, per-strategy) — the real state

Per-account journal-realized $ (artifacts excluded), reconciled against
exchange-fills wallet truth:

| account | role | 14d $ | 30d $ |
|---|---|---|---|
| **`bybit_2`** | **REAL money** | **−$30** | **−$30** |
| `bybit_portfolio` | paper mirror of bybit_2 (~$87k) | −$12,597 | −$12,597 |
| `bybit_1` | crypto soak fleet | −$14,750 | −$25,065 |
| **`alpaca_portfolio`** | paper mirror of the equity live-portfolio | **+$3,953** | +$3,953 |
| `alpaca_paper` | equity soak | −$2,616 | −$4,216 |

**Crypto is losing at every scale and in every strategy** (bybit_portfolio 30d):
`eth_pullback_2h` −$5,601 · `ict_scalp_5m` −$4,025 · `trend_donchian` −$1,705 ·
`trend_donchian_xrp_4h` −$938 · `xrp_pullback_2h` −$329. On real `bybit_2` the
same shape: `eth_pullback_2h` −$24.65 dominates, scalps ≈ flat. **This is a
strategy-edge problem, not only an exit problem.** Note `ict_scalp_5m` is
−$4,025 at portfolio scale — the +24R "win" that inflated the R-metric does not
survive at scale, so **the scalps are not clean winners and should not be
graduated to real money on this evidence.**

**The equity portfolio mirror is net-positive (+$3,953) but ENTIRELY carried by
one strategy:** `uso_trend_1h` **+$9,730** vs `slv_trend_1h` −$2,299 /
`tlt_pullback_1h` −$2,161 / `gld_pullback_1h` −$1,093 / `spy_pullback_1h` −$224.
So it is not "equities work" — it is "one oil-trend strategy had a big run and
everything else lost." And `alpaca_live` is **dry ($0)** — none of it is real.

**Bottom line:** the system is not currently profitable at any scale except one
oil-trend strategy whose edge is unverified (2 trades). Real money (`bybit_2`) is
down ~$30/14d, ~$262 lifetime. The exit-bracket fix (below) is real and worth
shipping, but it is a *contributing* fix to the crypto scalps, not the cure for
the bleed.

## Reset priorities (highest real-money leverage first)

1. **`eth_pullback_2h`** — top real-money bleeder (−$24.65 real, −$5,601 mirror).
   Pull its M7 review / backtest; if it fails net-of-cost, **demote to shadow**
   (Tier-3, operator).
2. **Crypto-book edge audit** — every crypto strategy is red at scale. Backtest-
   truth read of which crypto legs have *any* net-of-cost edge; demote the rest.
3. **Validate `uso_trend_1h`** — is +$9,730 a real edge or one oil spike (2
   trades)? Backtest before it's a deploy candidate.
4. **Scalp exit fix (shipped)** — keep + re-measure capture, but **do not
   graduate scalps to real money** (−$4k at scale).
5. **Deployment** — `alpaca_live` is dry. If a validated equity edge exists, the
   real opportunity is deploying live equity capital (operator/Tier-3), not more
   crypto scalps.

## Validation is BACKTEST-gated, not soak-days (operator correction 2026-07-30)

The fast-gate doctrine applies: **the confidence gate is the backtest, run and
implemented immediately** — not "wait days for a live soak to accrue N trades."
Live is for **mechanical verification only** (does the per-trade bracket fire
under partial mode) — one or two trades, **hours not days**. An earlier draft
wrongly framed graduation as "re-soak for a few days"; corrected here.

**Graduation gate, per leg, rechecked every review, graduated the moment it
passes (not batched):**
1. **Backtest PASS net of REAL costs** — fees AND funding (the M27 net-of-fee
   k-fold exists; funding must be included because it is a live-only drag the
   backtest may omit — see the translation gap below).
2. **Mechanical verification** — one clean fill on the fixed (`partial`-mode)
   bracket confirming TP/SL fires per-trade (hours).
3. → graduate that leg's routing to `bybit_2` + `bybit_portfolio` immediately
   (Tier-3, ping the operator), account_compat_matrix re-run for `bybit_2`.

Tracked as a standing backlog item (`PB-20260721`), rechecked each
`/performance-review` + `/system-review`; **each leg that clears is graduated on
that pass**, not held for a batch.

## The paper→live translation gap (the important one)

The operator's insight: if the paper mirror makes money but live does not, the
gap itself is the finding. The exit deep-dive already isolated **one** cause —
the `BYBIT_TPSL_MODE=full` bracket bug (now fixed) made live diverge from the
per-trade-bracket behavior the backtest assumes. Residual gap candidates to
quantify next: **funding** (bybit_2 perp funding — `MB-20260717-M24-FUNDING-VISIBILITY`
reports 0 funding records, a real visibility hole), **fees** (−$147.81 lifetime),
and **slippage/fill quality**. Graduating more perp legs to `bybit_2` before this
gap is understood risks adding more cost drag — so quantifying it is a
graduation prerequisite, not a nice-to-have.
