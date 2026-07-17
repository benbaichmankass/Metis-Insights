# "Reads-everything" encoder + master-AI convergence status (2026-07-16)

**Operator question (this session):** where are we on the *"reads market data from
all sources, not just strategies"* self-supervised encoder that was soaking — and
are we converging on the north star (a master AI that places the best trades on its
own)?

**Short answer:** the reads-everything subsystem is **built, sound, and soaking at
`candidate` (inert)** — but its central hypothesis (wide multi-asset *breadth* →
better decisions) has now tested **negative four independent times**. Every one of
those negatives points at the **same** binding constraint: the **decision label**
(~350 real trades), *not* data breadth, compute, or model size. We ARE converging
on the north star — but the convergence path has **pivoted, on evidence**, from
"add more data sources" (exhausted) to "manufacture more/better decision labels"
(**M23**, operator-approved today). This note consolidates the four results into
that one answer.

## What the reads-everything encoder is (recap)

The M19 T1.2 SSL corpus encoder (`corpus-ssl-encoder-mae-v1`) is the operator's
centerpiece: a **self-supervised** masked-reconstruction (MAE) autoencoder over the
wide, leakage-safe daily `corpus_panel` (aligned date×series matrix of
rates / VIX / equity / commodity / credit / FX context — data *beyond* what we
trade). It learns a market-state embedding (`corpus_emb_0..15`) from the abundant
**unlabeled** corpus — the one lever not bound by the real-trade label ceiling.
GPU-burst trained, encoder-trunk ONNX / CPU-served, never on the money-box. It fed
the boosting heads as an offline feature block. Infra is complete and reusable
(corpus store + panel + SSL trainer/predictor + fail-closed ONNX parity gate).

## The four independent negatives (the frontier is well-mapped)

| Lever | What was tested | Result |
|---|---|---|
| **T0.1 — frozen embeddings** | Chronos/off-the-shelf embedding into the regime head | **Marginal** — no robust lift at the production 0.005 base rate |
| **T0.2 — unsupervised regime (HMM)** | Gaussian-HMM regime discovery vs the class-weighted tree | **Negative** — matches at best (BTC, after class-weighting the projection), *loses* on ETH; dropped |
| **T1.1 — deep sequence (TCN)** | ≤5M-param causal TCN over raw bars vs the LightGBM head | **Negative** — below the tree on macro_f1 AND f1_volatile (0.534 vs 0.56–0.58) |
| **T1.2 — SSL wide-corpus encoder** | `corpus_emb_*` into the BTC-15m regime head, purged-CV | **Clean triple-negative** — beats neither the no-embedding baseline nor the frozen-Chronos emb; widening the corpus 13→28 series cut encoder overfit (val 2.0→1.3) but downstream got *worse* on the minority "volatile" recall the vol-gate needs |
| **T1.3 — learned cross-strategy ranker** | learned P(win) ranker as the portfolio selector (the last place the corpus emb might pay off) | **Negative (today)** — real OOS ranking AUC (0.61 market-only → 0.68 w/ owner/cell) but **no selection edge**: −$39 vs dumb priority, −$211 vs the rules EV scorer |

Root cause is consistent across all five rows: **the daily macro/cross-asset
backdrop carries risk-regime *context*, not the intraday-vol-burst or within-tick
selection signal the heads actually classify** — and where a model *does* have real
AUC (T1.3), it doesn't convert because the *decision* target is starved (few real
labels; the opportunity set itself is a net-negative book, so "good selection" only
loses slightly less).

## The one durable win (for contrast)

`fc` — the T0.4 quantile **forecast** feature — is the single M19 lever that pays
off base-rate-robustly and is soaking toward its advisory gate. It's a *task-matched
feature* (a forward price-range forecast feeding the tree), not a breadth-add or a
deep end-to-end replacement. That is the shape of what works here: task-matched
signal into the existing boosting heads, not more data width or bigger models.

## Convergence verdict

**Are we converging on the master AI?** Yes — and the value of this frontier work is
that it **converged the diagnosis**. Four negatives + one win triangulate the same
conclusion the 2026-07-16 review reached: for the decomposed-decision program (find
the best model for each sub-decision, converge them), the binding constraint is
**labels, not data/compute/model-kind**. That is a *productive* convergence: we now
know precisely which lever moves the needle.

**The pivot (evidence-driven, not abandonment):**
- **Data breadth / representation lever → exhausted** for the current heads.
  Reads-everything encoder stays `candidate`/dormant (ROADMAP D3): revive **only**
  when a *task-matched daily/cross-asset head* with enough labels exists, and only
  behind a mandatory spectral-overlap pre-check. Infra is banked, not discarded.
- **Label lever → now primary (M23, approved today).** In-distribution
  **backtest-augmented meta-labels** (triple-barrier + meta-labeling over years of
  our own strategy history → 10–100× the live labels, with our real costs/exits),
  gated to beat real-labels-only on purged-CV + a real-money holdout. This directly
  attacks the constraint every T0/T1 negative surfaced. Phase 2 (external
  copy-trade corpus as pretraining) is the reads-everything *idea* re-aimed at the
  label problem — behind ToS + a distribution-alignment pre-check so it doesn't
  repeat the T1.2 transfer failure.

**Net:** the reads-everything encoder did its job as a *falsifiable experiment* — it
told us breadth isn't the lever at this label scale. The master-AI north star is
now being pursued through the **labels-first pillar (M23)**, which is exactly where
the convergence evidence says the alpha is. The subsystem isn't a dead end; it's a
mapped one, with reusable infra waiting for the label wall to come down.

## Pointers
- Frontier phase table + each negative: `ROADMAP.md` § M19 (rows T0.1–T1.3).
- T1.2 A/B evidence: `docs/research/T1.2-ssl-encoder-AB-evidence-2026-07-04.md`.
- T1.3 ranker findings (today): `docs/research/T1.3-ranker-findings-2026-07-16.md`.
- The label-wall successor: `docs/research/M23-decision-label-wall-DESIGN.md`.
- North-star program: `docs/research/ai-model-strategy-roadmap-2026-07-01.md`.
