# Sprint M1.S1 — Infrastructure Audit & Stabilization (Pre-Kickoff Hardening)

**Classification**: `auto-claude`
**Tier**: Investigation is Tier 1. Fixes touching `src/runtime/orders.py` or `src/runtime/pipeline.py` are Tier 2 (ping with merge/hold). Strategy logic changes are Tier 3.
**Depends on**: M0.S0 (complete)
**Unlocks**: M2 (cannot build comms infra on top of an unstable bot)

---

## Why This Sprint Exists

The live system has three known issues that surfaced during planning. Building new infrastructure (comms, risk caps, web app) on top of an unstable foundation will compound the problems. This sprint stabilizes the foundation **first**:

1. **Mode confusion**: Errors keep referencing dry-run mode even though the bot is supposed to default to live. The system must default to live, AND ping Ben if it's ever started in dry-run.
2. **VWAP execution gap**: VWAP strategy generates trade signals but the Bybit account isn't placing orders. This is a P0 production bug.
3. **Architectural drift**: Modules have hidden coupling and inconsistent error reporting, making issues like #1 and #2 hard to diagnose.

This sprint is **investigation-heavy** by design. We don't want to start "fixing" until we know what's actually broken.

---

## Session Start Checklist

1. Read `CLAUDE.md` in full.
2. Read `docs/sprint-roadmap.md` — confirm structure and current pointer.
3. Read `comms/sprint_state.json` — confirm `current_sprint: M1.S1`.
4. Run `pytest tests/ -q` — record baseline pass/fail count.
5. Confirm git is clean and on the working branch.

---

## Checkpoints

### C1 — Live/Dry-Run Config Audit

**Goal**: One canonical mode flag, default to live, loud alert if dry-run is on.

**Tasks:**
1. Grep the codebase for every usage of `dry_run`, `DRY_RUN`, `live`, `LIVE`, `paper`, `simulation`, etc.
2. Document every flag in `docs/mode-flags-audit.md` — file, variable name, default value, who reads it.
3. Pick the canonical flag (likely one in `config.py` or env var). Mark all others for deletion.
4. Confirm canonical flag defaults to **live** (not dry-run).
5. Add a startup validation in `src/runtime/validation.py`:
   - If live trading is disabled at startup, write `comms/pending_input.json` with `type: "mode_alert"` and a loud warning message
   - Also log at WARNING level
6. **Tier**: Validation in `validation.py` is Tier 1. If you must touch `orders.py` or `pipeline.py` to remove a duplicate flag, that's Tier 2 — ping before merging.

**Acceptance:**
- `docs/mode-flags-audit.md` lists every mode reference
- Only one canonical flag remains (or a documented migration plan if removal is risky)
- Bot started with live trading disabled produces a `pending_input.json` and a WARNING log line
- Bot started in normal (live) mode produces no warning
- Test in `tests/test_mode_validation.py` covering both cases

---

### C2 — VWAP Order Execution Debug

**Goal**: Find why VWAP signals don't translate to Bybit orders, fix it, and prove it works.

**Tasks:**
1. Trace the VWAP code path end-to-end:
   - Where is the VWAP strategy registered?
   - Where does it emit signals?
   - Does the pipeline pick up VWAP signals the same way as `turtle_soup_mtf_v1` and `breakout_confirmation`?
   - Does the order package construct correctly for VWAP?
   - Does `orders.py` submit it, or is it filtered out?
2. Check production logs (or `runtime_logs/`) for VWAP signal events and any matching order attempts.
3. Document root cause in `docs/vwap-debug-findings.md` with:
   - Exact code path traced
   - Where the disconnect happens
   - Proposed fix
4. Apply the fix.
   - **If fix is in `strategies/`**: Tier 3 → ping Ben, do not merge until approved.
   - **If fix is in `src/runtime/orders.py` or `pipeline.py`**: Tier 2 → ping Ben with merge/hold buttons, attach test evidence.
   - **If fix is elsewhere (registration, config, plumbing)**: Tier 1 → self-merge.
5. Add a regression test that simulates a VWAP signal and asserts an order submission attempt is logged.

