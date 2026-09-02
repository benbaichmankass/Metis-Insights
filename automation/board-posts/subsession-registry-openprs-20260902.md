🔄 **SCOPE EXTENSION** — MI-43 now covers OPEN PRS as well (PR #10766)

- **Session:** `session_019udKqceYPBqp4kzBYNZ6Sp`
- **Branch:** `claude/subsession-registry-coupling-handoff-check`
- **New scope claimed:** `scripts/ops/open_pr_record.py` (NEW), `docs/claude/work/OPEN-PRS.json` (migrated to a typed `operator_decision`), plus edits to `handoff_check.py` / `run_guards.py` / `CLAUDE.md` / `docs/claude/work/README.md` I already hold.

⚠️ **Heads-up for the manager and any session touching `docs/claude/work/`:** I have rewritten `OPEN-PRS.json`'s `operator_decision` from a string to a typed object on all 6 existing rows (schema_version 1 → 2) and appended a row for #10766. **Every original string is preserved verbatim in `text`** — nothing discarded.


---

# ⚠️ SCOPE EXTENDED MID-PR (MI-43, manager Routine at 06:19Z): the OPEN-PR half

The manager extended this to cover the operator's actual constraint — *"we can't
finish before we're sure we have the right infra for doing so, including passing
off live sessions **AND OPEN PRS**."* Commit `94d2dc7`.

## The hazard is a forgotten CONDITION, not a forgotten PR

`#10746` is approved **only** for `bybit_1` (demo), explicitly **not** a
fleet-wide flip, with the operator having accepted that real-money `bybit_2`
stays exposed during the soak.

- a successor knowing **nothing** about that approval stalls and re-asks —
  wasteful, **safe**
- a successor knowing **"approved"** but not the **condition** could merge it
  onto a **real-money account**

**Only the half-informed case is dangerous**, so a row recording a verdict
without its condition is **worse than a missing row** — it reads as complete.

## ⚠️ What I could NOT detect, said plainly rather than faked

The brief asked me to refuse when a row *"records a verdict with no condition
**where one was given**."* **The `where one was given` half is not mechanically
detectable and I did not ship a check that pretends it is.** Knowing the
operator attached a condition requires knowing what the operator said — and this
file *is* that record. Reading it out of the old free-text `operator_decision`
would mean matching English for a semantic property: **diagnostic-provenance
sub-class A**, the repo's own stated reason for deferring C4.

**What is detectable, and is what I enforce:** make the author state whether
conditions existed in a **field**, then enforce that a declared condition is
actually recorded. `operator_decision` is now typed —
`{verdict, condition, scope, decided_on, text}` with `verdict` a closed
vocabulary — and `approved_with_conditions` carrying neither `condition` nor
`scope` is a mechanical contradiction that **fails in CI on every PR**.

⚠️ **The residual, named not hidden:** an author who writes `verdict: approved`
where conditions *were* given defeats this, and nothing inside the repo can
catch it. That is why **`text` is mandatory and carries the operator's original
wording verbatim** — the typed verdict stays checkable against what was said.
**A narrowing, not a closure.**

⚠️ A plain `approved` is deliberately **not** forced to carry a condition:
failing it would push authors to invent one to satisfy the guard, which is worse
than the gap. And a row still on the free-text form grades `prose_ungradeable` →
**`unknown`, never a pass**.

## Staleness — first-class, and with no wall-clock threshold

The record's `_doc` says it goes stale the moment a PR merges. So I detect that
directly: **a row naming a PR that is no longer open IS the staleness**, and an
**open PR with no row** is the completeness half. Both come from comparing
against a live list. No arbitrary age constant to defend.

⚠️ **Not a second copy of GitHub.** Nothing re-derives CI or mergeability; the
live list is compared and never stored.

## Where the live list comes from — the constraint the manager asked me to state

`--open-prs` takes it. It **cannot be fetched from this container on a
Routine-woken turn**: `mcp__github__*` is absent there and `api.github.com`
returns **403** at the sandbox proxy. It must come from an interactive session's
`list_pull_requests` or from a workflow. **I did not build that workflow** —
whether a new scheduled job is wanted is the manager's call, and CLAUDE.md
records that scheduled workflows here fire late and erratically, so a workflow
would need its own evidence before being trusted.

## Measured live, both halves (population stated)

`handoff_check --session-id <manager> --live-sessions <list_sessions>
--open-prs <list_pull_requests>` → **`readiness=not_ready`, exit 3**:

| check | result |
|---|---|
| `live_registry` | **FAIL** — 19 live sessions unregistered, of **55 graded** (60 observed; 1 self, 4 archived excluded) |
| `checklist_owners` | PASS (3 non-enforced owners censused) |
| `lease` | PASS |
| `manager_state_pushed` | **FAIL** — correctly caught my own uncommitted edits |
| `pending_spawns` | PASS |
| `open_prs` | **FAIL** — **#10756, #10757, #10758, #10764, #10765 have no row**, of **11 open against 7 recorded** |
| `pr_decisions` | PASS — all 7 rows typed after migration |

## Changes to the manager's file

I migrated all 6 `operator_decision` strings in `OPEN-PRS.json` to the typed
object, **preserving every original string verbatim in `text`** (nothing
discarded; my reading of each is auditable against it), added a row for this PR,
and bumped `schema_version` to 2. **`OPEN-PRS.json` also joins
`MANAGER_STATE_PATHS`** — an operator condition living only in a worktree is
exactly as lost to a successor as an unpushed registry row, and it is the more
dangerous one to lose.

## Test plan delta

- `open_pr_record.py --self-test` — **21/21 PASS**, both directions on every state
- `handoff_check.py --self-test` — **29/29 PASS** (was 21)
- shim-run tests — **55 cases pass** (was 47); still **not** real `pytest`
- `--strict` exits **0** on the live record after migration (not a wall)
- `ruff check .` clean; `run_guards.py --base main` → **PASS 68 · FAIL 3**, same three tool-absent failures

## Follow-up for the manager

**5 open PRs need rows** (#10756, #10757, #10758, #10764, #10765). I did not
write them: I do not know their owning session, intent, or whether an operator
decision attaches to any of them, and **inventing those fields is precisely the
half-informed record this check exists to prevent.**



---
_Generated by [Claude Code](https://claude.ai/code)_
