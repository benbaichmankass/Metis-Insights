# Alpaca Level-3 Options on `alpaca_live` — Research Memo (first pass)

**Date:** 2026-06-27 · **Branch:** `claude/alpaca-level3-options-research-djr017` · **Status:** first pass — **operator chose the Hybrid path (§9, option 3) on 2026-06-27**: build + paper-validate the debit-only slice now at $150, with a pre-agreed graduation to ≥$2k + credit structures once the infra validates.

**Scope:** (a) which options strategies + underlyings are worth trading on the small real-money `alpaca_live` account, each with a real edge thesis; (b) a feasibility-gated plan for the infra to trade + backtest + monitor them in this bot. Guiding principle: recommend only what is **both well-researched AND feasible to build here.**

**Confirmed constraints (operator, 2026-06-27):** `$150 = total deployable capital` (the whole `alpaca_live` balance, not a per-trade cap); per-trade risk knob currently `risk_pct: 0.02`; defined-risk strongly preferred; v1 selects structure from **IV-rank / term-structure signals** (the regime-ML synergy is deferred — see §4); overlay-vs-greenfield decided per-strategy after research (§3).

---

## 0. THE DECISIVE FINDING — a $150 cash account cannot run the high-edge structures

The single most important thing this research turned up. It flips the naive ranking and must be decided before we build anything.

