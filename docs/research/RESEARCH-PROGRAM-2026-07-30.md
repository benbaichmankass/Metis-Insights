# Research Program — 2026-07-30

> **Type:** Tier-1 research planning. **Supersedes the *plan* half (§3–§4) of
> [`AUTONOMOUS-WORKPLAN-2026-07-30.md`](AUTONOMOUS-WORKPLAN-2026-07-30.md)**, whose
> §1 scorecard and §2 blocker diagnosis remain valid and are inputs here.
>
> **Why a rewrite:** that document was a *bug-fix* plan wearing a research plan's
> clothes, and it contained a **framing error serious enough to have mis-set the
> quarter's priorities** (§1 below). Operator-corrected. This document is the actual
> research program across all three north stars, multi-day, with a Day-1 slice.
>
> **Scope of the three north stars** (from `ROADMAP.md`): **1** effective technical
> strategies · **2** a robust macro trading unit · **3** the overall "AI master trader."

---

## 1. Correction: "wait for the data to accrue" was wrong, and it is a rule violation

The prior plan concluded that the macro sleeve "cannot produce a graded edge this
month" because the PIT econ-calendar store holds max **n=7** per event kind against a
`min_honest_n` of 12, so weekly families would reach n=12 around mid-September. It
then *scheduled around that wait.*

**That conclusion was wrong, and this repo already knew it was wrong.** From
`scripts/macro/valuation_snapshot_backfill.py`'s own docstring:

> *"The producer records ONE snapshot row per run going forward, so a value backtest
> can't run until weeks of history accrue. But the whole value spine is pure +
> deterministic + takes its data INJECTED, and FRED returns each series' FULL history
> — so **we can reconstruct years of point-in-time snapshots in one shot instead of
> waiting.** This is the value-sleeve analogue of the ML `backfill-shadow-predictions`
> replay: generate the past so models/gates can be tested + promoted on real history
> immediately."*

That is *precisely* the problem I diagnosed, solved, and documented — **three times**:

| Backfill | Source | Reconstructs |
|---|---|---|
| `valuation_snapshot_backfill.py` | FRED (keyless, full history) | years of PIT valuation snapshots |
| `cot_snapshot_backfill.py` | CFTC Legacy COT (keyless Socrata) | years of weekly positioning snapshots |
| `crypto_signals_backfill.py` | Bybit v5 (keyless) | PIT funding / OI / basis snapshots |
| **`econ_calendar_*`** | Bigdata/FXStreet | **forward-only — no backfill sibling exists** |

The econ-calendar producer is the **one** macro producer built forward-only. The
correct reading was never "wait six weeks"; it was **"the backfill sibling is
missing."** I mistook an absent tool for a law of physics.

**This is also a governance failure, not just an analysis miss.** "Waiting for a soak
to accrue" is exactly the normalization pattern `CLAUDE.md` § *"If you see something,
say something"* exists to kill — the ETH-xa bug soaked for weeks inside accepted
noise. A plan that *schedules around* a wait, rather than interrogating it, launders
the wait into a fact. §5 therefore proposes a **binding rule** so no future session
can accept an accrual wait without first ruling out a backfill.

**And the same error is sitting in the ML backlog, open since May.**
`MB-20260530-001`: *"Augment the journal-backed decision models (setup_quality,
trade_outcomes) with PER-TRADE backtest rows to break the n barrier."* Same principle,
same unexploited unlock, two months idle. The convergence is the strategic finding of
this pass:

> **The backfill-first principle applies to all three north stars, and has only ever
> been executed on one of them (macro-value).** Strategies got it implicitly — the
> regime-debt matrix *is* 730 days of backfilled evidence, which is exactly why that
> track has real n. Macro-events and ML-decision-models never did.

---

## 2. The macro unlock: what actually blocks M1, precisely

Separating the two halves of M1's "clean joined dataset" gate makes the path obvious.

### 2a. Realized release values — **fully backfillable today, keyless, decades**

`fred_adapter.py` fetches `https://fred.stlouisfed.org/graph/fredgraph.csv?id={}` —
**keyless, full dated history**, with `fetch_fred_series_history_dated` already
written and off-VM-guarded. FRED carries the entire weekly high-n family M1 cares
about: EIA natural-gas working storage, EIA crude stocks, initial/continuing jobless
claims, plus the monthly CPI/PCE/payrolls set. That is **decades** of releases at
weekly cadence — n in the **thousands**, not 7.

**PIT caveat, stated honestly:** `fredgraph.csv` serves the *current* (revised)
vintage, not first prints. True vintage-accurate PIT needs **ALFRED**
(`realtime_start`/`realtime_end`, free API key). For the weekly EIA/claims headline
series revisions are small-to-nil, so keyless FRED is a defensible first pass — but
**the backfill must stamp which vintage basis it used**, and ALFRED is the upgrade
path if the edge proves revision-sensitive. Do not silently treat revised as PIT;
that is the failure mode M1's own stop condition names.

