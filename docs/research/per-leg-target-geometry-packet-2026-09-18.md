# The per-leg target-geometry decision packet — all 55 legs, at each leg's own stated n

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> MI-317 · `2026-09-18` · successor to MI-307 ([`offline-mfe-distribution-2026-09-18.md`](offline-mfe-distribution-2026-09-18.md), #12503) and MI-312 ([`scalp-family-target-arms-2026-09-18.md`](scalp-family-target-arms-2026-09-18.md), #12515, merged)
> · row `OI-20260906-THE-BRACKET-CALIBRATION-INSTRUMENT-EXISTS-AND-ITS-VERDICT-HAS-NOT-BEEN-ACTED-ON` clause (2)

**⚠️ PROPOSE ONLY. Nothing in `config/` is touched. Every `tp_r` and `tp_at_r` is Tier-3 and the
operator's. This object is complete when the packet exists, not when anything is applied.**

**⚠️ THIS UNIT COMMISSIONED NO SWEEP AND RE-RAN NEITHER INSTRUMENT.** It joins evidence that
already existed and had never been put in one place. MI-307's memo is explicit that re-measuring is
the failure mode `OI-20260906` exists to stop — *the diagnosis was established seven times before it
was built once* — so the one thing this unit must not do is establish it an eighth time.

---

## 0. The answer in one paragraph

Two lanes produced the distribution overnight and nobody wrote the decision. This is that packet,
over **all 55 entries in `config/strategies.yaml`**, and its shape is not the one the question
implied. **A per-leg number is owed on 8 legs, is already in `config` on 10, is refused by evidence
on 31, and is unknown on 6.** The reason so few are open is the finding: **44 of the 55 legs do not
set their own target at all.** Their take-profit is set by `TP_VENUE_CAP_PCT = 0.099`, one
fleet-wide constant named for a Bybit boundary — and **26 of those 44 legs touch no Bybit account**,
five of them routing **real money** through `alpaca_live`. Where a leg *does* set its own target,
the sweep has already been run and its answers are already shipped: across the **41 legs the e35
corpus covers (7,343 real-`tp_r` cells)** exactly **11 cells on 10 legs** survive walk-forward
without a timeout component or inert-fold contamination, and **on all 10 the surviving cell's
`tp_r` is the value `config` already declares.** So the genuinely open decision is **8 legs wide**
— the `ict_scalp` family and `fvg_range_15m`, the legs that apply no clamp — and **not one of the
eight places a real-money order today.**

---

## 1. Population, and what this joins

**POPULATION: all 55 entries in `config/strategies.yaml`.** Every leg is accounted for; the census
closes on 55 and a selftest asserts that it does.

| source | what it contributes | legs |
|---|---|---:|
| `config/strategies.yaml` | declared `tp_r` / `tp_at_r` / `tp_intent`, timeframe, execution gate | 55 |
| `config/accounts.yaml` | which book an order reaches, and whether it is real money | 55 |
| MI-307 `mi307-offline-mfe-2026-09-18.json` | uncapped MFE distribution, `cap_r`, clamp-binding rate | 19 |
| MI-312 `mi312-scalp-target-arms-2026-09-18.json` | `no_tp` MFE distribution for the non-clamping family | 8 |
| `e35-bracket-corpus.jsonl` | the `tp_r` sweep itself — ~180 cells per leg | 41 |
| `exit-refinement-coverage.json` | whether a bracket verdict was shipped | 52 |

**Instrument:** [`scripts/research/mi317_target_geometry_packet.py`](../../scripts/research/mi317_target_geometry_packet.py)
— **39 selftests, 7 of them negative controls** that fail if the rule they guard is deleted. It
**imports** rather than re-derives: `tp_venue_cap.{TP_VENUE_CAP_PCT, CLAMPING_UNIT_MODULES,
unit_module_clamps}`, `pipeline.monitor_unit_for` (the resolver the order-monitor itself uses), and
`m20_fleet_exit_sweep.classify`. Every figure in this memo is rendered from the committed JSON
rather than transcribed.

### 1.1 Evidence state, per leg — *we looked and found nothing* is not *we did not look*

| state | legs | meaning |
|---|---:|---|
| `distribution_and_sweep` | 19 | MI-307's MFE distribution **and** the e35 `tp_r` sweep |
| `sweep_only` | 22 | the sweep ran; no MFE distribution (no crypto candle feed) |
| `distribution_only` | 8 | MI-312's distribution; the e35 sweep never covered them |
| `not_measured` | 6 | **nobody has looked** |

`19 + 22 + 8 + 6 = 55`. ✅

### 1.2 Two corrections to my own working, recorded rather than quietly fixed

Both were caught by arithmetic that did not agree with an imported constant, which is the only
reason they are in this section rather than in the tables.

1. **A bare `except` reported that NO leg is clamped.** An early draft called
   `m20_fleet_exit_sweep.classify()` with the wrong arity behind a broad `except`, so all 55 legs
   resolved `None` and the packet said `declared_unclamped: 54` **while importing the frozenset that
   names four clamping families**. That is the silent-empty class this repo has a guard for,
   produced inside the unit that exists to report on it. The resolver now raises. Negative control
   NEG0 fails on it.
2. **A family-only clamp test under-claims, and `tp_venue_cap`'s own docstring says so.** Resolving
   the clamp on the *family* string misses `fade_breakout_4h` outright — it has no harness, so
   `classify()` returns `None`, while its unit module imports the clamp. The packet resolves on the
   **unit module** via `monitor_unit_for`. Negative control NEG0d fails on the regression.

---

## 2. What actually sets the target today

**MEASURED over all 55 legs, resolved on the unit module:**

| what sets the target | legs |
|---|---:|
| **the venue clamp** `TP_VENUE_CAP_PCT = 0.099` | **44** |
| the leg's own declared target, unclamped | 10 |
| nothing — no target declared and no clamp | 1 (`vwap`, `enabled: false`) |

### 2.1 The clamp reaches 26 legs that never touch Bybit — 5 of them on real money

This is the sharpest thing the join says that neither memo could say alone, because it needs the
**routing** table beside the geometry.

**POPULATION: the 44 clamped legs, joined to `config/accounts.yaml`.** **26 route to no Bybit
account at all** — GLD, SPY, QQQ, TLT, SLV, IEF, IAUM, USO, GDX, IWM, SPLG, SCHA, QLD, TQQQ, MES,
MGC, MHG, XAUUSD, and the two `breakout_1` prop legs. Their take-profit is a percentage named for a
Bybit venue limit.

**Five of the 26 route REAL MONEY through `alpaca_live`:** `tlt_pullback_1h`, `tlt_pullback_1d`,
`ief_pullback_1d`, `slv_pullback_1d`, `iaum_pullback_1d`.

⚠️ **How hard the clamp bites is a per-leg fact and on leveraged ETFs it is extreme.** MI-195
measured `cap_r` median **1.40 (QLD)** and **0.99 (TQQQ)** against **3.14** on the QQQ control
(`qld-tqqq-examined-2026-09-08.md`) — **on TQQQ the venue clamp binds BELOW 1R.** Those two are
`alpaca_paper` only, so no real money rides that particular figure; it is quoted to show the range
the constant spans, not to describe the real-money legs.

⚠️ **THIS IS NOT A CLAIM THAT THE CLAMP IS WRONG, and the packet must not be read as one.** § 4
records that the sweep looked for something better on 41 legs and did not find it. What is
established is only that the number is **fleet-wide, exchange-named, and was never chosen per leg** —
which is a fact about provenance, not about performance.

---

## 3. The packet

Each leg appears exactly once. **A "change nothing" verdict is a first-class answer here, not a
default** — and it is separated from *nobody looked*, which is a different and worse state.

### 3.A — A per-leg decision IS owed (8 legs)

These are the legs that apply **no clamp**, so the declared target is the entire mechanism. **It was
set to `tp_at_r: 1.5` identically on all of them** — including `ict_scalp_mgc_15m`, which trades a
different asset class — and until MI-312 there was no distribution to have chosen it against.

**POPULATION: MI-312's `no_tp` (target-disabled) arm, ALL trades, 2021-01-01 → 2026-09-16,
config-exact per leg.** `reach @ target` is the observed take-profit exit share of the live-parity
arm.

| leg | tf | n | p50 | **p80** | p90 | declared target | reach @ target | p90 truncated by | book |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `ict_scalp_avax_5m` | 5m | 1664 | 0.78 | **1.66** | 2.59 | 1.5R | 22.9% | +50.9% | paper (`bybit_1`) |
| `ict_scalp_sol_5m` | 5m | 1471 | 0.81 | **1.73** | 2.46 | 1.5R | 25.1% | +41.1% | paper (`bybit_1`) |
| `ict_scalp_xrp_5m` | 5m | 1210 | 0.79 | **1.68** | 2.59 | 1.5R | 24.4% | +44.2% | paper (`bybit_1`) |
| `ict_scalp_5m` | 5m | 1135 | 0.68 | **1.54** | 2.25 | 1.5R | 20.7% | +31.1% | paper (`bybit_1`) |
| `ict_scalp_sol_15m` | 15m | 663 | 0.68 | **1.47** | 2.12 | 1.5R | 18.1% | +25.9% | paper (`bybit_1`) |
| `ict_scalp_eth_15m` | 15m | 597 | 0.60 | **1.53** | 2.29 | 1.5R | 21.1% | +33.9% | paper (`bybit_1`) |
| `ict_scalp_xrp_15m` | 15m | 557 | 0.71 | **1.38** | 1.99 | 1.5R | 17.4% | +14.6% | paper (`bybit_1`) |
| `fvg_range_15m` | 15m | 45 | 0.63 | **2.21** | 4.53 | ~2.10R *(range, varies)* | 20.0% | +78.8% | shadow — no live order |

**The shape is identical on all seven scalp legs: the 1.5R target sits almost exactly at the p80 of
reachable excursion, and truncates the p90 by 14.6%–50.9%.**

**⚠️ THE CONTROL BEHIND THESE NUMBERS IS WEAK, AND THE OPERATOR IS MAKING A REAL DECISION OFF THEM.**
MI-312's positive control passes on 8 of 8 legs within 0.71 pp — and that is **not** the good news it
looks like. Measured per trade over the **7,338 trades whose entries join across the two arms, the
predicted and observed quantities disagree on 21 — 0.29%.** So **99.71% of that control is a
quantity compared with itself.** Given one entry and one set of bars, *"uncensored MFE reached
1.5R"* and *"the live arm exited at the 1.5R target"* are nearly the same event by construction.
**A control that cannot fail is not evidence the instrument is right**, and reporting MI-312's
0.71 pp beside MI-307's 3.2 pp as though the tighter number were the better instrument would be
exactly backwards. What the arm is genuinely worth is the **uncensoring** — the p90 figures above
did not exist before it — and that is a weaker claim than a validated instrument. This is the
cycle's own rule (`CY-20260906-TRADING-TRUTH`): a confident number with a weak control behind it is
worse than none.

**⚠️ NOT ONE OF THESE EIGHT LEGS PLACES A REAL-MONEY ORDER TODAY.** The seven `ict_scalp` legs route
to `bybit_1` only, which is `account_class: paper` (`ict_scalp_5m` was demoted off real-money
`bybit_2` on 2026-09-06, `OI-20260906-ICT-SCALP-5M-DEMOTED-OFF-BYBIT2`). `fvg_range_15m` appears on
`bybit_2`'s roster but is **`execution: shadow`**, so the strategy-level gate stops it before any
order — its roster line is not exposure. **The decision can be taken without money at risk while it
is taken**, which is a materially better position than most Tier-3 target questions start from.

**⚠️ `fvg_range_15m` IS n=45 AND DOES NOT BELONG IN THE SAME READING AS THE ROWS ABOVE IT.** It
clears the 30-reading floor and nothing more; its target is a **range boundary**, so `~2.10R` is the
median of a varying quantity rather than a declared constant; and its two arms have `entry_overlap`
**1.000** with a **0.00 pp** delta — the emptiest control of the eight. Quote it as a per-leg
curiosity, never pooled with the scalp legs. **It also carries a known defect that conditions its
harness runs at all:** `base_args` emits no `--exit-style` for fvg, so the harness falls through to
its own `mid` default while the live unit targets the **far** boundary
(`BL-20260918-BASE-ARGS-CANNOT-PRODUCE-A-LIVE-PARITY-FVG-RUN...`). MI-312's driver injects
`--exit-style far` for its own runs; every other caller is exposed.

**What is proposed for these 8: NO VALUE.** A reach-rate is not a P&L claim — a target further out
also converts trades that currently take +1.5R into trades that give it back, and net-R is a
sweep's question. **The e35 corpus does not cover any of these 8 legs** (see § 3.C for what it did
answer, and on which legs), so unlike the clamping families there is no completed net-R sweep to
appeal to here. That is the honest statement of what is and is not known.

