# M23 (candidate) — Break the decision-label wall: augmented + external trade-outcome labels

> **Status:** ✅ **APPROVED 2026-07-16 (operator)** — the milestone and tonight's plan are
> greenlit; **Phase 1 (in-distribution backtest-augmented labels) is cleared for Tier-1/offline
> build**, to run **in parallel** with the MB-20260701-001 vol-gate work. **Phase 2 (external
> copy-trade corpus) stays gated** on a ToS review + a distribution-alignment pre-check before
> any spend. No `config/` or live-path change without the standard `candidate → shadow →
> advisory` ladder + a backtest A/B + explicit Tier-3 approval — approval here is to *build and
> evaluate* the label pipeline offline, not to influence live orders.

## Why this milestone exists — it attacks the ONE binding constraint

The 2026-07-16 convergence review (across T0.1/T0.2/T1.1/T1.2/T1.3) found a single recurring
wall: **the decision label**, not features, compute, or model size. Where the label is
**price-derived / self-supervised** (the vol-regime head has 175,272 rows because its label
is computed from price) representation work gives a real, if modest, lift. Every attempt to
reach an actual **trade-decision** label failed *for a label reason*:

- Conviction/win-probability head — **99 closed BTC trades** (T0.3: "No model choice fixes
  that; only more trade history — or backtest-augmented labels — does").
- Cross-strategy net-R ranker — genuine ranking signal (OOS AUC 0.61–0.68) but **no selection
  alpha**, because true net-R (with real per-trade cost) isn't in the label, and the live
  opportunity set was a losing book (T1.3, `MB-20260629-ALLOC-COSTCAP`).

The operator's framing: *the live bot produces ~350 real trades slowly; can we source more
"what makes a trade good vs bad" examples so learning outpaces the bot's trade rate?* That is
exactly the right lever on the right constraint. This milestone is how we do it — safely.

## Feasibility of "open-source trade books" (researched 2026-07-16)

Three tiers of external trade data; only one is genuinely useful, and it's not the obvious one:

1. **The market tape** (Binance/Bybit aggregated trades, order books, the **FI-2010** LOB
   benchmark). Abundant + free, but these are *anonymous prints* or *mid-price-labeled* data
   (FI-2010's labels are next-move up/down/stationary). For our purpose **this is just price
   data** — we already have it and the price-derived heads already use it. Does NOT break the
   decision-label wall.
2. **Real individual-trader records with outcomes** — what the operator pictures. Honest
   finding: **essentially not public.** Research using transaction-level retail-trader data
   (e.g. *Learn to Rank Risky Investors*, arXiv 2509.16616) does **not** release it
   (privacy/sensitivity); CRSP-type sets are paywalled.
3. **Copy-trade / signal marketplaces** (Collective2 API returns historical per-strategy
   signals with entry/exit/timestamps; myfxbook has broker-verified track records). The one
   place semi-structured per-trade outcomes are API-reachable — but mostly retail FX/futures
   EAs, **survivorship-biased**, self-reported, on different instruments / costs / timeframes,
   with ToS constraints on training use.

**The catch (why naive external pooling is dangerous).** External trades come from other
traders, instruments, and cost structures — a **different distribution**. "Good" for a
myfxbook FX-EA is defined by *their* costs/exits/sizing, not ours. Pooling that into "what
makes *our* ICT net-R trade good" risks **exactly the representation–target mismatch that
killed the T1.2 corpus encoder**. So external labels are unlikely to help as *direct* labels.

## Architectural principle — a SHARED canonical label store, not an M23-only artifact (operator directive 2026-07-16)

The augmented relabeled-trade store must be built as a **shared canonical dataset asset** that
**retrains the existing decision heads**, not a bespoke input for one new M23 model. The label wall
constrains the *whole* trade-decision fleet, so a richer label store should lift all of it. Concretely
it is a leakage-safe dataset family in the **same decision-label schema the current heads already
consume** (per the repo's "pure feature blocks / dataset families drop in the same way" philosophy),
so any existing head can opt into it via a manifest `dataset.version` bump + a retrain.

**Which heads benefit (honest scoping) — the trade-OUTCOME-labeled heads only:**

| Head | Current label starvation | Benefits from the augmented store? |
|---|---|---|
| Conviction / win-probability (`conviction-meta-v1`) | **~99 BTC trades** | **Yes — directly** (more `setup → won/lost` rows). |
| Cross-strategy net-R ranker (M18 / T1.3) | net-R target corrupted + losing opportunity set | **Yes** — its own reopen condition names "clean per-trade cost labels + a net-positive opportunity set" (`MB-20260629-ALLOC-COSTCAP`). |
| Setup-quality heads | same trade-outcome label family | **Yes.** |
| Exit heads (M20 peak-is-in / `P(recover≥XR)`) | trade-outcome-ish, thin | **Yes** — a backtest-augmented exit-outcome store feeds them. |
| Direction heads | ~martingale (~chance) | Marginal at best — more labels won't manufacture a directional edge. |
| **Regime / vol heads** (e.g. `btc-regime-15m-lgbm-v2`) | **already label-rich (175k price-derived rows)** | **No** — their label is computed from price (self-supervised); they are *not* the bottleneck. Do NOT retrain these on trade labels. |

**Added deliverable + gate:** for each benefiting head, a **retrain A/B** — retrain it on `real ∪ augmented`
labels and require it to **beat the real-labels-only baseline** on purged-CV **and** a real-money holdout.
That per-head test is also the honesty check on the augmented labels themselves (if they don't lift a head,
they're noise/leakage for that head and are dropped). So M23 Phase 1's output is two things: (1) the shared
store, and (2) a measured verdict on which existing heads it actually improves.

## Design — two phases, in-distribution first

### Phase 1 (PRIMARY, Tier-1/offline) — in-distribution backtest-augmented meta-labels

The clean, high-ROI way to beat the bot's trade rate: **generate the labels ourselves from
history.** Replay our own strategies over years of BTC/ETH/SOL (and equities/futures) history
with the **triple-barrier method + meta-labeling** (López de Prado; empirically improves signal
efficacy — Hudson & Thames): a primary model (our strategy) marks the events; a secondary model
learns *whether to take the trade* using the triple-barrier outcome as the label. This produces
**thousands** of `(setup features → realized net-R outcome)` rows **in our own distribution,
with our own costs and exits** — a 10–100× multiple on the ~350 live labels, sidestepping the
ToS/bias/transfer problems entirely.

Seeds already in the backlog: `MB-20260530-001` (per-trade backtest rows to break the n≈78/99
decision-model wall), `MB-20260705-META-LABEL-WALL` (accrual-gated), `MB-20260629-ALLOC-COSTCAP`
(clean per-trade cost/net-R labels — the substrate T1.3 said was missing).

**The one real caveat — faithfulness.** The fc-geometry backtest showed the triple-barrier
engine diverges ~0.6R from live exits (live trades close on fees/monitor/flip/reconciler paths,
not clean barriers). So a naive barrier label is optimistic. Mitigations, in order:
(a) realistic cost + a faithful exit model in the label engine; (b) treat barrier labels as
**auxiliary / pretraining**, fine-tuned on the small real-label set; (c) calibrate the barrier
outcome against the ~350 real closed trades before trusting it as gold.

### Phase 2 (SECONDARY, gated feasibility spike) — external corpus as *pretraining*

Only where external data has a legitimate role: **pretrain a "trade-quality" representation** on
a large out-of-distribution corpus (a Collective2/myfxbook scrape, ToS permitting), then
**fine-tune on our in-distribution labels** — the transfer-learning play, NOT direct pooling.
Hard prerequisites before any spend: (a) a **ToS/licensing review**; (b) a **distribution-
alignment pre-check** (the spectral/feature-overlap analog of the corpus-encoder gate — measure
whether the external "good trade" structure overlaps ours *before* building the encoder); (c) it
competes on the same gate as Phase 1 and must beat real-labels-only. Public LOB benchmarks
(FI-2010) are for method validation, not our labels. Treat Phase 2 as high-risk/optional.

## Phase-1 build plan (2026-07-16 infra inventory — ~80% already exists)

The pipeline is largely **already built and tested** — Phase 1 is mostly *running it at scale* + two
small additions, not greenfield:

**Exists (do NOT rebuild):**
- `ml/datasets/labeling/triple_barrier.py` — de Prado triple-barrier (`label_event`) + CUSUM sampler,
  with adverse-first straddle resolution + a `slippage` knob (the realistic-fill discipline the caveat
  demands). Tests: `tests/ml/test_triple_barrier.py`, `test_metalabel.py`.
- `ml/datasets/families/setup_candidates.py` — **the shared canonical label family**: one signal-time
  (past-only) row per event + barrier label, ingesting FOUR event sources (`cusum` / `signal_log` /
  **`backtest`** / `live`) with `is_live_trade` + `event_source` split cols. Leakage PASSED by
  construction. This IS the store that retrains the existing decision heads (`conviction-meta-v1` etc.
  already consume this schema).
- `ml/datasets/backtest_recorder.py::write_backtest_trades` — `SimTrade → is_backtest=1` rows (excluded
  from money paths). Harnesses: `scripts/backtest_*.py`, `scripts/backtest_system.py` (`FEE_BPS_ROUNDTRIP=7.5`).
- Manifest `ml/configs/setup-candidates-metalabel-backtest-v1.yaml` (backtest-train + real-eval, target
  `won`) + both gate arms (`ml/experiments/splitters.py::split_purged_walk_forward` + `split_live_holdout`).

**The 3 gaps to close:** (1) **deep-history feedstock** — run each BTC harness over 2–3y (not the 2026-only
sample CSVs) via `write_backtest_trades` into a **temp DB** (never the money journal); (2) a
**faithfulness-calibration report** — backtest-label win-rate vs the ~350 real closed-trade win-rate for
the same leg (the ~0.6R barrier-vs-live gate); (3) **wire the paired gate** (purged-CV OOS + live-holdout
real slice) into one decisive PASS/FAIL report.

**Honest prior to respect:** `MB-20260705-META-LABEL-WALL` already RAN (2026-07-06) and was a *structural*
negative — but that was the **paper-pool** path (paper history postdates the real holdout → the leak-free
pool barely exists). Phase 1 uses the **`event_source=backtest`** deep-history path instead, which that
negative explicitly points toward ("only more trade history — or backtest-augmented labels — does"). This
is a different experiment, not a redo.

**Falsifiable first experiment (one leg, CPU-only, trainer-VM):** deepest BTC leg (confirm via a diag pull
of live per-strategy BTCUSDT closed-trade counts) → build deep `market_raw BTCUSDT/1h` → harness 2–3y →
`write_backtest_trades(tmp.db)` → build `setup_candidates` (backtest-train + real-eval) → calibration check
→ train `metalabel-backtest-v1` → eval on BOTH gate arms. **PASS** = augmented beats real-labels-only AND
the majority baseline with precision lift on the real slice, purged-CV corroborating; **FAIL** = ship the
negative (it resolves whether backtest labels are trustworthy for this system).

## Gates (non-negotiable)

Any augmented/external label must **beat the real-label-only baseline** on BOTH a purged-CV OOS
edge AND a real-money holdout — i.e. the extra labels must add signal, not noise or leakage. No
order-influencing use until the standard `candidate → shadow → advisory` ladder + a backtest A/B
+ operator approval (Tier-3), same as every other head.

## Falsifiable first experiment (cheap, Phase 1)

Pick one strategy×symbol with the most real trades. Generate triple-barrier + meta-labels over
2–3 years of history (realistic cost). Train the meta-label filter on the augmented set; evaluate
on a purged-CV **and** against the real closed trades for that leg. **Ship the negative if the
augmented labels don't beat real-labels-only** — that itself resolves whether backtest labels are
trustworthy for this system.

## Relationship to the north star

This is the labels-first pillar of "a master AI that trades on its own." The convergence review's
conclusion was that the path is (1) break the label wall, (2) promote proven pieces (fc) into the
decision one at a time, (3) one disciplined shot at the reads-everything encoder on a task-matched
head. M23 is pillar (1) — the highest-leverage of the three, and mostly Tier-1/offline.

## Sources

- *Learn to Rank Risky Investors* (retail-trader profitability; data not released) — arXiv 2509.16616.
- FI-2010 LOB benchmark (mid-price-labeled, CC BY 4.0) — arXiv 1705.03233.
- Collective2 API (historical per-strategy signals); myfxbook verified track records.
- Meta-labeling / triple-barrier (López de Prado, *Advances in Financial ML*); Hudson & Thames
  "Does Meta-Labeling Add to Signal Efficacy?"
