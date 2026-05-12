# Sprint Log: Health-Check Noise Cleanup

## Date Range
- Start: 2026-05-12
- End: 2026-05-12 (same-session ship)

## Objective
- **Primary:** Stop the recurring Telegram noise from the health-check pipeline. Every 6h the operator was getting at minimum two Telegram messages (new review request + auto-merge queued) plus a trickle of "REQ expired without an answer" pings — none of them actionable because the Layer-1 LLM was deliberately skipped and no autonomous Layer-2 reviewer existed.
- **Secondary:** Remove the dead code that produced the noise so future sessions can't accidentally re-enable it.

## Tier
- **Tier 2** — touches operator-action / comms infrastructure and workflow files.
- Justification: per `docs/CLAUDE-RULES-CANONICAL.md`, no Tier-3 paths (`config/*.yaml`, `src/runtime/orders.py`, `src/runtime/risk_counters.py`) are touched. Live trade flow is unaffected; the health-snapshot still runs on cron, just without the Layer-1 LLM call + comms request + PR auto-merge + Telegram fanout that produced the noise.

## Starting Context
- **Triggering report (2026-05-12 operator):**
  > "These messages won't stop coming, this must be fixed as well… the health check just needs to run the health snapshot, and then I will manually give that to Claude. That whole thing with LLM shouldn't even be coded anymore."
- **Recent noise sample (representative Telegram payload, 2026-05-12):**
  - `📨 Comms request REQ-20260512-020131-25708699987 — Topic: Health review needed — run 25708699987 (WARNING)` — auto-emitted by the 02:00 UTC cron.
  - All 11 layer-1 `checks` returned `"layer-1 verdict unavailable"` because `--skip-llm` was set by operator decision 2026-05-10.
  - Six trailing expiry pings (`⏰ Comms request REQ-… expired without an answer`) for older unanswered requests.
- **Audit reference:** `docs/audit/2026-05-12-end-to-end-audit.md` § L4 (observability gaps).

