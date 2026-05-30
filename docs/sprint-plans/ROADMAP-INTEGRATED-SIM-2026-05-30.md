# ROADMAP — Integrated Strategy+ML Simulation Harness (SIM)

> **Status:** DESIGN — operator-requested 2026-05-30. Not yet built.
> **Author:** Claude (session_0176Luj9yVhn39gz4WhfKbmJ).
> **Tier:** building the harness is Tier-1 (new tooling, no live-order-path
> change). Acting on its *output* (promoting a model, changing a strategy
> param) stays Tier-3 / operator-gated as today.
> **Companion canon:** `docs/ARCHITECTURE-CANONICAL.md` (pipeline + intent
> layer), `.claude/skills/backtesting/SKILL.md` (existing harnesses),
> `.claude/skills/model-training/SKILL.md` (registry + ladder).

## 1. Why this exists (the operator's three problems)

Today we evaluate strategies and models in **isolation**, and that hides
failures that only appear when they run **together in the real funnel**:

1. **Per-strategy backtests are siloed.** `scripts/backtest_{trend,fade,
   squeeze,ict_scalp}.py` + `src/backtest/run_backtest_vwap.py` each run ONE
   strategy on raw candles. They never see the **intent multiplexer**
   (conflict resolution, priority, dedupe) or the **risk gates** (daily-loss
   cap, position caps, prop-mission limits) that decide whether a signal
   becomes a real order live. So a strategy that looks profitable solo may
   place far fewer (or different) trades once it competes for the same
   account through `aggregate_intents`.

2. **Models are eval'd on a holdout, not in the decision funnel.** A
   regime/setup model reports `macro_f1` / `mae` on a time-split holdout.
   But live it only scores the signals that *survive* to the advisory stage
   — and on a quiet/sideways tape (cf. the 2026-05-30 finding that shadow
   preds only fire on actionable signals) it may influence a tiny fraction
   of the decisions its eval implied. **"Looks great in eval, barely fires
   live"** is invisible until promotion. The operator's words: *"maybe it's
   farther back in the pipeline and suddenly it's getting much fewer
   decisions than it looks like in the backtesting, so the quality of
   decision-making goes down."*

3. **No portfolio-level what-if.** We cannot answer *"what would the WHOLE
   system have returned over the last N years if strategies {A,B} were on,
   model M was advisory at factor F, and param P was X?"* — i.e. simulate
   changes against the **actual historical overall performance of everything
   together**, in variations.

SIM closes all three. It is the evidence step that should precede every
Tier-3 promotion/param change — the integrated analogue of the per-strategy
backtests.

## 2. Design principle: REUSE the live code, don't reimplement it

The cardinal rule (mirrors `new-strategy`'s "don't touch the aggregator"):
**SIM must drive the exact same functions the live pipeline uses**, feeding
them historical bars instead of live ticks. If SIM reimplements intent
resolution or risk logic, sim/live drift makes its results lies. The live
surfaces SIM composes (all already exist):

| Live component | Module | SIM uses it for |
|---|---|---|
| Strategy signal builders | `src/runtime/strategy_signal_builders.py` | produce per-bar signals from historical candles (the builders already take a candles df) |
| Intent aggregation | `src/runtime/intents.py::aggregate_intents` | resolve competing strategy signals into one intent per account |
| Execution delta | `src/runtime/intents.py::compute_execution_delta` | turn target intent into the order action |
| Risk gate | `src/units/accounts/risk.py::RiskManager.evaluate` | accept/refuse each order with the real caps (daily-loss, pos-size, prop mission) |
| Advisory ML influence | `src/runtime/advisory_sizing.py::compute_advisory_factor` + `apply_advisory_downsize` | let an advisory-stage model resize/skip the simulated order |
| Leakage-safe feature row | `ml/shadow/backfill.py` projection | build the signal-time feature row for a model WITHOUT post-decision columns |
| Fill model | `src/backtest/backtester.py::simulate_trade` (TP/SL touch, fee bps) | resolve a sized order into a realized R/PnL |

SIM is therefore a **driver + bookkeeper**, ~all new code is the historical
loop, the portfolio ledger, and the report — not the trading logic.

## 3. The four pieces (operator: "start with one but we need all")

Phased so each builds on the last; each phase ships independently and is
useful alone.

> **Phase 1 status (2026-05-30): BUILT.** `sim/` package (`engine.py`,
> `ledger.py`, `fills.py`, `__main__.py`) + `tests/test_sim_phase1.py`
> (15 passing, incl. a faithfulness test proving the engine drives the REAL
> `aggregate_intents` priority arbitration, not a copy). CLI verified
> end-to-end. Real evaluation runs on the trainer VM's 5y data (the sandbox
> sample is too short to be evidential, as documented in
> `docs/audits/backtest-harness-validation-2026-05-30.md`). Phases 2–4 next.