### 2b. Consensus — **not backfillable for free, and it is the wrong thing to block on**

Archived survey consensus (Reuters/Bloomberg/WSJ polls) is genuinely not available
historically at zero cost. The prior plan treated that as fatal, because M1's gate
says *"point-in-time **published consensus**."*

**But the research question is not "does published consensus predict returns." It is
"does the *unanticipated component* of a release predict returns."** Survey consensus
is one proxy for the market's expectation — not the only valid one, and not
necessarily the best one. The alternative is a **PIT expectation model**: for each
series, forecast the next release using only data available before it
(seasonal + AR on the trailing vintage-safe slice; for EIA gas storage, degree-day
seasonality is the dominant term), then define

```
surprise = actual − model_expectation(data available strictly before release)
```

This is **leakage-safe by construction**, fully backfillable over decades, and
**pre-registerable**. It answers M2's economic question directly.

**This is a gate-definition change and therefore an operator call (§6).** It swaps
"published consensus" for "model expectation" in M1's acceptance criteria. My
recommendation: **take it**, and run *both* where they overlap — the ~6 months of
captured survey consensus becomes the **validation set for the expectation model**
(if model-surprise correlates well with survey-surprise on the overlap, the model is
a sound stand-in over the deep history). That turns the shallow captured window from
a blocker into exactly the calibration asset it should be.

**Net effect: M1/M2 goes from "verdict ~mid-September" to "verdict achievable this
week," with n three orders of magnitude larger.**

---

## 3. The tooling-integrity problem: "green but vacuous"

The operator's third point — *if our tools aren't working, all the research is for
naught* — is correct and this pass found the gap is **structural, not incidental.**

### 3a. Two failure classes; only one is guarded

| Class | Shape | Guarded? |
|---|---|---|
| **A · Staleness** | Producer stops firing → ledger frozen → gate replays a dead log | ✅ `check_producer_liveness.py` + `macro-producer-liveness.yml` (daily, Telegram + issue) |
| **B · Vacuity** | Producer fires, ledger grows, artifact is **fresh** — but the measurement inside is **empty**, and a verdict is published anyway | ❌ **nothing checks this** |

The M1 event study is a textbook Class-B: `econ_calendar_snapshots.jsonl` grows
daily (fresh), the scorecard regenerates weekly (fresh), and the verdict is computed
from **`price_bars: 0`**. Every liveness signal is green. The artifact is *perfectly
fresh and completely vacuous.*

`check_producer_liveness.py`'s own docstring aims at Class A only — *"reads the newest
`observed_at` … reports STALE when the freshest row is older than a threshold"* — and
its scope is **one ledger** (`valuation_snapshots.jsonl`), with the other producers
deliberately excluded as dispatch-driven.

### 3b. Why the existing `silent-empty-guard` doesn't catch it either

`scripts/check_silent_empty_in_diff.py` is a **diff-scoped Python-`except` scanner**
over three paths (`src/web/api/`, `src/units/db/`, `src/web/runtime_status.py`). It
cannot see:

- `scripts/macro/` and `scripts/research/` — **the entire research/producer layer**
- **shell/YAML** degradation — and `|| echo "::warning::macro-candle fetch degraded"`
  in `econ-event-study.yml` is the actual mechanism that hid this for a full cycle
- the **output-side** contract — a verdict computed from an empty input set. The bug
  here was never a swallowed exception; it was an *honest* `price_bars: 0` that
  nothing was obliged to act on.

There is a **May 2026 audit** of this exact bug class
(`docs/audits/silent-empty-reporting-2026-05-10.md`, S-067) whose own taxonomy names
the problem: *"trust-corroding — the caller can't distinguish 'no data' from 'broken
source'."* It recurred anyway, in a layer the guard doesn't cover. **A guard that
covers three paths against one language's syntax is not a guard against a bug class.**

### 3c. The fix — assert on inputs, not just freshness

Extend the liveness monitor from *freshness* to *validity*:

1. **Declared-input assertion.** Every research artifact already reports its input
   counts (`price_bars`, `releases`, `n`, `total_scanned`, `records`, `max_n`). The
   monitor reads them and reports **VACUOUS** when a load-bearing input is `0` — or
   below the artifact's own declared floor — reusing the Telegram + GitHub-issue
   alerting already wired.
2. **Register every scheduled producer**, not one ledger.
3. **Producers fail loud.** A load-bearing fetch may not degrade to a warning. If the
   study was asked to measure a kind and got zero bars, exit non-zero.
