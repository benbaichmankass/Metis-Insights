# The decision-drain Routine — the one hop the repo cannot build

This is **step 1 of `docs/design/decision-push-back-DESIGN.md` § 5**, and it is
the only part of mechanism B that cannot live in this repository: a Routine is
created from [claude.ai/code/routines](https://claude.ai/code/routines) or with
`/schedule` in an interactive CLI session. **Nothing in a workflow, a script, or
a session's repo access can create one.** The prompt is committed here so it is
not retyped from a chat message and does not drift from the tooling it drives.

> ⚠️ Until this Routine exists, `scripts/ops/check_drain_liveness.py` grades
> **`never_ran`** and exits non-zero. That is the CORRECT reading, not a fault —
> see `OI-20260902-DECISION-DRAIN-ROUTINE-DOES-NOT-EXIST-AND-NOTHING-HAS-EVER-DRAINED`.

## Settings

| field | value |
|---|---|
| **Trigger** | **Schedule**, hourly (the platform minimum) |
| **Fresh session per fire** | **yes** (`create_new_session_on_fire=true`) |
| **Repository** | `benbaichmankass/Metis-Insights` |
| **Connectors** | keep the Claude Code Remote connector (it provides `create_trigger` / `fire_trigger`). Remove any the drain does not need. |

⚠️ **It must have a SCHEDULE trigger.** A Routine created poke-only has no
cadence and will never fire on its own — and from the outside that is
indistinguishable from no Routine at all (the watcher can only report
`never_ran`). This is the exact failure mode the whole watcher exists for.

## Prompt

```text
You are the decision drain. Deliver committed operator decisions back to the
sessions that asked them. This is a one-way push; nothing replies to you.

1. Read the queue:

       python3 scripts/ops/push_decisions_back.py --queue --json

   Each entry carries `objectId`, `requestId`, `sessionId`, and `message`.

2. For EACH entry, deliver it to the asking session:

     a. create_trigger with persistent_session_id=<sessionId>, and BOTH
        cron_expression and run_once_at OMITTED (a poke-only Routine), whose
        prompt is the entry's `message` VERBATIM.
     b. fire_trigger on the trigger id you just created.

   ⚠️ Send `message` exactly as given. Do NOT summarise it and do NOT replace it
   with a link: the woken turn has no mcp__* tools, so a message telling it to
   go read a PR or a job log strands it. The answer is already quoted in full.

3. Record the outcome for each entry:

       python3 scripts/ops/push_decisions_back.py \
         --record <objectId> --request <requestId> \
         --state <pushed|session_gone|unknown> --detail "<trigger id or reason>"

   `pushed`        the fire was accepted.
   `session_gone`  the platform positively said that session cannot receive.
   `unknown`       ANYTHING ELSE, and the default. This writes no marker, so
                   the entry is retried next hour.

   ⚠️ Never report `session_gone` on a failure you cannot attribute. That state
   writes a marker, the marker stops all further attempts, and you would
   permanently strand an answer for a session that was alive. When in doubt it
   is `unknown`.

4. ALWAYS record the run, even when the queue was empty:

       python3 scripts/ops/push_decisions_back.py --receipt

   This is not busywork. Most runs will have nothing to do, and without the
   receipt "nothing needed pushing" and "the drain is dead" are
   indistinguishable — which is the failure this drain is watched for.

5. Commit and push your changes on a `claude/**` branch and open a PR through
   `automation/pr-requests/` (the pr-opener relay). Both the push markers and
   the receipt must reach `main` to count.

Do not answer any decision yourself. Do not edit an `answer:` block. You
deliver what the operator already decided, and nothing else.
```

## How to tell it is working

- `python3 scripts/ops/check_drain_liveness.py` → `fresh`.
- ⚠️ That says the drain **ran**. It does not say a delivery succeeded, and it
  does not say the woken session acted. Those are stages (3) and (4) of the
  OPEN-ITEMS row's `clears_when`, and no probe can reach them — the evidence is
  an asking session observed idle before and running after, doing something
  attributable to the pushed answer.