---

### 3.B — The evidence already shipped (10 legs) — change nothing

The e35 sweep found a `tp_r` cell that survives walk-forward on these legs, and **`config` already
declares exactly that value.** Proposing a number here would be re-proposing what production
already runs.

**POPULATION: `e35-bracket-corpus.jsonl`, deduped on `(measurement_key, sweep_generated_at)`,
restricted to rows with a real `tp_r` (< 50), keeping only cells that pass walk-forward, carry no
timeout component, and have no inert folds.**


**⚠️ "SURVIVES WALK-FORWARD" IS DOING LESS WORK THAN IT SOUNDS, AND TWO FILTERS ARE LOAD-BEARING.**

1. **A `to*` cell is unshippable by construction.** No live trend/pullback/squeeze unit implements a
   bar-count exit (`BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES`),
   so a timeout-bearing cell describes a book production cannot run. **11 of the 25 walk-forward
   passes in the whole corpus are timeout cells** and are excluded here — the selection rule of
   `e35-passed-unshipped-proposal-2026-08-31.md` § 1, applied rather than re-derived.
2. **An INERT fold is scored as a win.** A fold where the cell changed no trade reads `ok: True` and
   lands in `wf_wins`, so a cell can report 5/6 while only 2 folds did anything
   (`BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`). **The folds were read, not the summary
   quoted**, and two cells are excluded on it: `spy_trend_long_1d` `tp2.5` and `uso_trend_1h` `tp4`
   each report 5 wins with **3 of 6 folds inert** — 2 real wins from 3 effective folds. Both legs
   keep a *different*, uncontaminated cell in the table, so neither leg's verdict changes; had I
   quoted `wf_wins` the two would have entered as strong evidence.

