# AI Model Strategy & Roadmap — new model *types* (M19)

> **Status:** 📋 PROPOSED 2026-07-01 (research/design session). **PROPOSE-ONLY** —
> no `src/`, `config/`, `ml/`, or live-path file is changed by this doc. Every
> model type below graduates observe-only through the existing
> `candidate → shadow → advisory` ladder; nothing influences a live order until a
> backtest A/B passes and the operator approves. Roadmap entry: `ROADMAP.md` §
> "M19 — AI Model Strategy (new model types)". Successor program to the
> largely-complete M14 ML-Optimization Program
> ([`docs/ml/optimization-roadmap.md`](../ml/optimization-roadmap.md)).

## Why this doc exists

The last long ML session concluded, honestly, that **the current models don't
carry a decisive edge for any of the specific decision areas we wanted** (regime
gating aside, where the BTC 15m vol head is live). That is a fine resting point,
not the end of the line. This doc asks the next question the operator posed:

> The next step isn't *new decisions* — it's *new **types** of models*. What can
> we build on the existing infrastructure? What are the trade-offs? What could a
> first step of **paid compute (~$10/month)** train and run? And, longer-term,
> can we build **one overall model that reads everything in the system** — fed
> data beyond just what we trade — slowly building toward a real "AI trader"
> brain?

This is the design + roadmap answer.

## The one reframe that drives everything

**Every model we have today is a narrow tabular classifier over hand-engineered
features.** 46 manifests in `ml/configs/`: LightGBM regime/vol heads,
trade-outcome / setup-quality heads, one causal HMM, and per-group baselines.
They differ in *target and symbol*, not in *kind*. The genuinely unexplored
frontier is a ladder of new model **types**:

