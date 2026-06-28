# Design A — ML regime head as the regime-router **vol-axis** verdict (2026-06-27)

Operator-approved (2026-06-27, all-three go). Tier-3 order-path program, built
in observe → use → enforce phases; each live phase is a separate operator-gated
draft PR. This doc is the spec.

## Headline reframe (why the original framing was wrong)

The ML regime heads predict a **2-class volatility label `{range, volatile}`**
(`P(volatile)`; `ml/configs/btc-regime-15m.yaml` `class_labels: [range, volatile]`).
The **live, populated** policy cells in `config/regime_policy.yaml`
(`trending`/`transitional`/`chop`) key on the **ADX *trend* axis** — a different
axis. `P(volatile)` cannot produce a trend label; feeding it into `intent.regime`
is a category error.

The coherent target is the **`vol_regime` axis** (`calm`/`volatile`), which feeds
the policy's 2-D `trend_vol:` block. So A is: **make the advisory head's
`P(volatile)` produce the `vol_regime` label** (replacing today's frozen-edge
threshold detector `vol_detector.py`), in shadow→use→enforce phases, ADX/frozen
as the fail-permissive fallback. Two consequences to state plainly:

- `trend_vol: {}` ships **empty**, so even a perfect ML `vol_regime` gates **zero**
  orders until OFF-cells are authored — a separate Tier-3 decision with its own
  backtest evidence.
- Today's `vol_regime` is already model-derived but via a **frozen-edge threshold**
  (`vol_detector.vol_regime_from_spec`), not the head's `predict_proba`. So A
  really means "replace the frozen-edge threshold with the head's live
  prediction," a narrower (and cleaner) change than "replace ADX."

If instead an ML **trend** verdict is ever wanted, that needs a head trained on
`regime_label ∈ {chop,transitional,trending}` (none exists) — a training task
first; the same verdict-source pattern would then apply to `intent.regime`.

## Current path (cited)

- Gate consumers: `intents.py:1014-1017` dispatch `_hard_regime_gate` (under
  `REGIME_ROUTER_ENABLED`, default off) vs `_shadow_regime_gate`. Both call
  `would_gate(strategy, side, regime=intent.regime, policy, vol_regime=intent.vol_regime)`
  (`intents.py:800-806`/`889-895`). `intent.regime`/`intent.vol_regime` come from
  `signal.meta` via `_stamp_regime_on_meta` (`strategy_signal_builders.py:28-74`,
  call sites `:57,95`).
- ADX detector: `regime/detector.py::detect_regime` → `{regime, adx, source:"adx-14"}`,
  `regime ∈ {chop,transitional,trending,unknown}`.
- Vol axis: `regime/vol_detector.py::detect_vol_regime` — resolves the head's
  **frozen** `vol_bucket_edges`, computes live `rolling_log_return_vol`, threshold-
  buckets → `calm`/`volatile`. Does **not** run the model. Shadow-stage heads
  only; skips yz heads (`:141-143`).
- The head's live `predict_proba` runs **only** in `regime_bar_scoring.emit_regime_bar_predictions`
  (writes `shadow_predictions.jsonl`, discards the score) — and only for
  **shadow**-stage heads (`discover_shadow_stage_model_ids`).
- Policy: `regime_policy.yaml` 1-D trend cells populated; `trend_vol: {}` empty.
  `would_gate` evaluates the vol cell only when `vol_regime` is passed and never
  lets it alter the 1-D `gated` (it sets `vol_gated`).

## Design — `ml_vol_verdict`

New module `src/runtime/regime/ml_vol_verdict.py`:
```
ml_vol_regime(symbol, timeframe, candles_df=None) ->
  {vol_regime: calm|volatile|unknown, p_volatile: float|None,
   source: "ml-advisory:<model_id>"|"unavailable", model_id}
```
1. Resolve the **advisory**-stage regime head for `(symbol, timeframe)` (new
   `discover_advisory_stage_regime_specs`, parallel to `vol_detector.resolve_vol_specs`
   but filtering `target_deployment_stage == "advisory"`, keeping the predictor).
   Prefer the **v2/non-yz** head (yz heads saturate live — same skip `vol_detector`
   already applies). Cache per-process.
