# Roadmap + Toolbox Assessment — 2026-07-29

> **Type:** Tier-1 research/analysis (docs + read-paths only; no live-path change).
> **Scope:** what we've *attempted*, what we've actually *accomplished*, how well we
> use our *toolbox*, and what *tools are worth investing in* — judged against the
> three north-star goals:
> 1. **Effective technical strategies**
> 2. **A robust macro trading unit**
> 3. **The overall "AI master trader"** (one engine that sees everything)
>
> **Method:** three parallel read-only sweeps + firsthand reads of `ROADMAP.md`,
> `ROADMAP_MACRO.md`, `docs/AI-TRADERS-ROADMAP.md`, `docs/strategy-coverage-matrix.md`,
> the three review backlogs, and the strategy/account configs, plus a **live
> Bigdata.com pilot** (§6). Live-registry/VM figures that can't be read from a
> read-only checkout are reconstructed and flagged as such (§7).

---

## 1. Executive summary

Metis-Insights is an **exceptionally disciplined, honesty-first research platform**.
Its defining feature — and the single most important thing this assessment can
tell you — is the **gap between what is *built/attempted* and what actually
*influences real money*:**

- **Real money at risk = ONE account** (`bybit_2`, real-money, `mode:live`) running
  **6 automated strategy legs**, plus **3 manual prop legs** (`breakout_1`, placed by
  the operator over Telegram). `ib_live` and `alpaca_live` are wired but held
  `dry_run`. **~50 other "live" strategy legs execute on paper.**
- **The ML fleet is ~90 manifests → effectively ONE advisory head** that influences a
  real-money decision (`btc-regime-15m-lgbm-fc-pcv-v1`, the BTC vol-gate). The rest
  is a large, well-governed shadow/candidate soak.
- **The macro unit places nothing.** The thesis engine, event calendar, valuation
  feed, and pairs executor are genuinely built, but the value edge tested **null and
  is now "conclusively exhausted,"** the FRED producer is unwired, the economic
  calendar is inert (`events: []`), and **no macro order path exists** (the P5
  defined-risk executor is "not yet wired").

This is not a criticism — it is the system working as designed. The roadmap is
unusually **honest about negatives**: M18, M19, M21, M22, M23, M28-value, and M29
are all documented as *attempted → gated out*, not quietly dropped, and the newest
milestone (M36) is a deliberate **consolidation** ("consolidate-before-expand").
The discipline is a genuine asset.

**The one theme that ties all three north stars together:** the machinery for
*generating and grading* ideas is mature and heavily used; what's thin is the
**input spine** (data) feeding it and the **output spine** (an order path) letting
graded macro ideas act. The recent frontier is correctly described in the repo's own
07-28 reconcile as *"data-accrual + trainer-gated + Tier-3 waits,"* not "build the
next thing." **The highest-leverage investments are therefore data and compute, not
more strategies or more models.**

---

## 2. North-star scorecard

| North star | What's built | What's actually LIVE (real money) | Binding constraint | Honest grade |
|---|---|---|---|---|
| **1 · Effective technical strategies** | Deep: 55 strategy blocks, mature backtest suite (walk-forward, triple-barrier, meta-label), M7 review gate, M8 tuning, exit-refinement pipeline, M30 quant-research platform | 6 automated legs on `bybit_2` + 3 manual prop legs | Governance lagging proliferation (33/44 strategies in regime-coverage **debt**); low fill rates; crypto-skewed | **B+** — strongest area; growth outran governance |
| **2 · Robust macro trading unit** | Thesis engine, event calendar, valuation feed, pairs executor, cost-model design, M28/M29 research harnesses, layer-guard enforcement | **Nothing** (pairs sleeve is paper-only + net-negative on fees; macro sleeve is observe-only) | **No data spine** (FRED-only, `events:[]`) **and no order path** (P5 executor unwired); value edge tested null | **C‑** — richly scaffolded, zero live expression, edge unproven |
| **3 · Overall "AI master trader"** | ~90 ML manifests, 3-stage ladder, conviction/unified-confidence architecture (M16), allocator (M18), "one-engine" M36 integration design | **1 advisory head** (BTC vol-gate); conviction/news sizing is `off`/annotate | **1-OCPU trainer** compute ceiling + data-blocked features; new model *types* closed negative | **C+** — right architecture, throughput-starved, mostly shadow soak |

---

## 3. Attempted vs Accomplished — the milestone ledger

Milestones M0–M36 (main ledger) + ROADMAP_MACRO M0–M5, bucketed by *real* status.

### ✅ DONE / CLOSED (shipped + verified)
- **M0–M5** foundation → strategy testing; **M7** strategy-review gate; **M11**
  multi-strategy architecture; **M13 S1+S2** AI Analyst; **M8** tuning tooling +
  first live tune.