**⚠️ `path B` IS THE WEAKER ROUTE AND SIX OF THE TEN RIDE IT.** Path B asks only for net_R across
folds. Reading each cell's own gate fields, all six path-B cells here **FAIL** `gate_is_passed`
and/or `gate_oos_passed`, every one of them on **`maxdd_worse`** — what is on offer is *more net R
bought with more drawdown*, not a free improvement. That is the same finding
`e35-passed-unshipped-proposal-2026-08-31.md` § 3 made about its own two named winners. **Four cells
pass the strong route cleanly:** `ada_pullback_2h` `tp4`, `gld_pullback_1h` `tp4`,
`scha_trend_long_1d` `tp1.5_sm3`, `uso_trend_1h` `tp4_sm2`.

**⚠️ `trend_donchian_eth_prop`'s cell is BLOCKED, not merely shipped.** Its `bracket_geometry.status`
reads `blocked:prop_ev_gate_would_be_invalidated` — changing the bracket would invalidate the prop
account's EV gate. The declared `tp_r: 6.0` matches the surviving cell, so nothing is owed, but the
reason it is closed is a prop-ruleset constraint and not this evidence.

**One leg carries an unshipped proposal that this packet does NOT re-open:** `gld_pullback_1h` reads
`passed_unshipped`, but its unshipped cell is `sm1.5` — a **stop-multiplier**, not a target. Its
`tp_r: 4.0` is already declared. That proposal is already in front of the operator in the 2026-08-31
memo and is out of scope here.

