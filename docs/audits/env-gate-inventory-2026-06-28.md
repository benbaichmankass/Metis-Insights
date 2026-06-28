# Env-gate inventory — 2026-06-28 (full-system audit, Workstream B)

**Status:** COMPLETE. Re-derived from the actual `os.environ` / `settings.get(...)`
call sites in `src/` — **not** from `CLAUDE.md` or any doc (the prior subagent
pass leaned on the doc; this pass reads the code). Companion to
`docs/audits/full-system-audit-2026-06-28.md` Workstream B.

## Why this exists

The Prime Directive (`docs/CLAUDE-RULES-CANONICAL.md` § Prime Directive, rule 6)
forbids a **third gate**: a *required* live-trading capability must never sit
behind a separate default-OFF `*_ENABLED` flag (the pattern that stranded MES —
`MULTI_SYMBOL_ENABLED` defaulted off, so `ib_paper` declared `mode: live` with
all strategies yet never traded). The two sanctioned gates are
`accounts.yaml::mode` and `strategies.yaml::execution`; both default permissive.

This inventory checks every env gate against that rule.

## Headline conclusion

**No new Prime-Directive "third gate" violation.** No default-OFF `*_ENABLED`
gate fronts a *required* live-trading capability. The only two default-OFF
`*_ENABLED` gates are opt-in tooling, explicitly allowed:

- `M5_CONSUMER_ENABLED` — on-demand backtest consumer (carved out in
  `docs/audits/env-gate-purge-2026-05-10.md` § exclusions).
- `COMMS_PUSH_ENABLED` — GitPusher auto-commit of aggregated comms (tooling).

Every *required* live capability that used to hide behind a default-off flag has
already been made baseline (`NAKED_POSITION_AUTOPROTECT`,
`MONITOR_RECONCILE_ENABLED`, `POSITION_NETTING_GUARD_ENABLED`,
`MULTI_SYMBOL_ENABLED`, `NEWS_ENABLED`, `ADVISORY_MODE` — all removed). The
remaining required capabilities are gated only by **kill-switches** (`*_DISABLED`,
default-ON) or **`*_MODE`** flags, both of which are Prime-Directive-compliant
(omitting the var leaves the capability ON).

## The one finding worth operator attention — `NEWS_VETO_ENABLED`

`NEWS_VETO_ENABLED` (`src/news/news_score.py:100`) is **default-ON** ("true").
It is NOT the stranding failure class (default-OFF on a required capability) —
it is the opposite: a default-ON gate over a **live trade-blocking condition**
(the news veto). Verified facts:

- The veto is checked in `src/runtime/pipeline.py:520`, **before**
  `multi_account_execute` — so a veto blocks the signal for **every account,
  incl. real money**.
- `get_news_score` (`src/news/news_pipeline.py`) returns a neutral (no-veto)
  result when the source yields no articles. With the default `NEWS_SOURCE=newsapi`
  and no key, `fetch_news` returns nothing → the veto can never fire. But with
  `NEWS_SOURCE=rss` (keyless, always active), real articles flow and the veto is
  armed.
