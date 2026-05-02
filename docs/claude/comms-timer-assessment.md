# Comms timer assessment — is 1-minute polling safe?

**Status:** assessment / recommendation. No timer change applied yet — this
doc is consulted by the operator before flipping the systemd unit.

**Author:** Sprint S-027 (telegram-comms-infrastructure foundation, PR 1).

---

## TL;DR

**Recommendation:** keep the existing **5-minute** `ict-git-sync.timer`
default. Add a **separate 1-minute comms-only poll** *inside the
Telegram bot process* once PR 2 lands, rather than tightening the
git-sync timer.

The 5-minute git-sync is the slowest step in the round trip
(Claude commit → operator menu → Claude reads answer ≈ 10–15 min worst
case). Cutting that to 1 minute would speed it up, **but** it does so by
running every part of the deploy pipeline 5× more often — `pip install
-r requirements.txt` checks, service-restart bookkeeping, ping fanout —
just to deliver a comms artifact that doesn't need any of that. A
dedicated comms poll inside `telegram_query_bot.py` reads
`comms/requests/` directly off the working tree and only needs to run
that one tiny check.

If the operator *really* wants the simpler approach (just turn the
existing timer up to 1 min), §4 lists the safeguards needed.

---

## 1. What's polling today?

Two unrelated polls already run on the VM (per
[`docs/claude/repo-map.md`](repo-map.md) and
[`docs/claude/telegram-pings.md`](telegram-pings.md)):

| Unit | Interval | Action |
|---|---|---|
| `ict-git-sync.timer` | every 5 min after boot+2 min, ±30 s jitter | `git fetch && git reset --hard origin/main`, conditional `pip install`, restart `ict-trader-live` + `ict-telegram-bot` if HEAD advanced, fire `notify_on_pull.py` |
| `ict-heartbeat.timer` | daily at 13:00 UTC | `scripts/daily_heartbeat.py` ping |
| **(in-process)** hourly summary | top of every UTC hour | inside `telegram_query_bot.py` (no external timer) |

The git-sync timer is the only loop relevant to comms delivery: it's
the channel between a Claude commit and the bot seeing the new artifact.

## 2. Failure modes if we just turn the timer up to 1 min

### 2.1 Overlapping runs

`OnUnitActiveSec=1min` with a deploy script that takes ~5–30 s on a
no-op tick is **fine** — systemd queues the next activation after the
previous run exits — but on a tick that hits `pip install` (any
requirements.txt change) the script can run 60–120 s. The timer will
not fire a second instance while the first is active (systemd guarantee
on `Type=oneshot`), but the *next* tick will fire immediately on
completion, which under bursty commit traffic can produce
back-to-back service restarts. Mitigation: keep the conditional
`pip install` gate (already present) and add an explicit
`StartLimitInterval=` if drift appears.

### 2.2 git pull conflicts / half-applied state

Not a real risk on the VM specifically — the deploy script does
`git fetch && git reset --hard origin/main` (no merge, no rebase, no
working-tree integration). At 1 min frequency the same hard reset
just runs more often. The only edge case is a ref-update
race during a force-push to `origin/main`, which is also a 5-min risk
today. **No new failure mode at 1 min.**

### 2.3 Service-restart churn

This is the real cost. Today, every commit that lands restarts the
Telegram bot **and** the live trader. At 1-min polling, any 5-commit
burst that today restarts 1× would restart up to 5× (one per tick that
sees a new HEAD). Restart cost: ~3–5 s of trader-loop downtime per
restart. The kill-switch flag persists across restarts (it's a file
under `runtime_flags/`), so this is a perf nuisance, not a correctness
risk — but it's measurable. The deploy script only restarts when HEAD
advances, so quiet periods are free.

### 2.4 Duplicate Telegram sends from `notify_on_pull.py`

Each `deploy_pull_restart.sh` run drains `pending-pings.jsonl` and
fires checkpoint pings. The drain is idempotent (atomic file truncate
after read), but the *checkpoint-ping* path uses
`PRE_SYNC_HEAD..POST_SYNC_HEAD` to decide what's new, and that range is
empty on no-op ticks. Risk at 1 min: low — the pre/post HEAD diff
mechanism handles it correctly today, and a tighter loop just exercises
the empty-range fast path more often.

### 2.5 Commit storms from response writeback