---

### 3.C — Change nothing, BY EVIDENCE (31 legs)

**These legs were swept and the sweep came back empty.** ~177–183 `tp_r` cells each across
{1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0}, and **zero survive walk-forward without a timeout component**.
The declared value — the `tp_r: 50` sentinel on 28 of them — **stands by evidence, not by nobody
having looked.** That is MI-307 § 7.1's conclusion, here extended from its 19 legs to 31.

**POPULATION: as § 3.B. `n (MFE)` and `p90` are MI-307's uncapped arm where it reached the leg
(13 of the 31); `—` means no crypto candle feed, not a missing result.**
| leg | tf | declared `tp_r` | cap_r p50 | n (MFE) | p90 | cells swept | survivors | book |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `avax_pullback_2h` | 2h | 50.0 | 2.27 | 398 | 3.50 | 181 | 0 | shadow |
| `eth_pullback_2h` | 2h | 50.0 | 2.61 | 368 | 3.09 | 178 | 0 | **REAL** |
| `eth_pullback_prop_2h` | 2h | 6.0 | 2.51 | 335 | 3.33 | 179 | 0 | shadow |
| `gdx_pullback_1d` | 1d | 50.0 | — | — | — | 177 | 0 | paper |
| `gld_pullback_1d` | 1d | 50.0 | — | — | — | 177 | 0 | paper |
| `htf_pullback_trend_2h` | 2h | 50.0 | 2.93 | 384 | 3.12 | 181 | 0 | shadow |
| `iaum_pullback_1d` | 1d | 50.0 | — | — | — | 178 | 0 | **REAL** |
| `ief_pullback_1d` | 1d | 50.0 | — | — | — | 178 | 0 | **REAL** |
| `mes_trend_long_1d` | 1d | 50.0 | — | — | — | 178 | 0 | paper |
| `mgc_trend_1h` | 1h | 50.0 | — | — | — | 178 | 0 | shadow |
| `mhg_pullback_1d` | 1d | 50.0 | — | — | — | 178 | 0 | paper |
| `qqq_pullback_1h` | 1h | 50.0 | — | — | — | 177 | 0 | paper |
| `slv_pullback_1d` | 1d | 50.0 | — | — | — | 178 | 0 | **REAL** |
| `slv_trend_1h` | 1h | 50.0 | — | — | — | 181 | 0 | shadow |
| `sol_pullback_2h` | 2h | 50.0 | 1.74 | 200 | 4.75 | 178 | 0 | paper |
| `splg_trend_long_1d` | 1d | 50.0 | — | — | — | 178 | 0 | paper |
| `spy_pullback_1h` | 1h | 50.0 | — | — | — | 177 | 0 | paper |
| `squeeze_breakout_4h` | 4h | 50.0 | 2.96 | 112 | 3.80 | 179 | 0 | paper |
| `tlt_pullback_1d` | 1d | 50.0 | — | — | — | 178 | 0 | **REAL** |
| `tlt_pullback_1h` | 1h | 50.0 | — | — | — | 177 | 0 | **REAL** |
| `trend_donchian` | 1h | 50.0 | 7.20 | 400 | 5.63 | 178 | 0 | **REAL** |
| `trend_donchian_1h` | 1h | 50.0 | 5.26 | 843 | 4.50 | 178 | 0 | shadow |
| `trend_donchian_ada_4h` | 4h | 50.0 | 2.00 | 173 | 5.51 | 179 | 0 | paper |
| `trend_donchian_avax_4h` | 4h | 50.0 | 2.46 | 233 | 4.46 | 181 | 0 | paper |
| `trend_donchian_eth` | 1h | 50.0 | 4.01 | 633 | 4.46 | 177 | 0 | paper |
| `trend_donchian_eth_4h` | 4h | 50.0 | 2.44 | 167 | 6.46 | 182 | 0 | **REAL** |
| `trend_donchian_sol` | 1h | 50.0 | 3.05 | 339 | 4.71 | 177 | 0 | paper |
| `trend_donchian_sol_4h` | 4h | 50.0 | 2.32 | 193 | 6.53 | 183 | 0 | paper |
| `trend_donchian_sol_prop` | 1h | 6.0 | 3.04 | 370 | 3.53 | 179 | 0 | prop |
| `xauusd_trend_1h` | 1h | 50.0 | — | — | — | 178 | 0 | paper |
| `xrp_pullback_2h` | 2h | 3.0 | 2.12 | 268 | 4.56 | 180 | 0 | **REAL** |


