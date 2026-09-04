# The manager-wake Routine — the one hop that must originate outside the manager

This is the deployed half of `WO-20260904-MANAGER-IDLE-IS-UNBOUNDED-AND-NOTHING-WAKES-IT`.
The repo half is [`scripts/ops/manager_wake.py`](../../../scripts/ops/manager_wake.py)
(assess / brief / receipt) and [`scripts/ops/check_wake_liveness.py`](../../../scripts/ops/check_wake_liveness.py)
(is the wake itself still firing).

**Why any of it is outside the repo at all.** The lease and R7 are both read by
the manager, or by a commit the manager makes. A silent manager makes neither.
So the detector cannot live inside the manager session — it has to originate
outside it, and a Routine is the only tool this account has that does. The
prompt is committed here so it is not retyped from a chat message and does not
drift from the tooling it drives, exactly as
[`decision-drain-routine-prompt.md`](decision-drain-routine-prompt.md) is.

> ⚠️ Until this Routine has fired at least once,
> `python3 scripts/ops/check_wake_liveness.py` grades **`never_ran`** and exits
> non-zero. That is the CORRECT reading, not a fault.

## Settings

| field | value |
|---|---|
| **Trigger** | **Schedule**, hourly (the platform minimum — see the bound below) |
| **Fresh session per fire** | **yes** (`create_new_session_on_fire=true`) |
| **Repository** | `benbaichmankass/Metis-Insights` |
| **Connectors** | the Claude Code Remote connector — it provides `create_trigger` / `fire_trigger`, which is how the poke is delivered |

⚠️ **It must have a SCHEDULE trigger.** A Routine created poke-only has no
cadence and never fires on its own — and from outside that is indistinguishable
from no Routine at all. That is the exact failure this mechanism exists to end,
so building it that way would reproduce the bug in the fix.

⚠️ **It must be fresh-session-per-fire, NOT bound to a session.** A Routine bound
to a `persistent_session_id` points at one session forever; managers change, and
the moment a new manager takes the lease a bound wake is aimed at a dead session
— inert and covered-looking. The target is derived from the lease on every fire.

## The bound this actually delivers

⚠️ **~30–90 minutes, NOT 30.** The silence threshold is 30 minutes (the
operator's bar, and the lease's own `HEARTBEAT_TARGET_MINUTES`), but the Routine
platform minimum is hourly, so a manager that goes quiet just after a fire waits
until the next one. Against the measured 720-minute gap that is roughly an
8–24× improvement. **It does not meet the stated 30-minute bar and must not be
reported as though it does.**

## Prompt

```text
You are the manager wake. A manager that stops taking turns is not detected by
anything in this repo -- the lease expires into nobody and R7 grades only
commits that never come. You are the mechanism that notices. You do not manage,
and you do not do the manager's work.

1. Assess, from the committed lease:

       python3 scripts/ops/manager_wake.py --assess --json

   Four states, and they are not interchangeable:
     active       a holder beat inside the threshold. Nothing to do.
     silent       a holder has NOT beaten past the threshold. WAKE IT (step 2).
     no_manager   nobody holds the lease. There is nobody to wake. Do NOT treat
                  this as healthy -- management has stopped entirely. Record it
                  and say so in your PR body; it is the reaper's case, not
                  yours, and the reaper does not exist yet.
     unreadable   the lease did not parse. You did not look. Record it.

2. ONLY when the state is `silent`, poke the holder:

     a. python3 scripts/ops/manager_wake.py --brief    -> capture the full text
     b. create_trigger with persistent_session_id=<the assess `wake_session`>,
        and BOTH cron_expression and run_once_at OMITTED (a poke-only Routine),
        whose prompt is that brief VERBATIM.
     c. fire_trigger on the trigger id you just created.

   ⚠️ Send the brief in full. Do NOT summarise it and do NOT replace it with a
   link telling the manager to go read the checklist: the whole point is that a
   manager woken with no state re-derives everything, and the woken turn may
   have no mcp__* tools to go fetch anything with. The state is already in the
   text.

   ⚠️ A poke that fails is NOT `session_gone` unless the platform positively
   said so. Anything else is `failed` with the error in --detail. Do not guess
   that a manager is dead; guessing that wrongly is how a live manager stops
   being woken.

3. ALWAYS record the fire, including the ones that did nothing:

       python3 scripts/ops/manager_wake.py --record \
         --state <active|silent|no_manager|unreadable> \
         --outcome <poked|no_action|failed> \
         --wake-session <id or omit> --silent-minutes <n or omit> \
         --detail "<trigger id, or the reason>"

   This is not busywork. Most fires will do nothing, and without the receipt
   "nothing needed waking" and "the wake is dead" are indistinguishable -- which
   is the exact failure class this mechanism was built for.

4. Land the receipt. Commit on the branch `automation/manager-wake`, push it,
   and open a PR through `automation/pr-requests/` (the pr-opener relay) ONLY IF
   no PR is already open for that branch -- push onto the existing one
   otherwise. Arm auto-merge with
   `.github/pr-automerge-requests/manager-wake.txt` so it lands on green.

   ⚠️ ONE open PR at a time, deliberately. An hourly PR would be 24 a day, and
   a branch that accumulates and never lands is the measured MI-62 failure
   (17 of 17 automation/* branches unreachable from main over ten weeks). The
   receipt has to REACH main or the liveness grader reads `never_ran` forever
   while the wake is in fact firing.

Do not manage. Do not merge anything, do not answer a question on the manager's
behalf, do not take a checklist item. You assess, you poke, you record. That is
the whole job.
```

## How to tell it is working

- `python3 scripts/ops/check_wake_liveness.py` → `fresh`.
- ⚠️ That says the wake **fired**. It does not say a poke was delivered, and it
  does not say the woken manager **acted**. Those are separate facts and the
  done-condition is the last one: a manager session observed idle before and
  running after via `get_session`, having acted on something that was waiting.
  Record which session and what it did.

## What this is NOT

- **Not the reaper.** A session that DIES cannot wake itself, and a lease
  expiring into nobody is that case — `assess` reports it as `no_manager` and
  stops there. Filed as `BL-20260904-NOTHING-REAPS-A-DEAD-MANAGER-OR-CLAIMS-AN-EXPIRED-LEASE`.
- **Not a GitHub cron.** Measured here: `work-digest` fired 5× against 24
  declared; `probes` fired ~4h50m late and once rather than daily; #10845 moved
  the digest off cron for this reason.
- **Not a minted credential.** Operator, 2026-09-02: "no minted tokens, ever."
- **Not another reminder.** "Check in every 5–10 minutes" already exists, was
  read at session start, and produced a twelve-hour gap.