2. Get current-bar `P(volatile)` — **preferred:** read from a small in-process cache
   that `emit_regime_bar_predictions` publishes (`{model_id:(bar_ts,p_volatile)}`),
   so the decision path adds **zero fetches**; **fallback:** score inline.
3. Map `P(volatile)` → label via `ML_VOL_VERDICT_THRESHOLD` (default 0.5).
4. **Fail-permissive:** no advisory head / unreadable / uncomputable / any error →
   `unknown` (which `would_gate` treats as `default-on`, never strands a signal).

**Wiring (recommended Option B — resolve in the gate):** keep the builder stamping
the frozen-edge `vol_regime` as fallback; inside `_shadow_regime_gate`/`_hard_regime_gate`,
compute `ml_vol_regime(...)` per candidate and (in `use`/`enforce`) pass it to
`would_gate` instead of `intent.vol_regime`. Localizes the change to `intents.py` +
the new module; the audit row carries both labels for the agreement comparison.

**Per-bar scoring extension:** add **advisory**-stage regime heads to the per-bar
scorer's discovery (today shadow-only) + publish their `P(volatile)` into the cache.
Reuses the existing fetch-gate/grouping → no new fetches; bounded by
`REGIME_BAR_SCORING_BUDGET_S`.

## Phasing (flag `REGIME_ML_VERDICT_MODE = off|shadow|use`, default off; `ML_VOL_VERDICT_THRESHOLD` float)

- **Phase 1 (shadow):** gate computes `ml_vol_regime` per candidate, emits a new
  `regime_ml_vol_shadow` audit row `{vol_regime_frozen, vol_regime_ml, p_volatile,
  agree, ml_source, enforced:false}`; decision still uses the frozen label. **No
  order change.** Requires ≥1 regime head at advisory + advisory heads per-bar scored.
- **Phase 2 (use):** gate passes the ML `vol_regime` (non-unknown) to `would_gate`.
  Still a behavioural no-op until `trend_vol` OFF-cells exist.
- **Phase 3 (enforce):** `REGIME_ROUTER_ENABLED=1` + authored `trend_vol` OFF-cells
  → the ML-derived `vol_regime` actually suppresses intents. OFF-cell authoring is
  its own Tier-3 PR gated on Phase-1 agreement evidence + the backtest below.

## Guardrails
Fail-permissive everywhere (broken ML path never strands a signal — identical to
the existing router safety contract). **Advisory-only** heads (promotion stays the
meaningful act). No new fetches (reuse the per-bar cache). Prefer v2/non-yz heads
(yz saturate). `ML_VOL_VERDICT_THRESHOLD` is Tier-3 (order-routing-affecting).

## Test + backtest plan
Unit: verdict happy/threshold-boundary/fail-permissive/stage-isolation; gate
Phase-1 emits the audit row + leaves the candidate set unchanged; Phase-2 passes the
ML label to `would_gate`; per-bar cache reuse (fetch count 0 on the decision path);
`unknown → default-on`.

Backtest (prereq harness work — Tier-1 research tooling, `scripts/backtest_system.py`):
the harness today builds intents with **no `regime`/`vol_regime`**, so it can't
measure any gating. Add: stamp `regime` (ADX-14) + `vol_regime` on harness intents,
`--vol-verdict={frozen,ml}` (replay the resolved head's `predict_proba` thresholded),
`--regime-router on|off`, and a backtest-local `trend_vol` OFF-cell set via a
`REGIME_POLICY_PATH` override (never the live YAML). Run the roster three ways —
ungated / frozen-vol-gated / ML-vol-gated — and compare net PnL, **maxDD%**, win
rate, per-(strategy,regime) fills. **Gate to enable Phase 2/3 live: ML-gated book ≥
frozen-gated book on net AND not worse on maxDD%** (mirrors the FLIP_POLICY
walk-forward acceptance). Cross-check vs the live Phase-1 agreement log.

**Evidence BEFORE promotion (`--ml-stage`, the option-2 lever — BUILT 2026-06-27):**
`--vol-verdict=ml` resolves an **advisory**-stage head by default (matching the live
verdict source), so with no regime head yet at advisory it degrades to frozen and the
ML arm == the frozen arm — the A/B can't be measured. `--ml-stage=shadow` (+ optional
`--ml-model-id <id>` to pin one candidate, e.g. `btc-regime-15m-lgbm-v2`) replays a
**shadow**-stage head's `predict_proba` so A's vol-gating evidence can be gathered on
the trainer **without** first doing the Tier-3 shadow→advisory promotion. Observe-only
— it never mutates the registry stage; the run's `evidence.ml_vol_stage` /
`ml_vol_model_id` make the resolved head explicit. The promotion remains the act that
gives the head **live** influence; this lever only lets the *backtest evidence* precede
(or run independently of) it.

