# Claude Code on the Web — Recurring Automations

This file is the **single source of truth** for the recurring-session automations
that run in Claude Code on the Web (claude.ai/code). Use these values verbatim
when creating each automation in the UI.

## How automations connect to the rest of the system

```
Cloud sandbox (Claude Code on the Web)
   ↓ runs Phase 1-3 of the recurring prompt
   ↓ commits to docs/claude/checkpoints/CHECKPOINT_LOG.md (and any artifacts)
   ↓ pushes to origin/main
   ↓
VM git-sync timer (every 5 min)
   ↓ pulls origin/main
   ↓ install_systemd_units.sh (no-op if no unit changes)
   ↓ deploy_pull_restart.sh restarts services
   ↓
Telegram ping fires off the new checkpoint commit
   ↓
Operator reviews in @bict_trading_bot
```

No new API keys or env vars are required for any of these — the GitHub repo
connection is the only outbound channel; the existing VM-side ping wiring
delivers the result to Telegram.

## Limitations

The cloud sandbox only sees what's in git. It does **not** have access to:

- `runtime_logs/` (signal_audit.jsonl, hourly summaries) — VM-local
- `trade_journal.db` — VM-local
- The live trader process or its env

Phase 1's live-data checks therefore run only against whatever snapshots are
committed to git. The git-state checks (recent commits, bug-log, code review,
strategy review against backtest results in `outputs/`) are the substantive
work the cloud automation can do. For full live-data coverage, prefer running
the corresponding Telegram trigger (`/audit /improve_strategy /train_model`)
from a Claude Code session that has the repo + VM access.

---

## Automation 1 — Bi-daily hardening audit

| Field | Value |
|---|---|
| **Name** | `ICT bi-daily hardening audit` |
| **Repository** | `benbaichmankass/ict-trading-bot` |
| **Trigger** | Schedule, cron `0 6 1-31/2 * *` (every other day, 06:00 UTC) |
| **Connectors** | none (GitHub repo is enough) |
| **Permissions** | GitHub repo: read + write |

**Instructions** (paste verbatim):

```
Read CLAUDE.md and docs/sprints/recurring-hardening-prompt.md.

Begin a recurring hardening session. Run Phase 1 (E2E health check) first. If anything fails, follow the outcome routing in the prompt — pivot, defer, or proceed only after operator weighs in. Otherwise:
 - For sessions 1-3, use the predetermined targets in section 2A.
 - For sessions 4+, use the prioritization formula in section 2B.

End with the standard summary ping per Phase 3.
```

---

## Automation 2 — Weekly strategy improvement

| Field | Value |
|---|---|
| **Name** | `ICT weekly strategy improvement review` |
| **Repository** | `benbaichmankass/ict-trading-bot` |
| **Trigger** | Schedule, cron `0 6 * * 1` (Mondays, 06:00 UTC) |
| **Connectors** | none |
| **Permissions** | GitHub repo: read + write |

**Instructions** (paste verbatim):

```
Read CLAUDE.md and docs/sprints/recurring-strategy-improvement-prompt.md.

Begin a recurring strategy improvement session. Run Phase 1 first. CRITICAL: this session NEVER edits parameters. It only proposes changes (Tier 3 — written to docs/strategy-reviews/) that require operator approval before any sprint touches them.

End with the standard summary ping per Phase 3.
```

---

## Automation 3 — Weekly model training review

| Field | Value |
|---|---|
| **Name** | `ICT weekly model training review` |
| **Repository** | `benbaichmankass/ict-trading-bot` |
| **Trigger** | Schedule, cron `0 6 * * 4` (Thursdays, 06:00 UTC) |
| **Connectors** | none |
| **Permissions** | GitHub repo: read + write |

**Instructions** (paste verbatim):

```
Read CLAUDE.md, docs/claude/ml-training-policy.md, and docs/sprints/recurring-model-training-prompt.md.

Begin a recurring model training session. Run Phase 1 first. CRITICAL: this session NEVER promotes a model to live. It trains a candidate, evaluates against the incumbent on holdout, and writes a promote/reject recommendation to docs/model-evals/.

End with the standard summary ping per Phase 3.
```

---

## Why these schedules

- **Every other day** (audit) — matches the hardening cadence in `docs/claude/recurring-sessions.md`. Two days gives Phase 2 deep-dives a real window of new failure data to investigate.
- **Mondays** (strategy improvement) — runs against the prior week's signals & fills, so Sunday's session would be too early.
- **Thursdays** (model training) — gives the operator a 3-day window after Monday's strategy review to decide if any proposals warrant a candidate model before the training session picks them up.

All times are UTC. Adjust if the operator wants local-morning delivery.

## Reference

- Master spec: `docs/claude/recurring-sessions.md`
- Telegram triggers (the manual equivalents): `/audit`, `/improve_strategy`, `/train_model`, `/roadmap` in `@claude_ict_comms_bot`
- Setup screenshot reference: see the operator's "Create Automation" form on claude.ai/code