**Acceptance:**
- `docs/vwap-debug-findings.md` exists with full root-cause analysis
- Fix applied via the correct tier path
- Regression test passes
- A staging signal trace shows: VWAP signal → order package → submission attempt → response (success or expected failure with reason)

---

### C3 — Modular Independence Audit

**Goal**: Inventory architectural issues. **No fixes in this sprint** — fixes go into M3 (Janitor).

**Tasks:**
1. Build an import graph for: `src/runtime/`, `strategies/`, `src/core/`, `src/exchange/`, `src/bot/`.
2. Identify:
   - Circular imports
   - Modules that import from siblings they shouldn't (e.g., a strategy importing from `src/bot/`)
   - Hidden global state
   - Modules that can't be unit-tested in isolation
3. Document in `docs/architecture-audit.md` with one row per issue:
   - File / module
   - Issue
   - Why it matters
   - Proposed remedy
   - Estimated risk of remedy (low/med/high)
4. Tag each issue as: `m3-janitor` (cleanup), `m1-blocker` (must fix now), or `defer` (low value).

**Acceptance:**
- `docs/architecture-audit.md` exists with full inventory
- Any `m1-blocker` items are escalated to Ben via ping before continuing

---

### C4 — Transparency Layer

**Goal**: Every meaningful event in the runtime emits a structured log line. If the system does something, we can see why.

**Tasks:**
1. Audit current logging coverage in:
   - `src/runtime/pipeline.py`
   - `src/runtime/orders.py`
   - `src/runtime/notify.py`
   - `src/runtime/signal_writer.py`
   - `strategies/*.py`
2. List every event that should produce a structured log line:
   - Signal generation (strategy, symbol, side, price, reason)
   - Risk-cap check (cap, current value, allowed/refused)
   - Order construction (full payload)
   - Order submission attempt (request)
   - Order submission response (response, success/error)
   - Mode switch (any toggle of live/dry-run)
3. Add missing log lines:
   - Tier 1 in `notify.py`, `signal_writer.py`, `validation.py`, `bot/`
   - Tier 2 in `orders.py`, `pipeline.py`
4. Add `/diagnose` Telegram command **stub** (full implementation in M2.S1) — for now it just prints the last 20 lines from the structured log file.

**Acceptance:**
- Every event in the list above produces a parseable structured log line (JSON or key=value)
- `/diagnose` stub returns log content
- Test: dry-run cycle produces a complete trace from signal → decision → order attempt → result

---

### C5 — Stability Smoke Test

**Goal**: Prove the fixes hold under sustained run.

**Tasks:**
1. Restart bot in live mode after C1–C4 are merged.
2. Monitor for 4 hours.
3. Verify:
   - No unexpected mode switches
   - Every signal produces an order attempt log (or a documented refusal reason like risk cap)
   - No silent exceptions
   - No mode-related warnings
4. Write `docs/m1-stabilization-report.md` summarizing:
   - Signals generated (by strategy)
   - Orders attempted
   - Orders filled/rejected
   - Errors observed
   - Sign-off: green/yellow/red

**Acceptance:**
- 4-hour clean run
- Stabilization report green or yellow with documented yellow items
- If red: write a `pending_input.json` with `type: "bug_report"` and stop until Ben responds

---

## End of Sprint Checklist

- [ ] All 5 checkpoints green
- [ ] Tests passing (no regressions)
- [ ] All Tier 2 fixes merged with Ben's approval
- [ ] All Tier 3 items either approved or deferred to M5.S2
- [ ] `comms/sprint_state.json` updated: `current_sprint: M2.S1`, `M1.S1.status: complete`
- [ ] `docs/sprint-roadmap.md` M1 marked complete
- [ ] `docs/SPRINT_M2_S1_PROMPT.md` created (port the relevant section from sprint-roadmap.md M2.S1)
- [ ] Sprint completion ping sent (manual for now — bot doesn't have ping handler until M2.S1 lands)

---

## Notes for Claude

- **Don't speculate; trace.** For VWAP, follow real code and real logs, not what "should" happen.
- **One PR per checkpoint.** Bundling makes review harder and bugs sneakier.
- **If you find a P0 (live trading at risk) during investigation**: stop, write `comms/pending_input.json` with `type: "bug_report"`, and wait.
- **If a fix would require a strategy change**: that's Tier 3 — escalate, do not merge.
