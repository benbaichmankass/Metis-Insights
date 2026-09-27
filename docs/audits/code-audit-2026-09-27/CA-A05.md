# CA-A05 — code audit of signal builders, strategy units, `ict_detection`, and regime

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> lane CA-A05 of the 2026-09-27 code audit · session `session_01HdCvEVhTk66qUep6h6pP5E` · reports to manager `session_01KAQRJxRbRwpYTkPgBjNVyQ` · audited HEAD `da9e68d2d` (origin/main at 2026-09-27T13:10Z, before CA-A03/prop-EV/PR#13156 landed later the same session)

**The question:** does the code in `src/runtime/strategy_signal_builders.py`, `src/units/strategies/**` (excluding `pairs_executor.py`, owned by CA-A02), `src/ict_detection/**`, the regime subsystem, and how `config/strategies.yaml`/`config/regime_policy.yaml` are actually consumed, do what their docstrings, callers, tests, and the canonical docs say — and where does it not?

**Answer:** **20 open findings**: 0 critical, 5 high, 9 medium, 6 low. No verified non-issues or duplicates dropped — every finding below survived scrutiny and is reported open. Machine-readable version: [`CA-A05.findings.jsonl`](CA-A05.findings.jsonl), one object per line.

## The 5 high findings

All 5 were filed as pipeline items immediately, in their own PR (`#13171`, per the lane's standing rule that a HIGH finding is filed at once rather than waiting for this consolidation).

- **CA-A05-001** (`PI-20260927-CAA05-0001`, money-at-risk): `_ict_scalp_variant_builder` never fetches HTF candles, so the HTF trend-bias filter is a silent no-op for 7 live-configured `ict_scalp` variants (sol/xrp/avax/xrp_15m/eth_15m/sol_15m/mgc_15m) despite all 7 declaring `htf_trend_filter_enabled: true`. The validated backtest ran with the filter on; live does not have it.
- **CA-A05-002** (`PI-20260927-CAA05-0002`, docs): the M28 macro event-resolver/store/calendar subsystem is marked "DONE" in its design doc but has zero production producer or consumer anywhere in the repo — built, never wired.
- **CA-A05-003** *(reclassified medium, see below)* — the ETF/futures long-only side-filter finding was originally scored high on the money-at-risk axis; on review its blast radius is *config drift risk* (current behavior is correct, only future edits would silently no-op), which is `CA-A05-003` at medium severity in the final table below. It was **not** filed as a pipeline item at HIGH; the 5 that were are listed here and match this doc's ids exactly.
- **CA-A05-004** (`PI-20260927-CAA05-0005`, observability): `LiquidityDetector.detect_liquidity_sweeps()` implements only the "pierce" half of its own documented "pierce, then reverse" sweep contract — reproduced with a synthetic 40-bar series that pierces and never reverses, still flagged `swept=True`.
- **CA-A05-005** (`PI-20260927-CAA05-0003`, docs): the M28 sleeve's own committed P4 scorecard shows it fails its own promotion gate (`calibration_rank≈0`, `edge_vs_baseline` negative) even at **zero cost**, but the design doc's "DONE" status doesn't surface this verdict.
- **CA-A05-006** (`PI-20260927-CAA05-0004`, docs): `htf_pullback_trend_2h.py`'s docstring claims "SCAFFOLD, not wired... inert" while the module is the live implementation behind 19 enabled strategies (16 `execution: live`), one of which traded real money on `bybit_2` for ~3 months.

**Correction:** the pipeline items were filed as findings were reported by the 4 parallel sub-lanes, in arrival order, before this consolidation assigned final `CA-A05-0NN` ids. The pipeline items' own `origin.ref` text cites the ids they were filed under (`CA-A05-001`, `-002`, `-004`, `-005`, `-006` — note **no `-003`**, which is the one reclassified to medium after filing since its live behavior is currently correct). This doc's ids are now the ground truth; a reader should resolve a pipeline item's `origin.ref` id against **this table**, not assume it is unchanged.

## Open findings, most severe first

| id | severity | axis | claim | location | pipeline |
|---|---|---|---|---|---|
| CA-A05-001 | high | trading-correctness | `_ict_scalp_variant_builder` never fetches HTF candles, so the HTF trend-bias filter is a silent no-op for 7 live-configured variants despite `htf_trend_filter_enabled: true`... | `src/runtime/strategy_signal_builders.py:726-855` | PI-20260927-CAA05-0001 |
| CA-A05-002 | high | operating-model | M28 event-resolver/store/calendar subsystem marked DONE but has zero production producer/consumer anywhere in the repo... | `src/units/strategies/macro_thesis/event_*.py` | PI-20260927-CAA05-0002 |
| CA-A05-004 | high | observability | `detect_liquidity_sweeps()` flags `swept=True` on pierce alone, never checking the docstring's required "then reverses" half... | `src/ict_detection/liquidity.py:120-159` | PI-20260927-CAA05-0005 |
| CA-A05-005 | high | operating-model | M28 sleeve's own P4 scorecard fails its own promotion gate at zero cost (`calibration_rank≈0`, `edge_vs_baseline` negative)... | `comms/macro/thesis_p4_scorecard.json` | PI-20260927-CAA05-0003 |
| CA-A05-006 | high | operating-model | `htf_pullback_trend_2h.py` docstring claims "SCAFFOLD, not wired, inert" while it is the live implementation behind 19 enabled strategies... | `src/units/strategies/htf_pullback_trend_2h.py` | PI-20260927-CAA05-0004 |
| CA-A05-003 | medium | trading-correctness | `long_only`/`side_filter` config key is decorative for 9 ETF/futures builders — suppression is hardcoded, decoupled from config... | `src/runtime/strategy_signal_builders.py` (9 sites) | — |
| CA-A05-007 | medium | operating-model | `htf_pullback_trend_2h.py` docstring cites a `timeout_bars:200` example removed from config 2 days before the docstring was last edited... | `src/units/strategies/htf_pullback_trend_2h.py` | — |
| CA-A05-008 | medium | operating-model | `squeeze_breakout_4h.py` docstring says "runs in SHADOW... never sends a live order" — has been `execution: live` since 2026-06-23... | `src/units/strategies/squeeze_breakout_4h.py` | — |
| CA-A05-009 | medium | trading-correctness | `vwap.py::order_package()` is a dead Coordinator-only path that would silently disable the HTF gate and break position monitoring if ever revived... | `src/units/strategies/vwap.py` | — |
| CA-A05-010 | medium | trading-correctness | Live valuation producer never fetches the non-FRED inputs SPY/GLD/SLV need — SPY can never form a live thesis... | `scripts/macro/valuation_snapshot_produce.py` | — |
| CA-A05-011 | medium | provenance | 3 macro_thesis config loaders swallow every exception into an unlogged empty/default fallback... | `macro_thesis/valuation_feed.py, event_calendar.py, thesis_tick.py` | — |
| CA-A05-012 | medium | trading-correctness | `thesis_tick` re-forms a thesis with `status=draft` on every tick with no check of stored status — latent lifecycle-clobber risk... | `macro_thesis/thesis_engine.py, thesis_tick.py, thesis_store.py` | — |
| CA-A05-013 | medium | observability | `detect_equal_highs`/`detect_equal_lows` emit N-1 overlapping duplicate pool entries for one physical level... | `src/ict_detection/liquidity.py:20-69` | — |
| CA-A05-014 | medium | docs | `ict_detection/__init__.py` claims a shared "filter layer" used by all strategies; 0 non-test callers of the 3 named detectors exist... | `src/ict_detection/__init__.py` | — |
| CA-A05-015 | low | docs | Stale `strategy_risk_pct` comment describes a mechanism removed 2026-06-29, in 2 of ~55 builders... | `src/runtime/strategy_signal_builders.py` | — |
| CA-A05-016 | low | docs | `vwap` block's `threshold: 0.01` config key is read nowhere; live threshold is a hardcoded constant... | `config/strategies.yaml` | — |
| CA-A05-017 | low | docs | `turtle_soup` block declares 3 keys read nowhere in the codebase... | `config/strategies.yaml` | — |
| CA-A05-018 | low | docs | 4 strategy files justify meta-threading on a "cfg={} in production" claim false since the 2026-08-31 M20 E3 fix... | `src/units/strategies/{trend_donchian,fade_breakout_4h,squeeze_breakout_4h,htf_pullback_trend_2h}.py` | — |
| CA-A05-019 | low | test-coverage | 3 `ict_detection` test functions are guard-only (0 asserts, unconditional `return True`)... | `tests/test_fvg_ob.py, tests/test_swing_detection.py` | — |
| CA-A05-020 | low | provenance | `regime_policy.yaml`'s `schema_version: 2` is asserted by 1 test but read by zero runtime code... | `config/regime_policy.yaml:54` | — |

## What the sub-lanes found, headline

- **Signal builders** (`strategy_signal_builders.py`, 5,661/5,661 lines read): the file is broadly sound, but two config-vs-code gaps were found where a YAML key that looks load-bearing is not actually read by the builder (CA-A05-001 money-at-risk, CA-A05-003 latent), plus one stale comment (CA-A05-015).
- **Core strategy units** (17/17 files read, `pairs_executor.py` and `macro_thesis/` correctly excluded): **no functional order-placement bug found** — every finding here is docs/config-vs-code drift (a docstring claiming "not wired"/"shadow-only" that is stale by weeks to months, or a config key nobody reads). The most consequential is CA-A05-006: a "scaffold, inert" docstring on a module that has carried real money.
- **`macro_thesis` subpackage** (19/19 files read, 3,670 lines): the point-in-time/no-lookahead discipline is sound for every currently-wired metric — a genuine positive finding, not just an absence of bugs. The two high findings (CA-A05-002, -005) are about the gap between the design doc's stated status and what is actually wired/proven; the sleeve currently has **no measured edge** at zero cost, which its own gate correctly reflects.
- **`ict_detection` + regime** (all files read, 3,525 + 1,152 + 145 lines of YAML): the **regime/vol-gating subsystem is the strongest-engineered part of this audit's scope** — every collapsed-state risk explicitly asked for (insufficient bars vs. computed-neutral, fetch failure vs. real signal) is designed against and distinguished in the audit trail. Every `regime_policy.yaml` key is read somewhere except one cosmetic `schema_version` tag (CA-A05-020). The real problems are in the smaller, less-used `ict_detection` package (CA-A05-004, -013, -014, -019).

## `config/strategies.yaml` and `config/regime_policy.yaml` key audit

**`config/regime_policy.yaml`** (145 lines, every key traced to a runtime read site): `trending`/`transitional`/`chop` blocks and their nested `<strategy>.<side>` cells are read by `src/runtime/regime/policy.py` and enforced end-to-end through `src/runtime/intents.py`'s hard/shadow gates — verified consistent with the file's own inline documentation of what is/isn't enforced. The 2-D `trend_vol` block and its cells are likewise read and enforced, gated correctly on `REGIME_ML_VERDICT_MODE=use`. **The only gap: `schema_version` (CA-A05-020)**, declared and test-asserted but read by zero runtime code.

**`config/strategies.yaml`** (2,940 lines, 55 strategy blocks, `yaml.safe_load`-parsed and cross-checked against every unit's `_DEFAULTS` + a repo-wide grep for reads): the large majority of declared per-strategy keys are genuinely consumed, many one layer removed from the strategy unit itself (in `strategy_signal_builders.py`, `exit_levers.py`, `exit_head_apply.py`, or `account_side_filter.py`). Confirmed dead keys: `vwap.threshold` (CA-A05-016), `turtle_soup.entry_tf`/`max_entry_wait_bars_1m`/`trail_atr_mult` (CA-A05-017), and the 9-strategy `long_only`/`side_filter` decoupling (CA-A05-003) — the last is the one genuinely risky case, since the config key currently agrees with hardcoded behavior by coincidence rather than by being read.

## Reading coverage

| scope | files | lines | read in full? |
|---|---|---|---|
| `src/runtime/strategy_signal_builders.py` | 1 | 5,661 | yes, 100% |
| `src/units/strategies/*.py` (excl. `pairs_executor.py`, `macro_thesis/`) | 17 | ~9,000 | yes, 100% |
| `src/units/strategies/macro_thesis/*.py` | 19 | 3,670 | yes, 100% |
| `src/ict_detection/*.py` | 7 | 1,152 | yes, 100% |
| `src/runtime/regime/*.py` + `regime_bar_scoring.py`, `regime_shadow.py`, `forecast_live.py`, `cross_asset_live.py`, `entry_head_pwin.py`, `liquidity_state.py`, `decision_subject.py` | 12 | 3,525 | yes, 100% |
| `config/strategies.yaml` | 1 | 2,940 | yes, key-by-key |
| `config/regime_policy.yaml` | 1 | 145 | yes, key-by-key |

**Coverage: every file in scope read start to end.** Files read only far enough to verify a specific cross-reference (out of stated scope, not audited in full): `src/runtime/exit_levers.py`, `exit_head_apply.py`, `trail_decay.py`, `trail_vol.py`, `target_extension_soak.py`, `account_side_filter.py`, `src/core/coordinator.py`, `src/runtime/order_monitor.py`, `src/runtime/intent_multiplexer.py`, `src/runtime/intents.py`. A full audit of those could surface further findings, particularly `exit_levers.py`'s stale-stop/giveback precedence (an already-filed, still-latent bug, `BL-20260818-LIVE-DONCHIAN-INVERTS-THE-HARNESS-LEVER-PRECEDENCE`, independently re-verified still latent during this pass, not re-reported here).

## Tests actually run (not just read)

327 tests (ict_detection + regime) + 314 tests (core strategy units) + 239 tests (macro_thesis) + 26+12 tests (side_filter / ict_scalp variants, counted within the signal-builders pass) — **906 total test executions across the 4 sub-lanes, 0 failed, 1 skipped.** A green run is evidence only for what the tests assert; several findings above (CA-A05-001, -003, -009, -019) name the exact reason a passing suite did not catch the gap (a fixture that disables the exact path in question, or a guard-only test with no assertions).

Two behaviors were verified by synthetic reproduction beyond static reading: CA-A05-004 and CA-A05-013 (the liquidity-sweep and equal-highs bugs), each confirmed with a hand-built OHLC series, not just inferred from the source.

## Behavioural coverage (checked against real tests/behavior, not just read)

| capability in scope | exercised? | result |
|---|---|---|
| HTF trend-bias filter fires for every `htf_trend_filter_enabled: true` leg | yes (code trace + test-suite read) | **fails** for 7 of 8 ict_scalp legs (CA-A05-001) |
| `long_only`/`side_filter` config key controls suppression | yes (grep + confirm_bars trace) | **fails** for 9 legs (decoupled, CA-A05-003) |
| Sweep detector requires pierce-then-reversal | yes (synthetic repro) | **fails** (CA-A05-004) |
| Equal-high/low pools deduplicate by price level | yes (synthetic repro) | **fails** (CA-A05-013) |
| `regime_policy.yaml` keys all reach a runtime read site | yes (grep, full key enumeration) | **holds**, except `schema_version` (CA-A05-020) |
| Regime hard-gate / vol-gate enforcement matches YAML's own documented enforcement conditions | yes (cross-checked against `intents.py`) | **holds**, no drift found |
| M28 point-in-time / no-lookahead discipline for every currently-wired metric | yes (code trace) | **holds** |
| M28 event-resolver join is live | yes (grep for producer/consumer) | **fails**, 0 wiring (CA-A05-002) |
| M28 sleeve clears its own P4 promotion bar | yes (read the committed scorecard) | **fails** at zero cost (CA-A05-005) |
| Strategy docstrings match current `execution`/`enabled` config | yes (4 files cross-checked) | **fails** on 2 (CA-A05-006, -008) |
| `vwap.order_package()` Coordinator path threads cfg correctly | yes (empirical repro) | **fails**, but 0 live callers (CA-A05-009) |

**Exercised: 11 of 11 capabilities identified as checkable within this lane's scope.**

## What I would audit next with more budget

1. **`exit_levers.py` / `exit_head_apply.py` in full** — read only far enough to verify cross-references from the in-scope strategy units; the stale-stop/giveback precedence bug already filed (`BL-20260818-...`) suggests more may be found here.
2. **Live journal cross-check for CA-A05-001**: whether the HTF-filter gap has produced any *observed* divergent trade outcome on `bybit_1`/`ib_paper` (this pass established the code path, not a production instance — no access to `signal_audit.jsonl` or the live journal DB from this environment).
3. **`src/runtime/intents.py` in full** — read only as far as needed to verify `regime_policy.yaml` consumption; a dedicated pass could find more in the intent-routing layer itself (out of this lane's declared scope, likely another lane's).
4. **A CI guard family for the "docs vs. code" class**: 6 of this lane's 20 findings (CA-A05-002, -005, -006, -007, -008, -018) are a strategy/design doc making a claim about wiring, execution mode, or config that the code/config directly contradicts. A single guard pattern (grep known stale-claim phrases in docstrings, cross-reference against `config/strategies.yaml` enabled/execution state) would catch most of this class going forward.
