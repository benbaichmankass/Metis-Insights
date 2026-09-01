✅ **DONE (PARTIAL — and I am naming which parts)** · session `session_01J16qHJHbnvqDyRWWAvdiRD`

Branches `claude/claude-bot-drain-route` · `claude/send-ping-kind-params` · `claude/claude-channel-registers` — **area now clear.** I hold no merge slot and merged nothing.

## Landed

| PR | State |
|---|---|
| **#10674** drain resolves its bot | ✅ **6/6 green → MERGED `5c45ca52` → live on VM `1bae542a`** |
| **#10683** three ping classes reachable from the action | ✅ **6/6 green, ready for review** — manager's merge |
| **#10685** registers (OPEN-ITEMS + 2 backlog rows) | 🟡 open |

## ⛔ NOT DONE, and not because I ran out of road

**(b) set-env · (c) the separation proof · (d) the three classes fired live — none happened.** Two blockers, and only one is tooling:

1. **`set-env` is Tier-2** (`docs/claude/system-actions.md` line 99) and `CLAUDE.md` requires an operator OK in chat. My instruction came from a **manager session, not the operator.**
2. **No system-action can be dispatched from this session at all** — `issue_write method=create` → **403**, twice. This blocks even Tier-1 `send-ping`. ⚠️ **Independently corroborated:** the Phase D session hit the identical 403 on `add_issue_comment` *and* `create_pull_request` minutes apart and called it a write-scope boundary, not the transient MCP drop. Two sessions, same conclusion — so it is structural and not worth retrying.

**I did not build the issue-opener relay that would fix (2).** It needs the PAT (GITHUB_TOKEN-created issues do not trigger workflows) and would let any `claude/**` push fire any allowlisted tiered VM action. Ask is posted above; it needs one operator click, and that click is simultaneously the Tier-2 OK.

## What I proved, and what I refused to claim

**Reproduced the defect before trusting it.** Stubbed `telegram`/`dotenv`, drove the real `_drain_pending_claude_pings` against both trees. PR tree **3/3**. `main` **fails** case 1 with `bot_token='trader-tok'` — **while `describe()` reports `isolated=True`.** That is the whole bug in one line, and it is why "the router says isolated" was never evidence.

**The live mechanism is now honest.** Post-merge, `21:37:08Z`:
```
claude ping route -> claude: token=TELEGRAM_BOT_TOKEN[fallback] ... isolated=False
[WARNING] ... pings will land in the shared trader conversation.
```
`True` → `False` **is the improvement**: it now reports the true state, where before it reported a comfortable false one.

⚠️ **I am NOT claiming separation, and `clears_when` is written so a future session cannot either.** `isolated=True` proves which **token** resolved. It cannot prove which **conversation** a message reached: a DM's `chat_id` is the operator's own id (`365546917`) for *every* bot by construction, so the VM side cannot distinguish the two chats **even in principle**. Clearing needs the dedicated startup line **AND** a human confirming which app it appeared in. And delivery clears nothing — it has been delivering all along, to the wrong place.

## (e) The 06:20 digest — three findings

**1 · It is not 06:20 daily.** Cron is `20 2,6,10,14,18,22 * * *`. 06:20 survives as one slot. If you are expecting one morning report you will get **six a day** — worth confirming which you meant.

**2 · The chain reaches the Claude bot — verified hop by hop, not assumed.** `work_digest.py` writes `"target": "claude"` (line 307) → `pending-pings.jsonl` → `ict-git-sync.timer` (5 min, `active`) → `deploy_pull_restart.sh:211` → `notify_on_pull.py` → `enqueue(target="claude")` → **the exact drain #10674 fixed.** So yes, **once the token lands.** I also checked the stale-replay risk — the drainer re-reads the *whole* file each pull and dedupes only on a VM-local hash file — and it is **not** live: the last four syncs each skipped exactly **49** hashes against **49** lines in the file. Arithmetic match, so a digest row would be the only thing enqueued.

**3 · ⚠️ I WATCHED THE 22:20Z WINDOW AND IT PRODUCED NOTHING.** work-digest merged `20:11:05Z`; its first cron window was observed live. At `22:26Z`: no new `pending-pings.jsonl` commit (still 49 lines, newest **2026-08-01**), no `automation/work-digest-*` branch. A run must produce both.

**I ruled out the innocent explanation before reading absence as evidence** — "it ran and legitimately queued nothing" leaves an identical trail. Checking the one guard that could cause that surfaced a **separate defect**: `_already_sent_today()` reads `runtime_logs/work_digest_state.json`; `.gitignore:29` ignores `runtime_logs/` and the file is absent from `main`, so **on a fresh Actions checkout the "one digest per UTC day" latch can never fire.** Its docstring claims a bound it does not impose, the as-written cadence is six a day, and the **same code would latch correctly on the VM** where `runtime_logs` persists — opposite behaviour from one source, decided by which box runs it, stated nowhere. Filed: `BL-20260901-WORK-DIGEST-DAILY-LATCH-CANNOT-FIRE-ON-THE-ONLY-PATH-THAT-RUNS-IT`.

⚠️ **What I cannot distinguish and did not assert past:** *"the cron never fired"* vs *"it fired and failed before the commit step"*. Identical empty trails. Separating them needs the Actions run list filtered to `event=schedule`, and **this session cannot reach it** (`api.github.com` 403, no Actions MCP tool). So `OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON` gains a third workflow with the same **symptom** — *not* a confirmed third instance of the same **cause**, and I have written it into the row that way.

## Corrections to what I was handed

- **The `.service` suffix is NOT required.** `set_env.sh` normalises before matching the allowlist. There was no second bug to hunt.
- **`main`'s `isolated=True` was actively misleading**, not merely uninformative — worth knowing for anyone who checked that surface earlier and came away reassured.

## Loud rows I am carrying forward

New: `OI-20260901-CLAUDE-CHANNEL-SEPARATION-SHIPPED-BUT-UNPROVEN` (loud). Also touched: the cron row above. I did **not** re-observe the other due monitoring rows (MHG over-cover, trainer capture, bybit hedge mode, prop risk gate) — out of scope for this session and I will not affirm what I did not check.

---
_Generated by [Claude Code](https://claude.ai/code)_
