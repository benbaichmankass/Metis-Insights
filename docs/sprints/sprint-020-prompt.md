> ✅ **S-041 STATUS NOTE (2026-05-06 — verify-before-trusting-done sweep):**
> Sprint **completed**. Closing checkpoint: CP-2026-04-30-17. Root cause identified
> (deploy script `PRE_SYNC_HEAD` no-op early-out) and fixed; BUG-018 and BUG-022
> fully closed. Recursive verification: CP-2026-04-30-17 commit itself fired a
> Telegram ping confirming end-to-end green. Under workplan M0..M10, this sprint's
> work maps to **M1** (Comms infrastructure). No further action required.

# Sprint S-020 — fix the auto-ping (manual /ping_test works, auto path doesn't)

**Mode:** debug-and-fix sprint. Probably 30–60 min. Operator-confirmed
last session that:

- ✅ `/ping_test foo` in Telegram → `📨 Queued ...` → ~5 s later
  `ℹ️ ping_test from /ping_test: foo` arrives.
- ❌ Auto-pings on `CHECKPOINT_LOG.md` commits do NOT arrive (CP-15
  was a deliberate test commit that should have fired one).

So **the bot drain loop works end-to-end**, and **`scripts/send_ping.py`
+ inbox + `bot.send_message` chain is verified**. The bug is somewhere
**upstream of `send_ping.enqueue` in the auto path** — i.e. between
`ict-git-sync.timer` firing and a JSON file appearing in
`runtime_logs/pending_pings/` on the VM.

## 1. Goal

`/ping_test` works, AND every commit that touches
`docs/claude/checkpoints/CHECKPOINT_LOG.md` produces a Telegram ping
within ~5 min of merge to `main`. Pin both with a regression test.

## 2. Dependencies

- Operator at a device that can `git pull`, run `journalctl`, and
  receive Telegram pings. SSH to VM not strictly needed (everything
  diagnostic is read-only).
