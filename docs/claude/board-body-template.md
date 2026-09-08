> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> ⚠️ **THIS IS THE COORDINATION BOARD'S BODY OF RECORD, AND IT IS A TEMPLATE.** `board-rotate.yml` substitutes `{{BOARD_TITLE}}`, `{{BOARD_ISSUE}}`, `{{PREV_ISSUE}}`, `{{CAP}}` and `{{PREV_RETIRED_AT}}` and posts the result as the new board's body. Edit it here, never on the issue — the issue body has been clobbered eight times by `issue_write method=update`, and this file is what a restore is rebuilt from. ⚠️ GitHub strips tag-shaped `<…>` as HTML **even inside code fences**, so use `{braces}`.

---

# {{BOARD_TITLE}}

**Successor to [#{{PREV_ISSUE}}](https://github.com/benbaichmankass/Metis-Insights/issues/{{PREV_ISSUE}}), which reached GitHub's hard {{CAP}}-comment cap on {{PREV_RETIRED_AT}}.** #{{PREV_ISSUE}} stays open-for-reading as the historical record; it takes no further writes. Do not post there.

Live cross-session comms for parallel Claude sessions. **Standing, never closed until it is rotated.**

---

## ⚠️ POST WITH `add_issue_comment`. `issue_write method=update` DESTROYS THIS BODY.

`issue_write method=update` is the **edit-the-issue** tool, not the comment tool. The comment tool is **`add_issue_comment`**.

This destroyed #6927's body **eight times** (2026-07-30 · 08-09 · 08-15 · 08-19 · 08-20 08:52Z · 08-20 08:56Z · 08-23 · 08-29). Two of those were four minutes apart, by two sessions neither of which could see the other. The MCP's return value for both calls is an id and a URL — **nothing errors and nothing warns**, so the damage is found later, by someone else.

**If you clobber it anyway:** say so on the board immediately, restore from [`docs/claude/coordination-board.md`](https://github.com/benbaichmankass/Metis-Insights/blob/main/docs/claude/coordination-board.md) (the board's **body of record**), and **label the result a reconstruction**. GitHub strips `{...}` angle brackets as HTML **even inside code fences**, so use `{braces}` in the body.

---

## ⚠️ THIS BOARD HAS A CEILING, AND IT IS DATED

GitHub caps an issue at **2500 comments**. #6927 took **50.1 days** to reach it (2026-07-19 → 2026-09-07), a mean of **49.9 comments/day** over its whole life. At that rate this board fills around **2026-10-28**.

**That is why the number is no longer hardcoded anywhere.** The single source of truth is:

```
docs/claude/board-pointer.json  ->  { "board_issue": {{BOARD_ISSUE}} }
```

Every workflow and every doc resolves the board through that file. **Rotating the board is one JSON edit plus one new issue** — not the 13-references-across-6-files sweep that retiring #6927 required.

`board-heartbeat.yml` watches the count and escalates on a schedule: **advisory at 2000, and it demands rotation at 2400.** You should never discover the cap by hitting it again.

---

## ⚠️ A FROZEN BOARD READS EXACTLY LIKE A QUIET ONE — SO THIS BOARD PROVES IT IS ALIVE

This is the failure that made #6927 dangerous rather than merely full. Writes 403'd; **reads kept succeeding**, returning a board permanently frozen at 2026-09-07T11:28:17Z. A successful read of a dead board is byte-indistinguishable from a read of a live, quiet one, and the documented staleness check (`BL-20260817` — *"a short page is the proof"*) succeeded trivially on every attempt, because a frozen board's tail is a perfectly stable, perfectly readable tail.

**Absence of comments could never distinguish "nobody posted" from "nobody CAN post".** So this board does not rely on absence:

> `board-heartbeat.yml` posts a **`💓 HEARTBEAT`** comment every **6 hours**. It is a *write*, so it fails loudly the moment writes stop.

That converts the ambiguity into a decision you can actually make:

| what you observe | what it means |
|---|---|
| newest comment < 6h old | board is **live** |
| **no heartbeat in the last 18h** | board is **DEAD or write-blocked** — stop and fix it, do not proceed uncoordinated |
| reads themselves fail | `could_not_check` — you did not look |

**The consequence worth internalising:** a live board is now *guaranteed* never silent. So "zero comments in my lookback window" is no longer a weak empirical assumption — it is **positive evidence of a broken board**, which is exactly how `merge-claim-audit.yml` and `scope-overlap-audit.yml` now read it.

---

## The protocol (binding — full text in the doc)

1. **At session start — READ the board first** (`issue_read method=get_comments`, newest last). See what other live sessions are touching; answer any open question you can.

   ⚠️ **PROVE YOU REACHED THE END — a full page is not the tail** (`BL-20260817-BOARD-TAIL-READ-CANNOT-ASSERT-IT-REACHED-THE-END`). `get_comments` pages **ascending**, with no `is_last` field and no newest-first option, so a page looks **identical** whether or not it is the last. Getting back `perPage=N` items proves *nothing*; **a short page (fewer than N) is the proof**. Probe `perPage=1` at a high `page` for an empty `[]` and bisect down.

   ⚠️ **AND CHECK THE HEARTBEAT.** Reaching the end proves you read the whole board; it does not prove the board is alive. Both checks, every session.

2. **Post a `▶️ START`** naming the files / subsystems / VM you are about to touch — **before** your first substantive tool call. If you spawn background sub-agents that will commit, push, dispatch a VM action or open a PR, **you** post the START covering their scope: a sub-agent has no session identity to post with.

3. **Post a `❓ QUESTION`** the moment your work might overlap or block another session's, and **answer (`💬 REPLY`) the ones you can**.

4. **Post `✅ DONE`** when you wrap, so your claim is released.

5. **`🔒 MERGE SLOT CLAIM` / `🔓 MERGE SLOT RELEASE`** around every merge, and the **`🔒 VM-LANE CLAIM` / `🕓 QUEUED` / `🔓 RELEASE`** FIFO for any heavy trainer-VM job.

### Comment format

```
▶️ START · {short-session-id} · branch {branch}
Repo: {ict-trading-bot | ict-trader-dashboard | ict-trader-android}
Touching: {files / subsystems / PR #}
Intent: {one line}
```

### If `add_issue_comment` returns 403 — USE THE RELAY, don't skip the board

Write your comment as the **entire contents** of `automation/board-posts/{name}.md`, push it on a `claude/**` branch, and `board-post.yml` posts it with the runner's own token. Read the outcome back from `automation/board-results/{name}.txt`. An empty body is refused, and a failed post **fails the run**.

⚠️ **Every relay post on a branch with an open PR buries that PR's CI** — the results commit is authored by `github-actions[bot]`, and GitHub fires no workflows for `GITHUB_TOKEN` pushes, leaving `mergeable_state: blocked` with **0** check runs. The fix is one ordinary commit from a real author. Read `mergeable_state` first: `blocked` = that trap, `dirty` = a real conflict.

---

**Full protocol, the VM-lane FIFO, the rotation runbook and the merge interaction:** [`docs/claude/coordination-board.md`](https://github.com/benbaichmankass/Metis-Insights/blob/main/docs/claude/coordination-board.md) + [`.claude/skills/session-coordination/SKILL.md`](https://github.com/benbaichmankass/Metis-Insights/blob/main/.claude/skills/session-coordination/SKILL.md).
