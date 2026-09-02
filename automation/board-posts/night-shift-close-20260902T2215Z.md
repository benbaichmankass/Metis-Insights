## ✅ DONE — night shift close. Four PRs open, none merged by me, and one thing I got wrong and fixed.

**Session:** `session_01AYPxs3aDHwv3XBLRF4oK15` (night manager)
**Releases the merge-slot claim** I posted for #10857 — see below, I did not use it.

### ⚠️ Read this first: I do NOT effectively hold the lease

I claimed it at 21:55:05Z over `state=released`. **That claim is on branch `claude/night-manager-lease-claim-and-correction` (#10873) and has NOT merged**, so `origin/main` still reads `released` / `holder: None`. By the lease file's own doctrine — *"a claim you did not push protects nothing"* — the lease is **effectively unheld**. A morning manager may claim it freely. Stated rather than left for someone to discover.

### What actually landed on main tonight: nothing of mine

- **#10857 merged 21:59:16Z on auto-merge**, 16s after `pytest-run` went green. **I did not merge it.** It is `landed_unproven`, not `done` — the ping PRODUCER exists; **delivery is a separate open defect** (`enqueue()` writes a local file and returns, so "delivered" attests a file write, not a Telegram send). **Do not tell the operator pings are fixed.**
- **#10859 untouched.** Still held on the operator's one Tier-2 OK.
- Live trader untouched. No Tier-2/3. No spawns.

### Four PRs, all stuck as drafts — a relay defect, not a choice

#10871 · #10873 · #10876 · `claude/night-brief`

`pr-opener.yml` created every one as a **draft despite `"draft": false`** in the request file (verified in the pushed JSON). `update_pull_request` **403s** for this session, and `claude-pr-automerge.yml` **deliberately refuses to un-draft a PR it did not create**. So I cannot un-draft or merge my own work. **A morning manager needs to un-draft and merge these.**

### Findings

- **MI-86 — an unpushed RELEASE strands the successor.** Released 21:38:05Z, reached origin 21:41:54Z; I read at 21:39:37Z, inside the window, and stood down 16 minutes. The guard was right, the file was stale. Structural: the outgoing manager releases and spawns in the same breath, so the successor's first read falls in the window *by construction*.
- **MI-87 — the `routine_scope_correction` never reached the prompt carrying the error.** The operator corrected "no cloud routines" at **21:50Z**; my prompt was frozen at **21:37:58Z** carrying `NO CLAUDE ROUTINE, in any form`. I acted on the stale copy and stated the 05:00Z brief could not be scheduled. **False.** Caught only because the rendered brief named the correction and I followed the reference instead of my own prompt — luck, not a mechanism.
- **MI-84 — registry is worse than the 20:52Z reading.** Of the 18 rows `SESSIONS.json` calls `working`, a live `list_sessions` read at 22:00Z agrees on **1** — **and that 1 is my own row**. Every inherited row is wrong: 17 of 17, including 1 FAILED and 9 BLOCKED. (`has_more: True`, so 100 is a cap — but all 18 graded rows were in the read, so the cross-check is complete.)
- **MI-85 — #10398 diagnosed, NOT resolved, and ⚠️ DO NOT CLOSE IT AS STALE.** Its blocker is a **merge conflict** (`mergeable_state=dirty`), not the 113h red. Its live-path claim is **true** against the diff (n=3, all `comms/macro/`). And it holds the **only copy of the 2026-08-29 capture** — main's series runs 08-27, 08-28, then **08-30**, with 0 rows mentioning `20260829`. Closing it loses a day of PIT data permanently. Fixing it needs the producer re-run; hand-resolving generated PIT data would fabricate a snapshot no capture produced.

### The 05:00Z brief — delivered two ways, neither assured

A brief is **rendered and pushed now** on `claude/night-brief`, graded `registers=all_read observations=all_observed` with all three live observations supplied. It covers 21:40Z→22:07Z, not the night.

Routine **`trig_01EWqusjwUZtGEZAjz8tekyW`** fires at **05:00Z** for the real one. ⚠️ **Treat it as at risk, not assured:** it stores **no MCP connectors**, so the fired session may have no `list_sessions`, no GitHub tools and no `add_repo` — it may be unable to observe live state *or to push*. Its prompt declares a degraded lane that reports `not_observed` honestly and prints the brief inline rather than faking `all_observed`.

### The one I got wrong

I published a claim that the spawn prompt had lied about the lease release. **It had not** — I read `origin` 2m17s before the release landed. Retracted on this board and in #10871's history before it could merge. A negative existence claim from a single read of a store with a known unpushed window is not a measurement.
