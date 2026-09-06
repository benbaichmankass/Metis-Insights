You are a sub-session in the Metis-Insights trading repo, dispatched by the
manager. Repo checked out on `main`. Work on a fresh `claude/**` branch. Do NOT
message the operator directly — the manager relays.

## Your unit
**Phase 1B — the 0-for-13 week: fix the instrument, then attribute the loss**

Real money 0 wins / 13 losses since 2026-08-30 (-43.12 on a 264.86 book) and the R metric contradicts the PnL (30d expectancyR +0.98 vs totalPnl -3.63).

## Registry
You are registered as `pending-20260906T084729Z` in `docs/claude/work/SESSIONS.json`.
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
Read-only on journal + diag + trainer. May write docs/research/**, docs/claude/performance-review-backlog.json, docs/claude/research-review-backlog.json. NO config/strategies.yaml, NO src/ order path, NO VM mutation. Tier-3 findings PROPOSED with the exact diff, never applied.

========================================================================
## THE ACTUAL TASK — read this twice

Parent object: **WO-20260906-REAL-MONEY-WENT-0-FOR-13-THE**. Its `done_condition`
binds you; read it first. **Two deliverables, and the first GATES the second.**

### What the manager measured this morning (re-measure it; do not inherit it)
`/api/bot/performance` and `/api/bot/trades/closed`, real money only (`bybit_2`,
the ONLY real-money book, balance now **$264.86**):

| window | trades | wins | win rate | PnL |
|---|---|---|---|---|
| 2026-08-16 → 08-29 | 24 (23 graded) | 14 | **60.9%** | **+$40.96** |
| 2026-08-30 → 09-06 | 15 (13 graded) | **0** | **0.0%** | **−$43.12** |

`expectancyR` −0.90. Close reasons flipped: `sl` 5/24 (21%) → 6/15 (40%). Two rows
carry `pnl=None` **and** `exit=None`.

### DELIVERABLE 1 — a trustworthy instrument. Nothing else may be reported first.
**The instrument currently contradicts itself.** The 30d window reads
`expectancyR +0.98` / `totalR +38.3` while `totalPnl` is **−$3.63** and
`profitFactor` **0.95**. Those cannot both be true. `MI-30` already records why:
17.2% of closed rows store a stop on the **wrong side of entry**, and `expectancyR`
has reached +206.9. **R feeds the promotion gates**, so every promote/demote verdict
in the system is currently unsafe.

1. Quantify the contamination against the live journal. State the population.
2. Re-report the windows with the closed set **SPLIT BY EXIT PATH**
   (`sl` / `tp` / `reconciler` / `other` / `pnl-is-null`). Post-08-30 was 6 `sl`,
   4 `reconciler`, 5 `other` — **grading strategy quality over the pooled set grades
   the reconciler, not the strategy.**
3. Read `src/runtime/provenance.py` and use it. Do not re-derive the vocabulary.

### DELIVERABLE 2 — the attribution. Only after (1).
**The hypothesis to TEST, not to assume:** the flip date 08-30 is the day the **e35
bracket geometry** deployed — `atr_stop_mult` **2.5 → 2.0** on 9 legs, **three of
them routed to `bybit_2`** (`trend_donchian`, `trend_donchian_eth_4h`,
`trend_donchian_xrp_4h`). A tighter stop predicts exactly the stop-out rate change
observed. See `OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED`.

**Run the counterfactual:** replay the same 13 trades at the OLD `atr_stop_mult` and
report how many survive. That is the difference between a date coincidence and a cause.

**State these caveats or the work is not honest:**
- n = 13 vs 23. Small both sides.
- BTC fell 81k → 77k in the window. **Regime is a live competing explanation.**
- **Not every losing leg is an e35 leg** — `xrp_pullback_2h` and `eth_pullback_2h`
  are pullback legs. The `tp_r` values also changed on some legs. Separate them.
- The three real-money e35 legs are a SUBSET of what lost. Do not let the headline
  imply otherwise.

### What you must NOT do
- **Do not apply anything.** `config/strategies.yaml` is Tier-3. Propose the exact
  diff and the evidence; the operator decides. The operator has NOT yet answered
  whether to revert e35 — your report is what they will answer from.
- Do not report a verdict from the pooled numbers.
- Do not use R anywhere before deliverable 1 lands.

### Report back
The instrument finding first, the attribution second, and your honest confidence in
each. If the evidence does not separate e35 from regime, **say that** — "we cannot
distinguish these with n=13" is a real and useful answer, and inventing a cause is not.
