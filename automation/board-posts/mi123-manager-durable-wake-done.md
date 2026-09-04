## ✅ DONE — MI-123: bound a manager's idle time with a durable wake

**session** `session_01SVUv3HZiqBsriCcc2RqQo7` · **branch** `claude/mi123-manager-durable-wake` · **PR #11014** (Tier-1, `landing: self`, automerge armed)

### DEPLOYED vs OBSERVED vs OWED — three facts, not one

| | state |
|---|---|
| **DEPLOYED** | the repo half: `manager_wake.py` (assess/brief/receipt), `check_wake_liveness.py`, the committed Routine prompt, 28 tests, the reaper filed |
| **OBSERVED** | the **DELIVERY** half, end to end (below) |
| ⚠️ **OWED — needs the operator** | the **SCHEDULING** half. The hourly Routine is **NOT deployed.** |

### OBSERVED — the delivery half, against the stage-(4) bar

`OI-20260902-DECISION-DRAIN-ROUTINE-...` sets the bar as *"idle/disconnected before and running/connected after via `get_session`"*. Measured on `session_016e2k4UmsMGgpbrJ5ctqeFv` (the manager):

| | 2026-09-04T23:09Z | 2026-09-04T23:11:29Z |
|---|---|---|
| `session_status` | `SESSION_STATUS_IDLE` | **`SESSION_STATUS_RUNNING`** |
| `connection_status` | `disconnected` | **`connected`** |

It was poked with `manager_wake.py --brief` verbatim — 901 minutes of measured silence, carrying checklist → recently done → next — through the same `create_trigger` + `fire_trigger` pair the scheduled wake makes. `check_wake_liveness.py` moved `never_ran` → `fresh` on the receipt.

**What that does NOT establish, kept separate:** the poke was fired **by hand from this session**, so it proves delivery and *not* cadence. And **woken is not acted** — whether the manager acts on what was waiting is its next turn to show, not something I can claim.

### ⚠️ THE BLOCKER — the Routine needs the operator, and I could not route around it

MEASURED, both directions:
- `create_trigger` via MCP returns **`mcp_connections: []`** and warns its fired sessions run **without `mcp__*` tools** — and the wake's poke step *is* `create_trigger`/`fire_trigger`. Such a Routine could assess and record and **never wake anyone**.
- Passing `connectors` is refused outright: **"the connectors parameter is not available for this organization."**
- Sub-hourly is also refused: `*/30 * * * *` → *"the minimum interval is 1 hour"*. That is the **measured** basis for the ~30–90 min bound, not an assumption.

I created the hourly Routine, confirmed it would be inert, and **DELETED it** — the operator's own standard from the decision-drain row: nothing may look armed and not be.

**What the operator needs to do:** create it from `claude.ai/code/routines` with the **Claude Code Remote connector** attached, pasting the committed prompt at `docs/claude/work/manager-wake-routine-prompt.md`. This is the same constraint `decision-drain-routine-prompt.md` already records for the drain.

### ⚠️ PR #11014 has the wrong title and an EMPTY body — please paste

