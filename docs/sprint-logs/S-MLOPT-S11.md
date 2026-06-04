# Sprint Log: S-MLOPT-S11 (crypto funding-rate + open-interest features)

## Date Range
- Start: 2026-06-04
- End: 2026-06-04

## Objective
M14 Phase 2.3 — the cheapest high-value **unused** feature family. After the
label-distribution arc (S6 / S6-FU / S6-FU-2) hit a confirmed data-scale floor
at n≈352 real trades, the lever is **better features**. S9 (range-based vol)
proved the feature lever moves the needle; S11 adds the next cheap, currently-
unused crypto-perp family: **Bybit funding-rate + open-interest** features for
the BTCUSDT regime/decision heads.

Research nuance baked into the design (optimization-roadmap.md § Session 2.3):
**funding is mostly a TRAILING byproduct of momentum — its signal is in the
EXTREMES, not the level.** So the headline features are the funding-rate
z-score and its absolute magnitude (an extreme detector), not the raw level;
open interest is fed as a **change** (log change + change-z), since the OI
level is non-stationary and exchange-scale-dependent.

## Tier
- **Tier-1** for the estimator module, the Bybit fetch adapter, the
  `market_features` columns, the fetch CLI, the opt-in build wiring, and the
  tests — additive, read-only over built `market_raw` + a public side-stream;
  past-only features → `leakage_test_status` stays `passed`. No `src/runtime/`,
  order-path, or live file touched. The fetch adapter is off-VM-guarded
  (`ICT_OFFVM_BUILD_HOST=1`) so it can never run on the live VM.
- **Tier-3** for `ml/configs/btc-regime-1h-lgbm-funding-v1.yaml` and any
  promotion past `research_only` — operator-gated. The manifest ships at
  `research_only`; this sprint **proposes** + provides the A/B harness.

## Starting Context
- M14 Phase 2 ("better features"), parallel to the labeling arc. S9
  (range-based vol) shipped + A/B-positive on `time_aware_holdout` (#2720); its
  purged-CV confirm is S-MLOPT-S9's open step (`MB-20260603-004`, addressed in
  the same session as this sprint).
- `market_features` carried close-to-close vol + log-returns + (S9) the four
  range-based vol estimators. No funding/OI, no order-flow, no cross-asset
  (gap **G5**). `account_context` / `review_journal` families exist but unused.
- The Bybit V5 klines path already exists (`bybit_offvm` adapter, off-VM
  guarded) — funding-rate + open-interest history are sibling public endpoints
  on the same ccxt connector.