## Repo State Checked
- Branch: `claude/fix-trade-pipeline-MG5qb` (post-T2 squash-merge of #1011).
- Workflows reviewed: `health-snapshot-pr.yml`, `health-review-trigger.yml`.
- Scripts reviewed: `scripts/run_health_check.py` (--skip-llm path; LayerOneSkipped error builder), `scripts/write_health_review_request.py` (REQ emitter), `scripts/collect_health_snapshot.sh` + `scripts/run_pipeline_health_test.sh` (kept).
- Comms machinery: `src/bot/comms_handler.py::_deliver` + `_alert_expired` (gated), `src/comms/models.py` (Request topic field).
- Backlog: 8 `comms/requests/REQ-*.json` files dating back to 2026-05-10.

## Files and Systems Inspected
- `scripts/run_health_check.py`, `scripts/write_health_review_request.py`
- `.claude/health_check_prompt.md`
- `.github/workflows/health-snapshot-pr.yml`, `.github/workflows/health-review-trigger.yml`
- `src/bot/comms_handler.py`
- `.claude/skills/health-review/SKILL.md`, `.claude/commands/health-review.md`
- `docs/runbooks/health-check.md`, `docs/runbooks/liveness-watchdog.md`, `docs/claude/INDEX.md`

## Work Completed

### Deletions (dead code + dead schedule + dead artifacts)
- `scripts/run_health_check.py` — Layer-1 Anthropic call + UNKNOWN-stub builder. Unused after operator's 2026-05-10 `--skip-llm` decision; was synthesising warning-shaped reports with no signal.
- `scripts/write_health_review_request.py` — comms-request emitter. Mint side of the noise.
- `.claude/health_check_prompt.md` — Layer-1 severity rubric. Only consumer was the deleted script.
- `.github/workflows/health-review-trigger.yml` — fired on the auto-merging review PR being merged. Nothing left to trigger.
- `.github/workflows/health-snapshot-pr.yml` — replaced with the leaner `health-snapshot.yml`.
- 8 stale `comms/requests/REQ-*.json` files (2026-05-10 through 2026-05-12 backlog). All EXPIRED or about to expire.

### New workflow
- `.github/workflows/health-snapshot.yml` — daily cron at 02:00 UTC (was every 6h). Steps:
  1. SSH to VM
  2. Run `scripts/collect_health_snapshot.sh` → `artifacts/health/health_snapshot.txt`
  3. Run `scripts/run_pipeline_health_test.sh` → `artifacts/health/pipeline_test.json` (continue-on-error)
  4. `actions/upload-artifact@v4` → operator downloads from the Actions UI
  - Issue-driven path retained (label: `health-snapshot-trigger`) for sandbox-session-driven invocations.

### Comms-handler gate (belt-and-braces for any in-flight backlog)
- `src/bot/comms_handler.py::_deliver` — when `topic.lower().startswith("health review")`, mark the request `sent` without firing Telegram + log an `request_sent` event tagged `skipped_telegram: True`. The poll loop stops re-trying it; it transitions normally to EXPIRED.
- `src/bot/comms_handler.py::_alert_expired` — same gate. Silent EXPIRED transition; no Telegram. The transition log entry + `request_expired` event remain auditable.
- `src/bot/comms_handler.py::_is_health_review_topic` — new module-level helper, single-line conservative match.

### Docs updated
- `docs/runbooks/health-check.md` — full rewrite. New flow (operator downloads artifact + pastes into Claude). "What changed" section enumerates the migration. "Re-enabling Layer-1 (if ever needed)" guidance for future operators: don't replicate the deleted pattern.
- `.claude/skills/health-review/SKILL.md` — rewrote input-acquisition + argument-handling sections. The legacy "read `artifacts/health/latest.json` from `main` HEAD" and "review every pending REQ" modes are removed; skill now expects operator to paste the snapshot in chat.
- `.claude/commands/health-review.md` — full rewrite. Brief command page that delegates to the skill, with explicit "how to fetch the snapshot" instructions.
- `docs/runbooks/liveness-watchdog.md` — comparison table updated (workflow name, cadence, code references).
- `docs/claude/INDEX.md` — entry for health-check runbook rewritten.

### Tests
- `tests/test_health_review_topic_gate.py` — 6 cases pinning `_is_health_review_topic` matches the legacy emitter's literal topic prefix + tolerates capitalisation, and doesn't false-fire on unrelated topics (M5 backtest, operator-action, etc.). 6/6 pass locally.

## Validation Performed
- Local pytest: `python -m pytest tests/test_health_review_topic_gate.py -v` → 6/6 green.
- Workflow YAML parsing: `python -c "import yaml; yaml.safe_load(open('.github/workflows/health-snapshot.yml'))"` clean.
- Manual review: verified no remaining production code references `run_health_check.py`, `write_health_review_request.py`, or `health_check_prompt.md`. Only the diagnostic doc updates mention them.
- The comms-handler gate is a no-op for non-health topics (M5 backtest comms, operator-action confirmations, …) — verified by reading the call sites and confirming the early-return is conditional on topic.

## Documentation Updated
- New runbook `docs/runbooks/health-check.md` (rewritten)
- Updated `docs/runbooks/liveness-watchdog.md`
- Updated `docs/claude/INDEX.md`
- New sprint log (this file)
- New tests with their own docstring explaining the gate

## Tier-3 paths NOT touched
- `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml` — unchanged.
- `src/runtime/orders.py`, `src/runtime/risk_counters.py` — unchanged.
- Live trader unit + liveness watchdog — unchanged. The watchdog (~5min latency to operator) is and remains the urgent-alert channel for actual trader stalls; the health-snapshot is the slow review channel for sanity-checking trade decisions.

## Known follow-ups (queued, NOT in this PR)
- If/when the operator wants automated grading back, see "Re-enabling Layer-1" in `docs/runbooks/health-check.md`. The right pattern is a workflow-failing deterministic check (no separate comms-request channel), not a regenerated LLM-call + comms artifact flow.

## Why this kills the recurring noise

The deleted machinery had three failure modes feeding the same Telegram chat every 6h:

1. **Layer-1 LLM-skipped path always returned WARNING.** The `_build_stub("LayerOneSkipped")` fallback was the only code path executed (per the 2026-05-10 `--skip-llm` decision). Status was always WARNING, summary was always "Layer-1 analysis unavailable", all 11 checks were always `"layer-1 verdict unavailable"`. No signal.
2. **Every run emitted a comms-request.** The "review is mandatory" design rule meant a REQ was created regardless of layer-1 status. The operator was pinged every 6h asking for a review they had already opted out of.
3. **Every unanswered REQ pinged again on expiry.** The `_alert_expired` Telegram + the comms-poller's expiry sweep produced N extra pings per unanswered REQ (where N = number of expirations during the sweep cycle).

This cleanup eliminates all three at the source (no more LLM call, no more REQ emission, no more PR / Telegram fanout) and gates the existing backlog at the comms-handler so any in-flight REQ drains silently. The audit trail (transition log + request_expired events) is preserved.
