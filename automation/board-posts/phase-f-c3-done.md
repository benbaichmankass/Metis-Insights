✅ **DONE** · Phase F / C3 (decision preparation) · session `session_01MDjxAnncsh71UiRAWtYHkH` · **PR [#10681](https://github.com/benbaichmankass/Metis-Insights/pull/10681)** (draft, pending CI)

## The headline is a correction, not a delivery

**Two of C3's three parts were already shipped and I did not rebuild them.** The build plan gives the repair as *"a cron and a committed path"*; verified rather than trusted, both landed today in #10649 (repaired by #10653) — the workflow with `cron: "40 4 * * *"`, and `comms/strategy_reviews/2026-09-01/` on main. **The plan paragraph is corrected in the PR**, because left as written it invites the next session to rebuild a shipped mechanism (`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`).

**What was actually missing is the part the repair did not name: a reader.** `grep` over `*.py`/`*.ts`/`*.svelte`/`*.yml` for `comms/strategy_reviews` returned the writer and the docs and **nothing else** — the durable record had zero consumers, which is what `provenance-consumer-guard` exists to catch. Closed by `GET /api/bot/strategy-reviews`.

## 🔴 The finding that matters more than the route — please read this half

**`actionable: 0` was two different facts rendering identically:** *the fleet is fine*, and *nothing could be graded*.

Population — all **52** enabled strategies, committed 2026-09-01 index, window **7 days**: `n_closed` was **0 for 34 legs**, 1–4 for 14, 5–19 for 4, and **never exceeded 8**, against the generator's own `MIN_CLOSED_FOR_ACTION = 20` floor. So **52/52 were ungradeable and no leg could produce a KILL/DEMOTE whatever its PnL** — including **13 losing legs** carrying **−$35,446** of provenance-trusted PnL between them.

At a 7-day window a leg needs ~3 closes/day; the fleet closed **50 trades that week**. **The floor is unreachable for every leg, every run, indefinitely.** Neither the floor nor the window is wrong on its own — each was chosen without reference to the other, and the defect lives at the seam, which is why it survived review.

**So C3's plumbing is complete and the decision surface still proposes nothing. Do not report the constraint as moved on the strength of the cron.**

## ❓ Decision for the operator — I did not take it

Widening `WINDOW_DAYS` would make the fleet gradeable. It is *not* in my forbidden set (a workflow env var, not `strategies.yaml`/risk caps/an order path) but it **changes what evidence a KILL badge rests on**, and widening far enough to make anything gradeable reintroduces the low-n hazard the floor exists to prevent. **The question is what evidence a KILL/DEMOTE should rest on — window and floor are only meaningful as a pair.** Filed `BL-20260901-REVIEW-PACKET-WINDOW-AND-EVIDENCE-FLOOR-ARE-INCOMPATIBLE-SO-NO-ACTION-CAN-EVER-FIRE` (**high**). **Manager: this is the one to escalate.**

## ⚠️ Deployed, not observed — two separate claims

1. **The cron has never had the OPPORTUNITY to fire.** It landed 11:58Z and fires 04:40 UTC; first slot is 2026-09-02T04:40Z. A third state, neither *fired and worked* nor *fired and failed*. Both runs to date (#10652, #10656) were dispatch-driven. `OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON` forbids reading correct syntax as evidence.
2. **The evidence block has only ever read `unknown`** — the one committed index predates the published field, so `none_gradeable`/`partly_gradeable`/`all_gradeable` have never come from a real run.

Both carried by `OI-20260901-REVIEW-PACKET-CANNOT-PROPOSE-AN-ACTION-AND-ITS-EVIDENCE-BLOCK-IS-UNEXERCISED` (**loud**), whose `clears_when` requires the run's **EVENT** to be `schedule` — a dispatch run writes a byte-identical artifact, so "an index exists" would go green on the state the row exists to hold open.

I could not read run history myself: `api.github.com` is **403** here and no Actions MCP tool was in this session's toolset. Stated as a limit, not inferred as a clean negative.

## Also found — filed, not fixed

- **`OPEN-ITEMS.json` cannot be round-tripped and has no append helper** (**medium**). No `(indent, ensure_ascii, sort_keys, newline)` combination reproduces it: it mixes an escaped `—` with literal em-dashes **and** mixes 3-space with 6-space item indentation. That is exactly the trap `backlog_append.py` was built for, one file over, on **the file every session edits at session end**. A naive write would have reformatted 64 lines and re-attributed them to my PR; I inserted surgically instead and asserted the parsed existing items were byte-identical (**20 insertions, 0 deletions**). ⚠️ **Heads-up to every session: do not read-append-write this file.**
- **A route is not a reader** (**medium**) — nothing renders the fleet index, so the defect is moved one hop, not eliminated. Any panel must show `evidence.floor_state` and `freshness` **beside** the action count.
- The one committed day holds 105 files from the pre-fix selector (**low**) — known, fixed at source, left rather than swept.

## Files touched + verification

`src/web/api/routers/strategy_review.py` · `scripts/ml/strategy_review_packet.py` · `scripts/ci/check_collapsed_states.py` · `tests/test_strategy_reviews_committed_read.py` (new) · `CLAUDE.md` · `docs/api-tier-policy.md` · `docs/design/operating-layer-build-plan-DESIGN.md` · `docs/claude/OPEN-ITEMS.json` · `docs/claude/health-review-backlog.json`.

**106 tests pass** (23 new, incl. a generator→route round-trip so producer and consumer cannot drift on the floor). `collapsed-state-guard` **clean, 22 contracts** (two new: `read_state`, `freshness` — coverage caveat that today's consumer is the test suite is recorded in each `why`) · `open-items-guard` **OK** · `api-tier-policy` **OK** · `diagnostic-provenance` **OK** · `backlog round-trip` **OK** · `session-brief` re-rendered, `--check --base origin/main` and `--self-test` pass.

**Tier-1 throughout** — read path, docs, guard registration. No `config/strategies.yaml`, no risk caps, no order path. **C4 not taken** (deferred with a reason I did not route around).

⚠️ **Not verified:** neither route observed serving from the live VM.

**Area is clear** — nothing held.

---
_Generated by [Claude Code](https://claude.ai/code)_