### Phase 1 — Integrated pipeline replay (FOUNDATION)
A harness that walks historical bars for a symbol set and, per bar:
strategy builders → `aggregate_intents` → `compute_execution_delta` →
`RiskManager.evaluate` → fill model → portfolio ledger. Output: realized
trades + portfolio equity curve for **the system as a whole**, with
per-strategy attribution AND the **funnel counts** (signals emitted →
survived multiplexer → passed risk → filled). No models yet.
- *Catches problem #1.* The funnel counts alone are new signal: how many of
  a strategy's solo-backtest trades actually survive the integrated funnel.

> **Phase 2 status (2026-05-30): BUILT.** `sim/models.py` — `ModelScorer`
> scores SIM decisions on the leakage-safe signal-time feature row and returns
> the size factor via the LIVE `advisory_downsize_factor` (not a copy).
> Counterfactual loading: scores a model at ANY stage (incl. shadow /
> candidate / research_only) by reusing the live factory's model-state load +
> predictor class + `ShadowPredictor`, skipping ONLY the stage gate — the
> scoring path is otherwise identical to live. `summary.json::models_in_loop`
> reports net_r with vs without model + cut_losers/cut_winners. `--models`
> CLI flag. `tests/test_sim_phase2.py` (15 incl. the leakage guard + the
> reductive-never-amplify invariant). Real diffs run on the trainer VM where
> the model files live (graceful factor=1.0 when files absent).

### Phase 2 — Models-in-the-loop
Inject advisory-stage models into the Phase-1 loop via the REAL
`advisory_sizing` path: at the order step, build the leakage-safe feature
row (backfill projection), call `compute_advisory_factor`, apply
`apply_advisory_downsize`. Run the same history **with-model vs
without-model** and diff realized portfolio PnL/DD/expectancy.
- *Catches "test MLs + strategies together."* This is the with/without-model
  counterfactual on the whole system.

> **Phase 3 status (2026-05-30): BUILT.** `sim/attrition.py` —
> `compute_attrition` reads the Phase-2 ledger and, per model, reports
> `funnel_scored` (decisions actually scored in the integrated replay) vs
> holdout `eval_n` (`eval_n_from_registry` reads `metrics.n_eval` from the
> model-state JSON) → `attrition_ratio`, plus `bearish` / `influenced`
> (downsized decisions it was bearish on) / `bearish_net_r` (flagged-trade
> quality: negative = flags losers = good) and a one-line promotion-readiness
> verdict gated on a `_MIN_FUNNEL_VOLUME` floor. Surfaced in
> `summary.json::decision_attrition` + the CLI. `tests/test_sim_phase3.py`
> (11). This is the "looks great in eval, barely fires live" detector.

### Phase 3 — Decision-attrition report
Instrument Phase 2 to count, per model: decisions the model COULD have
scored (signals reaching advisory) vs decisions its isolated eval implied
(holdout n), and the realized quality of the decisions it actually
influenced. Emit an **attrition ratio** + a "promotion-readiness on real
funnel volume" line.
- *Catches problem #2.* Surfaces "great f1, scores 3% of live decisions"
  BEFORE the shadow→advisory promotion.