1. Representation / embedding models (learn *features*, not a label)
2. Unsupervised regime discovery (discover states instead of bucketing vol)
3. Deep sequence models (consume raw bar sequences)
4. A shared **"reads-everything" encoder** (the operator's centerpiece)
5. Cross-sectional rankers (choose *across* candidates)
6. RL / policy models (sizing + exit management)

**The binding constraint is labels, not compute.** We have ~350 real-money
closed trades; MES is essentially label-blank; the small live accounts throttle
sizing so real labels accrue slowly. Compute is a *distant* second constraint —
the free 1-OCPU trainer finishes the whole daily cycle (~46 manifests) in under
an hour. Two consequences shape the entire roadmap:

- **Self-supervised representation learning is the highest-leverage next type,
  precisely because it needs *no labels*.** It learns from the abundant
  *unlabeled* candle history + external feeds we already can pull. This is also
  exactly the "reads-everything" model the operator wants — the framing and the
  best technical lever coincide.
- **For the *supervised* heads, gradient boosting still wins** on tabular
  financial data (it beats LSTM/Transformer on most financial series and trains
  in seconds). So deep models should enter the stack as **encoders that feed the
  existing LightGBM heads**, not as end-to-end replacements. That is the
  architectural spine of everything below: *learn a better representation, keep
  the boosting decision layer.*

## What already exists (inventory — build on it, don't rebuild)

The scaffold is mature and is exactly what new model types plug into:

| Layer | Where | What it gives us for free |
|---|---|---|
| Manifest + trainer factory | `ml/manifest.py`, `ml/trainers/` (LightGBM multiclass/regression, `causal_hmm_regime`, baselines) | Add a new type = new trainer/predictor class + a YAML manifest. |
| Dataset families + **pure feature blocks** | `ml/datasets/families/`, `cross_asset_features.py`, `funding_oi_features.py`, `macro_features.py`, `orderflow_features.py`, `volatility_estimators.py` | Leakage-safe, as-of-joined, train==live. A new feature block (e.g. embeddings) drops in the same way. |
| Evaluators + purged CV | `ml/evaluators/`, `ml/experiments/splitters.py` (`PurgedWalkForwardSplitter`, `live_holdout`) | Leak-free measurement is already the default. |
| Promotion gates | `ml/promotion/gates.py` (`gate-check`, `stage_guard`, `readiness_report`) | Mechanical shadow→advisory go/no-go — reuse verbatim for every new head. |
| 3-stage ladder + shadow side-channel | `ml/shadow/factory.py`, `src/runtime/shadow_adapter.py`, `ml/registry/` | `candidate`→`shadow` (auto-wire, observe-only)→`advisory` (operator-gated influence). |
| Per-bar scoring machinery | `src/runtime/regime_bar_scoring.py` (fetch-gate + `REGIME_BAR_SCORING_BUDGET_S` wall-clock budget) | The proven pattern for running *any* per-bar model on the CPU-only live VM without wedging the loop — reuse for deep-encoder inference. |
| Conviction framework | `src/runtime/conviction*.py` (`c_strat/c_setup/c_wr/c_reg` blend) + **`conviction-meta-v1` stacker already at `candidate`** | A multi-lens meta-model harness that new representations can feed straight into. |
| Regime router | `src/runtime/intents.py` hard-gate, live BTC vol-gate | The one place a model already changes real-money routing. |

**Everything below is an *extension* of this scaffold**, not a parallel stack.

## Compute tiers (the cost axis)

| Tier | $/mo | Hardware | What it can train / run | Posture |
|---|---|---|---|---|
| **Tier 0** | **$0** | OCI Always-Free, trainer 1 OCPU / 6 GB, **CPU-only** | LightGBM/baselines/HMM/GMM; **frozen** pretrained-TSFM embeddings (CPU `embed()`); calibrators; clustering | **Active now.** Maximize this first. |
| **Tier 1** | **~$10** | Spot/community GPU bursts (RunPod/Vast, ~$0.2–0.4/hr → ~25–50 GPU-hr/mo) | ≤50M-param deep sequence models; **in-house self-supervised encoder v0**; Optuna at scale — all with checkpoint/resume, **inference exported to CPU (ONNX)** and served on the free VMs | **Spec'd now, spending gated** behind an explicit operator go-signal. |
| **Tier 2** | **~$50–150** | Larger/longer bursts or a small dedicated GPU box | In-house **foundation encoder v1** (multi-task heads); frequent retrains; offline-RL training | Long-term; only after Tier-1 shows lift. |

**Key economics:** $10/mo is real capacity — at $0.4/hr that is ~25 GPU-hours,
enough for nightly-or-weekly training of a ≤50M-param model that checkpoints. It
is *not* enough for a large always-on GPU, which is why the design keeps
**training bursty on GPU and inference permanently on CPU** (the live VM is
CPU-only and latency-sensitive; the per-bar fetch-gate/budget machinery already
handles CPU-cadence inference). The label wall, not this budget, is the true
gate on Tier-1's *supervised* payoff — another reason the self-supervised
encoder (label-free) is the flagship Tier-1 item.

---

## The roadmap — a capability ladder of new model types

Each phase graduates observe-only through `candidate → shadow → advisory`. The
graduation gate is always the existing `gate-check` packet
(`ml/promotion/gates.py`): purged-CV OOS edge over baseline + soak + drift
bounds; for any order-influencing step, additionally a backtest A/B on
net-of-cost PnL + maxDD, then operator approval.

### Tier 0 — free CPU (start now)

**T0.1 — Frozen pretrained-TSFM embeddings (the first taste of "reads
everything").**
New pure feature block `ml/datasets/embedding_features.py` that runs a small
pretrained time-series foundation model — **Chronos-Bolt-Tiny (9M params,
sub-second on CPU)** via its `pipeline.embed()` API — over a bar window and
emits a fixed-width embedding vector as an as-of feature, cached exactly like the
other blocks. Feed it into the existing regime + `conviction-meta` heads.
- *Type:* representation/embedding features (a pretrained encoder as a **frozen
  feature extractor**).
- *Why first:* $0, CPU, no training, no labels; proves the "learned
  representation lifts a boosting head" thesis before we spend anything.
- *Gate:* does it raise regime AUC / conviction Brier in purged-CV vs the
  no-embedding baseline? If not, we learned that cheaply.

**T0.2 — Unsupervised regime discovery.**
Extend the lone `causal_hmm_regime` with GMM / clustering (k-means, agglomerative)
over a vol/return/drawdown feature set to *discover* regimes instead of
hand-bucketing volatility quantiles. Financial-regime literature converges on ~3
latent states (calm / neutral / stressed); Gaussian-mixture emissions handle the
fat tails a plain Gaussian HMM misses.
- *Type:* unsupervised.
- *Wiring:* output feeds the regime router as an alternative/ensemble label,
  shadow-only until it beats the frozen-edge detector under the router's gate.

**T0.3 — Graduate the conviction stacker.**
`conviction-meta-v1` is a true stacked/multi-task meta-model already sitting at
`candidate`. Add the T0.1 embedding features, re-fit, run `gate-check` + shadow
soak. This is the near-term "one head that reads all the lens signals at once."
- *Type:* stacked meta-model (López-de-Prado meta-labeling in spirit — a
  secondary model deciding *whether/how much* to act on the primary strategy
  signal, which is exactly our shadow→advisory framing).

**T0.4 — Probabilistic forecasting features.**
Use the pretrained TSFM's native **quantile forecasts** (expected range,
P(volatile), expected drift sign) as additional features, and — later, gated —
as geometry inputs for SL/TP placement.
- *Type:* probabilistic / generative forecasting.

### Tier 1 — ~$10/mo gated spot-GPU bursts (spec now, spend on go-signal)

**Delivery mechanism:** a GitHub Actions workflow (the same issue-label /
dispatch pattern the repo already uses) that spins a spot GPU box, pulls the
dataset corpus, trains with checkpoint/resume, **exports the model to CPU
inference format (ONNX / torch-CPU)**, uploads it to the registry mirror, and
tears the box down. Trainer VM stays the free CPU orchestrator; the GPU box is
ephemeral. Gated behind an explicit operator go-signal per the spend posture.

**T1.1 — Deep sequence models.**
Small TCN / lightweight Transformer over raw bar sequences (per-symbol first,
then a shared multi-symbol variant), trained in bursts, exported to CPU, served
via the per-bar scoring machinery.
- *Type:* deep sequence model.
- *Gate:* competes head-to-head with the LightGBM regime heads under the same
  `gate-check`. Deep only graduates where it *measurably* beats boosting — no
  architecture-for-its-own-sake.

**T1.2 — In-house self-supervised encoder v0 — the real "reads-everything"
model.**
A small masked-reconstruction / contrastive encoder trained on the **wide
multi-asset panel** (see the Data workstream) to produce a market-state
embedding that augments/replaces the T0.1 frozen embeddings and is consumed by
*every* downstream head.
- *Type:* self-supervised representation model.
- *Why this is the centerpiece:* it needs **no labels** (sidesteps the wall), it
  is tailored to our instruments + microstructure (unlike the generic
  pretrained TSFM), and it is the concrete substrate for "one model that
  understands the system's data as much as possible." v0 stays a *feature
  producer*; the boosting/meta heads remain the decision layer.
- *Gate:* the embedding must lift ≥1 downstream head's purged-CV metric vs the
  T0.1 frozen-embedding baseline; otherwise the pretrained encoder is retained.

**T1.3 — Cross-sectional ranker (revive M18 P3).**
The parked M18 expected-net-R ranker hit only **OOS AUC ≈ 0.51** on tabular
decision-time features — no feature separated winners. **Encoder embeddings are
the single untested lever there.** Re-run the ranker with T1.2 embeddings on the
existing sizing-normalized allocator harness before any routing plumbing.
- *Type:* learned ranker.
- *Gate:* must clear OOS AUC materially above 0.51 in the sizing-normalized
  harness (per the M18 findings doc) before it earns any allocator wiring.

### Tier 2 — long-term, larger spend (only after Tier-1 shows lift)

**T2.1 — In-house foundation encoder v1 + multi-task heads.**
A bigger encoder with a shared trunk and task heads (regime, direction,
win-prob, vol, exit) trained jointly — the unified "AI trader" brain. Multi-task
sharing is where a single representation starts to genuinely *understand* the
market rather than memorize one label.
- *Type:* multi-task foundation model.

**T2.2 — Offline RL / contextual-bandit for sizing + exit management.**
A policy layer on top of the encoder for position sizing and dynamic exits. This
is the most data-hungry type and therefore last; `exit-policy-v1` and the
conviction-sizing soak are the seeds. Offline RL (learn from logged
trade/exit histories) fits our constraint better than live trial-and-error.
- *Type:* RL / policy model.

### Parallel workstream — the wide multi-asset data corpus (enables Tier 1/2)

Per the operator's "wide multi-asset universe" decision, build ingestion for a
broad **context** panel *beyond what we trade*: equity indices & sectors, FX
majors, the rates curve (front-to-long), commodities, crypto majors + breadth,
and the VIX term structure (on-chain later). This is the fuel the self-supervised
encoder reads — it is what lets the model "understand data that isn't just the
things we're trading."
- *Where:* new dataset family + adapters extending `ml/datasets/adapters/` and
  the existing `macro_features.py` / `cross_asset_features.py` blocks; stored as
  a parquet / `trainer_store` corpus (read-mostly, never touches the money DB).
- *Sources:* mostly free/low-cost (the macro block already pulls VIX/DXY/rates);
  breadth and sector series are cheap to add.
- *Sequencing:* this is the **long pole for T1.2** and must start early, in
  parallel with the Tier-0 work, even though the encoder that consumes it is
  Tier-1. Corpus first, encoder second.

---

## Trade-offs (made explicit)

- **Boosting vs deep.** Deep earns its keep only via *representation learning +
  more data*; on today's tabular features boosting wins. Resolution: deep enters
  as **encoders feeding boosting heads**, never as an end-to-end replacement,
  and only graduates where it beats boosting on the same gate.
- **Pretrained vs in-house encoder.** Pretrained TSFM = free, CPU, immediate,
  but generic and largely univariate. In-house = tailored to our
  panel/microstructure, but costs GPU bursts and needs the corpus. **Chosen
  order: pretrained-first (T0.1) → in-house v0 (T1.2)** — prove the value for $0,
  then invest.
- **Labels vs compute.** Labels bind; compute is slack. Self-supervised
  (T0.1/T1.2) sidesteps the wall and is therefore front-loaded; the
  label-hungry types (meta-labeling refinements, RL) wait for labels to accrue
  and for the small-account sizing constraint to ease.
- **Inference constraint.** The live VM is CPU-only and latency-sensitive, so
  **every deep model must export to CPU (ONNX) and serve via the per-bar
  scoring fetch-gate/budget** — cached embeddings on the bar cadence, never a
  synchronous GPU call on the trade path.

## Honest gaps / risks

- **The label wall is not solved by any of this** — it is *routed around* for
  the self-supervised types and *waited out* for the supervised ones. Real-money
  labels still accrue slowly until account sizing eases (a separate, operator
  decision).
- **Pretrained TSFMs are generic.** T0.1 may show no lift on our microstructure;
  that is an acceptable cheap negative and is exactly why it precedes any spend.
- **Deep/embedding inference cost on a 2-OCPU live VM is real.** The per-bar
  budget machinery bounds it, but adding heads compounds it — each new per-bar
  model must be measured against the tick budget, not just its accuracy.
- **Corpus engineering is the hidden cost of the "reads-everything" vision** —
  the wide panel is more data plumbing than modeling; it must not starve the
  Tier-0 wins, hence the parallel-workstream framing.
- **Nothing here changes runtime behavior.** This is a proposal; each phase is
  its own Tier-appropriate PR, and every order-influencing step stays
  backtest-A/B-gated + operator-approved.

## Phase checklist (for the M19 execution sprints)

- [ ] **T0.1** `embedding_features.py` (frozen Chronos-Bolt-Tiny) + A/B vs no-embedding baseline
- [ ] **T0.2** GMM/clustering regime-discovery trainer + predictor, shadow
- [ ] **T0.3** re-fit `conviction-meta-v1` with embeddings → `gate-check` → soak
- [ ] **T0.4** TSFM quantile-forecast feature block
- [ ] **Data** wide multi-asset corpus ingestion (adapters + family + store) — *start early, parallel*
- [ ] **T1.1** deep sequence model + GPU-burst train/export workflow (gated)
- [ ] **T1.2** self-supervised encoder v0 on the corpus (gated)
- [ ] **T1.3** re-run the M18 ranker with encoder embeddings on the sizing-normalized harness
- [ ] **T2.1** multi-task foundation encoder v1 (long-term)
- [ ] **T2.2** offline-RL sizing/exit policy (long-term)

## References

- Current-state inventory: this session's three exploration passes (training
  infra, inference/decision wiring, trainer/data/compute) — summarized above.
- Predecessor program: [`docs/ml/optimization-roadmap.md`](../ml/optimization-roadmap.md);
  forward notes [`docs/research/ml-buildout-strategy-2026-06-30.md`](ml-buildout-strategy-2026-06-30.md).
- The parked cross-sectional ranker + its OOS-AUC-0.51 finding:
  [`docs/research/capital-allocation-ai-DESIGN.md`](capital-allocation-ai-DESIGN.md),
  [`docs/research/M18-allocator-backtest-findings-2026-06-29.md`](M18-allocator-backtest-findings-2026-06-29.md).
- External landscape (2025–26): small time-series foundation models run CPU-only
  and expose embeddings (Chronos-Bolt-Tiny 9M, `embed()`); gradient boosting
  still leads on tabular financial series; self-supervised (masked/contrastive)
  pretraining is the standard label-free representation route; meta-labeling
  (triple-barrier) is the canonical "act-or-not" secondary-model frame.