**⚠️ ON 6 OF THESE LEGS THE DECLARED `tp_r` IS OVERRIDDEN BY THE CLAMP ON A MAJORITY OF TRADES**, so
"change nothing" is doubly true — the declared number is largely not the operative one. MI-307
§ 4.1 measured it: `eth_pullback_prop_2h` 96.7%, `trend_donchian_sol_prop` 93.8%,
`trend_donchian_eth_prop` 79.0%, `xrp_pullback_2h` 78.4%, `ada_pullback_2h` 71.1%,
`trend_donchian_xrp_4h` 69.9%. **6 of 6 legs that declare a real `tp_r`.**

**⚠️ AND ABOVE `cap_r` A DECLARED `tp_r` IS INERT.** On **13 of MI-307's 19 legs the effective reach
at 4R and at 10R differ by less than 1 pp** — for those legs, declaring `tp_r: 4` and declaring
`tp_r: 50` produce the same book. Any proposal in that band would be a documentation change wearing
the costume of a parameter change.

**⚠️ A ZERO-SURVIVOR SWEEP IS NOT A STATEMENT THAT NO TARGET WOULD PAY.** It is a statement that
**these seven values, at ~180 cells per leg, did not generalise** — and at that multiplicity the
1–3 cells that passed IS+OOS on some legs are what noise produces. The sweep also measures **net-R
under the current trail**, so a target competes with a trail that is already taking profit.

