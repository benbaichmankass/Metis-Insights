# HANDOFF — day manager 2026-09-03 → successor

Written at ~83% context by `session_01Nopk1HcpvWBSEbZxEmALkd`, which held the
manager lease from 05:38Z. **The registers are the state of record and they are
current** — this file is the prompt, not the state. Read in this order:

1. `docs/claude/work/MANAGER-CHECKLIST.json` — 101 items, current to 14:2xZ
2. `docs/claude/work/MERGE-QUEUE.json` — the queue and what it learned today
3. `docs/claude/work/SESSIONS.json` — sub-sessions, refreshed from a live read at 12:52Z
4. `docs/claude/OPEN-ITEMS.json` — 51 rows; one was cleared today (see below)

⚠️ **CLAIM THE LEASE FIRST** — `python3 scripts/ops/manager_lease.py status`.
Mine was last heartbeated 13:08:45Z with a 90-minute TTL, so it expires ~14:38Z.
Takeover is time-based; you do not need my cooperation.

---

## What is TRUE now that was not this morning

- **#10920, #10933, #10941 merged.** #10933 canonizes the manager's three base
  duties (two as checks, one deliberately refused) — the operator's explicit ask.
- **The decision channel is proven end to end.** `GET /api/bot/work/decisions`
  returns 8 requests, **2 `not_submitted`**, and `writeGate: open`.
- **MI-103 resolved.** #10780 is dispositioned (`superseded`, from primary
  sources); `open_pr_record.py --strict` grades `settled_graded` on main; the
  reconcile-open-prs generator has gone QUIET; four stale PRs closed.
- **The incident question is answered.** One `broker_cancel_wedged` wedge stands:
  `alpaca_paper|GLD|long`, order `2e843e04` at `pending_cancel`, GLD long 39.0,
  unrealised −396.96. **PAPER, not real money.**
- **Verification: 20 done / 25 landed_unproven** (was 18 / 26). MI-94 and MI-35
  verified on the fleet; MI-20's premise REFUTED and corrected.

## What is BLOCKED, and do not force any of it

| | blocker |
|---|---|
| **#10895** (MI-83, merge-ping observability) | **MI-106** — commit `5504200c` carries a MIS-STAMPED `Claude-Session:` trailer naming the previous manager, so manager-scope-guard fails it. Do not except it, do not rewrite another session's commit, do not merge past a failing required check. Its author session may just need to re-commit with the right trailer. **Cost: it is what makes `OI-20260902-PER-MERGE-PING` verifiable at all.** |
| **the manager branch** `claude/workflow-overhaul-scope-z4sjm3` | **MI-105** — squash-merge keeps landed commits in `origin/main..HEAD` forever, so manager-scope R6 re-grades historical heartbeats permanently. 7 findings, all TRUE, condition already repaired, and no future good behaviour can clear them. Auto-merge is DISARMED and must stay that way while a queue exists. |

## The three things I would do first

1. **The ping chain as ONE cluster** (MI-01, MI-02, MI-16, MI-17, MI-80, MI-83) —
   six rows, one subsystem, one pass. `VERIFICATION-PLAN.json` says start from the
   per-merge ping the operator confirmed rather than from zero. **It needs several
   diag reads; I stopped rather than start it at 83% of this session's 1,000,000-token context budget (the denominator is the budget, not a share of any work).**
2. **MI-101's remaining half** — the digest reads a repo-relative path instead of
   `$DATA_DIR`. The ledger now EXISTS, so this is the half that would still hide a
   wedge. Two sessions have died on this work; a third needs the ruling in its
   prompt (recorded on MI-101).
3. **MI-105 / MI-106** — both are guard judgements. **The manager does not build
   them; route them.**

## Five things that cost me time — do not re-derive them

- **`diag_fetch.sh` prefixes `/api/diag/`.** Passing an `/api/bot/...` path 404s and
  I nearly reported "the decision route is not deployed". Use plain `curl` for
  `/api/bot/*`.
- **A relay-opened PR starts with ZERO checks** (no workflows fire for a
  `GITHUB_TOKEN` push) and auto-merge waits on green forever. Push one ordinary
  commit after the PR exists.
- **`run_guards.py` prints `FAIL 0` on a run it knows was incomplete** — an
  uncommitted tree silently skips ~17 guards. Commit first.
- **A new backlog row needs `opened_at`** (not `filed`, not `found_on`) or
  register-id-guard fails you. `backlog_append.py` does not add it.
- **`OPEN-ITEMS.json` does NOT round-trip.** Anchored textual edits only; check
  `git diff --numstat`.

## The one structural fact that shaped the whole day

**A register-only conflict is trivial locally and IMPOSSIBLE server-side.**
Measured three times on three PRs, each on the same commit pair both ways: clean
with the row-aware merge driver, CONFLICT without it. GitHub does not run custom
merge drivers, and `update_pull_request_branch` refuses. **So the queue needs
someone with an ARMED CLONE at every step** — all three PRs had idle authors and
none could free itself. `install_merge_driver.sh` arms your clone; do it first.

## Waiting on the operator — and ONLY these

- `DEC-20260903-SUNSET-DISPOSITION-POLICY` — ten retirement candidates, one policy
  question, four options. **Live in the inbox.**
- `DEC-20260902-LOCAL-LLM-WEIGHT` — pre-existing, also unanswered.
- The GLD `pending_cancel` order — needs operator/venue action, no bot-side lever.
  Paper, so not urgent.

