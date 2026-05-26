# AI Analyst (insights) — operator runbook

**Scope:** the M13 S1 server-side AI analyst on the live VM. Reads
trade state, emits prose summaries + grades, served from
`/api/bot/insights/*`. This runbook covers the operator-visible
controls — start/stop, cost budget, troubleshooting, where everything
lives.

**Not in this runbook:** the architectural rationale (that's
[`docs/sprint-plans/ROADMAP-AI-ANALYST-2026-05-26.md`](../sprint-plans/ROADMAP-AI-ANALYST-2026-05-26.md))
and the broader project context (that's `CLAUDE.md`).

---

## What runs where

| Component | Path / unit | Role |
|---|---|---|
| Generator timer | `ict-insights-generator.timer` | Fires every 10 min (after a 2 min boot delay) |
| Generator service | `ict-insights-generator.service` | Oneshot — runs the wrapper, exits |
| Cycle wrapper | `scripts/ops/run_insights_cycle.sh` | Drives the CLI through every endpoint |
| Generator CLI | `python -m src.runtime.insights generate …` | One endpoint per invocation |
| Cache files | `runtime_logs/insights/<endpoint>.json` | What the router serves |
| History table | `trade_journal.db::insights_history` | Durable record of every run |
| Usage table | `trade_journal.db::insights_usage` | Per-call tokens + estimated cost |
| Router | `src/web/api/routers/insights.py` | `/api/bot/insights/*` (read-only) |

---

## Toggles + env vars

All controlled via `/home/ubuntu/ict-trading-bot/.env` on the live VM.

| Env var | Default | Effect |
|---|---|---|
| `INSIGHTS_ENABLED` | `1` | Set to `0` (or `false` / `no`) to short-circuit the next timer fire. The router keeps serving the last-good cache; no tokens spent. |
| `INSIGHTS_MODEL_MODE` | `template` | **Default since M13 S2.** `template` runs the rule-based analyst (no API call, $0 cost, deterministic, zero hallucination risk). Set to `anthropic` to use the Claude API (requires `ANTHROPIC_API_KEY` + credit balance). The dashboard surface, cache files, `insights_history`, and `insights_usage` rows are identical between modes — `template` writes rows with `model_id="template:v1"` and `estimated_cost_usd=0`. |
| `INSIGHTS_MONTHLY_BUDGET_USD` | `5.00` | Calendar-month budget cap. Only enforced in `anthropic` mode — template mode bypasses the gate entirely. Once `SUM(estimated_cost_usd)` for the current month hits this, the generator skips calls and records `budget_skipped` usage rows. Bump it if you've raised your Anthropic monthly included usage; lower it to tighten. |
| `INSIGHTS_MODEL_SUMMARY` | `claude-haiku-4-5-20251001` | (Anthropic mode only) Override the model for the `summary` endpoint. |
| `INSIGHTS_MODEL_RECENT` | `claude-haiku-4-5-20251001` | Same for `recent`. |
| `INSIGHTS_MODEL_STRATEGY` | `claude-sonnet-4-6` | Same for `strategy/{name}`. |
| `INSIGHTS_MODEL_HEALTH` | `claude-sonnet-4-6` | Same for `health`. |
| `ANTHROPIC_API_KEY` | (already set on the VM) | Required only when `INSIGHTS_MODEL_MODE=anthropic`. Reuses the same key as `ict-claude-bridge.service`. |

After editing `.env`, the next timer fire picks up the new values
automatically — no service restart needed. (The systemd unit reads
`.env` via `EnvironmentFile=` on each invocation because the service is
`Type=oneshot`.)

---

## Activate / deactivate

The unit files install via the regular `pull-and-deploy` system-action
once this PR merges (`scripts/install_systemd_units.sh` is wired into
the deploy flow). After install, **enable + disable run through
allowlisted system-actions** — Claude dispatches them autonomously
with operator ack, per the Ship-Autonomously Rule. There is no manual
SSH step.

| Need | Action | Dispatch |
|---|---|---|
| Activate the timer (Tier-2) | `enable-insights-generator` | Open a `system-action`-labelled issue with body `action: enable-insights-generator\nreason: <text>`. The workflow runs `scripts/ops/enable_insights_generator.sh`, comments back with the post-state, closes. |
| Stop the timer (Tier-2) | `disable-insights-generator` | Same shape: `action: disable-insights-generator`. Hard disable — `INSIGHTS_ENABLED=0` in `.env` is the *soft* disable (next fire no-op without stopping the timer). |
| Trigger one cycle now (debug) | `systemctl start ict-insights-generator.service` via the on-VM wrapper if logged in | Rare — usually the 10-min cadence is enough. Mostly used to verify the cycle works end-to-end right after `enable-insights-generator`. |

The wrapper records an audit row + returns the post-state (timer
is-enabled / is-active + next-fire timestamp) in the issue comment, so
the issue comment is the verification artefact — there's no separate
"did it work" check.

---

## Where to look

### Latest output (what the dashboard / phone sees)

```bash
ls -la runtime_logs/insights/
cat runtime_logs/insights/summary.json | jq
curl -s http://localhost:8001/api/bot/insights/summary | jq
```

### Generator activity

```bash
# Most recent cycle:
sudo journalctl -u ict-insights-generator.service -n 50 --no-pager
# Last 24h of cycles:
sudo journalctl -u ict-insights-generator.service --since "1 day ago" --no-pager
```

From a PM-side session: open a `[diag-request] journalctl?unit=ict-insights-generator&lines=200` issue.

### History (what the analyst said over time)

```sql
-- trade_journal.db
SELECT generated_at, endpoint, strategy_name, grade,
       substr(summary_md, 1, 120) AS snippet
FROM insights_history
ORDER BY datetime(generated_at) DESC
LIMIT 20;
```

### Cost so far

```sql
-- this calendar month
SELECT SUM(estimated_cost_usd) AS spent_usd,
       SUM(input_tokens + output_tokens) AS tokens,
       COUNT(*) AS calls
FROM insights_usage
WHERE ts >= strftime('%Y-%m-01T00:00:00+00:00', 'now');
```

The dashboard exposes the same numbers via `GET /api/bot/insights/usage`
(landing alongside the dashboard tab in a follow-up PR).

---

## Cost ceiling math

Budget default: **$5.00/month**.

Per-cycle work: 3 globals + 6 strategies = **9 Anthropic calls**, mixed
Haiku/Sonnet. With prompt caching enabled (static system block carries
`cache_control: ephemeral`), the first call of a fresh cache window
pays full input price; subsequent calls hit the cached read tier at
~10% of input.

Worst-case daily envelope (no caching at all, ~8k input + 600 output,
Haiku-only): 9 calls × 144 cycles/day × ($1/MTok × 8000 + $5/MTok ×
600) / 1M ≈ **$0.20/day** ≈ $6/month.

With prompt caching working: typical day is well under **$0.10/day**.

The hard guard is the budget gate — the moment the calendar-month
estimate hits `INSIGHTS_MONTHLY_BUDGET_USD`, no more calls fire until
the next month rolls or you bump the env var.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Cache file missing after 30+ min | Timer not enabled, or service failing | `systemctl status ict-insights-generator.timer` + `journalctl -u ict-insights-generator -n 50` |
| All caches stuck at the same `generated_at` | `INSIGHTS_ENABLED=0` or budget exhausted | Check `.env`; query `insights_usage` for `status='budget_skipped'` rows |
| Router returns `cache_present: false` for everything | Generator has never successfully run | First-time activation — run manually once: `sudo systemctl start ict-insights-generator.service` |
| Generator logs say `anthropic call failed` repeatedly | `INSIGHTS_MODEL_MODE=anthropic` + API key missing / rate-limited / out of credit | Either top up Anthropic credit + check `ANTHROPIC_API_KEY` in `.env`, OR flip to `INSIGHTS_MODEL_MODE=template` (zero-cost rule-based mode — the default since M13 S2) |
| Cache `model_id` says `template:v1` and the operator wanted LLM prose | `INSIGHTS_MODEL_MODE` is the default `template` | Set `INSIGHTS_MODEL_MODE=anthropic` in `.env` (and ensure `ANTHROPIC_API_KEY` is valid + the monthly budget allows it). Next cycle uses the LLM. |
| `summary_md` cites no trade ids | Window had no closed trades | Working as designed — "no closed trades in the window" is the honest answer |
| Cost climbing faster than expected | Prompt caching not hitting | Inspect `cache_creation_tokens` + `cache_read_tokens` in `insights_usage` — if `cache_read` stays at 0, the SDK call isn't using the cache hint |

---

## Disable in a hurry

**Hard disable (stop the timer):** dispatch the `disable-insights-generator`
system-action. Body:

```
action: disable-insights-generator
reason: <text>
```

The action runs `systemctl disable --now ict-insights-generator.timer` —
no future fires, no more tokens spent.

**Soft disable (timer still scheduled but each fire no-ops):** the
`INSIGHTS_ENABLED` toggle in `.env` lets the timer keep ticking but
short-circuits each cycle. Flip it via the `set-env` system-action:

```
action: set-env
env_key: INSIGHTS_ENABLED
env_value: 0
service: ict-insights-generator.service
```

(Note: `set-env` restarts the named service. Since the generator is a
oneshot driven by the timer, a restart here is a no-op — the next fire
just sees the new value.)

Either way, the router endpoints keep returning the most recent cache
files until those files are removed — they don't disappear when the
timer stops.