- **LIVE state (diag probe #4937, 2026-06-28):** the news layer IS active on the
  trader — `news_decisions.jsonl` is 2.1 MB with fresh rows (09:30→11:24 UTC,
  real `item_count:1` fetches for `"Bitcoin OR BTC OR cryptocurrency"`). All
  sampled rows are `veto:false/neutral`, but the veto is **armed**: a single
  article with `sentiment < -0.6` AND `impact > 0.7` would block the next signal
  on every account.

This matches the canonical CLAUDE.md framing ("A live source can veto
(pipeline.py:477), so selecting rss / setting a key is the deliberate
activation"). It is the correct Prime-Directive *shape* — a per-trade refusal
with a Telegram ping, account stays live.

**Operator disposition (2026-06-28): keep `NEWS_VETO_ENABLED` default-on.** The
veto stays armed whenever `NEWS_SOURCE` is active (current behaviour). The only
fix is doc-hygiene: two inline comments (`pipeline.py:505`, `diag.py:183`) called
the news layer "observe-only … before it ever gates live money", conflating the
observe-only **soak log** + **influence sizing** (`NEWS_INFLUENCE_MODE`, default
`off`) with the **veto** (live-by-default). Those comments are corrected in the
same PR as this doc — the veto is NOT observe-only.

## Full inventory (code-derived)

### Default-ON kill-switches (`*_DISABLED`, omit ⇒ capability ON)

| Var | Call site | Capability | Required? |
|---|---|---|---|
| `SIGNAL_DUAL_WRITE_DISABLED` | `src/utils/signal_audit_logger.py` | dual-write `signals` table | required (persistence) |
| `REGIME_BAR_SCORING_DISABLED` | `src/runtime/regime_bar_scoring.py` | per-bar regime shadow scoring | tooling (observe-only) |
| `CROSS_ASSET_LIVE_DISABLED` | `src/runtime/cross_asset_live.py` | live cross-asset peer features | tooling (observe-only) |
| `ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED` | `src/core/coordinator.py` | per-signal context snapshot writer | tooling (observe-only) |
| `TRADE_EVENT_TELEGRAM_DISABLED` | `src/runtime/mobile_push/trade_events.py` | per-trade Telegram/FCM events | tooling (comms) |

All compliant: omitting the var leaves the capability ON, so a dropped `.env`
never strands it (the netting-guard / Ampere failure class).

### Default-ON enable flags (omit ⇒ ON)

| Var | Call site | Capability | Note |
|---|---|---|---|
| `MULTI_STRATEGY_INTENT_LAYER` | `src/runtime/intent_multiplexer.py` | core intent aggregation | required; default-ON ⇒ compliant |
| `NEWS_VETO_ENABLED` | `src/news/news_score.py:100` | news veto (live trade-block) | **see finding above** — live-armed |
| `INSIGHTS_ENABLED` | `src/runtime/insights/generator.py` | M13 analyst cache generator | tooling (read-only observability) |

### Default-OFF enable flags (omit ⇒ OFF)

| Var | Call site | Capability | Required? |
|---|---|---|---|
| `M5_CONSUMER_ENABLED` | `src/bot/comms_handler.py` | on-demand backtest consumer | tooling — **carved out**, allowed |
| `COMMS_PUSH_ENABLED` | `src/bot/comms_handler.py` | GitPusher auto-commit | tooling — allowed |

Neither fronts a required live capability → no violation.

### `*_MODE` gates (multi-value; omit ⇒ inert default)

| Var | Call site | Default | Tier |
|---|---|---|---|
| `FLIP_POLICY` | `src/runtime/intents.py` | `hold` | Tier-3 (order-routing) |
| `NEWS_INFLUENCE_MODE` | `src/runtime/runtime_flags.py` | `off` | Tier-3 (sizing) |
| `REGIME_ML_VERDICT_MODE` | `src/runtime/runtime_flags.py` | `off` | Tier-3 (order-routing) |
| `ML_VOL_VERDICT_THRESHOLD` | `src/runtime/runtime_flags.py` | `0.5` | Tier-3 (tuning) |
| `CONVICTION_SIZING_MODE` | `src/runtime/runtime_flags.py` | `off` | Tier-3 (sizing) |
| `CONVICTION_SIZING_DIRECTION` | `src/runtime/runtime_flags.py` | `reductive` | Tier-3 (sizing) |
| `INSIGHTS_MODEL_MODE` | `src/runtime/insights/generator.py` | `template` | tooling (provider) |
| `ORPHAN_POSITION_POLICY` | `src/runtime/order_monitor.py` | `adopt` | Tier-2/3 (reconciliation) |
| `REGIME_ROUTER_DISABLED` | `src/runtime/intents.py` | baseline-on | Tier-3 (kill-switch) |

`*_MODE` flags are the sanctioned pattern for an opt-in *apply* path (they pass
the `env-gate-guard` CI check, unlike a default-off `*_ENABLED`) — e.g.
`NEWS_INFLUENCE_MODE`, `CONVICTION_SIZING_MODE`.

## Verification

- All call sites read directly (grep `os.environ`/`getenv`/`settings.get` across
  `src/`); defaults read from the actual `.get(name, DEFAULT)` + truthy parse.
- `NEWS_VETO_ENABLED` live-armed state confirmed by diag probe #4937
  (`news_decisions.jsonl` present + fresh, real fetches).
- No code/config changed by this inventory; the paired PR fixes only the two
  stale inline comments + adds a `NEWS_VETO_ENABLED` row to the CLAUDE.md env
  table (operator-relevant: it live-gates real money).