### Phase 4 — Multi-variation sweep
A scenario runner over Phases 1–3: a small YAML of variants (strategies
on/off, which models advisory + at what factor, param overrides) run over
the same history, ranked by overall portfolio PnL / maxDD / expectancy.
Mirror outputs into the existing sweep surface (`/api/bot/backtests/sweeps`)
so the dashboard shows them next to the operator's manual sweeps.
- *Catches problem #3.* "Simulate changes against actual historical overall
  performance, in variations."

## 4. Hard rules (non-negotiable)

1. **Leakage discipline** (inherits `ml/shadow/backfill.py` + WS5 rules):
   the model feature row at decision-time T contains ONLY columns knowable
   at T. No `pnl`, `exit_*`, `r_multiple`, forward-vol, or the label. A
   leakage-guard unit test gates the feature projection.
2. **No live-path writes.** SIM is read-only against history + registry. It
   writes only to its own outputs (`datasets-out/sim/...` /
   `runtime_logs/sim/...`). It NEVER writes `trade_journal.db`,
   `config/*`, or the model registry. Acting on SIM output is a separate,
   operator-gated Tier-3 change.
3. **Reuse, don't fork.** If a SIM run needs a behaviour the live functions
   don't expose, extend the live function (with tests) — never copy it into
   SIM. Drift between SIM and live is the failure mode we're preventing.
4. **Determinism.** Fixed seed; same history + same config ⇒ byte-identical
   ledger, so variants are comparable and regressions are detectable.
5. **Runs on the trainer VM** (heavy historical pulls, WS9 rule), driven via
   the relay like training. Never heavy-runs on the live trader VM.

## 5. Data

- BTCUSDT: the 5-year `market_raw`/`market_features` the daily cycle now
  builds (PR #2399). MES: best-effort depth as today.
- Reuses the dataset path resolver + `market_raw` shards — SIM does not pull
  its own candles; it consumes what `build_trainer_datasets.sh` produced.

## 6. Outputs

- `runtime_logs/sim/<run_id>/ledger.jsonl` — every simulated decision +
  fill + funnel-stage tag.
- `runtime_logs/sim/<run_id>/summary.json` — portfolio + per-strategy +
  per-model attribution + funnel counts + attrition ratios.
- Phase 4: `SUMMARY.md` + `all_metrics.json` per variant, mirrored to
  `/api/bot/backtests/sweeps` (dashboard Backtesting tab).

## 7. Build order / acceptance

| Phase | Deliverable | Acceptance |
|---|---|---|
| 1 ✅ BUILT 2026-05-30 | `sim/` engine: historical driver + ledger + funnel counts, CLI `python -m sim run` | One BTCUSDT multi-strategy run over ≥1y reproduces each strategy's solo-backtest trade set MINUS those killed by the multiplexer/risk gate; funnel counts emitted; leakage test green |
| 2 ✅ BUILT 2026-05-30 | advisory-model injection via the real `advisory_downsize_factor` (`sim/models.py`) | with/without-model portfolio diff on the same history (`summary.json::models_in_loop`); `--models id1,id2` CLI flag; leakage guard + reductive-invariant tests green |
| 3 ✅ BUILT 2026-05-30 | attrition report (`sim/attrition.py`) | per-model `funnel_scored` vs holdout `eval_n` → `attrition_ratio` + flagged-trade quality + promotion-readiness verdict in `summary.json::decision_attrition` |
| 4 | variation sweep + dashboard mirror | N variants ranked; visible on `/api/bot/backtests/sweeps` |

Each phase = its own PR (draft, Tier-1), validated on the trainer VM before
merge. A new `/sim` skill is proposed once Phase 1 lands so future sessions
drive it the way `/backtesting` + `/model-training` are driven today.

## 8. Open questions for the operator (do not block Phase 1)

- **Fill realism:** start with the existing TP/SL-touch + fee-bps model
  (matches the standalone backtests for comparability), or invest in a
  higher-fidelity fill (intrabar path, partials, funding)? *Default: reuse
  the existing model in Phase 1; revisit for Phase 4.*
- **Account model:** simulate one account or the full multi-account routing
  (bybit_1/bybit_2/ib_paper/prop) from `accounts.yaml`? *Default: single
  configurable account in Phase 1; full routing in Phase 4.*
