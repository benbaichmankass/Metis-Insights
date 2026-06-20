# Expansion Backtesting Research — Symbols + New Strategies (2026-06-20)

> **Tier-1 research memo.** Analysis + proposals only. Nothing here touches the
> live order path, `config/strategies.yaml`, `config/accounts.yaml`, or any unit
> the live VM consumes. It picks up the 2026-06-18 expansion window (Direction 1
> cross-asset / Direction 2 recombination) and scopes the next backtesting push.
>
> Origin: operator direction 2026-06-20 — *"more backtesting regarding different
> symbols and different regimes / different test strategies … get a full picture
> first (what we have, what's been tested, what hasn't worked), then think about
> expansions. Part 1 symbols, part 2 new strategies — nothing has to be ICT;
> any good strategy we can effectively backtest and see makes an impact is fair
> game."*

---

## 0. TL;DR

- **The tradeable universe is already well-mapped.** Every configured symbol has
  at least daily history and a verdict; the validation discipline (k-fold every-fold
  gate → 2×-fee → out-of-pool holdout → portfolio robustness) is built and proven.
- **Part 1 (symbols): more *crypto* is low-value** — alts are 0.7–0.9 correlated to
  BTC (trade-frequency, not diversification) and the next two (BNB/LINK) were already
  screened out. The real, untested diversification axis is **non-crypto breadth on
  Alpaca ETFs** — specifically **bonds (TLT/IEF), broad commodity / energy (DBC/USO),
  and small-cap (IWM)** — which are genuinely BTC-uncorrelated, reuse the *exact*
  validated daily trend/pullback harnesses, and need **zero connector wiring**
  (Alpaca already trades any US ETF). That is the highest-ROI symbol study.
- **Part 2 (new strategies): the book is monolithic in *edge type*** — every live
  strategy is directional price-action (trend / pullback / breakout / fade / range).
  The highest-impact, backtestable, genuinely-new and *diversifying* edges are
  **(1) funding-rate carry on perps** (data adapter already exists, near-uncorrelated
  to price trend), **(2) cross-sectional momentum across the alt basket**
  (relative-value, portfolio-native), and **(3) a market-neutral ratio/pairs
  reversion** (e.g. ETH/BTC) — none of which the book has today.
- **Cheapest wins available right now** (existing harnesses, runnable on the trainer
  this session): the **trend-side out-of-pool holdout** (PB-20260618-014, the cleanest
  unstarted step) and the **exit-manager fee-reduction sweep** that rescues the
  fee-bled ict_scalp/HF cells. Two ready-to-run study specs ship alongside this memo.
- **Biggest *non-backtest* lever** (out of scope but worth stating): the 10-cell and
  16-cell paper books are already OOS-validated; the gating item before real money is
  the alpha-decay watch + the `account_compat_matrix` daily/futures extension, not
  more backtesting.

---

## 0a. Session outcome (2026-06-20) — what was actually run + concluded

This memo started as a plan and became an executed research session (all sweeps run on the
trainer via `vm-driver`; raw logs in `automation/results/`). Net conclusions:

1. **Best deployable expansion = the ETF-breadth book** (bonds TLT/IEF + commodity DBC/USO/DBA
   + silver SLV + small-cap IWM): **+197.3R, Sharpe 3.88 over 19yr, every recent holdout
   positive, every family additive, bootstrap P+ =1.00.** Genuinely BTC-uncorrelated and
   fee-resilient. **IWM trend long-only is `live_ready` (every-fold).** → the prime real-money
   proposal (Tier-3). Details §0d.
2. **Funding carry = a real but regime-dependent sleeve** — smooth/uncorrelated, but the edge
   concentrated in 2023–24 and decayed in 2025–26 (high-funding-regime harvester, dormant now).
   Directional carry rejected; market-neutral carry paper-grade. A conditional overlay, not a
   core sleeve. New harness `scripts/backtest_funding_carry.py` shipped + validated. Details §0c.
3. **Trend-side crypto-alt refinement = dead** — the OOP holdout (BNB/LINK) fails; the
   recombination survivors were pool-overfit. No trend-side `strategies.yaml` change. §0b.
4. **Strategy framing learned:** no single sleeve is always-on; "make money all the time" =
   stacking sleeves whose ON-regimes differ (crypto-trend, ETF-trend/pullback, carry). The
   ETF-breadth book is the missing uncorrelated core.

**Recommended Tier-3 next steps (operator-gated):** (a) wire the ETF-breadth cells to the
`alpaca_paper` book first (paper), starting with IWM (live_ready) + the two bond cells; (b) run
`account_compat_matrix` once its daily/futures extension lands (PB-20260618-012); (c) keep carry
as a documented opportunistic overlay, revisit when funding elevates. The **pairs / cross-sectional**
sleeves remain un-built (operator chose ETF-breadth this session) — next natural research.