4. **Standing vacuity sweep** over `comms/**` scorecards + the soak logs, scheduled,
   so a zero-input verdict cannot sit quietly. **There is already a second smell to
   check:** M20 recorded the exit-ladder soak at *"135 rows / 0 differing"* — fresh,
   growing, and possibly measuring nothing.
5. **Extend `silent-empty-guard`'s path scope** to `scripts/macro/` +
   `scripts/research/`, and add a shell rule for `|| echo`/`|| true` on a
   load-bearing fetch step.

This is the highest-leverage *infrastructure* item in the program: it protects every
other conclusion in it.

---

## 4. The research program

Four tracks. **T** is infrastructure that gates the credibility of the rest; **S/M/A**
are the three north stars. Each item states its **n-source** — the discipline §5 makes
binding.

### Track T — tool integrity (do first; everything else inherits its credibility)

| # | Item | Why | Effort |
|---|---|---|---|
| **T1** | Venue-aware fees in the research harnesses (`regime_debt_matrix.py:140` + call-site sweep) | A ~25× fee over-charge graded all 14 commission-free instruments; the same bug was fixed in the live close path (#7930) but not here | S |
| **T2** | M1 price join: install yfinance, map `=F`→Stooq futures form, **fail loud on 0 bars** | The join has never produced a bar; the silent-green is the real defect | S |
| **T3** | Liveness → **validity**: declared-input assertions, all producers registered, vacuity sweep over `comms/**` + soak logs | Closes failure-class B (§3) | M |
| **T4** | Extend `silent-empty-guard` to `scripts/macro|research/` + a shell `|| echo` rule | The guard's scope, not its idea, is what failed | S |
| **T5** | Audit **every** soak/scorecard for vacuity — starting with exit-ladder's "135 rows / 0 differing" | One instance found means the class is unswept | M |

### Track S — technical strategies (north star 1) — *n is already adequate here*

| # | Item | n-source | Why it pays |
|---|---|---|---|
| **S1** | Corrected-cost re-grade of the full 35-row roster + the 3 walk-forward verdicts | 730d backtest, free runners | Not bookkeeping — it **changes verdicts**. `qqq_pullback_1h`'s entire −0.069 R/trade drag is inside the phantom-fee band; `gld_pullback_1h` is a **live Tier-3 gate** resting on it |
| **S2** | **Roster-wide direction walk-forward** (not per-request) | 730d backtest | rec #5's real finding was *directional, not regime* — the pullback long side drags across ETH/SOL/XRP at adequate n, and #7915 refuted a long-drag on regime-of-sample grounds. Systematise it: this is where multiple Tier-3 cells actually live |
| **S3** | Exit-refinement coverage sweep for unprocessed legs | `exit-refinement` skill + free runners | M20's done-condition is the coverage matrix; many legs never processed |
| **S4** | **Is the equity/ETF sleeve actually good?** — re-ask at portfolio level once S1 lands | corrected 730d | 12 of 32 debt strategies are equity/ETF and have *never* been judged with correct costs. Bears on whether `alpaca_live` should leave `dry_run` (**Tier-3 proposal only**) |

### Track M — macro unit (north star 2) — *unblocked by §2, not calendar-bound*

| # | Item | n-source | Why it pays |
|---|---|---|---|
| **M1** | Build `econ_calendar_snapshot_backfill.py` — **the missing sibling** | FRED keyless, decades | Converts the whole track from a six-week wait to a same-week verdict. Stamp the vintage basis |
| **M2** | Pre-register the **PIT expectation model** (seasonal+AR, leakage-safe) and define `surprise = actual − expectation` | same | Removes the archived-consensus dependency — the actual blocker (§2b) |
| **M3** | Validate model-surprise against the ~6mo captured survey consensus on the overlap | captured window | Turns the shallow window from blocker into calibration asset |
| **M4** | Run the event study at real n across **all** weekly families → a genuine M1/M2 verdict | decades | The first honest macro edge read the programme has ever been able to take |
| **M5** | Re-examine the `vix_term` lead (the one robust M28 survivor) with corrected costs | existing | It is ETF-expressed, so T1's fee fix applies to its Sharpe too |

### Track A — AI master trader (north star 3)

**State:** 90 manifests, heavily BTC-skewed (36 btc-regime / 10 eth / 8 mes / 5 sol),
→ effectively **one** advisory head influencing a real-money decision. The
architecture (M16 conviction, 3-stage ladder, allocator) is right; throughput and
live influence are the gap.

| # | Item | n-source | Why it pays |
|---|---|---|---|
| **A1** | **Backfill decision-model training rows from backtests** (`MB-20260530-001`, open since May) | backtest per-trade rows | The §1 principle applied to ML — breaks the n-barrier on `setup_quality` / `trade_outcomes` without waiting for live trades. Also the lever `M23` meta-labeling is blocked on (label volume) |
| **A2** | Work the advisory-head queue: ETH `NO_EDGE` retrain · SOL v2 gate-check → promote · fc-pcv v2 swap · BTC-15m operating point | existing soaks | This is *how* "1 live head" becomes several. Each is gate-driven and concrete; promotions stay **Tier-3** |
| **A3** | M16 conviction matched-holdout AUC read (`MB-20260727`) | live rows | The gating evidence for P4 sizing. Conviction is `off`/annotate today, so the entire M16 architecture influences nothing. If it passes → draft the Tier-3 packet |
| **A4** | Offload v2 register-back into the live registry | — | Converts the free-runner offload from "emits an artifact" to "grows the shadow fleet"; unblocks the OOM-quarantined manifests' eval |
| **A5** | **Reframe the flow head's blocker:** it is a *label-threshold* problem, not a wait | 15.4k captured bars | The captured window is 0.27% volatile at `vol_threshold=0.005`, so both arms collapse to the trivial predictor. `MB-20260701-001` already proposes ~0.004 for the sibling head. **Re-label, don't wait for a volatile era** — the §1 principle a third time |

---

## 5. Proposed binding rule — "backfill before you wait"

The durable fix for §1. Proposed for `docs/CLAUDE-RULES-CANONICAL.md`, and for the
`macro-research` / `model-training` / `backtesting` skills:

> **No session may record "waiting for data to accrue" as a status without first
> stating, in writing, why a backfill is impossible.** Name the source, the reason
> its history is unreachable (keyed / not archived / not public / genuinely
> forward-only such as live L2), and the resulting honest ETA. "The producer only
> writes forward" is **not** a reason — it is a missing backfill sibling
> (`valuation_snapshot_backfill.py`, `cot_snapshot_backfill.py`,
> `crypto_signals_backfill.py`, `backfill-shadow-predictions` are the precedents).
> A soak is the *fallback* for data that provably cannot be reconstructed, never the
> default.

Corollary for label-blocked ML work: before accepting "wait for the regime/volatility
to arrive," check whether the **label threshold** is what is starving the window
(A5).

---

## 6. Operator decisions (none block Day 1)

| # | Decision | Tier | Recommendation |
|---|---|---|---|
| 1 | **Redefine M1's gate** from "point-in-time published consensus" to "PIT model expectation, validated against captured consensus on the overlap" | scope | **Take it.** It is the difference between a verdict this week and a verdict in September (§2b) |
| 2 | Keep or revert `trending.gld_pullback_1h { short: off }` once corrected evidence lands | **Tier-3** | Decide on the S1 packet; my estimate is it survives at reduced magnitude |
| 3 | ALFRED free API key, if the edge proves revision-sensitive | operator-only | Defer until M4 shows sensitivity |
| 4 | Pre-#7930 historical equity `estimate`-row re-stamp | **Tier-2** | Optional; new closes are already correct |
| 5 | IB Flex token · Bybit UM CSV (broker-truth tail) | operator-only | Unchanged from yesterday |
| 6 | Whether `alpaca_live` leaves `dry_run` | **Tier-3** | **Not yet** — gated on S4 |

---

## 7. Day 1 (today)

Ordered so the two small unblockers land first, the long free-runner jobs are
in flight by mid-morning, and the two backfills — the real unlocks — get the bulk of
the day. Nothing here needs an operator answer.

1. **T1 + T2** (~2h, 2 PRs) — fee resolver + call-site sweep; price join fixed and
   made to fail loud. *Land these first: every downstream number depends on T1, and
   T2 stops wasting accrual.*
2. **S1 dispatched** (free runners, harvest across the day) — corrected 35-row matrix
   + 3 walk-forwards. Deliverable is a **diff table vs #7918/#7924**, per changed
   verdict, fee delta attributed.
3. **M1 + M2** (the day's main build) — `econ_calendar_snapshot_backfill.py` over the
   FRED weekly families + the pre-registered expectation model. Vintage basis stamped.
   Then **M3/M4** if it lands: a first real event-study read at n in the hundreds+.
4. **T3** (if time) — declared-input assertions + register all producers. Cheap and it
   protects items 2–3.
5. **A1 scoped** — read `MB-20260530-001`, confirm the backtest→training-row path, and
   either start it or file a precise ready-to-execute spec for the next session.

**Standing:** hourly board checkpoint · file each finding to its backlog as you go
(§3's vacuity findings and T5's sweep are `see something, say something` items) ·
fix the `ROADMAP_MACRO` M1 "self-graduates" line and the rec #5 docs' fee basis ·
close with `doc-freshness` + a sprint log.

**Deliberately NOT today:** no new strategy, model type, or milestone (M36 is
consolidation, and §3 means the graders are not yet trustworthy); no P5 macro order
path (still correctly gated on an edge — but that edge is now weeks away, not months);
no Tier-3 self-approval anywhere.
