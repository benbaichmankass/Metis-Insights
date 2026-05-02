# Recurring Hardening Session Prompt

**Type**: Recurring (bi-daily by default)
**Cap**: 3 hours
**Spec**: `docs/claude/recurring-sessions.md`
**Format**: Phase 1 (E2E) → Phase 2 (Deep dive) → Phase 3 (Summary ping)

This file is loaded at the start of every recurring hardening session. Read CLAUDE.md first as always.

---

## Phase 1 — E2E Health Check (do this first, no exceptions)

### 1A. System mode verification
- Pull the latest hourly report (most recent in `runtime_logs/` or via `/status` Telegram).
- Confirm:
  - `ALLOW_LIVE_TRADING=true` and `DRY_RUN=false` in the running process env (verify via `/proc/<pid>/environ` if VM-resident, or `/status` output).
  - Each enabled account in `config/accounts.yaml` has `mode: live` (or no `mode` field per default-live rule).
  - No `failed_validation` events in last 6 hours.

### 1B. Strategy fill rate verification
For each enabled strategy in `config/strategies.yaml`:
- Count signals fired in last 24h (from hourly reports or `runtime_logs/signal_audit.jsonl`).
- Count orders placed in last 24h.
- Fill rate = orders / signals.
- **Red flag**: fill rate is 0% with > 5 signals fired (this caught the VWAP bug on 2026-05-02).
- **Red flag**: any strategy attribution shows up as `unknown` in the hourly report.

### 1C. Account API verification
For each enabled account, confirm hourly report shows `API OK`. `API ERROR` on a disabled/staging account is acceptable; on an enabled live account it is a hard stop.

### 1D. Comms round-trip verification
- Issue a smoke test via `scripts/comms_ask.py --topic "audit smoke" --slug auditsmk --question ack --type yes_no --prompt "smoke?" --expires-in 10m`.
- Wait up to 8 minutes for an `acknowledged` artifact in `comms/archive/`.
- If no response in 8 min, the comms wiring is broken — this is a Phase 1 failure.

### 1E. Git sync verification
- VM is in sync with `origin/main` (no drift, no uncommitted local changes on VM).
- `git_fetch` health check shows last fetch within expected interval.

### 1F. Phase 1 outcome routing

Based on results:
- **All green**: proceed to Phase 2.
- **Any red**: stop. Open a draft PR titled `BLOCKED: hardening session — <failure>`. Issue a `comms_ask` request to operator with three options:
  - `pivot` — pivot the entire session to fixing this issue
  - `defer` — log to `bug-log.md`, file a focused sprint, skip the deep dive
  - `proceed` — operator accepts risk, continue Phase 2
- Wait for response before any further work.

---

## Phase 2 — Deep Dive Target Selection

### 2A. Special case: First three sessions have predetermined targets

