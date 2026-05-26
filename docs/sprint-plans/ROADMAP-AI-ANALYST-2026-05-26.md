# Sprint Roadmap: AI Analyst (M13)

**Created:** 2026-05-26
**Initiative ID:** AI-ANALYST
**ROADMAP.md milestone:** M13
**Status tracking:** see the M13 row in `ROADMAP.md` and the active sprint
log under `docs/sprint-logs/S-M13-*`.

---

## 1. Mission

Add a server-side **AI analyst** to the ICT trading bot that emits
natural-language insights + structured grades over the bot's live
trading data. The analyst is **NOT a trained ML model** — it is an LLM
agent backed by the Anthropic Claude API, surfaced as a new FastAPI
router under `:8001`, consumed by the Streamlit dashboard and the M12
Android companion app (both already read-only consumers of the same
API).

The analyst answers four standing questions, refreshed on a cron:

1. **What's the overall system doing right now?** — status pill
   rationale, recent P&L direction, anything anomalous in the last 24h.
2. **How are the last N closed trades?** — narrative + grades over the
   recent closed-trade tape.
3. **How is strategy `<name>` doing?** — per-strategy session view:
   what's working, what's not.
4. **What does the latest health snapshot say?** — narrative over the
   most recent `artifacts/health/latest.json`.

---

## 2. Non-negotiable constraints (all sprints)

These mirror the brief operator session-locked on 2026-05-26.

- **Tier-1 read-only.** Nothing in `src/runtime/orders.py`,
  `risk_counters.py`, `coordinator.py`, `strategies/`, or
  `config/*.yaml` is touched. The analyst cannot influence the order
  path — it reads after the fact and writes prose.
- **NOT a registry artifact.** This is prose generation, not a
  classifier. No entry in `ml/configs/`. No `model_registry` row. No
  `shadow → advisory → live_approved` ladder. The trainer-VM lifecycle
  does not own this work.
- **Server-side only.** `ANTHROPIC_API_KEY` (already on the live VM in
  `.env`, shared with `ict-claude-bridge.service`) never leaves the
  bot. No client-side calls from the dashboard or the Android app.
- **Cached + scheduled.** Every endpoint serves a file-backed cache
  written by a background `ict-insights-generator` systemd timer
  (every 10 min). On-demand HTTP calls **only read the cache** — they
  never call the Anthropic API synchronously. Caps daily cost to a
  known ceiling and keeps tap latency under 100 ms for the phone +
  dashboard.
- **Prompt-caching enabled.** Each prompt is split into a static
  system block (`cache_control: ephemeral`) plus a per-call instruction
  + data block. The static portion is reused across calls so cached
  input tokens dominate.
- **No new ML deps.** Use `httpx` (already a dep) and the `anthropic`
  SDK (already a dep at `>=0.40.0`). No LangChain, LlamaIndex, or
  autogen.
- **Grounded.** Every claim in the prose must cite a `trade_id` /
  `order_package_id` / `signal_id` / time window. Hallucinated trades
  is the failure mode to avoid. The response envelope embeds the raw
  row counts pulled so the dashboard can sanity-check.
- **Killable.** `INSIGHTS_ENABLED=0` short-circuits the generator
  (writes nothing). The router still serves the last-good cache.
- **No third gate.** The two declared gates (`accounts.yaml::mode`,
  `strategies.yaml::execution`) decide trading. The analyst's
  feature-flag governs only the **analyst itself**, never anything on
  the order path — so the prime-directive rule about hidden third
  gates does not apply.

---

## 3. Current-state assessment (2026-05-26)

### What's already there to lean on

| Asset | Path | What we reuse |
|---|---|---|
| FastAPI app | `src/web/api/main.py` + `routers/` | Mount the new router exactly like the existing 21 routers. |
| Trade journal | `trade_journal.db::{trades, order_packages, signals}` resolved via `src.utils.paths.trade_journal_db_path()` | Read-only joins for the `recent` + `strategy/{name}` + `summary` endpoints. |
| Signal audit | `runtime_logs/signal_audit.jsonl` | Recent-tick / `*_eval` events for the `summary` endpoint's anomaly note. |
| Health snapshots | `artifacts/health/latest.json` + `health_check_<TS>.json` | Primary input for the `health` endpoint. |
| Strategy-decision scores | `comms/claude_strategy_scores.jsonl` | READ-only feedstock for the `recent` + `strategy/{name}` narrative. **The analyst never writes here** — that's the operator-invoked `/health-review` skill's job. |
| Anthropic SDK + key | `requirements.txt::anthropic>=0.40.0` + live-VM `.env::ANTHROPIC_API_KEY` | No new install / no new secret. |
| Systemd unit pattern | `deploy/ict-liveness-watchdog.{service,timer}` | Model the new `ict-insights-generator.{service,timer}` on it. |
| Diag-style read router pattern | `src/web/api/routers/health_snapshots.py` | Same shape — read a file, wrap in an envelope, return. |