- **ROADMAP_MACRO M0a** — import-linter layer enforcement (5 contracts, 0 broken).

### 🔄 ACTIVE (real work landing now)
- **M14** ML-optimization, **M15** Alpaca/OANDA/IBKR-STK venue expansion (largely
  shipped; two real-money accounts held `dry_run`), **M16** unified-confidence
  (Design-A vol-gate LIVE for BTC), **M17** full-system audit, **M20** exit
  refinement (E3 live; fleet-wide `exit_ladder` not literally complete), **M24**
  net-R, **M25** ML promotion/consolidation (real promotions executed 07-20/21),
  **M26** regime-transition, **M27** scalp expansion, **M28/M29/M30** research,
  **M36** consolidation. Plus long-running **M6/M9/M10/M12**.

### 🟥 HONEST-NEGATIVE / PARKED (attempted → gated out — *a feature, not a bug*)
This bucket is the strongest evidence of process health: expensive ideas were built,
tested honestly, and **stopped** on written pre-registered criteria rather than
shipped on a story.
- **M18** portfolio allocator — EV scorer's *selection* does not beat dumb priority
  (OOS AUC ≈ 0.51); P2/P3 **PARKED** pending a proven `P_win` input.
- **M19** new model *types* — frozen-embeddings / TCN deep-sequence / SSL wide-corpus
  encoder all **closed negative**; only the `fc` forecast feature survived.
- **M21** entry refinement — E-3 **closed as an honest negative** (one-bar-ahead
  leakage); dormant since 07-14.
- **M22** pairs sleeve — **not real-money-viable as specified**; live on paper only
  and net-negative on taker fees.
- **M23** meta-labeling — **P2 honest-negative, NO-GO on P3** (eval-side label wall).
- **M28 value frontier** — **conclusively exhausted**; one robust lead (`vix_term`,
  Sharpe ~0.0–0.18 w/ large DD) but "do not productionize standalone." Sub-experiments
  M32/M33/M34/M35 all null.
- **M29** AI system-dynamics gas model — P1b `equifinal_no_edge`, P1c
  `park_deeper_investment` (a third null alongside value-P4 and gas-P1b).

### 📋 PLANNED / PROPOSE-ONLY (spec'd, gated, not built or observe-only)
- **M18/M19/M23** (Tier-3 propose-only), **ROADMAP_MACRO M1–M5** (energy calendar →
  event-backtest → paper → carbon → live), all downstream of wiring a point-in-time
  producer.

**Read:** the DONE and ACTIVE columns are dominated by *infrastructure, plumbing, and
research machinery*. The HONEST-NEGATIVE column is dominated by *edge discovery*. The
system is very good at building and grading; it has **not yet found a second
durable, deployable edge** beyond the ICT/crypto-regime core — and it says so plainly.

---

## 4. Toolbox utilization