**Session 1 target**: Fix the four bugs visible in the 2026-05-02 hourly report:
- `pipeline_order:multi_account_execute` is calling the wrong execution path. The error message itself names the fix: "use `execute_pkg()` from `src.units.accounts.execute`". Trace `src/runtime/pipeline.py` → find where multi-account execute is invoked → confirm it routes through `execute_pkg()`. If not, fix it.
- VWAP signals firing with 0 fills (downstream of above; verify after fixing #1).
- `ALLOW_LIVE_TRADING=true` not propagating to the live process. Check the systemd unit env loading, EnvironmentFile path, and confirm `Environment=` lines vs `EnvironmentFile=`. Confirm the env var is read at process start, not module import (which doesn't refresh on systemctl restart).
- Comms ping system not firing despite `COMMS_PUSH_ENABLED=1`. Check `CommsPoller` is actually started in `src/bot/comms_handler.py`, that it's polling the right directory, and that the GitPusher has working credentials.

**Session 2 target**: Architecture audit of `src/units/accounts/execute.py` and the `Coordinator` translator pattern (S-008). Verify execute_pkg is the only live entry point and no legacy paths remain.

**Session 3 target**: Mode flag plumbing — full trace of every place `DRY_RUN`, `ALLOW_LIVE_TRADING`, and `mode:` are read; verify single source of truth; verify operator gets pinged if any flag is in unexpected state.

After session 3, switch to the prioritization formula.

### 2B. Prioritization formula (sessions 4+)

```
score = (criticality × 3) + (days_since_last_audit × 2) + (recent_commit_count × 1) + (open_bugs × 4)
```

Build the candidate list from these subsystems:

| Subsystem | Criticality | Watch path |
|-----------|-------------|------------|
| Order placement | 5 | `src/runtime/orders.py`, `src/units/accounts/execute.py` |
| Pipeline | 5 | `src/runtime/pipeline.py` |
| Mode flags | 4 | `src/runtime/trading_mode.py`, env loading |
| Risk engine | 4 | `src/runtime/risk_counters.py`, `src/units/accounts/risk.py` |
| Strategies | 3 | `src/units/strategies/*.py` |
| Coordinator | 4 | `src/core/coordinator.py` |
| Comms | 2 | `src/comms/`, `src/bot/comms_handler.py` |
| Bot | 2 | `src/bot/telegram_query_bot.py` |
| Observability | 2 | `src/runtime/health.py`, `notify.py`, `hourly_report.py` |
| Validation | 4 | `src/runtime/validation.py` |
| Outcomes/PnL | 3 | `src/runtime/outcomes.py` |
| Webapp backend | 2 | `src/webapp/` |
| Configs | 4 | `config/*.yaml` |
| ML training | 2 | `src/training/`, `notebooks/` |
| Backtest | 2 | `src/backtest/`, `bin/backtest_*.py` |

Read `docs/claude/audit-log.md` for `days_since_last_audit`.
Run `git log --since="14 days ago" --name-only --pretty=format:` then count per subsystem for `recent_commit_count`.
Read `docs/claude/bug-log.md` and count unresolved entries per subsystem for `open_bugs`.

Pick the highest-scoring subsystem. Tie → highest criticality wins. Still tied → most recent commits.

### 2C. Deep-dive activities

For the selected subsystem:

1. **Read the code** — every file in the subsystem, end to end. Not grep, not skim. Full read.
2. **Test coverage** — every public function has a test. Note any without one. Add minimal tests where trivial; flag gaps for follow-up.
3. **Import isolation** — `python -c "import <module>"` in a clean shell. No exceptions, no side effects. Flag any module that fails this.
4. **Error paths** — every `raise`, `except`, `return None` path is logged with structured fields (strategy, symbol, side, reason). Flag silent failures.
5. **Config / env defaults** — every `os.environ.get(...)` and `config[...]` call has a documented default in `docs/claude/`. Flag undocumented ones.
6. **Dead code** — unused imports, commented-out blocks > 3 lines, functions with no callers. Flag for cleanup (one PR per cleanup, per cleanup-policy).
7. **Live-mode invariant** — re-verify per CLAUDE.md § Live-mode invariant for any code in this subsystem that touches mode/order routing.

### 2D. Recording findings

Append to `docs/claude/audit-log.md`:

```markdown
## YYYY-MM-DD — <subsystem>

**Score**: criticality=X days_since=Y commits_14d=Z bugs=W → total=N
**Time**: HhMm
**Findings**:
- Finding 1 (severity: low/med/high)
- Finding 2 ...
**Fixes shipped this session**: PR#X (...)
**Issues filed**: BUG-NNN (...) in `docs/claude/bug-log.md`
**Follow-up sprint candidates**: ...
**Operator pings**: count, types
```

### 2E. Fix authority within a hardening session

- **Tier 1** (cleanup, dead code, log line additions, missing tests, doc updates): self-merge as you go. Multiple small PRs preferred.
- **Tier 2** (changes to `orders.py`, `pipeline.py`, `execute.py`): follow CLAUDE.md ping-PR pattern — draft PR + tiny ping-PR + wait.
- **Tier 3** (strategy params, risk caps, model params): never touch in a hardening session. File a sprint candidate, escalate.

---

## Phase 3 — End-of-Session Summary Ping

Use `scripts/comms_ask.py` to fire a structured summary:

```
📊 Hardening Audit — YYYY-MM-DD

Phase 1: <green | yellow with notes | red — pivoted>
Focus: <subsystem>
Findings: <2–4 bullets>
Fixes shipped: <PR list or "none">
Issues filed: <bug-log entries>
Follow-up sprints proposed: <list>
Next session target: <subsystem from prioritization formula>
Time: <total>
Operator pings during session: <count>
```

Then:
1. Append a `CHECKPOINT_LOG.md` entry per `HANDOFF_TEMPLATE.md`.
2. Open the summary PR (docs-only) with the audit-log update.
3. Self-merge.
4. Sprint completion ping fires from the merge.

---

## Operator Cancellation

If the operator at any time issues `/halt` or replies `cancel` to a mid-session `comms_ask`, stop work, commit current state with `[CANCELLED]` prefix, write summary, exit cleanly.

---

## Reference

- Master spec: `docs/claude/recurring-sessions.md`
- Bug log: `docs/claude/bug-log.md`
- Audit log (created in session 1): `docs/claude/audit-log.md`
- Live-mode invariant: `CLAUDE.md`
- Comms: `docs/claude/comms-architecture.md`