---

### 3.D — Nobody looked (6 legs)

**This is `unknown`, and it is deliberately not pooled with § 3.C.** These legs have neither a
distribution nor a sweep cell.

| leg | tf | clamped? | declared | why unmeasured | book |
|---|---|---|---:|---|---|
| `fade_breakout_4h` | 4h | yes | 50.0 | no harness branch in `classify()` (MI-307 § 1.1) | shadow |
| `ict_scalp_mgc_15m` | 15m | no | 1.5R | MGC is non-crypto — no candle file resolves | paper |
| `qld_trend_long_1d` | 1d | yes | 50.0 | no free-lane candle feed; `tp_intent: unexamined` | paper |
| `tqqq_trend_long_1d` | 1d | yes | 50.0 | no free-lane candle feed; `tp_intent: unexamined` | paper |
| `turtle_soup` | 15m | no | 1.0R | no harness branch in `classify()` | shadow |
| `vwap` | 5m | no | — | no harness branch in `classify()`; `enabled: false` | shadow |

**None is a data-availability wall in the same sense.** Three are a **one-branch resolver gap** —
`classify()` has no `fade`/`turtle_soup`/`vwap` branch, and `backtest_fade.py` already implements
both `--emit-trades` and `--tp-cap-pct`, so `fade_breakout_4h` is reachable today by adding a
branch. `ict_scalp_mgc_15m` is the one leg of the scalp family whose asset class has no crypto
candle file, so it inherits the § 3.A question with **no evidence at all** — and it declares the
same `tp_at_r: 1.5` as the seven crypto legs while trading gold futures. **`qld`/`tqqq` are the real
feed limit**, and MI-195 already put their `tp_intent` disposition to the operator separately.

---

## 4. What the operator is asked to decide

**Nothing in this packet is a value.** Three decisions, ordered by how much evidence stands behind
them, and **none is recommended** — the evidence does not pick one.

