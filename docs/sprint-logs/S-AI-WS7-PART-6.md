# S-AI-WS7-PART-6 — Coordinator-side shadow predictor cache

**Date:** 2026-05-10
**Authority:** [`docs/sprint-logs/S-AI-WS7-PART-4.md`](S-AI-WS7-PART-4.md), [`docs/sprint-logs/S-AI-WS7-PART-5.md`](S-AI-WS7-PART-5.md)
**Status:** ✅ COMPLETE

## Goal

Lift the per-tick `ml.shadow.factory.resolve_predictors(...)` call
out of the strategy hot path. Both `vwap` and `turtle_soup` already
support resolution mode 1 — accept a pre-resolved
`cfg["_shadow_predictors"]` list and skip their own factory call.
PART-6 wires the Coordinator to populate that field once per
strategy and reuse the cached list across every tick, with cache
invalidation on `reload_strategy_config` so a YAML edit is picked
up without restarting the bot.

## Decisions

- **Cache lives on the Coordinator, keyed by strategy name.**
  `Coordinator._shadow_predictors_cache: dict[str, list]`. Lazily
  populated on the first dispatch that needs it; subsequent ticks
  hit the cache and pay zero factory cost.
- **Empty result is cached too.** Strategies with no
  `shadow_model_ids` field, or with an empty list, still get an
  entry in the cache (`[]`). This avoids re-checking the YAML for
  the absence of the field on every tick.
- **Cache invalidation = `reload_strategy_config` clears the
  whole cache.** A YAML edit (adding/removing/changing
  `shadow_model_ids` for any strategy) re-resolves on the next
  dispatch. Coarse-grained but cheap, matches operator
  expectations: `reload_strats` in the runbook is the canonical
  "I edited YAML, please pick it up" trigger.
- **Dispatcher injects via `_shadow_predictors`.** The
  `strategy_order_pkg` path now builds cfg as
  `{**self._strategy_cfg(name), "symbol": symbol,
  "_shadow_predictors": self._get_shadow_predictors(name)}`. The
  strategy's own `_resolve_shadow_predictors` honours mode 1 first,
  so the per-tick factory call is short-circuited.
- **Per-tick factory cost stays available for direct
  `mod.order_package(cfg, ...)` callers.** The strategy still
  supports modes 2 (singular legacy injection), 3 (raw
  `shadow_model_ids` resolved per-tick), and 4 (no shadow). Tests
  that bypass the Coordinator continue to work unchanged.
- **No public API change for the cache.** `_get_shadow_predictors`
  is a private helper. The cache dict is a public attribute only
  for test inspection — the runtime contract stays "use the
  Coordinator's `strategy_order_pkg`".
- **Lazy import of `ml.registry` and `ml.shadow.factory`.** Kept
  the `from ml...` lines inside `_get_shadow_predictors` so
  Coordinator init doesn't pay the import cost when no strategy
  uses shadow mode.

## Deliverables

- `src/core/coordinator.py`:
  - `__init__`: new `self._shadow_predictors_cache: dict[str,
    list] = {}` attribute.
  - `strategy_order_pkg`: cfg build now includes
    `"_shadow_predictors": self._get_shadow_predictors(strategy)`.
  - New `_get_shadow_predictors(name)` private helper —
    cache-or-resolve-and-cache; reads `shadow_model_ids` (and
    optional `_shadow_registry_root` / `_shadow_log_path`
    overrides) from the per-strategy cfg.
  - `reload_strategy_config`: now clears
    `_shadow_predictors_cache` before returning the load summary.
- `tests/test_coordinator_shadow_cache.py` (new) — 5 tests:
  - `test_strategy_without_shadow_field_returns_empty` — strategy
    with no `shadow_model_ids` gets an empty list, cached as `[]`.
  - `test_resolves_once_then_caches` — `resolve_predictors` is
    called exactly once across three back-to-back
    `_get_shadow_predictors` calls; identity comparison
    confirms the same list is returned.
  - `test_reload_invalidates_cache` — `reload_strategy_config`
    clears the cache (verified post-reload).
  - `test_separate_strategies_cache_independently` — vwap and
    turtle_soup get independent cache entries; lists don't
    share storage.
  - `test_dispatch_injects_shadow_predictors` — full dispatcher
    path: spies on `vwap.order_package` and verifies the cfg it
    receives carries `_shadow_predictors` populated with the
    expected `model_id` values.

## Acceptance

- [x] `pytest tests/ml/ tests/runtime/` — 266 / 266 pass + 1
      skipped (the new coordinator shadow cache test skips on dev
      sandboxes without pandas; CI has pandas).
- [x] `ruff check` clean on `src/core/coordinator.py` and the new
      test module.
- [x] No behavioural change for strategies without
      `shadow_model_ids` — the cfg gets `_shadow_predictors=[]`,
      which the strategies' resolution mode 1 treats as a pass-
      through to mode 4 (empty list).
- [x] No behavioural change when `shadow_model_ids` IS set —
      production runs that already had non-empty
      `shadow_model_ids` still get the same list of predictors,
      just resolved once per strategy instead of once per tick.
- [x] `reload_strategy_config` invalidates the cache (verified
      by `test_reload_invalidates_cache`).

## Out of scope (filed for follow-ups)

- **Train + register the WS5 baselines.** Still the operator
  task — needs real `trade_journal.db` data on the VM.
- **Shared resolver helper.** Both strategies still have their
  own `_resolve_shadow_predictors` (now mostly dead code since
  the Coordinator pre-resolves; mode 3 is reachable only by
  direct `mod.order_package(cfg, ...)` callers). Tempting to
  drop mode 3 entirely; left in for now so direct-call testing
  paths stay open.
- **Per-tick perf budget enforcement.** With caching, the only
  per-tick cost is `predictor.predict(row)` for each cached
  predictor. A future part can add an optional per-call timeout
  so a slow model can't push the tick over budget.
- **Audit log rotation.** `runtime_logs/shadow_predictions.jsonl`
  needs daily rotation when shadow mode is actually being used.

## Live runtime impact

For strategies WITHOUT `shadow_model_ids` set: byte-identical to
pre-PR. The cache holds an empty list, the strategies see
`_shadow_predictors=[]` (no-op), and tick latency is unchanged.

For strategies WITH `shadow_model_ids` set: first dispatch
incurs the factory cost (model state JSON read + predictor
class instantiation per id, same as before); every subsequent
dispatch is a dict lookup and a list iteration. The factory call
moves from O(ticks) to O(reloads).
