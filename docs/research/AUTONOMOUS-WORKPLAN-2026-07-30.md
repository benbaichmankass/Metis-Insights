# Autonomous Work Plan — 2026-07-30

> **Type:** Tier-1 planning doc. Successor to
> [`roadmap-toolbox-assessment-2026-07-29.md`](roadmap-toolbox-assessment-2026-07-29.md)
> (yesterday's 7-recommendation plan against the three north stars).
>
> **Purpose:** (1) close the books on what rec #1–#7 actually accomplished in the
> 07-29 → 07-30 cycle, (2) record three **blockers found during this planning pass**
> that change the priority order, and (3) hand a fresh session an ordered,
> dependency-correct plan it can execute autonomously for a full day without
> stalling on an operator gate.
>
> **Method:** firsthand reads of the shipped code/config/workflows + the PIT snapshot
> store + the scorecards, on `main` @ `d90ca90`. Findings are labelled by confidence:
> **[verified]** = read directly from code/data in this pass · **[estimate]** =
> quantified inference stated with its arithmetic · **[unverified]** = needs a runner.

---

## 1. Scorecard — rec #1–#7 after one cycle

An unusually productive 24h: **~30 PRs merged**, every recommendation moved.

| # | Recommendation | Status | Evidence |
|---|---|---|---|
| **#1** | Premium data + econ-calendar spine (Bigdata.com) | **BUILT — but its payoff measurement is broken (§2.1)** | `scripts/macro/econ_calendar_{data,produce}.py`, FXStreet keyless source (#7876), `config/economic_calendar.yaml::events` now **populated** (was `[]`), daily cron `30 22 * * *`, 898 snapshot rows |
| **#2** | P5 defined-risk macro order path | **NOT STARTED — correctly deferred** | Gated on a macro edge clearing M2; §2.1 shows that verdict is calendar-bound to ≥ Sept |
| **#3** | Relieve the 1-OCPU trainer ceiling | **SHIPPED (v1), 2 tail items open** | Proposal corrected to **$0 free-runner offload** (#7908); `trainer-offload-train.yml` (#7909) + 2 fixes (#7911, #7914). Open: register-back into the live registry; microstructure publish |
| **#4** | Close data-blocked ML feature gaps | **DIAGNOSED + join wired; re-blocked on a data *distribution* fact** | Order-flow capture found **alive @ 15,422 bars** (~6 weeks stale "data-blocked" status corrected); `build_microstructure` wired (#7901); A/B **unmeasurable** — volatile = **0.27%** of the captured window |
| **#5** | Pay down 33-strategy regime-coverage debt | **Engine + gate + evidence shipped; 1 cell authored — evidence now suspect (§2.2)** | `regime_debt_matrix.py` (#7916), `regime_cell_walkforward.py` (#7919), 4 findings docs, 35-row matrix on free runners. Debt **33 → 32**, celled 4 → 5 |
| **#6** | A dedicated `macro-research` skill | **✅ DONE** | `.claude/skills/macro-research/SKILL.md` (#7884) |
| **#7** | Broker-truth cost coverage | **✅ DONE for the automatable set; operator tail remains** | Bybit trio (#7891) + Alpaca trio (#7895) → 6/8 accounts accrue truth. Tail: `ib_paper` IB Flex token, `bybit_2` UM CSV — **both operator-only** |

**The honest headline:** the *machinery* asked for by every recommendation got built.
What did **not** happen is any new **measured edge** — and §2 explains why two of the
three measurement paths were silently broken the whole time.

---

## 2. Three blockers found in this pass (these reorder the plan)

All three are the **same failure mode**: a pipeline that reports a *plausible* honest
negative (`insufficient_history`, `no cell warranted`) while a defect upstream
guarantees that result. This is precisely the normalization pattern
`CLAUDE.md` § *"If you see something, say something"* exists to kill — the ETH-xa
lesson, repeating.

### 2.1 The M1 event study has **never joined a single price bar** [verified]

`comms/macro/econ_event_study_scorecard.json` reports `price_bars: 0` and
`verdict: "no_data"` for **both** natgas and crude. Two independent causes:

1. **yfinance is not installed on the runner.** `econ-event-study.yml` installs only
   `python -m pip install --upgrade pip "pyyaml>=6.0"`. `_resolve_fetchers` catches the
   `ImportError` and degrades to Stooq — so the primary feed never runs at all.
2. **The Stooq fallback URL is malformed for `=F` futures tickers.**
   `_STOOQ_URL = ".../?s={sym}.us&i=d"` lowercases and appends `.us` (the **US-equity**
   suffix), producing `s=ng=f.us` — a literal `=` inside the value plus an equity
   suffix on a futures symbol. Stooq's futures form is `ng.f`.
   *(Egress to stooq.com is firewalled from the planning sandbox, so the mapping fix
   itself is **[unverified]** — assert it on a runner.)*

**Why it stayed invisible:** the fetch step ends in
`|| echo "::warning::macro-candle fetch degraded"` and the study treats an empty panel
as a soft `price_bars=0`. So the workflow goes **green**, lands a scorecard, and the
verdict reads `insufficient_history` — indistinguishable from "just needs more time."

**Consequence:** `ROADMAP_MACRO.md` M1 currently states the price-join is *BUILT* and
"Remaining: **multi-year PIT-consensus accrual** … the verdict self-graduates from
`insufficient_history` as history accrues." **It will not self-graduate.** With a
zero-bar join the verdict is pinned at `no_data` forever, no matter how much
consensus history accrues. That roadmap row is **doc drift to correct.**

### 2.2 The rec #5 equity/ETF evidence was graded with a **~25× fee over-charge** [verified]

Yesterday's other session fixed exactly this bug in the **live close path** —
`profile_loader.roundtrip_fee_bps_for()` resolves **0 bps** for commission-free
`(alpaca, spot)` US equities/ETFs instead of the flat 7.5-bps crypto-perp default
(#7930, which resolved the `spy_pullback_1h` net-R sign-flip).

**The same bug is still live in the research harness built the day before:**

```
scripts/research/regime_debt_matrix.py:140
    "--fee-bps-roundtrip", "7.5",   # ← hardcoded, every symbol
```

`regime_cell_walkforward.py` calls `rdm.build_harness_cmd`, so it inherits it.
**All 14 commission-free instruments** are in that roster:
`SPY QQQ TQQQ QLD GLD IWM TLT IEF SLV USO GDX SPLG IAUM SCHA`.

So the entire equity/ETF regime-debt matrix (#7918), all three walk-forward verdicts
(#7920–#7924), **and the one Tier-3 OFF cell actually shipped** —
`trending.gld_pullback_1h { short: off }` — rest on a phantom fee.

**Direction and magnitude of the bias.** The harness computes
`fee_r = (bps/10_000) × ((entry+exit)/2) / risk`, and `risk = atr_stop_mult × ATR`.
For a 1h ETF pullback (`atr_stop_mult 2.5`, ATR ≈ 0.25–0.6% of price):
**≈ 0.04–0.12 R of phantom drag per trade** [estimate]. Over-charging can only make a
strategy look **worse**, so the risk is **false OFF cells** — gating trading that is
actually fine. It cannot manufacture a false edge.

Applying that to the two cells in question:

| Cell | Reported | Per trade | Phantom fee | Read |
|---|---|---|---|---|
| `qqq_pullback_1h` trending short | −2.84 R / 41 t | **−0.069 R** | ≈ 0.04–0.12 R | **Entirely inside the phantom-fee band → almost certainly a false OFF.** The doc even describes it as "small (~−0.07R/trade)" — that *is* the fee. |
| `gld_pullback_1h` trending short | −15.68 R / 36 t | **−0.436 R** | ≈ 0.04–0.12 R | **Survives**, magnitude overstated ~10–27%. Its `+32.98 R` long side is *understated*. |

`slv_trend_1h` was **refuted** (not a stable drag); removing an over-charge makes a
short look *better*, so a refutation stays refuted. No re-check needed for direction,
though it comes free in the sweep.

**This is a live-gate integrity item**, not just a research nit: a cell in
`config/regime_policy.yaml` is hard-enforced by `Coordinator.aggregate_intents`.

### 2.3 The PIT store is **retro-filled**, and that touches M1's explicit STOP condition [verified]

Of **256** distinct resolved releases (range **2026-02-11 → 2026-07-29**),
**253 were captured *after* their release** (`observed_at > scheduled_for`) — i.e.
back-read from the source's own history window, not observed point-in-time. Only
**3** were captured at-or-before release (the producer's first forward day).

M1's written stop condition is unambiguous:

> *"Consensus not available point-in-time (revised-only) → the whole study is unsafe;
> stop and re-scope the data source."*

Whether that condition **fires** depends on a fact nobody has established yet: does
FXStreet/Bigdata **revise** the consensus field after a print? (Consensus is usually
fixed once published — it is `previous` that gets revised — but M1's own gate makes
this a must-verify, not a may-assume.) Until answered, the 253 retro rows are of
**unknown PIT integrity** and the store cannot distinguish them from clean rows.

**Also binding on scheduling: sample size.** Max **n = 7** per event kind
(the weekly series: EIA crude, EIA natgas, API crude, jobless claims, Redbook, MBA,
Baker Hughes) against `min_honest_n = 12`. Even with a perfect price join **no kind
clears the honesty bar today.** Weekly families cross n=12 in **≈ 5–6 weeks**
(mid-September); monthly families (CPI, PCE, NFP) need **~12 months**.

**Strategic consequence — the single most important scheduling fact in this doc:**
the **macro sleeve cannot produce a graded edge today, tomorrow, or this month.**
Any plan that points a day of work at "get an M1 verdict" will fail. The correct
macro objective for today is to make the accrual **valid and measurable**, then step
away and let the cron do its job.

---

## 3. Where the payoff actually is

Given §2.3, the day's *edge-seeking* effort should go where **n is already adequate**:
the technical roster, on 730-day windows, on free runners. And the highest-value
single action is not new research at all — it is **re-grading existing research with
correct costs**, because §2.2 means an entire asset class (14 commission-free
equity/ETF instruments, ~12 of the 32 debt strategies) has been judged
**0.04–0.12 R/trade too harshly**.

That reframes rec #5's finding. "No cell warranted" was read as *the debt is a
bookkeeping problem, not a hidden-edge problem.* With the fee corrected, the more
likely reading is: **several equity/ETF legs are better than the matrix said**, and at
least one authored gate is over-aggressive. That is a genuine, cheap, measurable win —
and it protects a live routing gate.

Ranked by (payoff × confidence) ÷ effort:

1. **Correct the research fee model and re-grade the equity/ETF roster.** One-line
   resolver swap; re-run is $0 on free runners; corrects a live Tier-3 gate and ~12
   strategies' verdicts. **Highest leverage available today.**
2. **Fix the M1 price join + make it fail loud.** Small PR. Every day it stays broken
   is a wasted accrual day, and the silent-green is what hid it for a full cycle.
3. **Stamp PIT provenance + settle the revision question.** Cheap, and it is a
   pre-registered STOP-condition check on the macro programme's foundation.
4. **Broaden the event study to all weekly high-n families.** So that when n crosses
   12 in ~6 weeks the weekly cron lands verdicts across ~7 series automatically,
   instead of two hand-picked ones.
5. **Offload v2 register-back** (rec #3 tail). Real throughput value, largest build,
   and its headline beneficiary (the flow head) is independently
   volatile-coverage-blocked — so its payoff is **bounded**. Correctly last.

---

## 4. The plan — an ordered autonomous day

Designed so **nothing blocks on an operator decision**: where a Tier-3 call is
required the session *prepares the packet and moves on* (per `research-driver`).
Blocks are dependency-ordered; Block 2's long runner jobs are kicked off early and
harvested while Block 3 proceeds.

### Preflight (~10 min)
- Read `docs/CLAUDE-RULES-CANONICAL.md`; post a **`▶️ START`** on the coordination
  board (**issue #6927**) naming: `scripts/research/*`, `scripts/macro/*`,
  `.github/workflows/{econ-event-study,regime-debt-matrix,regime-cell-walkforward}.yml`,
  `docs/research/*`, the three backlogs. No live-path files, no VM mutation.
- `git checkout -B <branch> origin/main`.

### Block 1 — the two silent-failure fixes (~2h, 2 PRs, Tier-1)

**1A · Venue-aware fees in the research harnesses.**
- In `regime_debt_matrix.build_harness_cmd`, replace the hardcoded `"7.5"` with
  `core.profile_loader.roundtrip_fee_bps_for(symbol)`, falling back to `7.5` on
  `None` (mirroring `trade_costs.DEFAULT_FEE_BPS_ROUNDTRIP` semantics — **do not**
  duplicate the constant).
- **Sweep for the same bug class:** `grep -rn 'fee.bps\|7\.5' scripts/ src/` and fix
  every research call site that hardcodes a venue-blind fee. Record what you find.
- Emit the resolved bps into each matrix row so a future reader can see which fee
  graded it. Tests: an alpaca-spot symbol resolves 0.0, BTCUSDT/MES resolve the
  default.

**1B · M1 price join — make it work, and make it fail loud.**
- Install `yfinance` in `econ-event-study.yml` (it is the primary feed and is simply
  absent).
- Add a futures→Stooq symbol map in `fetch_macro_candles` (`NG=F`→`ng.f`,
  `CL=F`→`cl.f`, `GC=F`→`gc.f`, `ES=F`→`es.f`, `HG=F`→`hg.f`); keep the `.us` suffix
  for equities/ETFs, which is already correct.
- Prefer/allow the liquid **ETF proxies** (`UNG`, `USO`) as a documented fallback —
  they resolve on the existing equity path today.
- **Delete the silent degradation.** The fetch step must fail the job, and the study
  must exit non-zero, when `price_bars == 0` for a kind it was asked to study. A
  green run that measured nothing is the actual defect.
- Verify on a runner via issue label **`econ-event-study-now`**; require
  `price_bars > 0` in the comment before calling it done.

### Block 2 — corrected re-grade sweep (kick off early; harvest across the day)

Dispatch on free runners via issue labels; these are the same engines, so **one
corrected pass covers both the rec #5 re-grade and the debt paydown**:

- **2A** — full 35-row corrected matrix (label `regime-debt-matrix-request`, 730d).
- **2B** — corrected walk-forward for `gld_pullback_1h`, `qqq_pullback_1h`,
  `slv_trend_1h` (label `regime-cell-walkforward-request`).
- **2C** — findings doc: **a diff table vs #7918/#7924**, one row per changed
  verdict, with the fee delta attributed. This diff *is* the deliverable.
- **2D — dispositions:**
  - `qqq_pullback_1h` — if it flips as predicted, **withdraw the offered cell** and
    record a no-cell disposition. (Nothing to revert; it was offered, not shipped.)
  - `gld_pullback_1h` — if it still clears the gate, record the corrected magnitude
    and leave the live cell alone. If it **no longer clears**, open a **Tier-3 DRAFT
    revert PR** (never self-merge a `regime_policy.yaml` edit) and ping the operator.
  - Any equity/ETF leg that moves from marginal-negative to positive: record it — that
    is the asset-class-level finding, and it belongs in the performance backlog.
  - Continue **2E**: re-run the 19 **approximate** rows *faithfully* (model the
    declared levers) so the fidelity gate stops blocking their dispositions, and push
    the debt count down by **evidence**, not by assertion.

### Block 3 — macro accrual validity (~2h, while Block 2 runs)

- **3A** — Stamp every snapshot row with PIT provenance: a `pit_captured:
  forward|retro` flag derived from `observed_at` vs `scheduled_for`. Make
  `econ_event_study` able to filter on it, and report both n's in the scorecard.
- **3B** — Settle the revision question: re-pull a handful of **already-resolved**
  releases and diff their `consensus` against the stored value. Same → retro rows are
  PIT-safe (say so, with evidence, and M1's stop condition does **not** fire).
  Different → **M1's stop condition fires**: mark retro rows unusable for the gate,
  keep them as context only, and escalate to the operator, because that re-scopes the
  data source.
- **3C** — Broaden the study to every weekly high-n family (EIA crude/natgas, API
  crude, initial/continuing claims, Redbook, MBA, Baker Hughes) against liquid
  proxies, so the weekly cron grades ~7 series automatically as n crosses 12.
- **3D** — Record the honest ETA in `ROADMAP_MACRO.md`: **weekly families ≈ mid-Sept,
  monthly ≈ 12 months** — replacing the "self-graduates" language, which §2.1 shows
  is false.

### Block 4 — offload v2 (whatever time remains; stop at a checkpoint)

- **4A** — Register-back: land the offload's trained model + metrics into the
  trainer registry/mirror (mirror the research-build cluster's `comms/` PAT
  auto-merge publish). This is the slice that converts the offload from "produces an
  artifact" into "grows the shadow fleet."
- **4B** — Publish the `market_microstructure` capture off the trainer so the flow
  head can build full-history off-VM.
- **4C** — Use the 16 GB runner for the **OOM-quarantined manifests' eval metrics**
  generally — *not* framed as "unblock the flow head," whose `f1_volatile` lift stays
  unmeasurable until a higher-vol era arrives (§1 rec #4). State that plainly.

### Standing obligations
- **Hourly** board checkpoint (per `research-driver`); one consolidated ping, not a
  narration per PR.
- **Log every finding** to the right backlog as you go — §2.1 and §2.2 are both
  *"see something, say something"* items and must land as filed rows, not just prose
  in this doc.
- **Fix the doc drift** found here: the `ROADMAP_MACRO.md` M1 "self-graduates" claim
  (§2.1) and the rec #5 findings docs' fee basis (§2.2). A corrected finding whose
  source doc still asserts the old conclusion is how the next session re-inherits it.
- **End** with the `doc-freshness` skill + a sprint log per
  `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.

---

## 5. Operator queue (do not block on these)

| Item | Tier | Ask |
|---|---|---|
| `gld_pullback_1h` OFF cell — keep or revert once corrected evidence lands | **Tier-3** | Decide on the Block-2D packet if the verdict changes |
| Historical equity `estimate`-row re-stamp (pre-#7930 rows are stale-high) | **Tier-2** | Approve the one-off DB writeback, or leave deferred |
| `ib_paper` broker truth | operator-only | **IB Flex Web Service token** → the minted secret slot (#7896) |
| `bybit_2` lifetime wallet truth | operator-only | Bybit **UM CSV export** for the netting stitch |
| Paid compute | **deferred** | Only if a manifest OOMs at **16 GB**. Do not raise the runpod cap for a RAM problem |

---

## 6. What this plan deliberately does **not** do

- **No new strategy, model type, or macro milestone.** M36 is a consolidation
  milestone and §2 shows two measurement paths are broken; adding surface area while
  the graders are wrong is how the fee bug propagated in the first place.
- **No P5 macro order path** (rec #2). Still correctly gated on an edge that §2.3
  shows cannot be graded before ~September. Wiring execution for an ungraded thesis
  is the one thing the macro programme's own gates forbid.
- **No Tier-3 self-approval.** `config/regime_policy.yaml`, `strategies.yaml`,
  `accounts.yaml`, risk caps and the order path stay operator-gated; this plan only
  ever *drafts* against them.