### What does NOT exist yet (the work)

- `src/runtime/insights/` package.
- `src/web/api/routers/insights.py`.
- `deploy/ict-insights-generator.{service,timer}`.
- `docs/runbooks/insights.md`.
- The cache directory `runtime_logs/insights/`.
- Sprint log + sprint plan (this doc + the S1 log).

---

## 4. Architecture

```
                                                  ┌────────────────────────┐
trade_journal.db ─┐                               │ Streamlit dashboard    │
runtime_logs/   ─┼─►  ict-insights-generator      │ + Android companion    │
artifacts/      ─┘   (systemd timer, every 10m)   └──────────┬─────────────┘
                          │                                   │
                          │   Anthropic Claude API            │  GET (cached)
                          ▼   (Haiku 4.5 / Sonnet 4.6)        │
                  runtime_logs/insights/                      │
                    summary.json  ────────────────────────────┤
                    recent.json   ────────────────────────────┤
                    strategy_<name>.json  ────────────────────┤
                    health.json   ────────────────────────────┘
                          ▲
                          │
                          └── FastAPI router /api/bot/insights/*
                              (read-only, cache-only path —
                              no synchronous API calls)
```

**Two-process split is load-bearing:** the generator owns Anthropic
calls; the router never imports `anthropic`. Verified by a test that
asserts `anthropic` is absent from `sys.modules` after the router is
imported.

---

## 5. Endpoint contract

All four endpoints serve the same envelope:

```json
{
  "summary_md": "<markdown text>",
  "grade": "good | mixed | concerning",
  "signals": [
    {"kind": "...", "severity": "low | med | high", "note": "..."}
  ],
  "data_window": {"start": "<iso>", "end": "<iso>"},
  "row_counts": {"trades": 0, "order_packages": 0, "signals": 0, "audit_events": 0},
  "generated_at": "<iso>",
  "cache_age_seconds": 0,
  "model_id": "claude-haiku-4-5-20251001"
}
```

| Path | What it answers | Data window | Model |
|---|---|---|---|
| `GET /api/bot/insights/summary` | Overall system status — last 24h | rolling 24h | Haiku 4.5 |
| `GET /api/bot/insights/recent?limit=N` | Narrative over last N closed trades (default 20, max 50) | newest N closed in `trade_journal.db::trades` | Haiku 4.5 |
| `GET /api/bot/insights/strategy/{name}` | Per-strategy session view | rolling 7d for `<name>` | Sonnet 4.6 |
| `GET /api/bot/insights/health` | Narrative over the latest health snapshot | newest `artifacts/health/latest.json` | Sonnet 4.6 |

Model split: Haiku for the high-cadence/low-nuance endpoints,
Sonnet for the deeper grading endpoints. Per-endpoint override via
`INSIGHTS_MODEL_<ENDPOINT>` env vars (documented in the runbook).

**Cache miss path.** If a cache file is missing (fresh deploy, or
generator failed first run), the router still returns 200 with
`summary_md: "<not yet generated>"`, `grade: "good"`, `signals: []`,
and `cache_age_seconds: null`. The dashboard renders a neutral
placeholder rather than erroring.

---

## 6. Cost model

10-minute cadence × 4 endpoints × 24 hours = **576 generator calls/day**.

Per call (rough):
- Static system prompt (cached after first call): ~5–8k tokens.
- Per-call data block: ~2k tokens uncached.
- Output: ~600 tokens.

At Haiku 4.5 list pricing (~$1/MTok input, ~$5/MTok output; cached
input ~10% of base): a typical day stays comfortably under **$1/day**
even with the Sonnet endpoints. The runbook tracks the running cost
estimate against the `system fingerprint` field in Anthropic responses.

