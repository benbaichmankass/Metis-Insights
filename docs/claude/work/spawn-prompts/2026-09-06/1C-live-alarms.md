You are a sub-session in the Metis-Insights trading repo, dispatched by the
manager. Repo checked out on `main`. Work on a fresh `claude/**` branch. Do NOT
message the operator directly — the manager relays.

## Your unit
**Phase 1C — three unowned live alarms: SOL orphan, MES data blindness, pending restart**

Live at 07:13Z: orphan trade 5516 adopted on bybit_1 SOLUSDT (604.7 short), mes_trend_long_1d blind since 06:33Z, and restart_pending=true with live code behind main.

## Registry
You are registered as `pending-20260906T084730Z` in `docs/claude/work/SESSIONS.json`.
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
Diag reads + the sanctioned system-actions allowlist. Tier-2 actions need an operator OK relayed through the manager -- ask, do not self-approve. NO strategy config, NO risk caps, NO order-path code.

========================================================================
## THE ACTUAL TASK

Parent object: **WO-20260906-THREE-LIVE-ALARMS-ON-THE-TRADING-FLEET**. Read its
`done_condition`. Three independent alarms, read off `/api/bot/notifications` and
`/api/diag/version` at 2026-09-06T07:13Z. They may resolve differently. **A banner
clearing on its own is not a diagnosis.**

### (a) The SOL orphan — do this one first, it is a live position
`🚩🚩 ORPHAN TRADE CREATED` at 06:39:14Z — `bybit_1` **SOLUSDT**, side **short**,
size **604.7**, entry **105.41**, adopted as **trade 5516** by
`reverse_reconciler_adopt`. A stuck-strategy watchdog fired on `ict_scalp_sol_5m`
at 30 min on the same package.

Establish: genuine exchange position needing adoption, or a reconciler artifact?
Then **confirm its PROTECTION** from a **FRESH** `/api/diag/bybit_open_orders` read
— never the cached monotonic view
(`BL-20260826-DIAG-IB-OPEN-ORDERS-SERVES-A-STALE-MONOTONIC-ORDER-VIEW`).

⚠️ **`BYBIT_HEDGE_MODE_SYMBOLS` is ARMED on `bybit_1`.** So `covered_qty` is
**side-blind** and an other-book leg can make a genuinely naked position read as
covered. Use `src/runtime/bybit_leg_sides.py::graded_book_coverage` for the grading,
and read the `BYBIT_GRADED_COVERAGE_MODE` row in `CLAUDE.md` before you conclude
anything about coverage. `bybit_1` is the demo book, so this is the right place to
find out.

### (b) MES is blind
`mes_trend_long_1d` has returned **no candle data since 06:33Z** —
`transient_market_data_unavailable` on the IBKR path. That is the class that
historically precedes a gateway wedge (see the `IB_*` runbook rows in `CLAUDE.md`
and `docs/runbooks/ib-integration.md`). Root-cause it to gateway / IBKR session /
config. Fix it or escalate with the cause **named**.

### (c) The trader is behind main
`/api/diag/version`: `git_sha 4ec87e38`, `git_sha_on_disk c2e47af5`,
**`restart_pending: true`**. Either perform the restart via the sanctioned
`system-actions` route, or write the reason it is being held. Check what is IN that
gap first — a restart picks up whatever merged since, so know what you are arming.

### Tier discipline
A service restart is **Tier-2**. You do NOT self-approve it. Prepare it, validate
it, and ask the MANAGER to relay to the operator — the manager is
`session_01HrmZ1RRNM4UnEUaFdrPEjj`; put the ask in your PR body and your board post.
Diag reads and the read-only allowlist are yours. **No strategy config, no risk
caps, no order-path code.**

### Report back
Per alarm: what you observed, what you concluded, what you did or what you need
approved. Say plainly which of the three you could not settle and why.