⚠️ **THE STANDING RULE I LEARNED THE HARD WAY TODAY:** an item is only "waiting on
the operator" once it is a `decision_requests[]` block on a work object **ON
MAIN**. Reporting it in chat reaches nobody, and an object on an unmerged branch
reaches nobody either — the route reads `main`. The operator caught me doing this
all day.

---

## The wind-down, run as a protocol rather than as a feeling (2026-09-03)

The operator asked whether a manager wind-down protocol exists and what was
actually implemented. **It exists and it is binding**: `CLAUDE.md` already says
a manager must run `scripts/ops/handoff_check.py` before handing over. I wrote
the body of this document *without* running it. Running it graded me
**`not_ready`** on three of eight checks, and every one named something a
successor would genuinely have lost.

| check | verdict | what it caught |
|---|---|---|
| `live_registry` | PASS | all 6 graded live sessions registered (observed 60) |
| `checklist_owners` | PASS | all 58 owner mentions across 104 items resolve |
| `lease` | **FAIL** | expired — 197 min against a 90 min TTL |
| `manager_state_pushed` | **FAIL** | 4 register files differed from `origin/main` |
| `pending_spawns` | PASS | no unconfirmed `spawn_pending` rows |
| `open_prs` | **FAIL** | **1 row against 14 open PRs** |
| `pr_decisions` | PASS | every conditional verdict records its condition |
| `settled_prs` | PASS | all 26 settled rows carry a terminal and a reason |

**The three failures had one root cause between them and one of their own.**
The lease and the state-push failure were the same fact seen twice — a manager
branch 77 commits deep that never reached `main`, carrying both of the day's
operator decisions. The open-PR gap was independent and was simply never done.

### What the run is worth keeping for

1. **`ready` is unobtainable without a live observation, and that is the
   enforcement.** The check cannot enumerate running sessions; only a session
   holding `list_sessions` can. Omitting `--live-sessions` grades `unknown`
   forever, and there is deliberately no flag to assert the registry is fine —
   asserting it is what failed twice before.
2. **Three of its eight checks passed, so it is not a rubber stamp in either
   direction.** A check that always fails gets disabled; one that always passes
   is decoration.
3. **It found what a narrative would not.** "Everything is pushed" was my honest
   belief and it was wrong, because *pushed* and *on `main`* are different facts
   and only the second one a successor reads.

### The two defects the run itself exposed — MI-110 and MI-111

Filed, not fixed here. **MI-111 is the one that matters for the protocol:**
`check_lease` PASSes only on `held_by_me`, so **releasing the lease — the
correct final act of a handover — makes the readiness verdict go red.** A
manager following the check literally would hand over a lease it still held,
which is precisely the outcome the check exists to prevent. So the honest
reading of a post-release `handoff_check` run is: the three failures above are
closed, and `lease: FAIL` now means *"this manager holds nothing, and released
it deliberately at 17:42Z so the successor can claim instantly instead of
waiting out a TTL."* That is a clean handover, and the tool cannot yet say so.

**MI-110** is the sibling: `run_guards.py` grades a missing `pytest` identically
to a failing test, so three of its five red lines said nothing about the code.

### What a successor should do differently

Run `handoff_check.py` **at the start** of the wind-down, not after writing the
handoff. It takes one command and two live observations, and everything it found
today would otherwise have been discovered by the successor, from the wrong side.

---

## Final state at hand-off (2026-09-03, after the operator's approval)

**#10944 merged** — squash `717f00eb`, on the operator's Tier-2 approval, unconditional.
MI-101 is **`landed_unproven`, not `done`**: the close-wedge ledger has never been
observed present on the fleet, and the done-condition is a post-restart
`/api/diag/log_file?name=close_wedge_standing` reading `present: true` with a fresh
stamp, plus a digest run naming `/data/bot-data/...` in its own output. **Expect the
digest to report `NOT EXAMINED` until that restart** — that is the honest state of a
ledger nothing writes, and it replaces a false clean bill that had been printed on 12
rows. Do not roll it back.

### One question only the operator can answer

MI-109's session needs to know **which Telegram bot the operator actually has open** —
`@ict_claude_bot`, `@claude_ict_comms_bot`, or the trader bot. Nothing in the repo can
establish that, and the decision-delivery fix cannot be verified without it.

### MI-109 is blocked, and the cause was the manager's

`session_011J1VB7UsXW1LZb5bCFixRD` went `need_input` **six minutes after spawn** with
*"work object WO-20260903-DECISION-DELIVERY-UNATTRIBUTED.yaml unreachable"* — because
that object existed only on the manager's branch. **That is the same defect the
wind-down landing was opened to fix, and it had already cost a live sub-session two
hours before anyone noticed.** The object is on `main` now (`2b2508be`), so the blocker
is cleared at the source; the session cannot be told, because a manager cannot message
a running cloud sub-session. Respawn it against `main`.

If nothing else survives this document, let it be that one: **"pushed" and "on `main`"
are different facts, and only the second is one another session can act on.**

### A hypothesis I formed and refuted — MI-112

Both sub-sessions checked record `the-lizardking/ict-trading-bot` as their only source —
the legacy owner *and* the legacy repo name — while this manager carries all three
correct `benbaichmankass/...` sources. I was about to report that as the cause of the
refused push and as something that would break every spawned session. **It is not.**
The MI-101 respawn shipped a 9-file, 6-commit PR with green CI on `Metis-Insights`
while carrying the same stale source. Real record defect, filed as MI-112; **not** a
push blocker, and worth not re-deriving.