**A/B RESULT (2026-06-27, `A-vol-gating-AB-evidence-2026-06-27.md`): POSITIVE.** The
4-arm run (ungated / 1-D-only / frozen-vol / ML-vol) over full BTC history: ML-vol-gated
**net $424 / maxDD 8.07% / ret-DD 0.47** vs frozen-vol-gated **$59 / 10.1% / 0.05** vs
ungated **$353 / 8.24% / 0.39** — the SAME OFF-cells driven by the ML head's vol label
beat the frozen-edge label decisively AND beat no-gating, while the frozen label *hurt*.
The gate criterion (ML ≥ frozen on net AND not worse on maxDD) is strongly met.
**Still required before any live flip (Tier-3):** a purged walk-forward + multi-symbol
re-run (the FLIP_POLICY live bar), then author live `trend_vol` OFF-cells, then flip the
flags — gated on the live Phase-1 agreement log (now accruing).

**VALIDATED on the trainer (2026-06-27, trainer-vm-diag #4765):** a real BTC run with
`--ml-stage shadow --ml-model-id btc-regime-15m-lgbm-v2` reports
`ml_vol_available=true, reason=ok, scored_bars=10, fallback_bars=0` — every intent's
`vol_regime` came from v2's live `predict_proba`. Getting there caught FOUR latent
harness bugs (each a false-evidence trap that would have made the ML arm silently equal
the frozen arm): (1) `scripts/ml/` shadowed the repo `ml/` package under `PYTHONPATH=.`
(`ModuleNotFoundError: ml.registry`) → force repo root ahead of `scripts/` on sys.path;
(2) resolver read `class_labels` from the regime-spec dict (which has none) instead of
the predictor → it rejected every head as `no_regime_spec`; (3) it called
`predict_proba` on the `ShadowPredictor` wrapper (which only exposes `.predict`) →
`AttributeError` per window → score the wrapped base instead; (4) opaque error/skip
reporting (now surfaces the message + per-window skip tallies in
`evidence.ml_vol_skips`). The remaining A-evidence step is the **full gated A/B**
(ungated / frozen-vol-gated / ML-vol-gated with `--regime-router on` + a backtest-local
`trend_vol` OFF-cell policy via `--regime-policy`) — which needs candidate OFF-cells
authored first (its own Tier-3 decision).

## Files (tiers)
New (Tier-1): `src/runtime/regime/ml_vol_verdict.py`, `tests/runtime/test_ml_vol_verdict.py`.
Modified (Tier-2, order-routing-adjacent; default-off flag → deploy is a no-op;
operator-ack PR): `src/runtime/intents.py` (mode + thread ML vol + audit rows),
`src/runtime/regime_bar_scoring.py` (advisory discovery + cache), `regime/__init__.py`.
Modified (Tier-1 research): `scripts/backtest_system.py` (regime/vol stamping +
`--vol-verdict`/`--regime-router`). Docs: this file, `CLAUDE.md` env table, ROADMAP/sprint.
**Tier-3 hard-blocked (NOT required for the code; separate operator-gated acts):**
authoring `config/regime_policy.yaml` `trend_vol` OFF-cells; flipping
`REGIME_ML_VERDICT_MODE=use`/`REGIME_ROUTER_ENABLED=1`/`ML_VOL_VERDICT_THRESHOLD` on
the VM; promoting a (v2) regime head shadow→advisory. **Not touched:** strategies.yaml,
accounts.yaml, risk_caps.yaml, orders.py, risk_counters.py, unit files.

## Open caveats
1. The premise is the **vol** axis, not ADX — if a trend ML verdict is wanted, train
   a trend head first.
2. The yz heads saturate live — use **v2** advisory heads, not yz.
3. Empty `trend_vol` means Phase 2 is a behavioural no-op (observe-until-authored);
   "enable the ML verdict" and "the ML verdict changes a trade" are two switches.