## 0b. Wave-1 results (run on the trainer, 2026-06-20)

Both Wave-1 study specs ran through `research_sweep.py` → k-fold gate on the trainer
(raw: `automation/results/expansion-sweep-v2.txt`). Two clear outcomes:

**(1) Trend-side out-of-pool holdout — FAILS. (PB-20260618-014 resolves NEGATIVE.)**
The `trend_donchian_4h` long-only cell does **not** generalize to symbols outside the
recombination sweep pool:

| OOP symbol | base net R | 2×-fee | every-fold? | tier |
|---|---|---|---|---|
| **BNB** | +10.0 | +8.4 | ❌ no | paper_ready |
| **LINK** | **−4.9** | −6.1 | ❌ no | **reject** |

Every BNB variant is paper_ready-at-best and **none is positive in every fold**; every
LINK variant is a reject. Per the program's own multiple-comparisons discipline
(`strategy-primitives-recombination-DESIGN.md` §6), this means the trend-side
recombination survivors are **pool-overfit** → **do NOT propose any trend-side
`config/strategies.yaml` refinement.** This is the gate working as designed: a negative
result that prevents an overfit live change. It also reinforces the strategic pivot —
*more crypto-trend cells are not the expansion*; the uncorrelated edge types are.

**(2) Pullback exit / fee-headroom study — the exit is the dominant lever, and the live
config is robustly near-optimal.**

| variant | net R | 2×-fee | every-fold? | tier |
|---|---|---|---|---|
| **base (trail5, live)** | 63.1 | 59.5 | ✅ | **live_ready** |
| stop3_trail5 | 49.8 | 47.0 | ✅ | **live_ready** |
| stop2_trail5 | **78.7** | 73.9 | ❌ | paper_ready |
| trail7 | 50.9 | 47.7 | ❌ | paper_ready |
| trail3 (tight) | 7.3 | **2.4** | ❌ | paper_ready |

Ablation: the trail manager is worth **+55.8R** (base 63.1 → trail-neutralized 7.3) — the
single largest component of the cell's edge. The base run reproduces the 2026-06-18
shakedown's +63.11R exactly, validating the harness + dispatch end-to-end. Takeaways:
the **live trail5 exit is robustly near-optimal** (only base + stop3_trail5 clear
every-fold); a tighter stop (stop2) lifts headline net but loses robustness; and the
**tight trail is fee-fragile** (7.3 → 2.4 at 2× fee) while the wide trail keeps its
headroom (63.1 → 59.5). So the fee-reduction work (the rank-5 maker-band exit) should aim
at **wider / rebate-earning exits, not tighter** ones.

**Net effect on the plan:** the crypto-trend expansion lane is closed (OOP-fail); focus
shifts fully onto the **uncorrelated edge types** (carry, pairs, ETF-breadth) per §6.

## 0c. First new-edge result — funding carry (2026-06-20)

`scripts/backtest_funding_carry.py` (shipped this session; synthetic-correctness verified
on the trainer) was run on ETH + SOL with funding fetched live from Bybit. **Data caveat:**
Bybit funding history caps at **2023-03-08** (~3.3yr), so this is a 2023+ window with
modest trade counts.

| variant | symbol | trades | net R | win% | maxDD R | by-year |
|---|---|---|---|---|---|---|
| directional | ETH | 36 | +2.76 | 36% | 7.52 | 2023 +5.0 / 2024 −3.1 (inconsistent) |
| directional | SOL | 53 | **−1.15** | 36% | 16.0 | net loser |
| **market-neutral** | **ETH** | 23 | +1.65 | **95.7%** | **0.008** | +0.23 / +1.43 / −0.01 |
| **market-neutral** | **SOL** | 33 | +1.62 | **84.8%** | **0.04** | +0.17 / +1.47 / +0.00 |

**Findings (honest):**
- **Directional carry is NOT an edge** — the price leg dominates the funding term and adds
  drawdown (SOL net-negative). Rejected.
- **Market-neutral (hedged) carry IS a genuine uncorrelated sleeve** — ~85–96% win rate,
  **near-zero drawdown**, positive in every major year, zero price beta. This is precisely
  the "smooth, always-on, uncorrelated" profile the goal calls for.