My GitHub MCP is **write-scope 403**: `add_issue_comment`, `create_pull_request` and `update_pull_request` all return `403 Resource not accessible by integration`, while `issue_read` on the *same* objects succeeds. Not the transient drop — backoff will not clear it. `claude-pr-automerge` opened the PR from my last commit subject. Neither relay can fix it (`pr-opener` only creates; `board-post` is hardcoded to #6927 by design).

**Intended title:** `feat(ops): bound a manager's idle time with a durable wake (MI-123)`

<details><summary><b>Intended PR body — click to expand, paste verbatim</b></summary>

## What this is

`WO-20260904-MANAGER-IDLE-IS-UNBOUNDED-AND-NOTHING-WAKES-IT`. Tier-1, `landing: self`.

MEASURED 2026-09-04, **population = one manager session (`session_016e2k4UmsMGgpbrJ5ctqeFv`), one gap**: last act ~09:45Z, operator asked at 21:53Z. **Twelve hours** against a standing thirty-minute bar — 24× — and nothing noticed.

**The manager did not die.** It stayed alive and connected and stopped taking turns. Every mechanism here is aimed at DEATH (the lease's 90-minute TTL, time-based takeover), and death is already covered. An alive-and-silent manager is covered by nothing.

- **The lease is not a detector.** It expires correctly and *nothing reads the expiry*. It is a mutual-exclusion token, not an alarm. It sat expired 746 minutes.
- **R7 is not one either.** It grades a heartbeat only when a commit advances one, so a manager writing no commits is graded by nothing at all.

Both are read *by the manager, or by a commit the manager makes* — and a silent manager makes neither. **So the detector has to originate outside the session.** That single structural fact is what picks a Routine.

## What landed

| file | what it is |
|---|---|
| `scripts/ops/manager_wake.py` | `--assess` (four states, never collapsed), `--brief` (the delivered text), `--record` (bounded receipt) |
| `scripts/ops/check_wake_liveness.py` | grades the receipt `fresh`/`stale`/`never_ran`/`unreadable` — a dead detector reads exactly like a healthy one from every other surface |
| `docs/claude/work/manager-wake-routine-prompt.md` | the Routine prompt + settings, committed so it is not retyped from a chat message |
| `tests/test_manager_wake.py` | 28 tests, mostly asserting states are **not** merged |
| `BL-20260904-NOTHING-REAPS-A-DEAD-MANAGER-...` | the reaper **filed, not folded in** |

**It self-rebinds.** The target is derived from the lease `holder` on every fire, never hardcoded — a Routine bound to one `persistent_session_id` is aimed at a dead session the moment a new manager takes over, which is covered-looking and inert.

**It detected the live incident:** `state: silent`, `wake_session: session_016e2k4UmsMGgpbrJ5ctqeFv`, `silent_minutes: 885`, `lease_expired: true`.

## DEPLOYED vs OBSERVED — not collapsed

**OBSERVED (delivery half):** the manager went `IDLE`/`disconnected` → `RUNNING`/`connected` across the poke, via `get_session` — the stage-(4) bar. **NOT observed:** cadence (the poke was fired by hand), and whether the woken manager *acted*.

⚠️ **The hourly Routine is NOT deployed.** `create_trigger` via MCP produces a Routine whose fired sessions have no `mcp__*` tools, so it could never poke; `connectors` is "not available for this organization". One was created, confirmed inert, and **deleted** rather than left looking armed. It must be created from the routines UI with the Claude Code Remote connector.

## Two limitations, stated rather than hidden

1. **The bound is ~30–90 minutes, NOT 30.** MEASURED: `*/30 * * * *` is rejected — *"the minimum interval is 1 hour"*. Against a measured 720-minute gap that is ~8–24×. **It does not meet the stated 30-minute bar and must not be reported as though it does.**
2. **The lease heartbeat is only as fresh as the last push**, so a manager working without pushing looks silent and gets poked. Chosen on an asymmetry: a false wake costs one queued turn in a session already working (and carries the status update it owed anyway); a missed wake cost 720 minutes, a green Tier-2-approved PR sitting ~7h, and three sub-sessions blocked on single acts. The brief says plainly that being woken is not proof of being broken.

## What it is NOT

Not the **reaper** (filed as its own row — a dead manager cannot be woken, and `assess` reports that as `no_manager` and deliberately stops). Not a **GitHub cron**. Not a **minted credential**. Not another **reminder** — that instruction already exists, was read at session start, and produced the twelve-hour gap.

## Guards

`python3 scripts/ci/run_guards.py --base main` → **PASS 65 · FAIL 1 · SKIP 21**.

⚠️ The one failure is **`layer-guard`, which I COULD NOT RUN**: `lint-imports` exited **127** (command not found — `importlinter` absent from this container). I am **not** reporting it green. This diff touches no `src/` file, so the import graph is untouched by construction, but that is an argument, not a run.

Three guards failed on my first run and **all three were mine**, found by reproducing each rather than assuming they were pre-existing. Fixed by filing the row through `backlog_append.py::append_row` (10 insertions, 0 deletions — no reformat), correcting a truncated BL id that resolved to nothing, declaring the wake's real runner with the guard's own sanctioned `# wiring: manual-only`, and giving the row a `resolution_criteria`. **No guard was weakened and no test was loosened.**

</details>

### Also measured, flagged rather than normalised

- `MANAGER-CHECKLIST.json` uses three states — `waiting`, `deferred`, `ready` — that its own `states` map does not declare.
- Of **95 `SESSIONS.json` rows: 67 never observed, and 1 has a `last_observed` that is a bare timestamp string** rather than an object. That row crashes any reader assuming the documented shape (it crashed mine) and is invisible to every status query.
- `WO-20260904-MANAGER-IDLE-IS-UNBOUNDED-AND-NOTHING-WAKES-IT` exists only on `claude/manager-handoff-2026-09-04-f2ru37` — **not on `main`**. The contract for this unit is unmerged.
- The reaper is **filed, not built**: `BL-20260904-NOTHING-REAPS-A-DEAD-MANAGER-OR-CLAIMS-AN-EXPIRED-LEASE`, in the health backlog and as a work object, with a `resolution_criteria` that names what would close it.

Registry row: `pending-20260904T224451Z`. Scope held: manager infrastructure only — no `src/`, `config/`, `deploy/`, or order path.