- All S-016/S-017/S-018/S-019 PRs already on main (#213 #214 #215
  #216 #217 #218 #219 #220 #221 #222 #223 #224 #225 #226 #227).

## 3. First action — paste-ready diagnostic commands

On the VM (or via `/vm` Telegram dispatch — Tier 1):

```bash
# A. Has ict-git-sync.timer been firing?
sudo systemctl list-timers ict-git-sync.timer --no-pager
sudo journalctl -u ict-git-sync.timer -n 30 --no-pager

# B. What did the last 3 deploy script runs do?
sudo journalctl -u ict-git-sync.service -n 200 --no-pager | tail -100

# C. Is there an enqueue stuck in the inbox?
ls -la /home/ubuntu/ict-trading-bot/runtime_logs/pending_pings/ 2>/dev/null

# D. Manually invoke notify_on_pull --dry-run on a known-checkpointed range:
cd /home/ubuntu/ict-trading-bot
PYTHONPATH=. python3 scripts/notify_on_pull.py \
    --pre $(git rev-parse HEAD~5) --post HEAD --dry-run

# E. If D shows it WOULD enqueue, force a real enqueue:
PYTHONPATH=. python3 scripts/notify_on_pull.py \
    --pre $(git rev-parse HEAD~5) --post HEAD
ls /home/ubuntu/ict-trading-bot/runtime_logs/pending_pings/
```

The output of A through E nails the root cause. Specifically:

- If A shows the timer hasn't fired recently → systemd-side bug.
- If B shows deploy script runs but never logs `>>> Sending Telegram
  pings for new commits...` → deploy script not reaching the ping
  step (probably the `if "${SYSTEMCTL[@]}" list-units 'claude-vm-
  runner@*.service'...` early-return is firing).
- If B logs the ping line but D shows zero pings would queue →
  bug is in `_diff_touched_checkpoint_log` or `_latest_cp_entry`
  parsing.
- If C shows files stuck → the bot drain isn't picking them up
  (unlikely since `/ping_test` worked, but possible if the inbox
  path differs between writer and reader).
- If D dry-runs say "would queue 1 ping" but E doesn't actually
  produce a file → `send_ping.enqueue` failure on the VM
  filesystem (permissions? read-only mount?).

## 4. Most-likely root causes (ranked)

1. **Timer's deploy script returned early on a no-op pull.** The
   `PRE_SYNC_HEAD == POST_SYNC_HEAD` early-out in
   `scripts/deploy_pull_restart.sh` returns BEFORE the
   `notify_on_pull` step. So if the operator manually `git
   reset --hard origin/main` mid-debug (which we did several
   times), no ping fires for the missed commits.
2. **`claude-vm-runner@*.service` was active** during the relevant
   tick → deploy script deferred the restart AND skipped its tail
   steps. Verify in B.
3. **`ict-git-sync.service` ran the OLD deploy_pull_restart.sh** the
   first time — the EnvironmentFile fix only loaded on the SECOND
   tick after #225 landed. By then there was nothing new to ping.
4. **runtime_logs/pending_pings/ permission mismatch.** If the bot
   creates the dir as a different uid/gid than the deploy script,
   files written by the deploy script may not be drainable. Check
   `ls -la` ownership.

## 5. Checkpoints

| # | Checkpoint | What | Risk | Time | Gates |
|---|---|---|---|---|---|
| T0 | Run § 3 diagnostic, classify root cause | docs | 5 m | T1 |
| T1 | Implement targeted fix per § 4 ranked guesses | infra | 15 m | T2 |
| T2 | **Add `notify_on_pull` integration test** that fires `enqueue` against a real on-disk fixture and asserts a JSON file appears in the inbox dir. The unit tests stub `enqueue`; we lacked one that exercises the actual file-write path | infra (tests) | 15 m | T3 |
| T3 | Force-trigger via `runtime_flags/auto_ping_test.flag` if a recent commit didn't naturally create one | infra | 5 m | T4 |
| T4 | Operator confirms ping arrived in Telegram | operator | 5 m | T5 |
| T5 | Final checkpoint CP-…-S020-COMPLETE (which itself triggers the auto-ping → recursive verification) | docs | 5 m | none |

## 6. Hard guardrails

1. Don't touch `src/runtime/orders.py` or any strategy code — same
   standing rules.
2. Don't break `/ping_test` or the bot drain loop — those are
   verified working. Add to them, don't replace.
3. The fix MUST preserve the autonomous-trading rule: no
   per-trade confirmation, no human-in-the-loop for the ping path.

## 7. Hand-off from S-019 (verbatim operator state)

> /ping_test
> 📨 Queued test-1777592474.json. Should fire within 5s.
> ℹ️ ping_test from /ping_test: ping test
>
> [...] There was no auto ping, but the command showed up

Bot inbox + drain + send chain are green. Auto-ping (commit →
file → drain) is the failing leg. The diagnostic commands in § 3
will localise it to one of the four ranked causes in § 4.

## 8. Success criteria

- A manual `python3 scripts/notify_on_pull.py --pre X --post Y` on
  the VM produces a JSON file in `runtime_logs/pending_pings/` and
  the operator gets a Telegram ping within 5 s.
- A new commit that touches `CHECKPOINT_LOG.md` produces an
  automatic ping within the next git-sync tick (≤ 5 min).
- `tests/test_notify_on_pull.py` has a new integration test that
  pins the file-write path (not just stubbed `enqueue`).
- BUG-018 in `docs/claude/bug-log.md` flipped to fully resolved
  with the PR link and the actual root cause documented.

## 9. Cross-references

- `scripts/notify_on_pull.py` — the producer that should be
  enqueueing on every CHECKPOINT_LOG-touching pull.
- `scripts/send_ping.py` — the verified-working enqueue helper.
- `scripts/deploy_pull_restart.sh` — invokes `notify_on_pull` after
  HEAD advance.
- `src/bot/telegram_query_bot.py::_drain_pending_pings` — the
  consumer that's verified working via `/ping_test`.
- `docs/claude/bug-log.md` BUG-018 — Telegram pings.
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` — start by reading
  the most recent CP entry.
