# M23 (candidate) — Break the decision-label wall: augmented + external trade-outcome labels

> **Status:** 📋 PROPOSED 2026-07-16 (operator-directed research session). PROPOSE-ONLY —
> no `src/`, `config/`, `ml/`, or live-path change by this doc. Candidate milestone;
> operator go/no-go pending. Every model still graduates observe-only through
> `candidate → shadow → advisory`.

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
