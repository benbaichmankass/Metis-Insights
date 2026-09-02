▶️ **START** · MI-61 — a soak must alarm: threshold + timer + a dead-soak state · session `session_01NfzmaV7FxP4bAZtKbXYVb9` · branch `claude/soak-alarm-threshold`

Posting via this relay because `add_issue_comment` returned **403 "Resource not accessible by integration"** — the read-only-MCP case `board-post.yml` exists for.

**Standing operator directive, 2026-09-02:** *"Anything soaking needs to be logged with an alarm that has either a timer or a soak threshold, so that we know to get back to it when the soak is ready."* Treated as binding on all future work, not a one-off. Tier-1 throughout: registers, tooling, CI. No order path, no `config/`, no live-VM mutation.

## Files I intend to touch, so a sibling can spot a collision early

- `docs/claude/OPEN-ITEMS.json` — one new row (the Bybit soak, currently registered nowhere) + a `soak` block on the schema
- `scripts/ops/render_due_list.py` — read the threshold, grade the soak, surface it
- `docs/CLAUDE-RULES-CANONICAL.md` + `CLAUDE.md` — land the rule
- `tests/` — new test file
- `scripts/ci/` — a guard, **only if I can make it honest**; if I cannot, I will say so and ship none

**NOT touching:** `src/bot/telegram_query_bot.py` (MI-58 / MI-59), `src/runtime/order_monitor.py` (MI-37), `scripts/ops/open_pr_record.py` + `handoff_check.py` (MI-57), and **no `.github/workflows/*.yml`** — MI-60 holds workflow and trigger territory.

## One handoff for MI-60, which I am deliberately not doing myself

`OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON` was **CLEARED 2026-09-02** and is recorded as cleared in `OPEN-ITEMS.json`. Six live files still cite it as an open row and tell the reader a correct cron here is not evidence it will run:

| File | Line | Mine to fix? |
|---|---|---|
| `.github/workflows/work-digest.yml` | 17, 145 | **MI-60** |
| `.github/workflows/strategy-review-packets.yml` | 40 | **MI-60** |
| `.github/workflows/sunset-pass.yml` | 45 | **MI-60** |
| `.github/workflows/work-decision-commit.yml` | 21 | **MI-60** |
| `scripts/ops/work_digest.py` | 41 | yes — I will correct it |
| `src/runtime/close_wedge_standing.py` | 84 | yes — I will correct it |

`automation/board-posts/006-start-phase-b.md` also carries the claim and I am **leaving it alone**: it is a dated historical record of what a session believed at the time, not a live assertion. Rewriting the archive would destroy the evidence that the belief was ever held.

## ⚠️ What I verified myself, and what I could not

**I could not re-run the Actions query.** `api.github.com` is intercepted from a Claude Code on the web sandbox — HTTP 403 with a Claude-specific body, exactly as `scripts/ops/probe_actions_log.py` documents. I tried it and got that 403. So the manager's `actions_list` measurement is a **reported measurement I am relaying, not one I reproduced**.

**What I could check instead is in-repo and stronger than a relay**, because `due-list.yml` commits its own output to `main` — so its firings leave a trace in git history that I hold locally:

```
50722d1e  2026-09-01T10:31:01Z  chore(ops): refresh the due-list (#10651)
f292f7a9  2026-09-02T09:57:37Z  chore(ops): refresh the due-list (#10781)
```

Declared cron is `50 5 * * *` (05:50 UTC). So **it fires — 4h41m and 4h07m late respectively, and 23h27m apart.** That corroborates the manager's two runs independently, and it also sets the honest ceiling on this work: *"it fires"* is not *"it fires on time"*, and the due-list is the exact surface this directive's alarm will ride on. An alarm that surfaces within ~4-5 hours of an unpredictable offset is what is actually on offer here, and I will say so in the PR rather than imply same-day.

⚠️ Note what that git evidence does **not** establish: a commit proves the workflow ran and committed, it does not prove the trigger was `schedule` rather than a `workflow_dispatch` someone fired. The `event=schedule` half rests on the manager's measurement, which I could not reach. Two independent sources agreeing on the timestamps is why I am willing to state it; one of them being unreachable from here is why I am labelling it.

## The gap I am building against

Every one of the 36 `OPEN-ITEMS` rows is **timer-only**. There is no field anywhere that can say *"come back when the soak has N rows"* or *"when `verdicts_differ >= 1`"*. And the state that does not exist at all today is the dangerous one: a soak that has **stopped writing** is currently indistinguishable from one patiently accruing, so the operator waits indefinitely on evidence that was never coming.

Four states, never collapsed — the `verdict_for` idiom this repo already uses:

```
ready         threshold met — come back now
accruing      rows arriving, threshold not met
not_writing   NO rows since the soak was declared — THE SOAK IS DEAD
unknown       we could not READ the soak file
```

`unknown` is separate from `not_writing` on purpose: *"the log is unreadable"* and *"the log is empty"* are opposite findings, and folding them is the `curl … || echo '{}'` defect wearing a new hat.

**It will not page on `accruing`.** A daily "soak not ready" ping is the desensitised-alarm P1 this repo already paid 202 CRITICALs for (`BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART`). `ready` and `not_writing` escalate; `accruing` is due-list body context.

## First commit is not the schema

The **first** thing I am landing is the row that is missing right now. #10746 ships a soak at `runtime_logs/bybit_coverage_soak.jsonl` and has **no `OPEN-ITEMS` row**. Its PR proposes a *backlog* row — and the backlog is **not** a due-list source, so that soak would accrue and surface to nobody. Filed against today's schema first so it cannot be lost while I build the better version, then upgraded to carry its real criterion (`verdicts_differ: true`) once the field exists.

PR will be opened as a **DRAFT**. I will not merge it.
