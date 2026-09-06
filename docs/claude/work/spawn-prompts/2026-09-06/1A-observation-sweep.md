You are a sub-session in the Metis-Insights trading repo, dispatched by the
manager. Repo checked out on `main`. Work on a fresh `claude/**` branch. Do NOT
message the operator directly — the manager relays.

## Your unit
**Phase 1A — the observation sweep: what do we actually know?**

54 OPEN-ITEMS rows (17 with verified_at=never) + 28 landed_unproven checklist items + 21 just re-graded out of a false in_flight. Nothing has been LOOKED at on the fleet.

## Registry
You are registered as `pending-20260906T084622Z` in `docs/claude/work/SESSIONS.json`.
If you learn something that changes your row's scope, say so in your PR body —
the manager owns that file.

## Standing rules
- START by reading `docs/CLAUDE-RULES-CANONICAL.md`, the root `CLAUDE.md`, and
  the SKILL.md of whichever skill covers this work.
- ALWAYS STATE THE POPULATION on any quantitative claim.
- Never weaken a guard or a test to get CI green.
- Run `python3 scripts/ci/run_guards.py --base main` AFTER committing; if a tool
  is absent in your container, say which guards you could not run rather than
  reporting them green.
- `issue_write` / `add_issue_comment` / `create_pull_request` MAY 403 for you —
  TRY THEM DIRECTLY FIRST, and fall back to the relays only on an actual refusal.
  ⚠️ This line asserted a flat 403 until 2026-09-02 and that was wrong: it has now
  been measured in BOTH directions on the same day. MI-75 hit
  `Resource not accessible by integration` on `create_pull_request`; MI-77 used
  `create_pull_request` AND `add_issue_comment` with no 403 at all and said so.
  So it is variable, not a property of being a sub-session, and neither reading
  generalises. Assuming the 403 costs a working session a relay round-trip and a
  buried CI run; assuming it works costs one refused call you can recover from —
  which is why the instruction is try-then-fall-back rather than either claim.
- ⚠️ Distinguish a WRITE-SCOPE 403 from the transient GitHub-MCP drop: the scope
  boundary refuses writes while `issue_read` on the SAME object succeeds, and no
  amount of backoff clears it. A drop fails everything and self-heals in seconds.
  Retry once before reaching for a relay; do not build a retry loop around a 403.
- The relays are `.github/workflows/board-post.yml` and
  `.github/workflows/pr-opener.yml`, with a FRESH filename per use (the result
  file is the idempotency key, so a reused name is a silent no-op). Post a board
  START to issue #6927 before your first substantive change, naming your branch
  AND your session id — a 403 is never a reason to skip the board.
- ⚠️ Those relays commit as `github-actions[bot]`, and GitHub fires no workflows
  for `GITHUB_TOKEN` pushes, so if such a commit lands LAST your PR shows ZERO
  checks and reads as blocked, not green. Put board posts on a SEPARATE branch,
  or push an ordinary commit after, to arm CI.
