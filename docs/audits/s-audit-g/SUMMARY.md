# S-AUDIT-G — un-swept-areas sweep (M17 full-system audit, 2026-06-28)

Second wave of the M17 audit, covering the areas the first wave (S-AUDIT-F)
did not reach: broker/account internals (`src/exchange/`, `src/units/accounts/`),
the ML/trainer lifecycle (`ml/`), the Telegram bridge + news layer
(`src/bot/`, `src/news/`), and operational scripts + config (`scripts/ops/`,
`config/`). ~50k lines read line-by-line across four disjoint sub-slices.

**Headline: the live order/money core stays clean.** No bug found that can place,
mis-size, or mis-close a real-money order. All findings are trainer-side,
ops-tooling, or doc-drift.

## Bugs (3)

| ID | Where | Tier | Status |
|----|-------|------|--------|
| **B1** | `ml/registry/model_registry.py` `promote()`/`promote_stage()` rebuilt `RegistryEntry` without `runs=`, wiping training-run history (the `cross_run_stability` gate reads it). | 2 (trainer) | **FIXED** — PR #4958 |
| **F1** | 13 `scripts/ops/*.py` used the CWD-relative `os.environ.get("TRADE_JOURNAL_DB","trade_journal.db")` fallback the canonical-db-resolver guard forbids; the guard didn't scan `scripts/ops`. | 1/2 (ops) | **FIXED** — PR #4959 |
| **bybit_2 sizing freeze** | `risk.py` `_DEFAULT_MIN_QTY=0.001` bump pins real-money `bybit_2` at `intent_noop:at_target` (the live bug PR #3910 Piece-2 targeted; never landed). | **3 (real money)** | **ACCEPTED / WON'T-FIX** — operator decision 2026-06-28: a ~$100 account sitting idle is fine; the risk-faithful refloor was never a wanted improvement, so leave it. NOT a bug to re-flag. (Reopen only if bybit_2 is funded enough that its risk % buys ≥ one min-lot and we want it trading.) |

`B2` (low-magnitude `vol_bucket` quantile leak mislabelled `PASSED` in
`ml/datasets/families/setup_candidates.py`) — deferred to the ml-review backlog
(real fix = train-only edges).

## Dead-code / zombies

| Item | Disposition |
|------|-------------|
| `ml/src/` + `ml/config/` (singular) — prototype island, broken import (`src.strategies_manager` gone), zero callers | **REMOVED** (this PR) |
| `src/bot/recurring_dispatch.py` — `log_trigger`/`build_starter_prompt`/`_STARTER_PROMPTS`/`VALID_SESSION_TYPES` for the retired `/audit`,`/train_model` commands | **DEFERRED** — needs care (`render_roadmap_summary` shares the module); backlog. |
| tailscale-funnel system-action (`scripts/ops/setup_tailscale_funnel.sh` + allowlist) points at the retired Vercel `vercel.json` | **REMOVED** — PR #4961 (operator-directed 2026-06-28). |
| `ml/predictors/external.py` `ExternalPredictor` — unused scaffold, no concrete subclass | **REMOVED** — PR #4961 (operator-directed 2026-06-28). |

## Doc-drift fixed in this PR

- `src/news/__init__.py` — front-door docstring claimed the layer "does not alter
  runtime behavior"; it arms a **live real-money veto** when the source is active
  (safety-relevant honesty fix).
- `src/units/accounts/alpaca_options_exec.py` — "DORMANT / not imported" was stale;
  it's wired (paper) via the options overlay.
- `src/units/accounts/prop_risk.py` — two "persistence deferred" notes; persistence
  is live (`runtime_state/prop_state.json`).
- `ml/shadow/drift_retrain.py` — "up to `live_approved`" → canonical `advisory`.
- `config/regime_policy.yaml` — "(2) REGIME_ROUTER_ENABLED=true" → the gate is
  baseline-on; kill-switch is `REGIME_ROUTER_DISABLED`.

## Doc-drift deferred to backlog (low-value / churny)

- 19 `ml/configs/*.yaml` manifests declare the retired `research_only` stage
  (runtime-equivalent to `candidate` via `canonical_stage`) — normalize in a
  dedicated config pass.
- `ml/cli.py` `list-trainers`/`list-evaluators` + `ml/trainers/__init__.__all__`
  omit the lightgbm/regime/causal trainers actually used by manifests.
- `ml/trainers/regime_classifier.py` + `market_features.py` stale "3-class"
  (collapsed to 2-class); dead `trend_threshold` param.
- `src/news/news_normalizer.py` — RSS articles labelled `newsapi:<source>`.
- `src/units/accounts/context_snapshot.py` — "Default-off via flag" wording for a
  default-ON kill-switch.
- `config/units.yaml` — stale `strategies` list + bare-basename `trade_journal.db`.

These are recorded here and in the review backlogs so the next review picks them
up; none affect live trading.
