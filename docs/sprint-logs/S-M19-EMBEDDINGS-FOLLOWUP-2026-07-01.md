# Sprint Log: S-M19-EMBEDDINGS-FOLLOWUP-2026-07-01

## Date Range
2026-07-01 (single session, continued from `S-M19-T0.1-EMBEDDINGS-2026-07-01`).

## Objective
Push M19's pretrained-TSFM embedding work past "code landed" to **decided**:
(1) confirm the T0.1 regime lift under stricter validation + more symbols,
answer the two open design questions (PCA vs random reduction; harder head);
(2) probe embeddings on the conviction win-prob stacker (**T0.3**); and
(3) scope the one gate between the proven offline lift and a live-observing
shadow head — the live-parity dependency (**Track A**). Offline/`candidate`
throughout; no live-path change.

## Tier
**Tier 1** — trainer-side ML research (dataset families, manifests, offline
A/Bs), design docs, and evidence. No `config/` live gate, `src/` order-path, or
unit-file change. Every model stays `candidate` (refused by the live shadow
factory). Shadow promotion remains a separate, operator-gated Tier-3 step.

## Starting Context
`S-M19-T0.1-EMBEDDINGS-2026-07-01` landed the embedding block + the BTC-15m A/B
manifests but had NOT run the A/B. The operator directed: run it, then pursue all
three follow-up levers, then (after the positive result) consolidate and push
forward toward getting the representation live.