This is the **comms-specific risk** that doesn't exist today. When PR 2
adds Telegram → repo writeback, every operator answer becomes a commit
on the VM, pushed to `origin/main`. If the operator answers a
multi-question request in three taps, that's three commits, three
pushes, three triggered git-sync runs (one of which is the bot's *own*
push pulling its *own* changes back). The mitigations are PR-2 design
constraints, not timer constraints:

- **Batch writebacks per request, not per answer.** Buffer answers
  in-memory until either (a) all required questions are answered or
  (b) a 30 s idle timer expires, then commit once.
- **Rebase-aware push retries.** The bot's push must handle the
  inevitable race where a Claude commit lands between the bot's `git
  pull` and `git push`.
- **Skip self-triggered re-pings.** `notify_on_pull.py` already filters
  by commit-message prefix (`[BLOCKED-PM]`, etc.); the response-writeback
  commit message must use a prefix the ping pipeline ignores
  (proposed: `comms(response):` — see comms-architecture § Commit
  prefixes).

These are safeguards for **any** comms writeback, regardless of timer
frequency. They do not become harder at 1 min.

## 3. Why a dedicated comms poll is cleaner

The bot is a long-running Python process that already reads files off
the same working tree the deploy script writes to. Adding an
`asyncio.create_task` loop inside `telegram_query_bot.py` that:

```python
while True:
    for req in RequestStore().list_pending():
        await deliver(req)
    await asyncio.sleep(60)
```

…runs every 60 s with **none** of the deploy overhead. The artifacts
appear in the working tree on every git-sync tick (still 5 min), so
the worst-case latency from Claude commit → operator menu is:

    git-sync 5 min  +  comms poll 1 min  +  Telegram delivery ~1 s
    ≈ 6 min worst case, 1 min best case

…vs. ~10 min worst case today. That's a 40 % improvement without
touching the deploy timer at all.

The reverse direction (operator answer → Claude reads it) is bounded
by the bot's commit-and-push, which fires immediately on answer
completion (see PR 2). So the *answer* leg becomes ~5 s irrespective of
either timer.

## 4. If the operator chooses to drop git-sync to 1 min anyway

The repo change is one line in
[`deploy/ict-git-sync.timer`](../../deploy/ict-git-sync.timer):

```diff
 [Timer]
 OnBootSec=2min
-OnUnitActiveSec=5min
+OnUnitActiveSec=1min
 RandomizedDelaySec=30
 Persistent=true
 Unit=ict-git-sync.service
```

The operator must then, on the VM:

```bash
sudo cp /home/ubuntu/ict-trading-bot/deploy/ict-git-sync.timer \
       /etc/systemd/system/ict-git-sync.timer
sudo systemctl daemon-reload
sudo systemctl restart ict-git-sync.timer
sudo systemctl list-timers | grep ict-git-sync   # confirm next-fire ~60 s
```

Required safeguards before doing this:

1. **Confirm `pip install` is conditional on HEAD advance** —
   `scripts/deploy_pull_restart.sh` already does this (line ~143);
   verify it has not regressed.
2. **Confirm comms writeback uses a non-pinging commit prefix.** This
   is a PR-2 deliverable. Don't drop the timer until PR 2 is merged
   AND the `notify_on_pull.py` filter list includes `comms(response):`.
3. **Add a per-tick log line** so the operator can see overlap behaviour.
   `deploy_pull_restart.sh` already logs `===== DEPLOY STARTED: $(date) =====`
   — at 1 min cadence, `journalctl -u ict-git-sync.service --since '10 min ago'`
   will reveal any stuck runs.
4. **Watch service-restart count for one trading day.** If
   `systemctl status ict-trader-live` shows >20 restarts in 24 h
   without commit activity to match, roll back to 5 min.

## 5. Recommendation

| Option | Latency (Claude→operator) | Risk | Repo work |
|---|---|---|---|
| **A. Keep 5 min, add in-bot 1-min comms poll** (recommended) | ~6 min worst | minimal — no deploy churn | PR 2 includes the asyncio loop |
| B. Drop timer to 1 min | ~2 min worst | medium — restart churn, commit storm risk amplified | one-line + operator VM steps + PR 2 prefix filter |
| C. Status quo | ~10 min worst | none | none |

Option A is the path of least resistance: it gives the operator the
fast-feeling channel without changing any infra. PR 2 should
implement it.

If after a week of A the operator finds the **commit-side** latency
(Claude pushing → VM seeing it) too slow, option B becomes a small
follow-up — but that's a "future operator decision" call, not a PR-1
decision.
