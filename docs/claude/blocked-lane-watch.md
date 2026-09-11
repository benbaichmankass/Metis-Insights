# Blocked-lane watch — telling a lane when its blocker clears

> **Doc status:** `live` · category `instruction` · last verified `2026-09-11` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**MI-235.** A sub-session blocked on a manager action had no mechanism that told
it the action had happened. Three lanes, measured:

| lane | asked | blocker actually cleared | blocked for |
|---|---|---|---|
| MI-222 | 2026-09-09T17:53:18Z — "close PR #11579" | 19:41:11Z, ~2h later | **13 hours** |
| MI-139 | 2026-09-06T19:10:07Z — "disposition #11105" | 10:22:01Z, **9h before it asked** | **3.5 days** |
| MI-238 | 2026-09-11T03:56:52Z — `post_turn_summary`: "#215 awaiting merge" | merged 04:29Z | **33 minutes** |

All three were freed only because a manager happened to read the roster by hand.
The third is the mildest and the most damning: the lane announced its blocker in
a **machine-readable field** and nothing read it.

## If you are a blocked lane, this is the whole contract

Before your last turn, declare what you are waiting for — **and push it**:

```bash
python3 scripts/ops/session_registry.py blocked-on \
  --session-id <your id> --kind pull_request --ref '#1234' \
  --clears-when closed_or_merged
```

| `--kind` | `--ref` | `--clears-when` |
|---|---|---|
| `pull_request` | `#1234`, or `owner/repo#1234` | `merged` · `closed_or_merged` |
| `branch` | `claude/my-branch` | `deleted` · `exists` |
| `path_on_main` | `docs/claude/FOO.json` | `exists` · `deleted` |
| `work_object` | `WO-20260911-…` | `done_or_accepted` |
| `operator_decision` | `WO-20260911-…::DEC-…` | `answered` |

Discharge them when you resume: `blocked-on --session-id <id> --clear`. **A stale
edge is read as a live blocker**, and this repo already records that a false
blocker is worse than a missing one.

⚠️ **A declaration that is not on `main` is read by nothing.** The watcher grades
the committed register.

## Why it REFUSES a kind it cannot grade

`blocked-on` rejects any kind with no resolver, at write time. That refusal is
load-bearing: it is the only reason `could_not_look` can mean *we tried and
failed* and never *there was never a resolver for this*. Those are opposite
findings with opposite remedies — a **resolver** gap versus an **author** gap —
and `src/runtime/decision_subject.py` keeps `unknown` and `undeclared` apart for
exactly the same reason.

If your blocker genuinely is not one of the five kinds, say so in prose and tell
the manager. An *undeclared* blocker is an honest gap; a *declared-but-ungradeable*
one is a lie the watcher repeats every few hours.

## The four states

| state | means | pages? |
|---|---|---|
| `cleared` | the condition is satisfied — **the lane is free and does not know it** | **yes** |
| `still_blocking` | resolved; the condition holds. The ordinary, correct case | no |
| `could_not_look` | the resolver **ran and failed**. *We did not look* | **yes** |
| `undeclared` | nobody wrote a blocker down — an author gap | no |

⚠️ **`could_not_look` pages.** A blocker nothing can grade is a lane nothing can
free, which is MI-235 itself one level up. Reading it as `still_blocking` would
make the sensor's own blindness indistinguishable from a lane that is
legitimately waiting.

⚠️ **`undeclared` does not page**, and that is not laziness. Measured
2026-09-11: **159 of 159** non-terminal rows were `undeclared`, so pooling it
into `could_not_look` would page on every row from the first run and the alarm
would be switched off inside a day — the desensitised-alarm P1 (202 of 376
CRITICALs in one measured window were a single un-latched alarm).

**It fails toward waking.** A spurious wake costs a lane one turn; a missed one
cost 3.5 days. So *any* cleared blocker frees a lane (never *all*), and an
ungradeable one surfaces rather than staying quiet.

## The carrier, and the evidence it fires

A step in **`.github/workflows/pr-queue-watch.yml`** — deliberately **not** a new
cron. Measured 2026-09-11 over that workflow's last 12 receipt landings: a
continuous dated series from `2026-09-06T20:49:05Z` to `2026-09-11T05:14:26Z`,
2–3 per day. Against that, `probes.yml` was **67.7h** stale and
`session-reaper.yml`'s ledger carries no `generated_at` at all. A cadence nobody
has seen fire is the looks-armed-is-not failure this row exists to end — and
that workflow already fetches the open-PR listing the watcher needs.

Receipt: `docs/claude/work/BLOCKED-LANE-WATCH.json`. **It is also the paging
latch**, and it latches BOTH paging states — `cleared` and `could_not_look` —
so a blocker pages once per STATE it reaches and not again while it stays
there. It is durable (committed) because the condition outlives any process,
the correction `BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART`
forced. If the receipt fails to land the latch does not advance and the next
run pages again — failing toward waking, deliberately.

⚠️ **THE LATCH KEY INCLUDES THE STATE, AND THIS PARAGRAPH DESCRIBED THE LATCH AS
CLEAR-ONLY UNTIL 2026-09-11 WHILE THE KEY OMITTED THE STATE ENTIRELY.** The two
faults compounded: `could_not_look` latched under the same key as `cleared`, so
a blocker that paged once as *we could not look* could never page again — and
the prose said nothing about `could_not_look` latching at all, so reading it
would not have revealed the hole. **The transition `could_not_look -> cleared`
is the single one this mechanism exists for**, and it was silent. Found by the
MI-235 review lane, confirmed by execution against a positive control on both
routes that reach it (a cross-repo PR whose listing was unreadable and is later
covered — MI-238's own case — and a same-repo PR later found merged into main),
and pinned by a test plus a mutation check. A blocker whose state does NOT
change still pages exactly once: only a genuine change of state mints a new
key.

## ⚠️ What it does NOT do

**It does not wake the lane.** The wake hop is `create_trigger` + `fire_trigger`
— `mcp__*` tools **CI does not hold**, and the operator ruled out minted
credentials for CI on 2026-09-02 ("no minted tokens, ever"), so no workflow can
ever perform it. That channel already exists and is already proven (MI-123's
poke-only Routine woke a manager out-of-band at 2026-09-04T23:09:19Z). **What
failed in all three incidents was NOTICING, not poking.** This is the noticer;
the page names the session and the exact poke to fire.

**A clean run is not a clean roster.** Exit 0 means every *declared* blocker
still holds. A lane that declared nothing is invisible to this sweep, and the
output says so on every run.

**A lane can also just run it on itself** — `--session-id <id>`, no manager and
no MCP required. The cadence is the floor for lanes that could not or did not.