## Repo State Checked
- Branch `claude/mlopt-s9-s11-features-bJBfQ` cut from `main` @ `9e8d80b`
  (PR #2717 — the S6-FU-2 + S9 merge). `git log origin/main` confirmed the
  branch base is current.
- Canonical docs reviewed: `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/ml/optimization-roadmap.md` (§ Phase 2, Session 2.3), `ROADMAP.md`
  (§ M14), `docs/sprint-logs/S-MLOPT-S9.md`, `docs/claude/ml-review-backlog.json`.

## Files and Systems Inspected
- `ml/datasets/volatility_estimators.py` (the S9 estimator-module shape mirrored),
  `ml/datasets/families/market_features.py` (the past-window feature core +
  schema + leakage discipline + the S9 column-add pattern),
  `ml/datasets/adapters/bybit_offvm.py` (the off-VM guardrail + ccxt pagination
  + `_build_exchange` test hook mirrored), `scripts/ml/eval_split_compare.py`
  (the purged-WF A/B tool), `scripts/ops/build_trainer_datasets.sh`
  (`build_btcusdt_pair`, the v3 build invocation), `ml/configs/btc-regime-1h-lgbm-v2.yaml`
  (the A/B champion).

## Work Completed
- **`ml/datasets/funding_oi_features.py` (new)** — pure estimators over an
  already-aligned past window (same contract as `volatility_estimators.py`):
  `rolling_zscore` (z of the last value; `None` on <min_n or ~zero variance),
  `extreme_magnitude` (`abs(z)` — the "signal is in the extremes" feature),
  `log_change` (OI log change over the window, first-positive base),
  `change_zscore` (z of the latest first-difference — extreme-of-change),
  `diffs` helper, and `_finite_or_zero` (feature-emit shape: `None`/non-finite
  → 0.0). Pure stdlib (no numpy/pandas) so it unit-tests in CI.
- **`ml/datasets/adapters/bybit_funding_oi.py` (new)** — off-VM-guarded Bybit V5
  funding-rate + open-interest **history** fetcher (`fetch_funding_oi_rows`):
  paginates the public `fetch_funding_rate_history` (8h cadence) +
  `fetch_open_interest_history` (chosen `oi_interval`, e.g. 1h) with
  retry/backoff, merges into a ts-sorted union of
  `{ts, symbol, funding_rate?, open_interest?}` rows. `_build_exchange` /
  injected `exchange` test hook so CI never touches the network (mirrors
  `bybit_offvm`). Refuses to run unless `ICT_OFFVM_BUILD_HOST=1`.
- **`scripts/ml/fetch_funding_oi.py` (new)** — Tier-1 CLI that writes the
  funding/OI side-stream (`data.jsonl` + `metadata.json`) `market_features`
  joins.
- **`market_features` integration** — optional `funding_oi_path` kwarg +
  `funding_window_n` (default 168 = ~1 week on 1h). When given, the builder
  **as-of (past-only) aligns** the funding/OI side-stream onto the bars
  (`_align_asof`, carry-forward — a bar never sees an observation timestamped
  after it) and emits five new columns:
  `funding_rate`, `funding_rate_zscore`, `funding_rate_abs_z`,
  `open_interest_change`, `open_interest_change_zscore`. **Default-preserving**:
  omit `funding_oi_path` → every column emits 0.0, so every existing build and
  every non-crypto symbol is unchanged. `builder_version` bumped `v3 → v4`
  (metadata-only). Leakage-safe by construction (past-only window + as-of join).
- **`ml/configs/btc-regime-1h-lgbm-funding-v1.yaml`** *(Tier-3 proposal, draft)*
  — a clean A/B against `btc-regime-1h-lgbm-v2`: identical trainer / split /
  recency + class weighting / dataset, the ONLY change is adding the funding/OI
  feature columns. Ships at `research_only`. Requires a v4 `market_features`
  rebuild WITH a funding/OI side-stream joined (documented in the manifest +
  below).
- **`scripts/ops/build_trainer_datasets.sh`** — opt-in `build_funding_oi`
  (gated on `ICT_BUILD_FUNDING_OI=1`, default OFF). When set, fetches the
  BTCUSDT funding/OI side-stream and rebuilds the BTC `market_features` shards
  with it joined. Default off keeps the daily cycle's `market_features`
  identical (funding columns 0.0) — the funding A/B only needs it during eval.
- **Tests** — `tests/ml/test_funding_oi_features.py` (every estimator + edge
  cases: zero-variance/short-window→None, |z|=abs(z), first-positive base, NaN
  handling), `tests/ml/test_bybit_funding_oi.py` (off-VM guard, funding+OI merge
  with a mocked exchange, OI value-fallback, bad-interval/end<start guards), and
  `tests/ml/datasets/test_market_features.py` additions (columns 0.0 without a
  path; populated + |z|=abs(z) with one; **as-of past-only alignment** — a
  side-stream timestamped entirely after the bars yields all-zero funding; the
  five columns in-schema + full build round-trips through `validate_dataset`;
  `builder_version == v4`; invalid `funding_window_n` raises).

## Validation Performed
- **Local (sandbox, no LightGBM):** the pure modules + the `market_features`
  builder run on stdlib only, so verified directly:
  - `funding_oi_features` estimators: z-score known value (2.0), zero-variance →
    None, |z| = abs(z), `log_change` first-positive base, `change_zscore` flags
    an outlier diff, `_finite_or_zero` maps None/NaN/inf → 0.0. ✓
  - `market_features` end-to-end on synthetic bars + a synthetic funding/OI
    side-stream: the five columns present; non-zero funding carried forward;
    OI change computed; **no-path build → all-zero funding columns AND yz still
    computed**; emitted row keys == schema keys exactly. ✓
  - `ruff check` clean on all new/edited files; `bash -n` clean on the edited
    build script. ✓
- **Trainer VM A/B — DONE, honest NEGATIVE (#2745).** The off-VM Bybit V5
  funding/OI adapter ran end-to-end on the trainer: `fetch_funding_oi` returned
  **funding_rows=5460 (full 5y, 8h cadence)** but **open_interest_rows=191
  (~8 days only — Bybit's public OI history retention is short)**. Rebuilt 1h
  `market_features` v4 with the join; sanity check: the v2 champion on the
  funding-joined dataset scores f1_volatile 0.5009 — *identical* to the
  no-funding build, confirming v2 correctly ignores the funding columns.
  **A/B (purged WF-CV, leak-free): `btc-regime-1h-lgbm-funding-v1` f1_volatile
  0.4859 vs v2 0.5009 (−0.0150)**, macro_f1 −0.0029, accuracy +0.0070 →
  funding/OI did NOT improve the regime head. HONEST READ: OI was effectively
  untested (~8 days coverage → the OI columns are ~all-zero across the 5y
  dataset), so only the funding-rate z-score/extreme features were really
  exercised, and they added slight noise the booster overfit rather than signal
  on the *volatility-regime* label. No promotion proposed; the manifest stays
  `research_only`. The S11 infra (estimators + off-VM adapter + as-of join +
  fetch CLI + opt-in build wiring) stands as reusable, leakage-safe tooling.

## Documentation Updated
- `docs/data/dataset-taxonomy.md` (market_features funding/OI columns + the
  `funding_oi_path` side-stream source); `docs/architecture/ai-model-platform.md`
  change-log (S11 row); `docs/ml/optimization-roadmap.md` Session 2.3;
  `ROADMAP.md` S-MLOPT-S11 row; `docs/claude/ml-review-backlog.json`
  (`MB-20260604-001`, the funding A/B eval follow-up); this sprint log.

## Contradictions or Drift Found
- None new. (The S9 A/B manifest extension to 5m/15m/MES landed in the same
  session — see S-MLOPT-S9 log; those are additive research_only manifests.)

## Risks and Follow-Ups
- **A/B eval DONE — NEGATIVE** (`MB-20260604-001`, #2745): funding/OI did NOT add
  an `f1_volatile` lift (−0.0150 vs v2 under purged WF-CV). The manifest stays
  `research_only` — no claim of lift. **The OI half is the key blocker: Bybit's
  public OI history retention is ~8 days, so the open-interest features were
  effectively untested across the 5y dataset.** The funding-rate features (full
  5y coverage) added noise, not signal, on the vol-regime label.
- **Follow-up levers** (not pursued this session): (a) wire funding/OI into
  `setup_candidates._feature_fields` — a momentum/positioning *decision* target
  may suit funding better than the *volatility-regime* label; (b) a rolling
  forward OI capture (accrue the OI history the public endpoint won't backfill);
  (c) a different `funding_window_n`. Logged in `MB-20260604-001`.
- **Microstructure alpha decays** (research caveat): if funding/OI earns
  promotion later, monitor it via the KS/PSI drift gate — don't assume permanence.
- **Funding/OI is crypto-perp-specific** — these columns are 0.0 for MES (no
  funding/OI side-stream), so the feature is BTCUSDT-only by nature.
- **setup_candidates funding features** — the task allowed `market_features`
  AND/OR `setup_candidates`; this sprint did `market_features` (the cleaner
  S9-mirror A/B). Wiring the same funding/OI columns into
  `setup_candidates._feature_fields` is a natural follow-up once the regime A/B
  confirms the family carries signal.
- **Tier-3 gate stands**: the manifest is a proposal at `research_only`;
  promotion past `shadow` is operator-gated.

## Deferred Items
- `setup_candidates` funding/OI `_feature_fields` (decision-model side) — the
  funding A/B negative on the *vol-regime* label suggests funding may fit a
  *decision* target better; a natural next lever.
- A rolling forward OI capture (Bybit's public OI history is retention-limited to
  ~8 days; accruing it forward would let the OI features be properly tested).
- Auto-wiring funding/OI into the daily cycle by default stays OFF (opt-in via
  `ICT_BUILD_FUNDING_OI=1`) — correct given the A/B negative.

## Next Recommended Sprint
- The funding A/B landed negative (OI retention-limited); the S11 infra stands.
  Move to **S-MLOPT-S12** (2.4 cross-asset/macro for MES + wire the unused
  `account_context` family) or **S-MLOPT-S13** (3.1 per-bar regime scoring,
  Tier-2 — the highest-leverage regime unblock, `MB-20260529-001`; also the gate
  on promoting the S9 range-vol heads past `shadow`).

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage / live-path file touched; manifest is a Tier-3 proposal.
- [x] Roadmap status checked + updated.
- [x] Contradictions were recorded (none new).
- [x] Remaining unknowns stated clearly: the trainer-VM A/B is DONE and NEGATIVE
      (#2745, f1_volatile −0.0150); the OI half was effectively untested (Bybit
      ~8-day OI retention); funding-rate features added noise on the vol-regime
      label. Infra stands; manifest research_only; follow-up levers logged in
      `MB-20260604-001`.