| # | decision | tier | what this packet supports |
|---|---|---|---|
| **1** | **The `ict_scalp` family's `tp_at_r`** — keep 1.5R, or move it. 7 live legs, n = 557–1,664 each. | **Tier-3** | The distribution **exists** and says the target sits at the p80, truncating the p90 by 14.6–50.9%. It supports **asking the question**, not a value — and its control is weak (§ 3.A). |
| **2** | **`TP_VENUE_CAP_PCT`** — leave it, **declare** it, or set it per leg. | **Tier-3** (leave/per-leg); **Tier-1** (declare) | **Declaring it is supported now.** **22 legs carry `tp_intent: {mode: none}`, of which 20 give the reason `"trail_is_the_profit_exit"`** (re-counted against `config/strategies.yaml` this session; MI-307's "22 legs" is the `mode: none` total and the two figures are not the same set), and that reason is **not accurate as stated** — on `trend_donchian_ada_4h` the trail is the profit exit on 56% of trades and the clamp on the other 44%. Setting it **per leg is NOT supported** (§ 5). |
| **3** | **Whether `fade_breakout_4h` and `ict_scalp_mgc_15m` should be measured at all** — one resolver branch and one feed question. | Tier-1 to build | Supported: both are one-change-wide, and `ict_scalp_mgc_15m` currently carries a fleet-default target on a different asset class with zero evidence. |

**Decision 2's "declare" option is the cheapest true thing available** and is the only item here
with no counter-argument: it changes no order, and it stops 22 legs from documenting a mechanism
they do not use.

---

## 5. What this does NOT establish

- **Not a P&L claim, anywhere.** Every reach figure says a target would have been **touched**. It
  says nothing about whether the leg earns more, because a target further out also converts trades
  that currently take profit into trades that give it back.
- **Not a fidelity clearance — and do not read § 3.B's shipped cells as one.** Gate condition 1
  (backtest↔live exit-location fidelity) is **UNMET on all 44 enabled+live legs**, every one grading
  `insufficient_n` with max `n_live` **25** against a floor of **30**. MI-307 re-aimed that abstain
  (the cited ~2.5× is a horizon-composition artifact, not infidelity) and **opened a new question it
  did not close**: at matched timeframe, backtest p90 exceeds live p90 on **12 of 12** legs, median
  **2.8×**. The remainder is a **soak** — waiting, not building. **Any argument in this packet that
  leans on backtest-to-live fidelity is leaning on an unmet condition, and § 3.A's is the one that
  does.**
- **Not a measurement.** Every distribution here is simulated; `src/runtime/provenance.py`
  classifies every harness row behind it `unverified`, correctly — a harness row carries no
  `exit_price_source` because no broker filled it.
- **Not a re-validation of the two instruments.** Neither was re-run. This unit joins their
  committed artifacts, and inherits every caveat each carries.
- **Not applicable to the 6 legs of § 3.D.**
- **Nothing was armed, merged into `config`, or proposed as a value.**

---

## 6. Filed, not fixed

- **`BL-20260918-SCRIPTS-ON-SYS-PATH-SHADOWS-THE-ML-PACKAGE`** — `m20_fleet_exit_sweep` inserts
  `<repo>/scripts` at `sys.path[0]` on import, after which `scripts/ml/` **shadows the real
  top-level `ml` package**, so any later `import src.runtime.pipeline` dies on
  `No module named 'ml.datasets'`. Measured: the two are usable in one process only if the pipeline
  symbol is bound **first**. This unit works around it with an eager import and a comment saying
  why; the fix is a Tier-1 change to that script's path handling and is not this unit's call.
- **Two legs carry a target chosen for a different asset class** — `ict_scalp_mgc_15m` (gold
  futures) declares the same `tp_at_r: 1.5` as the seven crypto scalp legs, with no evidence of any
  kind. Recorded in § 3.D rather than proposed on, because proposing a number for a leg with n=0 is
  the thing this packet exists to refuse.

---

## 7. Reproduce

```bash
python3 scripts/research/mi317_target_geometry_packet.py --selftest   # 39 checks, 7 negative controls
python3 scripts/research/mi317_target_geometry_packet.py \
    --write docs/research/mi317-target-geometry-packet-2026-09-18.json --report
```

Artifact: [`mi317-target-geometry-packet-2026-09-18.json`](mi317-target-geometry-packet-2026-09-18.json)
— all 55 legs with declared geometry, routing, what sets the target, the MFE quantiles at each
leg's own n, every surviving sweep cell with its inert-fold accounting, and the derived
disposition.

⚠️ **THIS PARAGRAPH SAID "Until #12515 merges, MI-312's artifact is not on `main`" AND THAT IS NOW
FALSE — do not re-quote it.** It was true when § 3.A was built and became stale mid-session, in the
dangerous direction: a reader acting on it would go hunting a branch for a file that is on `main`.
**#12515 MERGED** while this PR was open (`origin/main` moved `d4ff439ce → 8460ff8b2`), so
`docs/research/mi312-scalp-target-arms-2026-09-18.json` is canonical and **no `--mi312` override is
needed** — the two commands in § 7 are the whole reproduction.

**VERIFIED RATHER THAN ASSUMED, because § 3.A was built before the merge and a silent divergence
would have been invisible.** Two checks, both run after merging `origin/main`: the branch copy this
memo was built from (`origin/claude/mi312-scalp-family-capped-arm@f271931ed`) is **byte-identical**
to the artifact that landed, and re-running the instrument against the canonical path with **no
override** reproduces this memo's committed JSON **byte-identically**. So every § 3.A figure was
computed from exactly what is now on `main`.

⚠️ **The absent-artifact path is still live and still tested**, because it is what a future reader
on an older tree will hit: with the file missing, the 8 legs of § 3.A grade `not_measured` rather
than silently reading as unproblematic, and a selftest asserts that rather than letting them
default.
