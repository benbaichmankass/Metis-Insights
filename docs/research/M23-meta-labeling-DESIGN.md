# M23 — Meta-Labeling Program (secondary take/skip confirmation head)

> **Status:** 📋 PROPOSED 2026-07-17 (design of record). **PROPOSE-ONLY** — no
> `src/`, `config/`, `ml/`, or live-path file changes from this doc. Every phase
> graduates observe-only through `candidate → shadow → advisory`; letting a
> meta-head gate a live entry is **Tier-3**, backtest-A/B-gated + operator-approved.
> Anchors: `MB-20260705-META-LABEL-WALL` (the label-wall spike this formalizes) +
> `MB-20260530-001` (per-trade backtest rows to break the n≈78 wall).

## The idea

Meta-labeling (López de Prado, *Advances in Financial ML* ch. 3) splits the
decision in two: the **primary model** (here, each existing ICT strategy) decides
*direction + entry*; a **secondary "meta" model** decides *whether to act on that
signal at all* — a binary take/skip (later, a fractional bet size). The primary
keeps high recall (never miss a setup); the meta-head raises precision (skip the
setups that historically don't pay). It never invents trades — it only filters
the primary's own signals, so it composes cleanly with the "one switch per
account" gate model and the M18/M21 P_win efforts (which tried to *rank/select*;
this *filters in place*).

Why it's attractive here: we have ~40 configured strategies emitting order
packages with rich decision-time context already persisted (`order_packages.meta`,
`model_scores`, `account_context_snapshots`, signal_logic). The raw material for a
per-signal take/skip label — "did this order package's trade close net-positive in
R?" — is already in `trade_journal.db`.

## The binding constraint: the label wall

The honest blocker (why prior decision-model attempts stalled, `MB-20260530-001`):
the **real-money closed-trade count per (strategy, symbol) cell is tiny** — n≈78
resolved rows for the biggest cells, single digits for most. A LightGBM take/skip
head on 78 rows overfits and RG-fails. Three ways out, in ascending
trust-but-difficulty:

1. **Pool across strategies/symbols** with cell one-hots (more rows, but mixes
   heterogeneous edges — the base-rate-vs-selection trap that sank the M18 ranker).
2. **Augment with in-distribution backtest rows** — replay each strategy over
   history, generate the same decision-time feature vector + realized-R outcome the
   live path would, and pool live + backtest. The backtest rows must be
   *in-distribution* (same feature construction, same cost model, same
   censoring/label rules) or the head learns the simulator, not the market. **This
   is the M23 thesis.**
3. **Wait for live rows to accrue** (slow; the fc/regime soaks already do this).

## Phase 1 (DONE this session — honest result)

Built the **in-distribution backtest-augmented meta-label pipeline** and ran the
first legs. Findings (record; full detail in the session's research notes +
`MB-20260705-META-LABEL-WALL`):

- **Pipeline built + tested**: per-strategy backtest replay → decision-bar feature
  vector + realized-R label, pooled with live rows, purged walk-forward eval.
- **Pooled multi-strategy leg** (fair, population-matched): ran; the pooled head's
  edge is **base-rate between cells, not a within-cell take/skip edge** — the same
  failure mode as the M18 allocator ranker. A pooled meta-head that "knows" cell A
  pays more than cell B is not filtering; it's re-deriving the strategy roster.
- **Live-row realized-R fix + variant C (outcome/R-aware relabel)**: corrected the
  live-row R computation; the exact-R gate showed the label is faithful but the
  **signal is thin at current volume**.
- **Conclusion**: the pipeline is sound and reusable; the *evidence* for a
  live-gating meta-head is not yet there at current label volume. Next levers are
  about **label volume and label faithfulness**, not model class.

## Phased plan

| Phase | Scope | Tier | Graduation gate |
|---|---|---|---|
| **P1 — Backtest-augmented pipeline** ✅ | In-distribution replay → pooled purged-CV meta-label eval; live-row R fix; variant-C outcome relabel. | T1 | DONE — pipeline validated; honest thin-signal result recorded. |
| **P2 — Label-volume expansion** | The lever P1 identified. (a) Extend `market_raw` candle coverage so more (strategy,symbol) cells can be replayed (`MB-20260712-EXIT-ANALYSIS-COVERAGE`); (b) generate per-trade backtest rows at scale for the thin cells (`MB-20260530-001`); (c) confirm backtest-vs-live label parity per cell (KS on the feature marginals + realized-R distribution) before pooling. | T1 | Per-cell n crosses a power threshold (≥ ~300 pooled rows/cell) AND backtest/live parity holds (no distribution shift that would make the head learn the sim). |
| **P3 — Per-cell / per-family meta-heads** | Train take/skip heads scoped to a single strong cell (e.g. `trend_donchian` BTC 1h) or a coherent family, NOT the global pool — so the head learns a *within-cell* skip edge, not cross-cell base rate. Purged walk-forward + RG3/RG4-style robustness. Register `shadow`. | T3 (train autonomous to shadow) | A head beats "take-all" on net-of-cost R AND maxDD in ≥ ⅔ walk-forward folds, incl. the trend years; per-fold min AUC ≥ 0.55; not a knife-edge. |
| **P4 — Shadow soak → advisory** | The shadow head annotates every matching order package (`model_scores`); accrue a live decision-time track record; powered readiness eval. | T3 gate | Live agreement + net-R lift over the soak window; operator promotes shadow→advisory. |
| **P5 — Gate a live entry** | An advisory meta-head's skip actually suppresses the order (reductive-only, `*_MODE` flag `off\|annotate\|apply`, never a default-off `*_ENABLED`). Backtest A/B: meta-filtered vs take-all on net PnL + maxDD, OOS ≥ IS. | T3 | A/B PASS + operator approval; rollback = `off`. |

## Anti-patterns to avoid (learned from M18/M21)

- **Base-rate ≠ selection.** A pooled head that just ranks cells by their known
  win rate is worthless as a filter. Test within-cell, or use a contrastive target.
- **Don't learn the simulator.** Backtest augmentation only helps if the backtest
  rows are byte-faithful to the live feature/cost/label construction. Parity-gate
  before pooling.
- **One-bar-ahead leakage** (the M21 E-3 trap): anchor every feature strictly at
  the decision bar; never the age-0 post-fill bar.
- **Reductive-only at P5.** The meta-head can shrink/skip, never enlarge or add.

## Relationship to other milestones

- **M24 (net-R modeling)** supplies the clean net-of-cost R label (Slice B
  broker-truth costs) that P2/P3 should train against — M24's label is M23's target.
- **M18 allocator** is *selection across symbols*; M23 is *filtering in place*.
  They can compose (filter first, then allocate the survivors) but are independent.
- **M21 entry P_win head** is the closest cousin — a per-signal quality head. If
  M21's `entry-pwin-*` shadow head matures first, M23 P3 can reuse its features.