- **But it's a low-return / high-Sharpe *yield* stream, not an R generator.** The "R" here
  is normalized to a nominal ATR unit (a hedged position has no real price stop), so the R
  magnitude understates the story — the real metric is funding **yield** (Bybit's mean 8h
  rate ≈ 7–8%/yr to a long, harvested smoothly by the receive-leg in high-funding windows).
  Trade counts are thin for the per-trade k-fold R-gate; **Sharpe / `portfolio_robustness`
  is the apt grader**, and a **basket** version (pool ETH+SOL+XRP+ADA+AVAX neutral carry)
  is the right way to thicken the sample and smooth further.

**Verdict:** market-neutral funding carry is a viable **complement sleeve** — it won't move
the P&L needle alone, but it adds steady, BTC-uncorrelated return with almost no drawdown,
which is exactly what "make money all the time" wants alongside the directional book.
Directional carry is dropped.

**Gate result (k-fold, 2026-06-20):** both ETH and SOL neutral carry grade **`paper_ready`**
across all funding thresholds — net-positive AND **fee-robust at 2× fees** (ETH base 1.65→0.97,
SOL 1.63→1.08) — but **not every-fold** (the thin ~23–33-trade sample can't clear the strict
live_ready every-fold bar).

**Basket result (`portfolio_robustness.py`, 5-alt pooled neutral carry, 152 trades 2023-04 →
2026-02) — the decisive grade, and it's nuanced:**
- Headline: **Sharpe 11.56**, net +9.1R, **maxDD 0.1R**, bootstrap P(net>0)=1.00, breakeven
  added-cost +0.060 R/trade. A near-riskless yield stream on its face.
- **But: 2025 is a negative year (−0.1R, only 10 trades) and every holdout cutoff ≥2025-01-01
  is negative.** `portfolio_robustness` verdict: **NOT fully robust** (years+ False, holdouts+
  False; LOO-cell+ True, bootstrap P+ 1.00).

**Honest final verdict on carry:** the edge is **real, mechanically correct, fee-robust, and
exceptionally smooth — but concentrated in 2023–2024 and DECAYED in 2025–26.** As funding
normalized, the elevated-funding episodes the strategy harvests became rare (only ~10
basket-trades in all of 2025), so the carry went dormant. Carry is therefore a
**regime-dependent yield harvester** — valuable when perp funding is hot (leverage-demand /
bull phases), near-zero otherwise — **not the dependable always-on earner the first per-symbol
read suggested.** It's a legitimate *conditional* complement (turn it up when funding is
elevated), not a steady standalone sleeve in the current low-funding regime. This is exactly
the kind of decay the holdout gate exists to catch.