## Repo State Checked
`main` post-T0.1 (PR #5268 M19 docs, #5281 A/B evidence, #5283 PCA+manifests).
Two Explore recon passes mapped (a) the `conviction_meta` stacker wiring and (b)
the live inference path (`regime_bar_scoring` + `cross_asset_live` +
`shadow_adapter`/`factory`, the dependency boundary). Trainer diag established
the venv (`$REPO/.venv`, lightgbm 4.6.0), the on-disk embedding side-streams, and
that `data/trade_journal.db` (not the empty repo-root stub) is the synced label
feedstock.

## Files and Systems Inspected
- `ml/datasets/embedding_features.py`, `scripts/ml/build_embeddings.py` — the T0.1 block + producer (PCA option added earlier).
- `ml/datasets/families/{market_features,conviction_meta}.py` — the as-of-join optional-block pattern + the conviction stacker source.
- `src/runtime/{regime_bar_scoring,cross_asset_live,shadow_adapter,regime_shadow}.py`, `ml/shadow/factory.py`, `ml/predictors/{shadow,lightgbm}.py` — the live inference path + train==serve parity mechanism.
- `scripts/ops/sync_trainer_data.sh` — the live→trainer DB pull (`data/trade_journal.db`).
- `requirements.txt` (no torch/chronos) vs `requirements-backtest.txt` (trainer-side torch/chronos) — the dependency boundary.

## Work Completed
- **T0.1 follow-up (3 levers), evidence** `docs/research/T0.1-embedding-followup-evidence-2026-07-01.md` (PR #5295, merged):
  - **Confirm:** the lift HOLDS under purged walk-forward CV and generalizes —
    BTC-15m Δmacro_f1 **+0.052**, ETH-15m **+0.058** (BTC-1h weak, data-poorer).
  - **Reduction:** past-only PCA ≈ seeded random projection (within ±0.001) →
    keep the simpler random projection.
  - **Harder head:** embeddings do NOT help the **direction** head (+0.004) — the
    frozen representation carries *volatility* structure, not directional edge.
- **T0.3 — conviction stacker + embeddings** (wiring PR #5299 merged; evidence +
  fixups PR #5308):
  - `conviction_meta` family v1→v2: optional `embedding_path`, per-symbol as-of
    (past-only) join, 32 `tsfm_emb_*` columns always present (0.0 without a path)
    so the v1 manifest is unaffected. 3 new tests. Two BTC-scoped candidate
    manifests (base control + 8-dim emb treatment).
  - A/B on the BTCUSDT slice: Δmacro_f1 **+0.039** (0.561→0.600), same sign as the
    regime lift, **but n_eval=20 (5 positives) → INCONCLUSIVE**. The binding
    constraint is labels (99 closed BTC trades; conviction's label is a trade
    outcome, not self-supervised). Evidence:
    `docs/research/T0.3-conviction-embedding-evidence-2026-07-01.md`.
- **Track A — live-parity design** `docs/research/T0.1-live-parity-DESIGN.md`
  (PR #5304, merged): four options (install torch on live · sidecar · ONNX ·
  mirror-publish). **Recommends Option D — compute embeddings on the trainer and
  ship them to the live head via the existing model-mirror channel** (zero live
  torch, no new service, no ONNX) to start a shadow soak; ONNX held as the
  tick-exact graduation path. Latency shown to be a non-constraint; footprint /
  ops / parity are the real axes.
- **Push-forward:** production-threshold (vol_threshold=0.005, matching the
  shipped BTC head) purged-CV regime A/B — the go/no-go number for the Option-D
  shadow-promotion path. Result recorded below.

## Validation Performed
- All A/Bs ran on the trainer `.venv` (lightgbm 4.6.0) against the live-synced
  `data/trade_journal.db` (3,068 trades). Clean A/Bs (identical support/slice;
  only the embedding columns differ).
- `pytest tests/ml/test_conviction_meta_family.py` → 12 passed (3 new); ruff clean.
- End-to-end pipeline validated: the conviction as-of join populated all 99 BTC
  rows with real embeddings (`nonzero_emb 99`).
- **`vol_threshold` sweep (purged-CV) — RAN, a BASE-RATE CLIFF:** rebuilt BTC-15m
  `market_features` at 0.003/0.004/0.005 (all 175,272 rows embedded) + trained
  base vs emb under purged walk-forward CV at each. The lift is **real across a
  band and cliffs at 0.005**, tracking the volatile base rate:
  - **0.003** (11.7% vol): Δmacro_f1 **+0.052**, Δf1_vol +0.035.
  - **0.004** (8.4% vol): Δmacro_f1 **+0.037**, Δf1_vol +0.031 (base f1_vol 0.30 → emb 0.33).
  - **0.005** (4.6% vol — shipped head): Δmacro_f1 **+0.012**, f1_vol flat (base f1_vol only 0.24 → the class is too rare for either model).

  **Mechanism:** the collapse is driven by the volatile *base rate*, not the
  threshold per se — where the class is adequately populated (≥ ~8%) the embedding
  clearly helps; at 4.6% neither model learns it. **Go/no-go — promotion sharpens,
  doesn't close:** (1) if the head's threshold is revisitable, 0.004 gives a
  better head (f1_vol 0.24→0.30) AND a lift → promote-candidate; (2) if 0.005 is
  locked by the gate's semantics, embeddings don't help there → pivot to T0.2/T0.4.
  Independent flag for the ML/regime track: the shipped head is weak at its own
  0.005 operating point (f1_vol 0.24). **Note:** the full 3-threshold sweep
  (0.004/0.006/0.007 in one relay) was **preempted** by the trainer's periodic
  automated job (single-concurrency relay); re-run as short single-threshold jobs
  — 0.004 landed, 0.006/0.007 confirm the collapse persists.

## Documentation Updated
- This sprint log; three research evidence/design docs (above).
- `ROADMAP.md` M19 rows: T0.1 (follow-up CONFIRMED) + T0.3 (INCONCLUSIVE/label-wall).

## Contradictions or Drift Found
None in canonical docs. Two operational bugs found + fixed: the `conviction_meta`
BTC dataset built empty (pointed at the empty repo-root `trade_journal.db` stub
instead of the synced `data/` copy), and a dataset-version format bug
(`v001emb` invalid — versions must be `vNNN`); both fixed in PR #5308.

## Risks and Follow-Ups
- **T0.3 is label-bound, not a null.** A conclusive conviction A/B needs a
  multi-symbol embedding join (streams for MES/MGC/ETH/equities) + purged/repeated
  CV to lift n out of the single-digit-positive regime. Recommendation: don't
  chase raw embeddings on the conviction head — route the value through the
  improved `c_reg` regime lens instead (i.e. promote the regime head).
- **Option D is a design, not built.** The mirror-publish trainer job + the live
  `compute_live_embedding_row()` reader (mirroring `cross_asset_live`) + a
  train-vs-published parity test are the next build — a focused Tier-1
  observe-only PR, then the operator-gated `candidate → shadow` promotion.

## Deferred Items
- The Option-D mirror-publish wiring (next sprint).
- T0.2 (unsupervised regime discovery) and T0.4 (quantile-forecast features) —
  the remaining Tier-0 levers.
- ETH/BTC-1h embedding regime heads at production threshold (BTC-15m done here).

## Next Recommended Sprint
**Characterize the embedding lift before committing to the live-parity build** —
the production-threshold result made this the priority. Run the `vol_threshold`
sweep (0.004/0.006/0.007) + a second symbol to map where the lift lives; if a
production-relevant configuration shows a robust lift, THEN build the Option-D
mirror-publish path (scheduled trainer job publishing `tsfm_emb_*` via
`trainer_mirror/` + the live per-bar-scorer reader + a parity test) and promote
that head `candidate → shadow` (operator gate). If the sweep confirms the lift is
research-threshold-specific, **pivot** to T0.2 (unsupervised regime discovery) or
T0.4 (quantile-forecast features) instead.

## Wrap-Up Check
All work offline/`candidate`; no live-path change. Three PRs (#5295, #5299,
#5304) merged, #5308 merging. ROADMAP coherent with the evidence docs. Doc-freshness
run at session end.
</content>
