✅ **#10674 IS GREEN AND READY TO MERGE** · session `session_01J16qHJHbnvqDyRWWAvdiRD`

**Manager: #10674 is yours to merge.** 6/6 checks success — `pytest-run` completed `success` at `21:35:55Z`, plus `guards`, `pytest-collect`, `repo-inventory`, and 2× `audit`. Marked ready for review (out of draft). **I have not merged it and will not.**

## I did not take the diagnosis on trust — I reproduced it

`pytest` is absent from this sandbox, so I stubbed `telegram`/`telegram.ext`/`dotenv` and **drove the real `_drain_pending_claude_pings`** against a temp inbox on both trees, recording the `bot_token` it actually passed to `send_telegram_direct`.

**On the PR branch — 3/3 as claimed.** Dedicated secret set → `bot_token='claude-tok'`; no secret → falls back to `'trader-tok'` (never silence); dedicated chat id honoured.

**Positive control on `main` — the harness FAILS, so its PASS means something:**

```
[FAIL] dedicated secret present -> DEDICATED bot
       bot_token='trader-tok'  chat_id=None
       route=claude: token=TELEGRAM_CLAUDE_BOT_SECRET[dedicated] ... isolated=True
```

⚠️ **Read that last line.** On `main` the router already reports `isolated=True` **while the drain sends via the trader token.** That is the whole defect in one line: the route was correct and the only consumer that mattered never asked it. "Configured, resolvable and inert" is not a turn of phrase — it is what the log says today.

## ⚠️ A claim in my own handoff was FALSE, and I am retracting it here

I was told the `.service` suffix is **REQUIRED** on `set-env`, and that a previous dispatch "would have failed on this". **It would not.** `scripts/ops/set_env.sh` normalises before it matches:

```bash
case "${SERVICE}" in
    *.service|none) ;;
    *) SERVICE="${SERVICE}.service" ;;
esac
```

Bare `ict-claude-bridge` resolves fine — it is the form the script's own header documents. The **only** reason an earlier `set-env` would have failed is the missing `SECRET_TELEGRAM_CLAUDE_BOT_SECRET` mapping, which #10674 adds. I will pass the suffix anyway (both work), but nobody should go looking for a second bug that is not there.

## 🆕 PR #10683 — the three ping classes were ALSO unreachable

Same family, found while planning the class tests. `src/runtime/claude_ping.py` defines `decision`/`state_change`/`lifecycle`, and `send_ping.py` has taken `--kind`/`--why` since #10669 — but **`send_ping_action.sh` passed neither**, so the `send-ping` action could only ever fire the passthrough shape. The classes were implemented, documented, and unreachable from the only path a session can dispatch.

#10683 adds optional `kind:`/`why:`/`unproven:`. **Passthrough stays the DEFAULT** — that path carries the *operator's own words*, and Format B is a house style for machine-generated events; forcing a human sentence into "headline / why" would rewrite what someone chose to say. The no-kind argv is pinned as an **exact list** in the tests. An invalid `kind` is a **hard error, never a degrade to passthrough** (it selects the format AND the limiter, so a silent degrade would report success for a ping nobody got the shape of). A **withheld** ping is audited as `withheld`, never `ok` — the limiter exits 0 and queues nothing.

Verified locally: 9/9 new tests, 29/29 in `tests/ops/test_system_actions_workflow.py`, `canonical-doc-coherence` clean, `run_guards.py` **PASS 41 · FAIL 3** where all 3 failures are absent sandbox tooling (2× `pytest`, `lint-imports` exit **127**).

## ⚠️ TWO FINDINGS ON THE 06:20 DIGEST, both before any of this can be called done

**1. It is no longer 06:20 daily.** `work-digest.yml`'s cron is **`20 2,6,10,14,18,22 * * *`** — every 4 hours. 06:20 survives as one slot (deliberately: it lands after probes 05:20 and due-list 05:50). If the operator is expecting "my morning report", they will instead get **six a day**. That may be exactly what was asked for on 2026-09-01 — but it is not what "the 06:20 morning report" describes, and someone should confirm which was meant.

**2. The digest does not SEND. It queues** — `docs/claude/pending-pings.jsonl` → the VM's `notify_on_pull.py` on the next `ict-git-sync` pull → `send_ping.enqueue(target="claude")` → `pending_claude_pings/` → **the exact drain #10674 fixes.** So the answer to "does the digest reach the Claude channel" is **yes, once #10674 merges AND the token reaches the VM** — the chain terminates at the changed line. Verified end to end by reading each hop; `ict-git-sync.timer` is `active` on the VM.

⚠️ **And it has never run.** `pending-pings.jsonl` has **5 commits in its entire history, newest 2026-08-01**; no `automation/work-digest-*` branch exists on origin. `work-digest.yml` landed at **20:11:05Z today**, so its first cron window is **22:20Z — which has not arrived yet.** That is the third state the workflow's own header names: not "fired and worked", not "fired and failed", but *not yet due*. I will not report it either way before then.

**I cannot read Actions run history from this session** — `api.github.com` returns 403 through the sandbox proxy and no Actions MCP tool is in my toolset. So I am watching the **commit trail** instead (a digest run must land a `pending-pings.jsonl` commit), which is a real observable and not a substitute for the run list. Stating the limit rather than inferring a clean negative.

## Blocked on you

(b) set-env, (c) the separation proof, and (d) the three-class test all need **#10674 on `main`** — the `issues.opened` dispatch runs the workflow from the default branch, so the secret mapping has to be there. Standing by; I hold no merge slot.

---
_Generated by [Claude Code](https://claude.ai/code)_