`INSIGHTS_ENABLED=0` is the kill switch. The router keeps serving the
last-good cache after the generator stops.

---

## 7. Sprint breakdown

### S1 — Scaffold (this sprint, 2026-05-26)

**Deliverable:** real cached insights flowing from the four endpoints
to a curl on the live VM, generated by a real systemd timer.

Five PRs, in order:

1. **PR A — Sprint plan + ROADMAP cross-link** (Tier-1, docs only).
   This file + a one-line ROADMAP update pointing at it.
2. **PR B — Router skeleton with cache-or-placeholder behaviour**
   (Tier-1). `src/web/api/routers/insights.py` + mount in `main.py` +
   `tests/test_insights_router.py`. The router never imports
   `anthropic`; tests assert that.
3. **PR C — Generator + cache writer** (Tier-1).
   `src/runtime/insights/{cache,data_sources,prompts,generator}.py` +
   `tests/test_insights_generator.py`. Mocks the Anthropic client.
4. **PR D — Systemd unit + activation** (Tier-2 — needs one operator
   ack for the timer enable).
   `deploy/ict-insights-generator.{service,timer}` +
   `scripts/install_systemd_units.sh` update +
   `docs/runbooks/insights.md`. Activation runs through the
   `system-actions` workflow.
5. **PR E — S1 sprint log** (Tier-1, docs only).
   `docs/sprint-logs/S-M13-S1-INSIGHTS-SCAFFOLD-2026-05-26.md` per the
   canonical sprint-log template — verified reality only.

### S2 — Dashboard wiring (next, not started)

Streamlit panels that render `summary_md` + `signals[]` from the four
endpoints. Out of scope for S1.

### S3 — Android wiring (later, not started)

Reuse the M12 companion app's existing HTTP layer to consume the same
endpoints. Out of scope for S1.

### S4 — Event-driven nudges (later, not started)

A second, lower-cadence track that runs the analyst against a *change
trigger* (new closed loss, account flipped dry) and pushes the result
through FCM. Owned jointly by M12 S4 + M13. Out of scope for S1.

### What is NEVER in scope for M13

- **Per-user chat / Q&A.** That's a different feature (a separate
  endpoint, conversation state, totally different cost model).
- **Statistical anomaly detection.** Keep this LLM-only; pair with
  statistical signals later if needed.
- **Influencing the order path.** Hard rule. The analyst is a
  read-only observer for the lifetime of the milestone.
- **Writes to `comms/claude_strategy_scores.jsonl`.** That file is
  written by the operator-invoked `/health-review` skill; the analyst
  is read-only of it.

---

## 8. Verification gate for S1

S1 closes when **all** of the following hold:

1. `curl -s http://158.178.210.252:8001/api/bot/insights/summary` on
   the live VM returns the envelope with `cache_age_seconds < 700` and
   a non-empty `summary_md` that references at least one real
   `trade_id` or `order_package_id` from `trade_journal.db`.
2. `journalctl -u ict-insights-generator.service -n 20` shows ≥3
   successful generator runs spaced ~10 min apart.
3. The `INSIGHTS_ENABLED=0` kill-switch is verified: setting it in the
   `.env`, restarting the timer, and observing the next generator run
   exit cleanly without writing the cache.
4. The S1 sprint log records the curl output, the journalctl excerpt,
   and the kill-switch test — verified reality, not intent.

---

## 9. Composition with the rest of the system

- **`/health-review` (operator-invoked).** Same vendor, same data
  sources, very different cadence and scope. `/health-review` is the
  human-triggered, comprehensive layer-2 review that **writes**
  `comms/claude_strategy_scores.jsonl`. The M13 analyst is the
  autonomous, scheduled, low-cost cousin that **reads** that file plus
  the trade journal and emits prose. Neither replaces the other.
- **M12 Android.** The phone is a consumer of the same `/api/bot/*`
  surface. S3 wires `insights/*` into the existing dashboard tabs.
- **Dashboard `/api/bot/health/*`.** The `health` endpoint here is the
  narrative companion of those raw-snapshot endpoints, not a
  replacement.
- **Future trainer-loop feedback.** The trainer can read this file
  hierarchy later if the operator decides analyst grades are useful
  feedstock — but the analyst itself never writes to the trainer's
  inputs in M13.