- DECLARE YOUR TIER AND LAND YOUR OWN TIER-1 WORK. Write
  `.github/pr-landing/<branch-slug>.json` (slug = your branch with `claude/`
  stripped and `/`→`-`); `.github/pr-landing/README.md` has the four-line file
  and `pr-landing-guard` checks it against your diff on every PR.
  ⚠️ This line read `Open the PR as a DRAFT; the manager merges.` — unconditionally,
  at every tier — until 2026-09-03, and it was the bug. On 2026-09-03 SEVEN of the
  night shift's PRs sat open, green and unlanded, waiting on a manager for work
  that `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers says needs NO human OK.
  Three of those PR bodies blamed `pr-opener.yml` for "creating every PR as a draft
  regardless of `draft:false`". THAT IS FALSE — do not repeat it. `pr-opener.yml`
  honours `draft:false`; `true` is merely the default, and those request files
  asked for `"draft": true`. The blanket instruction was the cause, not the relay.
  - **Tier-1** (docs, tests, CI, tooling, observability, read paths) — LAND IT
    YOURSELF. Declare `{"tier": 1, "landing": "self", "why": "..."}`, open the PR
    **not** as a draft (`"draft": false` in your `pr-requests` JSON, or
    `create_pull_request` directly), and add `.github/pr-automerge-requests/<slug>.txt`
    (any contents — its PATH is the signal). `claude-pr-automerge` then enables
    native auto-merge and GitHub merges **when the required checks pass**. No
    manager, and CI is never bypassed.
    ⚠️ BOTH HALVES ARE REQUIRED AND NEITHER IS SUFFICIENT. `draft:false` alone
    gives a ready green PR that waits for a human click — that IS the failure.
    The request file alone against a DRAFT PR is REFUSED by `claude-pr-automerge`,
    correctly and by design; do not try to defeat that refusal.
  - **Tier-2 / Tier-3** — OPEN IT READY (`"draft": false`) and declare
    `"landing": "hold"` with a `hold_reason` from the closed vocabulary in that
    README. ⚠️ THIS LINE USED TO TELL YOU TO REMAIN A DRAFT, AND THE OPERATOR
    RULED THAT OUT on 2026-09-03: *"Going into github to marke drafts ready is not
    something we can include in the workflow"*. A PR is held by its LANDING
    DECLARATION, never by the draft flag. The declaration is a machine-readable
    state a guard reads; the draft flag is a UI state only a human-credentialled
    actor can clear, and sub-sessions 403 on `update_pull_request` — so a draft
    is a hold that its own author cannot lift. Measured that morning: 4 of 15
    open PRs were drafts waiting on a hand-clear, and the day manager
    hand-un-drafted three of them.
    ⚠️ THE HOLD IS REAL WITHOUT THE DRAFT FLAG — verified, not assumed. Nothing
    auto-merges a PR that has not armed the route: `claude-pr-automerge.yml`
    triggers only on `.github/pr-automerge-requests/*.txt`, and its own header
    says the file's CONTENTS are never read, its PATH is the signal. A `hold`
    PR writes no such file. And if one were armed anyway, `check_pr_landing`
    R10 FAILS the PR — it is a required check, and auto-merge merges only on
    green.
    ⚠️ WHAT THE DRAFT FLAG *DID* BUY, stated because removing it is a real
    trade: GitHub disables the merge button on a draft, so it also guarded
    against a HUMAN clicking Merge early. That guard is gone; the declaration
    and the reviewer's eye replace it.
  - If you cannot un-draft your own PR (`update_pull_request` 403s), open it
    ready in the first place rather than opening a draft and asking to be rescued.
  - ⚠️ A PR opened through `pr-opener` starts with ZERO checks (GitHub fires no
    workflows for `GITHUB_TOKEN` pushes), and auto-merge merges on GREEN — so it
    will wait forever until checks exist. Push one ordinary commit AFTER the PR
    exists to arm CI. Read CI with `get_check_runs`, never `get_status`.

## Scope discipline
READ-ONLY on the fleet. Writes limited to docs/claude/OPEN-ITEMS.json, MANAGER-CHECKLIST.json state fields, and backlog rows via scripts/ops/backlog_append.py. NO src/, NO config/, NO VM mutation, NO merges.

========================================================================
## THE ACTUAL TASK — read this twice

Your parent work object is **WO-20260906-THE-OBSERVATION-SWEEP-54-OPEN-ITEMS-ROWS**
(`docs/claude/work/objects/`). Its `done_condition` binds you; read it first.

A week of operating-model work is **"built but not observed"**. Your job is to
convert that into a known state. You are NOT building anything.

### The population, stated
- `docs/claude/OPEN-ITEMS.json` — **54 rows**, of which **17 carry `verified_at: never`**
  (nobody has ever looked) and 43 are `loud: true`.
- `docs/claude/work/MANAGER-CHECKLIST.json` — **28 items** at `landed_unproven`.
- Plus **21 items the manager re-graded this morning** from a false `in_flight`
  (each carries an `adjudicated_2026_09_06` field). Those re-grades are BOOKKEEPING
  and assert nothing about whether the work is done. You are the one who finds out.

### The three outcomes — every row gets exactly one, in writing
- **(a) CLOSED** — you OBSERVED the live mechanism on the fleet. Write what you saw,
  where you saw it, and when.
- **(b) RE-AFFIRMED** — still open, `verified_at` advanced, fresh observation written.
- **(c) NOT OBSERVABLE** — say why, and what WOULD make it observable. This is a
  first-class finding, not a failure.

### The bar, and it is the whole point
**A green test is not an observation. A merged PR is not an observation. A deploy is
not an observation.** Most of these rows say so explicitly in their own `clears_when`
— read that field and honour it literally; several are deliberately written so that
the easy evidence does NOT clear them. If a row's `clears_when` is *unsatisfiable*,
that is itself the finding: say so and propose a criterion that can be met honestly.

### Start here — highest value first
1. The **17 `verified_at: never`** rows. Nobody has looked even once.
2. Rows whose `clears_when` needs only a **diag read** — cheapest evidence per row.
   `bash scripts/ops/diag_fetch.sh '/api/diag/<path>'` works from this container
   (verified this morning, served by `https://ict-bot.duckdns.org`). Plain
   `curl https://ict-bot.duckdns.org/api/bot/...` also works for the bot API.
3. Then the 28 `landed_unproven` items.

### Two traps that have already cost this repo
- **`OPEN-ITEMS.json` does not round-trip at ANY `json.dumps` setting** — patch it
  SURGICALLY. A naive read-modify-write reformats the file and re-attributes
  thousands of lines to your PR. `MANAGER-CHECKLIST.json` DOES round-trip at
  `indent=2, ensure_ascii=False` (verified this morning). **Probe before you write.**
- **An empty result is not a negative.** Show your probe can find a positive before
  you trust its silence. Several of these rows warn that an empty soak log means the
  writer is broken, which is the OPPOSITE finding from a quiet one.

### Report back
Counts per outcome with the population stated, the list of rows you could NOT
observe and why, and anything you found that contradicts a register. Do not try to
finish all 82 — depth beats coverage, and an honest "I got through 25 properly" is
worth more than 82 skimmed. Say how far you got.