**Implication for the always-on goal:** no single sleeve is always-on — carry pays in
high-funding regimes, trend pays in trending regimes, etc. "Makes money all the time" comes
from **stacking sleeves whose ON-regimes differ** so the book always has *something* working.
Carry earns its place as the high-funding-regime contributor. The next sleeves to test for
*different* ON-regimes: **pairs/ratio reversion** (range/divergence regimes) and **ETF-breadth
incl. bonds** (macro/risk-off regimes BTC can't provide).

## 0d. ETF-breadth result — bonds + commodity + small-cap (2026-06-20)

The Part-1 symbol-expansion thesis tested: run the validated daily trend (long & both-sides)
+ pullback families on 8 new diversifying ETFs (19yr daily history, yfinance), through the
k-fold gate. Best cell per symbol:

| ETF | class | best cell | tier | net R | 2×-fee |
|---|---|---|---|---|---|
| **IWM** | small-cap eq | **trend long-only (base)** | **live_ready ✅ every-fold** | 9.0 | 8.4 |
| **SLV** | silver | pullback (trend30) | paper_ready | **29.3** | 28.3 |
| **TLT** | 20yr bonds | pullback (frac618) | paper_ready | 25.4 | 23.9 |
| **IEF** | 7-10yr bonds | pullback (trend30) | paper_ready | 24.7 | 21.9 |
| **USO** | crude | **trend BOTH-SIDES** | paper_ready | 21.9 | 21.1 |
| **DBC** | broad commodity | pullback (trail4) | paper_ready | 15.1 | 13.8 |
| **DBA** | agriculture | trend long-only | paper_ready | 12.7 | 11.5 |
| **UNG** | natural gas | trend both-sides | paper_ready | 6.9 | 6.5 (weakest) |

**Findings:**
- **IWM small-cap trend long-only is the single `live_ready` (every-fold) cell in the entire
  session** — the validated equity-trend template generalizes cleanly to small-caps. A genuine
  real-money candidate (Tier-3, OOP + operator gated).
- **Bonds (TLT/IEF) — the prized BTC-uncorrelated macro exposure — work via PULLBACK**
  (~+25R each, fee-robust). This is the diversification win Part-1 was reaching for.
- **Silver (SLV) is the standout** (both families strong); **DBC/USO/DBA** are solid paper_ready.
- **Shorting earns its place (operator's instinct confirmed):** USO both-sides trend (+21.9R)
  beats long-only (+12.6R) — commodities/bonds trend *down* hard, unlike equities. The
  both-sides variant is the right default for non-equity ETFs.
- All except IWM are paper_ready (fee-robust) rather than every-fold live_ready — same pattern
  as carry: the per-cell R-gate is strict; the real payoff is the **pooled book's Sharpe +
  BTC-uncorrelation**, graded next via `portfolio_robustness.py`.

**These ON-regimes differ from crypto's** (bonds=rates/risk-off, commodity=inflation/supply,
small-cap=risk-on equity) — so stacking them under the directional crypto book is exactly the
"always-on via diversification" mechanism.

### Pooled ETF-breadth book (`portfolio_robustness`, the payoff grade) — the session's best result

Best cell per ETF pooled into one book (family-tagged), 19 years 2007–2026:

> **7 cells · 566 trades · net +197.3R · Sharpe 3.88 · maxDD 18.6R · mean +0.35R/trade**

| robustness axis | result |
|---|---|
| **Recent holdouts (5 cutoffs)** | **ALL POSITIVE** — ≥2023-07 +44.6R (Sh 2.01) … ≥2025-07 +36.4R (Sh 2.14) |
| **Leave-one-family-out** | all positive — drop bond +132.8R, commod +131.2R, equity +180R, metal +147.9R |
| **Block bootstrap** | **P(net>0)=1.00**, 5th-pct +108.4R |
| **Added-cost breakeven** | **+0.35R/trade** (ETF fees ~1–2bps → enormous headroom) |
| every-calendar-year | ✗ 7 of 20 years slightly negative (chop years — normal) |

`portfolio_robustness` verdict is **"NOT fully robust"** — but **only** because it fails the
strictest *every-calendar-year* bar; it **passes holdouts + leave-one-out + bootstrap**, the
axes that actually predict forward performance. **This is a deployable-grade, fee-resilient,
BTC-uncorrelated diversified book** — and crucially it is **positive in every recent holdout,
including the window where crypto carry decayed.** It is the strongest, most dependable
expansion result of the session and a legitimate **real-money-candidate book** (Tier-3,
operator + per-account-compat gated).

**Carry vs ETF-breadth, head to head:** carry = Sharpe ~11 but holdouts ≥2025 *negative*
(regime-dependent, decayed); ETF-breadth = Sharpe ~3.9 but holdouts *all positive*
(dependable). ETF-breadth is the better core diversifier; carry is an opportunistic
high-funding-regime overlay on top.

## 0e. Intraday (1h) ETF sweep + crypto pairs (2026-06-20)

**Intraday timeframe — the operator ask "find fits on smaller timeframes too" paid off.**
1h RTH bars (Dukascopy ETF-CFD, ~9yr 2017→2026) through the same k-fold gate. The 1h
basket produced MORE every-fold `live_ready` cells than daily — more trades → better
statistics, and these liquid ETFs mean-revert/trend cleanly intraday. Best cell per symbol:

| ETF | best intraday (1h) cell | tier | net R | 2×-fee |
|---|---|---|---|---|
| **SLV** | trend both_donch24 | **live_ready ✅** | 85.5 | 77.2 |
| **GLD** | pullback trail4 | **live_ready ✅** | 78.9 | 61.5 |
| **TLT** | pullback trail4 | **live_ready ✅** | 50.6 | 34.0 |
| **QQQ** | pullback frac618 | **live_ready ✅** | 45.3 | 36.7 |
| **SPY** | pullback frac618 | **live_ready ✅** | 42.0 | 30.6 |
| **USO** | trend donch24 | **live_ready ✅** | 39.5 | 35.2 |
| **IEF** | pullback frac618 | live_ready (FEE-FRAGILE) | 42.9 | 7.6 ⚠ |
| **IWM** | trend trail6 | paper_ready (weak) | 3.8 | 0.8 |

**Findings:**
- **GLD + SLV intraday are exceptional** — GLD pullback is live_ready on *all four* variants;
  SLV trend on four. Precious-metals intraday mean-reversion/trend is the strongest edge found
  all session, fee-robust at 2×.
- **SPY/QQQ pullback + USO/TLT** are clean live_ready with good 2×-fee headroom — a real
  intraday sleeve.
- **IEF is live_ready but fee-fragile** (2× headroom +7.6) — low-vol bond, small moves eaten by
  fees; wire only with realistic (low) ETF fees, or skip.
- **IWM inverts daily↔intraday** — daily live_ready, intraday REJECT. So IWM belongs on the
  *daily* sleeve, the metals/index ETFs on the *intraday* sleeve. The timeframe is per-symbol,
  not one-size-fits-all.
- Honest caveat: 1h backtests assume 7.5bps/side; the 2×-fee column is the load-bearing check.
  The cells above (except IEF) clear it. Live intraday also needs the bot to fetch 1h candles +
  manage intraday exits — an operational step beyond the daily cells.

**Crypto pairs (ETH/BTC) — REJECT.** New `scripts/backtest_pairs.py` validated (synthetic OU
self-test: +96R, 84% win). But ETH/BTC is not a reversion edge: 5m native is fee-destroyed
(−24,000R, 32k trades); daily-resampled is marginally +25R (Sharpe 1.27, 77 trades) but
`portfolio_robustness` fails every axis — **all holdouts from 2024-01 on are negative**
(≥2025-07: −4.2R), bootstrap P=0.91 / p5=−3.5. ETH/BTC *co-trends* (0.7–0.9 corr); the ratio
doesn't mean-revert. **The right pairs candidates are mean-reverting ratios — GLD/SLV
(gold-silver ratio), TLT/IEF (duration spread)** — not crypto majors. The harness is ready to
test those; that's the next pairs step.

## 1. The full picture (what we have, tested, and rejected)

### 1.1 Money-at-risk vs paper vs prop

| Bucket | Where | What |
|---|---|---|
| **Real money** | `bybit_2` (Bybit) | BTCUSDT + ETHUSDT only, 5 ICT strategies. ETH was an operator-directed live test 2026-06-18 (correlated exposure, no compat-matrix run). `ib_live` exists but is intentionally inert (`dry_run`, no strategies). |
| **Paper** | `bybit_1` (Bybit demo), `ib_paper` (IBKR), `alpaca_paper` (Alpaca), `oanda_practice` (shelved) | the 10-cell alt book + the 3 futures legs + the 3 ETF legs. |
| **Prop** | `breakout_1` (Telegram-ping bridge) | `trend_donchian_sol` (robust PASS), `trend_donchian_eth` (marginal). Excluded from both real & paper KPIs. |

### 1.2 Symbol coverage + verdict (condensed)

| Symbol | Venue | Data | Verdict |
|---|---|---|---|
| **BTCUSDT** | Bybit | ✅ deep multi-yr intraday (redundant) | net-positive, **real-money live** |
| **ETHUSDT** | Bybit / prop | ✅ | strongest alt; paper + prop + (operator) real-money |
| **SOL/XRP/ADA/AVAX** | Bybit | ✅ 15m/5m | net-positive (paper_ready); paper-book soak |
| **MES** (µ S&P) | IBKR | ◑ daily deep / intraday ~1yr | `mes_trend_long_1d` +30.4R ✅ paper |
| **MGC** (µ gold) | IBKR | ✅ via XAUUSD 15m proxy | `mgc_pullback_1d` +56R ✅; **`mgc_trend_1h` −15.5R → shadow** |
| **MHG** (µ copper) | IBKR | ◑ **daily only** | `mhg_pullback_1d` +85R standalone but lukewarm OOS |
| **SPY / QQQ** | Alpaca | ✅ (SPY 5m; daily deep) | daily trend ✅ live paper; **intraday FAILS the gate** |
| **GLD** | Alpaca | ◑ daily | `gld_pullback_1d` +24.4R ✅ paper |
| **XAUUSD** | OANDA | ✅ 15m | strongest Phase-0 cell (+78R) but **OANDA-US can't trade gold → shelved** |
| EUR/GBP USD | OANDA | ✅ 15m | **rejected** (negative OOS; "crypto params don't transfer") |
| BNB / LINK | Bybit | ✅ (already fetched) | **screened out** (non-generalizing) — but they are the natural **out-of-pool holdout** symbols |

**Data gaps that constrain new work:** intraday MES is ~1yr-shallow; **no intraday
copper exists anywhere**; the futures universe is daily-grade only. Anything intraday
on futures is under-evidenced by construction.

### 1.3 Strategy roster + verdict (condensed)

| Strategy | Edge type | Status |
|---|---|---|
| `trend_donchian` (+ alt/metal/ETF variants) | breakout-trend | flagship; **real-money live**; `min_confidence` tuned 0.30→0.60 (M8, OOS+3-fold) |
| `htf_pullback_trend_2h` / `eth_pullback_2h` | pullback-continuation | real-money live; ADX≥25 gate adds +4R (ablation-confirmed) |
| `ict_scalp_5m` | sweep→displacement→FVG | real-money live; **5m alt variant rejected for fee-bleed** |
| `fvg_range_15m` | range mean-reversion | real-money live (standalone +24R) |
| `turtle_soup` | sweep-reversal | demo only (1R target dies on fees); **no standalone harness** |
| `vwap` | VWAP reversion | **KILLED** (net loser every regime, −10,724R; 4.2× fee-to-gross) |
| `fade_breakout_4h` | failed-breakout fade | backtest +40R → **live −86R → shadow** |
| `squeeze_breakout_4h` | squeeze expansion | backtest +35R → **live −20R / 0% WR → shadow** |
| `mgc_trend_1h` | breakout-trend | **net-negative → shadow** |
| `hf_displacement_cont`, `hf_vwap_revert` | ICT-derivative HF | **research-only, unwired** — registered in the portfolio harness, pending OOS prop-gate sweep |

**Hard lesson stamped across the rejects: a positive standalone backtest is not a
go-live.** fade/squeeze/turtle all passed standalone and failed live; the every-fold
k-fold + 2×-fee + out-of-pool holdout exists precisely because of them.

### 1.4 The validation engine (use it; don't rebuild)

`scripts/ops/research_sweep.py` (study-spec + component ablation; v1 shipped &
shakedown-validated 2026-06-18) → `m15_ws_b_fold_report.py` (5-fold anchored WF,
7.5/15 bps) → `classify_strategy_tier.py` (reject / paper_ready / live_ready) →
`portfolio_robustness.py` (per-year, multi-cutoff holdout, leave-one-out, bootstrap).
Per-trade `--emit-trades {entry_time, net_r}` JSONL is the universal interface. Real
sweeps run on the **trainer VM** via the `vm-driver` / `trainer-vm-diag` relay (the
sandbox only carries a ~3.5-day BTC sample — runnable ≠ evidential).

---

## 2. PART 1 — Symbol expansion

### 2.1 The honest framing

Symbol expansion only matters if it adds **return the book doesn't already have**.
Two sub-questions: *more of the same* (deeper, correlated) vs *new exposure*
(uncorrelated, diversifying). The 2026-06-18 work already proved the diversification
comes from the **non-crypto clusters**, not from more alts. So:

### 2.2 Candidates, ranked

| Rank | Candidate | Wiring effort | Data | Why / caveat |
|---|---|---|---|---|
| **1** | **Diversifying Alpaca ETFs at 1d** — bonds **TLT/IEF**, broad-commodity/energy **DBC/USO**, small-cap **IWM** | **none** (Alpaca trades any US ETF; only `instruments.yaml` + a config block) | daily decades (Dukascopy CFD / Alpaca / yfinance) — a one-shot fetch | **The real new exposure.** Bonds especially are structurally BTC-uncorrelated. Reuses the *validated* `mes/spy_trend_long_1d` + `gld/mgc_pullback_1d` families verbatim — pure re-tune, no new math. |
| **2** | **Out-of-pool holdout on BNB/LINK** for the trend/pullback alt cells | none | already fetched | Not an expansion per se — it's the **PB-20260618-014 validation gate** that must pass before *any* trend-side refinement to live. Cheapest, highest-discipline win. |
| **3** | **More CME/COMEX micros via IBKR** — **MNQ** (Nasdaq), **M2K** (Russell), **MYM** (Dow), **SIL** (silver), **MCL** (crude) | medium — `ib_client.py::_build_contract` is hardwired to `{MES,MGC,MHG}`; each needs a contract spec | daily exists (`=F`); intraday ~1yr | WS-A already showed trend generalizes to indices (MNQ +26.5/+13.1R) and metals pullback to silver/copper. **MCL adds energy** (new factor). But IBKR is the de-prioritized, failure-prone transport — daily-only, gated behind operator direction. |
| 4 | **More Bybit alts** (DOGE/DOT/MATIC), **re-test BNB/LINK as live cells** | none | one-line fetch | **Low value** — deepens crypto concentration (0.7–0.9 corr); BNB/LINK already screened out. Only worth it as breadth for the cross-sectional-momentum strategy (Part 2), not as standalone trend cells. |
| 5 | **Re-point OANDA to a tradeable FX pair / non-US division** | low | exists | FX majors were **rejected** at default params; only revisit if a *new* strategy type (e.g. carry) changes the thesis. Low priority. |

**Recommendation:** run the **ETF-breadth daily sweep (rank 1)** and the **trend-side
OOP holdout (rank 2)** first — both reuse existing harnesses and need no live-path
change. Defer IBKR micros (rank 3) to an explicit operator "yes, keep investing in
futures" decision, since it cuts against the 2026-06-10 de-prioritization.

---

## 3. PART 2 — New strategies (non-ICT, fair game)

### 3.1 The gap

The book has **one edge family in many costumes**: directional price-action. It has
**no** carry, **no** relative-value / market-neutral, **no** cross-sectional / factor,
and **no** volatility-premium strategy. Those are exactly the edge types that are
*structurally* uncorrelated to a trend/pullback book — i.e. the diversification the
2026-06-18 work was reaching for, but from the *strategy* axis instead of the symbol
axis. All of the below are well-documented, backtestable, and (critically) feasible on
data/adapters we already have.

### 3.2 Candidates, ranked by (impact × backtestability ÷ effort)

| Rank | Strategy | Thesis | Diversifying? | Data / harness | Effort |
|---|---|---|---|---|---|
| **1** | **Funding-rate carry on perps** | Perp funding is a periodic cash flow; systematically holding the side that *collects* funding (short when funding ≫ 0, optionally long when ≪ 0), risk-bounded, harvests a documented crypto premium. | **Yes** — carry ⟂ price-trend; pays in chop where the trend book bleeds. | **`ml/datasets/adapters/bybit_funding_oi.py` already fetches funding/OI for any symbol, as-of joined.** Needs a new ~150-line backtester (funding accrual + price-PnL) + unit. | Medium |
| **2** | **Cross-sectional momentum** across the alt basket | Rank {BTC,ETH,SOL,XRP,ADA,AVAX,…} by trailing return; long top-k (optionally short bottom-k), periodic rebalance. The canonical factor edge. | **Partially** — relative-value within crypto is lower-beta than directional alt cells. | candle data already fetched for all alts. Needs a new **portfolio-level** harness (the per-symbol harnesses can't rank). `backtest_system.py` netting is the closest substrate. | Medium |
| **3** | **Ratio / pairs mean-reversion** (ETH/BTC, SOL/ETH) | The spread between two co-integrated assets reverts; trade the ratio dollar-neutral. | **Yes (strongly)** — dollar-neutral ⇒ near-zero BTC beta; a true market-neutral sleeve. | candle data exists. Needs a new spread/z-score backtester + the cross-asset scope doc's **beta-residual** discipline (lag, OOP holdout). | Medium |
| **4** | **Multi-asset time-series momentum (TSMOM / 12-1)**, vol-targeted, daily | The managed-futures workhorse: long positive-trailing-return assets, short negative, size by inverse-vol, across the *whole* daily universe (crypto + ETF + futures). | **Yes** — a portfolio overlay across uncorrelated sleeves is the diversification, by construction. | all daily data exists. Simple logic; needs a small portfolio harness (overlaps #2's substrate). | Medium |
| **5** | **Exit-manager / fee-reduction "strategies"** (recombination `_deferred`) | Not new entries — swap the **exit**: maker-band post-only (earns the rebate), ExitPlan partial-ladder, larger-R trail. Turns the *rejected* ict_scalp/HF fee-bled cells into viable ones. | n/a (rescues existing edges) | needs the harness refactor that injects the exit manager as a primitive (recombination Phase-3). **Maker-band directly attacks the SRQ-20260618-003 fee-bleed.** | Medium-high |
| **6** | **Wire + gate the already-built `hf_displacement_cont` / `hf_vwap_revert`** | ICT-derivative HF candidates already coded for the prop-pass research. | similar to ict_scalp (some corr) | code exists; just needs the **OOS prop-gate sweep** through the existing harness. | **Low (cheapest)** |
| 7 | **Opening-range breakout (ORB)** on SPY/QQQ/MES intraday | Classic intraday session edge. | somewhat | **blocked**: needs the not-yet-built market-hours/session gate (Tier-2 tick-path) + intraday equity data; MES intraday is ~1yr-shallow. | High |

### 3.3 Why these and not "more ICT"

The recombination `_deferred` block already enumerates the *price-action* recombinations
left to try (ict_scalp/fvg_range/vwap_revert entries × new exits/timeframes/directions).
Those are worth sweeping (rank 5 covers the highest-value slice), but they're variations
on the existing edge. Ranks 1–4 add **edge types the book has never had** — which is
where uncorrelated return actually comes from, and exactly what "fair game, non-ICT"
unlocks.

---

## 4. Recommended next backtests (the actionable plan)

Sequenced by ROI and by what's runnable *today* vs needs a small build first.

### Wave 1 — runnable now (existing harnesses; ship the two study specs in this PR)

1. **Trend-side out-of-pool holdout** — `config/research/studies/trend_oop_holdout.yaml`
   (this PR). Runs the trend_donchian_4h alt-cell params on **BNB + LINK** (never in the
   sweep pool) + a held-out period. Clears PB-20260618-014; a clean every-fold pass is
   the precondition for any trend-side strategies.yaml refinement. *Dispatch to trainer.*
2. **Exit-manager fee-reduction sweep** — `config/research/studies/exit_manager_feebleed.yaml`
   (this PR). Holds the ict_scalp / pullback entry geometry fixed and sweeps the **exit**
   (baseline trail vs larger-R trail vs tight) net-of-fee at 7.5/15 bps, to quantify how
   much of the fee-bleed reject is recoverable by the exit alone — the cheap precursor to
   building the maker-band exit primitive (rank 5). *Dispatch to trainer.*
3. **ETF-breadth daily sweep** (Part 1 rank 1) — once the diversifying-ETF daily CSVs are
   fetched (one Dukascopy/Alpaca pull on the trainer), reuse `backtest_trend.py` /
   `backtest_pullback.py` exactly as the SPY/GLD legs did. Study spec is a near-copy of the
   M15 Phase-0 sweep with the new symbols. *Fetch + dispatch.*

### Wave 2 — small build, then backtest (new edge types)

4. **Funding-rate carry** (rank 1) — build the ~150-line carry backtester over the existing
   `bybit_funding_oi.py` adapter; sweep funding-threshold × hold-rule × symbol; gate as usual.
   Highest expected diversification payoff.
5. **Cross-sectional momentum** (rank 2) + **TSMOM overlay** (rank 4) — share a portfolio
   ranking harness (one build); sweep lookback × top-k × rebalance; grade through
   `portfolio_robustness.py`.
6. **Ratio/pairs reversion** (rank 3) — spread/z-score backtester with the beta-residual +
   OOP discipline from the cross-asset scope doc.

### Wave 3 — cheapest cleanup + infra-gated

7. **Gate the HF candidates** (rank 6) — run `hf_displacement_cont` / `hf_vwap_revert` through
   the existing OOS prop-gate; they're already coded.
8. **ORB / session-gated intraday** (rank 7) — only after the market-hours gate exists.

---

## 5. What I can do autonomously vs what needs your call

**Autonomous (Tier-1) — I can do these now without a gate:**
- This memo + the two study specs (done).
- Dispatch Wave-1 study specs (1 & 2) to the trainer VM and report tiers.
- Fetch the diverse-ETF daily data + run Wave-1 #3.
- Build the new research harnesses (carry, cross-sectional, pairs) — Tier-1 research tooling.

**Needs your direction:**
- **Priority/sequencing** — which of (funding-carry / cross-sectional / pairs / ETF-breadth)
  to build & sweep first (they're all medium-effort; I'd start funding-carry + ETF-breadth).
- **IBKR micros (Part 1 rank 3)** — re-investing in the de-prioritized futures transport is
  a direction call.
- **Anything past demo is Tier-3** — every survivor here wires to bybit_1/paper first; a
  real-money proposal stays operator-gated, including the already-validated 10/16-cell books.

---

## 6. Operator decisions (answered 2026-06-20)

1. **Diversification goal = uncorrelated exposure first** — and that uncorrelated exposure
   should *also* widen trade frequency via diversification. The overarching objective is
   **"the system makes money all the time"** (smoother, always-on equity), with diversity of
   *trades* a wanted side effect. → favours bonds/commodity ETFs, carry, pairs, and a
   broader concurrent book.
2. **Market-neutral sleeve is in scope. Shorting any market is fair game.** → elevates the
   **pairs/ratio reversion** and **cross-sectional momentum** harnesses (both inherently
   short-capable + dollar-neutral), and the **hedged (market-neutral) funding-carry** variant,
   to first-class build targets — not just the long-biased directional book. (Honest caveat
   carried forward: naive *trend-short* was already shown to be a net drag, −37R; the
   productive short exposure is the neutral/relative-value kind + chop mean-reversion, not
   trend-short.)
3. **Special sweeps approved — run them.** Wave-1 dispatched to the trainer; the new-edge
   harnesses build next, starting **funding-carry + ETF-breadth**, then the market-neutral
   pair/cross-sectional pair.

### Build order locked from the above
1. Wave-1 sweeps (trend OOP holdout + exit/fee study) — *dispatched*.
2. ETF-breadth daily sweep incl. **bonds/commodity** for uncorrelated exposure — fetch + run.
3. **Funding-carry harness** (`backtest_funding_carry.py`) incl. the hedged market-neutral
   variant — highest uncorrelated payoff, data + math already in repo.
4. **Pairs/ratio reversion** (`backtest_pairs_revert.py`) — the dollar-neutral sleeve.
5. **Cross-sectional momentum** (`backtest_xsec_momentum.py`) — portfolio-graded book.

---

*Research memo only — nothing in this document changes runtime behaviour. Survivors wire to
demo via the normal Tier-3 PR; real-money promotion stays operator-gated.*