- **The best-documented edge in options is the Volatility Risk Premium (VRP)** — implied vol systematically exceeds realized vol on equity indices, so the *seller* of premium is paid (Carr & Wu 2009; Bondarenko; the Cboe PUT/BXM benchmark indices all beat buy-and-hold on a risk-adjusted basis). Harvesting VRP with **defined** risk means **credit spreads and iron condors**.
- **But credit/short spreads require a MARGIN account with ≥ $2,000 equity** (FINRA Rule 4210; Alpaca's own rule: "to trade on margin or sell short you must have $2,000 or more account equity"). Alpaca's "universal spread rule" holds margin = `(strike width × 100) − net credit` against the position.
- **A $150 account is a cash account by definition.** Therefore **every premium-selling / credit structure is gated out** — credit spreads, iron condors, *and* cash-secured puts / covered calls / the wheel (those additionally need 100 shares ≈ $3k+ of collateral even on the cheapest ETF).
- **What survives at $150: cash-securable DEBIT structures** — long single options, debit verticals, calendars — where max loss = premium paid, fully covered by cash. Their edge thesis is **weaker** (directional / convexity; you *pay* VRP rather than collect it).

**Consequence:** at $150 the structures we *can* run have weak/neutral edge; the structures with *real* edge need ~$2,000. This is the fork (§9). The honest framing: a $150 pilot validates **plumbing**, not **alpha**.

Sources: [FINRA 4210](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210) · [Alpaca margin determination](https://alpaca.markets/support/determine-margin-account) · [Alpaca L3 docs (universal spread rule)](https://docs.alpaca.markets/docs/options-level-3-trading) · [Carr & Wu 2009 (PDF)](https://engineering.nyu.edu/sites/default/files/2019-01/CarrReviewofFinStudiesMarch2009-a.pdf) · [Bondarenko, Why Are Put Options So Expensive (PDF)](https://www3.gmu.edu/schools/vse/seor/studentprojects/graduate/2009Fall/ISG/Investment_Optimization/Resources_files/Bondarenko-Puts.pdf) · [Cboe benchmark indexes](https://www.cboe.com/insights/posts/key-cboe-benchmark-indexes-using-spx-options-offer-strong-risk-adjusted-returns/)

---

## 1. Alpaca capability reality (corrects the premise; all verified against live docs 2026-06-27)

| Topic | Finding |
|---|---|
| **Trading levels** | Alpaca uses **Levels 0–3, there is NO Level 4, and naked/uncovered short options are not offered at any level.** L1 = covered call + CSP; L2 = + long options; L3 = + multi-leg defined-risk spreads (verticals, condors, butterflies, straddles). Design for 0–3, never naked. |
| **Multi-leg execution** | Atomic spreads via **`order_class="mleg"`, 2–4 legs**, per-leg `position_intent`. `market`/`limit` + `time_in_force=day` are safe; **no equity leg allowed in an mleg order**; no fractional/notional; whole-contract qty; no extended hours. Verify `stop`/`gtc` on a live account (docs conflict). |
| **SDK** | **`alpaca-py` 0.43.4** (current) fully supports options: `GetOptionContractsRequest`, `OptionLegRequest`, `Limit/MarketOrderRequest(legs=…, order_class=MLEG)`, `OptionHistoricalDataClient`, `OptionDataStream`. Our current `AlpacaClient` uses **raw `requests`, not the SDK** — adopting the SDK (or hand-rolling the mleg REST) is a build decision. |
| **Greeks / IV** | The snapshot/chain endpoint **returns implied vol + all five greeks computed server-side** — we do **not** need a Black-Scholes engine for live trading. (Nullable on illiquid contracts → render "—".) |
| **Real-time data cost** | Free/Basic tier = **15-min-delayed "indicative"** options data (dev only, explicitly "not for live trading"). **Real-time OPRA = Algo Trader Plus, $99/mo.** Live options trading effectively needs the paid feed. |
| **Paper supports options** | **Paper environment has L3 multi-leg enabled by DEFAULT** → we can shadow/paper-soak spreads before real money, matching the bot's existing shadow discipline. Caveats: paper non-trade activities (assignment/exercise/expiry) lag ~1 day; paper fills are optimistic (no NBBO-depth/queue model) → apply a spread haircut. |
| **Assignment / expiry** | **Auto-exercise of ITM longs ≥ $0.01 at expiry (done by 6 PM ET).** Insufficient BP → Alpaca sells the position out ~1 hr before expiry. **Assignment is NOT pushed over websocket — you must POLL `GET /v2/account/activities`** (non-trade activities). This is load-bearing for the monitor loop. Manual exercise: `POST /v2/positions/{id}/exercise`. |
| **Symbology / multiplier** | OCC format `UNDERLYING + YYMMDD + C/P + strike×1000 (8-digit)`. **Multiplier = 100.** Size against the `options_buying_power` account field, whole contracts, minimum 1. |
| **Historical data (the hard part)** | **Alpaca historical options only go back to Feb 2024** (~2 yrs) — too shallow for serious multi-year validation. See §5. |

---

## 2. What the bot has today (grounded in code) vs. what options need

| Area | Today | Gap for options |
|---|---|---|
| `AlpacaClient` (`src/units/accounts/alpaca_client.py`) | **Equity bracket-market orders only** (`place`, `close`, `modify_protective`, `positions`, `balance`, `buying_power`); raw `requests`; no SDK; **zero options code** | All options execution is greenfield: contract discovery, mleg order build/submit, options position read, exercise/close |
| `RiskManager` (`src/units/accounts/risk.py`) | Sizes by **risk-distance notional** `balance×risk_pct/(entry−SL)`; forces **whole shares** for Alpaca; refuses sub-minimum | New **premium-/max-loss-based** sizing path: contracts = floor(max_loss_budget / (debit×100)), floor 1, refuse if 1 contract > budget |
| `order_monitor` (`src/runtime/order_monitor.py`) | SL/TP + qty management on single-leg positions; calls strategy `monitor()` | Expiry awareness, **poll-based assignment/exercise detection**, multi-leg position tracking, roll logic |
| Backtest (`scripts/backtest_system.py`, `src/backtest/`) | Candle-based, single-leg, notional sizing; no greeks/IV/expiry | Multi-leg P&L, greeks-aware sizing, expiry/assignment, option-quote data ingest |
| `instruments.yaml` | Spot equities + futures (`contract_value_usd`); **no option type/strike/expiry/multiplier** | Options contract modeling (or a separate options config layer) |
| Regime ML | `*-regime-*` models cover **BTC, MES (S&P futures), ETH** — **no equity-ETF regime**; predictions are observe-only `advisory`-stage shadow logs, never order-influencing. No wired "vol-router" found in code (`detect_vol_regime` is observe-only soak). | An equity/ETF IV-regime signal does not exist; the regime→structure synergy is not buildable as stated for SPY/QQQ/XLF/GDX today (§4) |

> **Honesty flag — RECONCILED 2026-06-27.** A repo-wide search found **no completed result** that "the vol-router beats the frozen-edge label in backtest + walk-forward." What exists is a *designed-but-unrun* A/B: [`docs/research/A-regime-router-ml-vol-verdict-DESIGN-2026-06-27.md`](./A-regime-router-ml-vol-verdict-DESIGN-2026-06-27.md) (Tier-3 design) + [`docs/research/A-vol-gating-AB-plan-2026-06-27.md`](./A-vol-gating-AB-plan-2026-06-27.md) (the A/B plan, **no results attached**). The live `detect_vol_regime` (`src/runtime/regime/vol_detector.py`) is a **frozen-edge threshold detector — observe-only, no ML, never order-influencing**. So the "vol-router wins" belief is the *plan*, not a validated outcome; the ML-vol verdict is itself unvalidated and Tier-3. This **confirms** keeping the regime→options-structure thread decoupled to IV-rank for v1 (§4): there is no validated vol-regime signal to lean on yet, on any asset.

---

## 3. Strategy families — edge thesis, capital, $150 verdict

(Full evidence + citations in the appendix.) Governing constraint from §0: **debit = feasible (cash-securable); credit/collateral = gated out.**

| Strategy | Edge thesis | Max-loss / capital | $150 verdict | IV regime |
|---|---|---|---|---|
| **Long single call/put** | Directional + convexity (long gamma/vega); pays VRP (avg negative-EV), bounded loss | = premium; $15–80 on cheap ETFs | **FEASIBLE** — cheapest, minimal-infra (no mleg) | Low IV-rank (<30) |
| **Vertical DEBIT spread** | Directional, defined, cheaper/lower-theta than the naked long | = net debit; $25–70 (1-wide, cheap underlying) | **FEASIBLE** — defined-risk, cash-securable, fits 1–2 positions | Low IV / directional |
| **Calendar / diagonal** | Theta differential + term-structure; sell fast front, own slow back | = net debit; $30–120 | **FEASIBLE** but tighter/harder to manage | Low IV expecting normalization |
| **Vertical CREDIT spread** | **Premium-harvest (best edge: VRP + put-skew)** | BP = width×100 − credit | **GATED OUT** — needs $2k margin account | High IV-rank (>50) |
| **Iron condor** | Two-sided premium-harvest (best-studied IV-rank trade) | BP = wider width×100 − credit | **GATED OUT** — $2k margin | High IV-rank (>50) |
| **Covered call / CSP / wheel** | Premium-harvest on collateral (the Cboe PUT index edge) | 100 shares or strike×100 ($500–$3k+) | **INFEASIBLE** — collateral ≫ $150 | High IV |

**Feasible shortlist at $150 (ranked):** ① **debit vertical** (best feasible risk/reward, defined, cheap), ② **long single option** (smallest infra, convexity sleeve, negative-EV — use sparingly), ③ **calendar/diagonal** (only feasible theta/term-structure play, conditional edge).

---

## 4. Underlyings — budget-fit ranking (approx. June-2026 magnitudes)

The cheap-ETF vs deep-liquidity tension: SPY/QQQ/IWM have the tightest (penny-wide) chains but contracts at $300–$730 leave no headroom in $150; the cheap ETFs fit the budget but have wider near-ATM markets.

| Rank | Underlying | ~Price | Why it fits $150 | Watch-out |
|---|---|---|---|---|
| **1** | **XLF** | ~$54 | Cheapest premiums, **penny-wide ATM**, $1 strikes → 1-wide debit vertical ~**$20–50** max-loss; best liquidity-per-dollar of the cheap names | Low IV = smaller % moves |
| **2** | **GDX** | ~$82 | $1 strikes, penny-pilot, **high IV** (richer structures), ~27k vol; spreads ~$30–70 | Slightly wider fills |
| **3** | **SLV** | ~$62 | $0.50/$1 strikes, ~62k vol, high IV; spreads ~$25–60 | Metals-correlated with GDX |
| 4 | **TLT** | ~$86 | Most liquid bond options, $1 strikes, low premiums | Rate-driven, low IV |
| 5 | **USO** | ~$109 | $1 strikes, fits budget | Thinner (~8.8k vol), wider fills |
| 6–9 | IWM / GLD / SPY / QQQ | $300–730 | Deepest, penny-wide markets | Contracts too expensive for $150 headroom |
| Avoid | GDXJ, IEF | — | Too thin/wide — slippage eats a small account | — |

**Best 2–3 for a $150 defined-risk pilot:** **XLF** (tightest cheap market), **GDX** (high IV makes structures meaningful per dollar), **SLV/TLT** (liquid cheap alternates). **Skip SPY/QQQ** for now (best markets, wrong price tag) and **IEF/GDXJ** (too thin).

**Regime-fit reality:** the user's "regime → structure" synergy wanted MES/S&P regime to pick options structures. But (a) the premium-harvest (calm-regime) side is gated out at $150, and (b) the budget-fit underlyings (XLF partially S&P-correlated; GDX/SLV metals; TLT bonds) are mostly **not** covered by the existing BTC/MES/ETH regime models. So for v1 we **decouple to IV-rank / term-structure signals** computed directly from Alpaca's snapshot (greeks/IV are returned server-side). The regime-ML selector becomes meaningful only after the $2k graduation (premium-selling unlocked) **and** an equity/ETF IV-regime signal exists — a Phase-4 enhancement, not v1.

---

## 5. Backtest feasibility — the honest hard part

**State fidelity limits, stated plainly:**
- **Alpaca historical options: Feb 2024 → present only** (~2 yrs). Enough for a *real-quote sanity check*, not for multi-regime validation.
- **Black-Scholes reconstruction from underlying + a VIX/IV proxy is PROTOTYPE-ONLY.** It omits bid/ask (often the dominant cost — biases edge upward), uses one IV number instead of the real skew/term-structure (mis-prices OTM/short-dated badly), and ignores American early-assignment. Acceptable to sanity-check directional logic; **never** to justify promotion.
- Third-party deep history if we get serious: **ThetaData ~$80/mo** (intraday tick, 8 yr) or **historicaloptiondata.com ~$1,495 one-time** (EOD + greeks/IV back to 2002). Not needed for a debit-only $150 pilot.

**Most rigorous FEASIBLE validation given the above (recommended):**
1. **Forward paper-soak is the primary evidence** — paper supports L3 by default; run the candidate structure in paper with a conservative spread haircut, logged like the existing shadow soak. This is real Alpaca quotes, real fills (optimistic-adjusted), no historical-data dependency.
2. **Short real-quote backtest** on Alpaca's Feb-2024+ option bars as a secondary cross-check (directional sanity, fill realism on actual quotes).
3. **BS-reconstruction harness** only as a fast offline prototype for the sizing/structure-selection logic, clearly labelled non-promotable.
4. Extend `account_compat_matrix` to score the option strategy's paper-soak ledger (R-multiples, P(breach), survival) against `alpaca_live`'s ruleset before any real-money routing.

---

## 6. Infra-scope — build list, feasibility, minimal slice

| # | Component | Build | Feasibility / effort | Risk |
|---|---|---|---|---|
| I1 | **Options execution in `AlpacaClient`** — contract discovery (`get_option_contracts`), mleg order build/submit, options position read, close/exercise | Adopt `alpaca-py` 0.43.4 for the options path (keep raw `requests` for equities, or migrate). New methods: `list_option_contracts`, `place_option_spread`, `option_positions`, `close_option`, `exercise` | **Medium.** SDK does the heavy lifting; mleg shape is documented | Med — real-money order path = Tier-3 |
| I2 | **Premium-/max-loss sizing** in `RiskManager` | New path: `contracts = floor(max_loss_budget / (max_loss_per_contract×100))`, floor 1, **refuse if 1 contract > budget** (the "trades actually fire" knob the operator flagged) | **Low-Med.** Self-contained, unit-testable | Low |
| I3 | **Options config / contract modeling** | Options fields in `accounts.yaml` (`options: {enabled, level, data_feed}`), an options layer for `instruments.yaml` (or a new `options.yaml`: underlying, dte window, width, structure), strategy declaration in `strategies.yaml` | **Low.** Config + schema | Low |
| I4 | **Chain + IV + greeks data** | Snapshot client (greeks/IV server-side — no BS engine). Decide data plan: free 15-min indicative (dev/paper) vs **$99/mo OPRA** (live) | **Low** (SDK client). Cost decision is the gate | Low (cost) |
| I5 | **Expiry / assignment / roll monitor** | `order_monitor` extension: **poll `/v2/account/activities`** for assignment/exercise/expiry (not on websocket), expiry-window logic, optional roll | **Medium.** New poll loop + state | Med — money state correctness |
| I6 | **Multi-leg P&L + position model** | Track a spread as one logical position (legs, net debit, max-loss, current mid); surface on `/positions`, dashboard, Android | **Medium** | Low-Med |
| I7 | **Backtest path** (§5) | Paper-soak logger + short Alpaca-history cross-check + (optional) BS prototype + compat-matrix scoring | **Medium-High** (the real cost sink) | Med — fidelity honesty |
| I8 | **Strategy unit(s)** | Greenfield options strategy module(s) + signal builder driven by IV-rank/term-structure; OR overlay on existing equity signals (decide per-strategy — §3 verdict: pilot is **greenfield debit vertical**, IV-rank-gated) | **Medium** | Med |

**Minimal first slice (smallest thing that proves the loop end-to-end):**
> **Paper, one underlying (XLF), one structure.** Start with a **long single option** (no mleg — smallest possible execution path) to validate: contract discovery → premium-based sizing → order submit → snapshot greeks/IV read → poll-based expiry/assignment monitor → position/P&L surfacing. Then add **mleg** to graduate to the **1-wide debit vertical** (the actual pilot strategy). Everything in paper, zero real money, until the gates in §7 pass.

---

## 7. Phased, gated build + test plan

- **Phase 0 — Verify (no build).** Confirm on the live account: **L3 options approval active**; data plan (free indicative vs $99 OPRA); paper options enabled (default, but confirm on the actual paper account); `alpaca-py` 0.43.4 import-check of the options classes. Verify `stop`/`gtc` acceptance on options. *Gate: all green or scoped.*
- **Phase 1 — Paper options MVP (no real money).** Build I1–I6 against **`alpaca_paper`**. Long single option on XLF → then 1-wide debit vertical. Shadow/paper-soak logged. *Gate: clean fills, correct sizing (≥1 contract within a max-loss budget), assignment/expiry correctly detected via polling.*
- **Phase 2 — Validation (no real money).** Forward paper-soak accrual (primary) + short Alpaca-history cross-check (secondary) + `account_compat_matrix` extension scores the soak ledger vs `alpaca_live`'s ruleset. *Gate: positive risk-adjusted soak with spread haircut; compat-matrix route=true.*
- **Phase 3 — Tiny real-money pilot (Tier-3, operator-gated).** `alpaca_live`, **one underlying (XLF), one structure (1-wide debit vertical), max-loss ~$30–50/trade, ≤2 concurrent**, premium-based sizing tuned so trades actually fire. Live order path is Tier-3 → explicit operator approval before the mode/strategy wire merges. *Gate: operator approval + Phase-2 evidence.*
- **Phase 4 — Graduation (the real edge).** If the pilot validates the plumbing: **fund `alpaca_live` to ≥$2,000** → unlock **credit spreads / iron condors** (the actual VRP edge) → build the equity/ETF **IV-regime selector** so "regime → structure" becomes first-class. This is where alpha (not just plumbing) starts.

---

## 8. Recommended first pilot

**Strategy:** 1-strike-wide **debit vertical** (bull call or bear put), direction from the existing equity trend/pullback signal, **entry gated on low IV-rank** (debit is cheap when IV is low).
**Underlying:** **XLF** (~$54; penny-wide ATM, $1 strikes, max-loss ~$25–50 → 2–3 positions coexist in $150).
**Why this one:** the best-evidenced *feasible* structure (defined-risk, cash-securable, lowest slippage of the cheap names), minimal incremental infra over the single-option plumbing slice, and it exercises the full options path (mleg, greeks, expiry/assignment) at the smallest honest dollar risk.
**Honest caveat:** this pilot proves **infrastructure + small directional expression**, not a durable edge. The durable edge (VRP/premium-selling) is locked behind the $2k margin floor (§0/§9).

---

## 9. The capital fork — DECIDED: Hybrid (option 3)

**Operator decision (2026-06-27): option 3, Hybrid.** The options below are kept for the record.

1. Stay $150 / cash / debit-only — build the debit-vertical pilot now; accept it's an infra-validation exercise with weak edge.
2. Fund `alpaca_live` to ≥$2,000 — unlock credit spreads / iron condors (the real VRP edge) and make the regime→structure synergy worth building. Straight to the real edge, more capital at risk.
3. **Hybrid — CHOSEN.** Build + paper-validate the debit-only slice now (cheap, proves the plumbing), with an explicit, pre-agreed graduation to ≥$2k + credit structures once Phase-3 validates the infra. Sequences spend behind evidence.

**What "Hybrid" locks in:** Phases 0–3 proceed as written (debit vertical on XLF, paper → tiny real-money). The §0 / §9 graduation to ≥$2k + credit structures (Phase 4) is **pre-agreed in principle**, triggered only after Phase-3 validates the infra on real money — not before. The build targets the debit path first; the credit-structure code is deliberately deferred to Phase 4 so we don't carry margin-account complexity we can't yet use.

### Still-open Phase-0 verifications (do not block the memo, but gate the real-money pilot)
- Confirm `alpaca_live` has **L3 options approval** active.
- Confirm the **options data plan**: free 15-min indicative (dev/paper-OK) vs paid OPRA $99/mo (needed for live).
- Point me at the **vol-router / frozen-edge backtest artifact** if it exists — I could not find a wired vol-router in code (`detect_vol_regime` is observe-only soak; no equity-ETF regime model), so the regime→structure thread stays decoupled to IV-rank for v1 until that's reconciled.

---

## Appendix — edge evidence (citations)

- **VRP:** [Cboe benchmark indexes](https://www.cboe.com/insights/posts/key-cboe-benchmark-indexes-using-spx-options-offer-strong-risk-adjusted-returns/) · [ACG/Cboe PUT vs BXM vs S&P study (PDF)](https://cdn.cboe.com/resources/indices/documents/pap-assetconsultinggroup-cboe-feb2012.pdf) · [Carr & Wu 2009 (PDF)](https://engineering.nyu.edu/sites/default/files/2019-01/CarrReviewofFinStudiesMarch2009-a.pdf) · [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1701685)
- **Skew / put richness:** [Bondarenko, Why Are Put Options So Expensive (PDF)](https://www3.gmu.edu/schools/vse/seor/studentprojects/graduate/2009Fall/ISG/Investment_Optimization/Resources_files/Bondarenko-Puts.pdf) · [Cboe — existence of skew](https://www.cboe.com/insights/posts/dawn-of-a-new-era-brings-on-the-existence-of-skew/)
- **IV-rank:** [tastytrade volatility metrics](https://support.tastytrade.com/support/s/solutions/articles/43000539059) · [Volatility Box IV-rank study](https://volatilitybox.com/research/iv-rank-vs-iv-percentile/) (practitioner/backtest, not peer-reviewed — directional only)
- **Term structure / theta:** [Volatility Box VIX contango/backwardation](https://volatilitybox.com/research/vix-contango-backwardation/) · [Days to Expiry — theta decay](https://www.daystoexpiry.com/blog/theta-decay-dte-guide)
- **Margin floor:** [FINRA 4210](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210) · [Alpaca margin determination](https://alpaca.markets/support/determine-margin-account)
- **Alpaca options API:** [options-trading-overview](https://docs.alpaca.markets/us/docs/options-trading-overview) · [L3 trading](https://docs.alpaca.markets/docs/options-level-3-trading) · [historical option data](https://docs.alpaca.markets/docs/historical-option-data) · [alpaca-py PyPI](https://pypi.org/project/alpaca-py/)
- **ETF options liquidity/cost:** [Macroption ETF options volume](https://www.macroption.com/etf-options-volume/) · [ApexVol best ETFs for options](https://apexvol.com/best/etfs-for-options-trading) · [Ticker Tape defined-risk verticals](https://tickertape.tdameritrade.com/trading/calculate-risk-defined-risk-vertical-spread-16053)

*Approximate prices/premiums are June-2026 magnitudes and move continuously — verify live on the chain before trading.*