| Category | Inventory (representative) | Utilization |
|---|---|---|
| **Claude skills** (28) | session-coordination, system-review + the 3 reviews, vm-ops, diag-data, git-actions, backtesting, exit-refinement, model-training, new-strategy/new-broker, drift-remediation, **macro-research** | **Mature & heavily used** — the autonomous-ops backbone. *macro-research skill added 2026-07-29 (rec #6), closing the last catalog gap.* |
| **GitHub Actions** (~110) | CI invariant guards (layer-guard, strategy-coverage-guard, dry-run-guard, env-gate-guard…), diag relays, provisioning, training, backtest harnesses, m28–m34 macro grading | **Mature & heavily used.** *Note: macro-grade workflows are `workflow_dispatch`-only (run-on-demand), unlike the scheduled ML cadence.* |
| **Backtest / research harnesses** | `backtest_*.py` (13 strategies), `src/backtest/`, M5 `/test` consumer, trainer-VM sweep mirror, `src/research/` (triple-barrier, meta-label, microstructure) | **Mature & heavily used** — arguably the deepest single area. Strongest on intraday crypto/futures. |
| **Market-data feeds** | Bybit, IBKR, Alpaca, OANDA connectors; historical fetchers; **news layer** (RSS active, NewsAPI key-gated) | **Connectors mature; news built-but-underused** (RSS-only, influence-only). |
| **ML training infra** | Trainer VM + `run_training_cycle.sh`, `ml/` package, ~90 manifests, 3-stage ladder, shadow-drift, GPU burst | **Mature, arguably over-built vs compute** — ~90 manifests on **1 OCPU**. Crypto-regime-skewed; **zero macro/fundamental ML heads.** |
| **Macro / fundamental tooling** | `macro_thesis/` thesis engine, valuation/event feeds (FRED), `scripts/macro/` probes, `src/sysdyn/` | **Built but observe-only.** No order path (P5 unwired), inert calendar (`events:[]`), FRED-only data, no macro ML, no macro skill. |

**Standout gap — the premium-data spine.** Every premium/fundamental channel is
stubbed as "honest-null until wired." In particular: **Bigdata.com is already
available as a connected MCP tool and is even name-checked in
`config/economic_calendar.yaml` as a *future* source — but it is NOT wired into the
bot.** The economic-calendar file's `events:` list is literally empty. This is the
single clearest case of an available tool going unused.

---

## 5. Tools worth investing in — prioritized backlog

Ranked by leverage against the three north stars. Each item names the gap it closes,
the expected payoff, and a rough effort tier.

### #1 — Wire a premium data + economic-calendar spine (Bigdata.com). *[North star #2, unblocks #3]*
**The single highest-leverage change.** It turns the macro unit from *observe-only*
into *able to form a point-in-time view*, and directly unblocks ROADMAP_MACRO M1's
"clean joined dataset" gate and the M28 producer (`MB-20260723-M28-VALUATION-PRODUCER-UNWIRED`).
The §6 pilot proves it returns — **in one call** — the forward economic calendar
with consensus, actual-vs-consensus-vs-surprise per release (incl. the EIA natural-gas
storage number that is ROADMAP_MACRO's canonical M1 test case), CFTC positioning, VIX
term structure, and the Treasury curve. That is precisely the *surprise-vs-consensus*
input M28/M29/energy has **no source for today**.
**Effort:** medium (an off-VM producer that writes point-in-time snapshots the live
tick reads — the compute-invariant §1c of ROADMAP_MACRO already anticipates this).
**Watch-item:** LLM/data $-budget (`insights_usage`) — widen deliberately, keep observable.

### #2 — Build the P5 defined-risk order path. *[North star #2]*
Today nothing can trade a macro thesis even if one clears the gate. Sequence this
**after** an edge clears (no point wiring execution for a null) — but it is the
structural blocker between "macro research" and "macro trading unit."
**Effort:** medium; reuses the wired IB adapter (MNG-via-IB) per ROADMAP_MACRO M3/M5.

### #3 — Relieve the 1-OCPU trainer compute ceiling. *[North star #3]*
The binding constraint on the entire ML fleet (OOMs, serialized cycles, ~90 manifests
on one core, promotion-readiness sweeps OOM the 6 GB box). More advisory-head
throughput is how #3 stops being "one live head." Options, cheapest first: lean harder
on `gpu-burst-train.yml` / GitHub-runner offload; then a modest **paid spot-compute
budget**. **Effort:** low–medium (tooling exists; this is a routing + budget decision).

### #4 — Close the data-blocked ML feature gaps. *[North star #3]*
The ml-backlog names them: L2/order-flow capture (VPIN heads read dead), longer OI
history (`open_interest_change` ~99.6% dead), funding capture (first pull returned 0
rows). **Every "no-edge" head may be an input problem, not a model problem** — worth
ruling out before concluding a model type is dead. **Effort:** medium (data capture).

### #5 — Pay down the 33-strategy regime-coverage debt. *[North star #1]*
The roster grew 6 → 44+ live strategies while `regime_policy.yaml` stayed at 4 celled
· 7 exempt · **33 in debt** (`BL-20260717-REGIME-COVERAGE-DEBT`). The governance
tooling exists (`strategy-coverage-guard`); the debt is the gap. Folds into M36
consolidation. **Effort:** medium (per-strategy direction-aware cells).

### #6 — A dedicated `macro-research` skill. *[Cross-cutting]* — ✅ SHIPPED 2026-07-29
Macro work runs ad hoc under `research-driver`. A skill gives it the same repeatable
rigor the technical side already has (`backtesting`, `exit-refinement`,
`model-training`). Cheap, high consistency payoff. **Effort:** low.
**Built:** `.claude/skills/macro-research/SKILL.md` — codifies the three binding
invariants (off-VM compute · point-in-time/no-lookahead consensus · verify-the-source-
before-you-build), the data→PIT-store→honest-edge pipeline, and the toolbox map
(`src/units/strategies/macro_thesis/`, `scripts/macro/`, the macro workflow cluster,
`comms/macro/` artifacts, `config/macro_*.yaml`). `research-driver` dispatches to it.

### #7 — Broker-truth cost coverage across all accounts. *[Cross-cutting data integrity]*
Only 3/8 accounts have broker-truth; `bybit_2` journal under-records PnL vs wallet
(−$33 journal vs −$262.52 truth). This blocks M24 net-R and makes honest grading
untrustworthy *everywhere* — a precondition, not a feature. **Effort:** low–medium.

> **✅ IN PROGRESS 2026-07-29.** The gap was a *rollout* gap, not a hard problem
> (scheduling already existed since 2026-07-13/07-19; the pull was single-account).
> **Bybit trio** (#7891) + **Alpaca trio** (#7895) shipped — the 6 API-automatable
> accounts now accrue exchange-truth fills on the daily timer. Remaining is the
> **operator-gated tail**: `ib_paper` needs an **IB Flex token** (design +
> operator steps: [`broker-truth-ib-flex-DESIGN.md`](broker-truth-ib-flex-DESIGN.md);
> secret slots minted via #7896), and `bybit_2` lifetime wallet-truth needs the UM
> CSV export (netting stitch). No further code for the automatable set.

**Sequencing note:** #1, #3, #6, #7 are independent and can start now. #2 waits on a
macro edge clearing the gate (#1 makes that possible). #4 and #5 are steady
consolidation work that fits M36.

---

## 6. The Bigdata.com pilot (live evidence for recommendation #1)

To make recommendation #1 concrete rather than asserted, I ran a live pilot against
the already-connected Bigdata.com MCP on **2026-07-29**. A single
`bigdata_country_tearsheet(US)` call returned, in one shot, the exact data classes the
macro unit is missing:

- **A forward economic calendar with consensus** (Jul 29 – Aug 3): Fed rate decision
  (consensus 3.75%), Core PCE (cons. 0.2% MoM / 3.3% YoY), Q2 GDP (cons. 2.1%),
  **EIA Natural Gas Storage Change (Jul 30)**, ISM Manufacturing PMI, Michigan
  sentiment. → directly fills the inert `config/economic_calendar.yaml` `events: []`.
- **Actual vs Consensus vs Surprise% for every recent release** — e.g. CPI (Jun)
  actual −0.40% vs cons. −0.10% (**surprise −300%**), NFP (Jun) 57 vs 110
  (**−48.2%**), Philly Fed 41.4 vs 13 (**+218.5%**). → this *is* the
  surprise-vs-consensus signal M28's event studies and ROADMAP_MACRO M2 need and have
  **no source for today**.
- **The canonical M1 test case, delivered live:** `EIA Natural Gas Storage Change`
  2026-07-23 **actual 32 vs consensus 29 (surprise +10.3%)** — the exact
  release/consensus/realized triple ROADMAP_MACRO M1 is built to study.
- **CFTC net positions** (Gold / Oil / S&P 500) — the same positioning data M29's COT
  sleeve currently hand-scrapes from CFTC Socrata, in the same feed.
- **VIX term / market indices / full Treasury curve + key spreads** — feeds M28's
  `vix_term` (the one robust lead) and the M32 credit/rates sub-experiment.

`find_securities("UNG")` cleanly resolved the US Natural Gas Fund to entity
`4EAE01`, confirming the entity-resolution path for company/ETF-scoped follow-ups
(sentiment tearsheets, event calendars, filings/transcript search).

**Conclusion:** the pilot is decisive. The macro unit's #1 blocker (no
consensus/surprise/calendar data) is solved by a tool **we already have connected**.
The remaining work is a point-in-time producer + PIT integrity discipline (never use
revised consensus — ROADMAP_MACRO §6 already specifies this), not a new integration
from scratch.

---

## 7. Appendix — sources & caveats

**Primary sources (all in-repo, read-only):**
`ROADMAP.md` (milestone table; Historical Sprint Ledger; authority-ladder north star),
`ROADMAP_MACRO.md` (macro family + change log), `docs/AI-TRADERS-ROADMAP.md`,
`docs/strategy-coverage-matrix.md`, `config/strategies.yaml`, `config/accounts.yaml`,
`config/pairs.yaml`, `config/macro_theses.yaml`, `config/macro_valuation.yaml`,
`config/economic_calendar.yaml` (`events: []` confirmed), `ml/configs/` (~90 manifests),
`docs/claude/{health,performance,ml}-review-backlog.json`, `docs/sprint-logs/S-*2026-07-*`,
`.claude/skills/`, `.github/workflows/`.

**Backlog counts (open items):** health 55 · performance 47 · ml 36.

**Caveats:** the live model registry, trainer state, and actual `bybit_2` fills live
on the production VMs and are **not reachable from a read-only checkout**. The
"advisory = 1 head" count and live-leg counts are reconstructed from `strategies.yaml`,
`accounts.yaml`, the `CLAUDE.md` env-doc contract, and the 07-19 → 07-28 ML/reconcile
sprint logs — not a live registry dump. Confirm against `/api/bot/ml/registry` +
`/api/bot/strategies` before treating any single figure as authoritative. Milestone
statuses are as written in `ROADMAP.md` on 2026-07-28.

**Nature of this document:** an assessment/mapping, not a proposal to build. Every
Tier-3 implication (wiring a data spine, a macro order path, paid compute) is a
*proposed investment* for operator decision, not an approved change.
