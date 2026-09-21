---
name: close-out
description: How a session knows it is DONE. Read this before you stop — whether you finished, ran out of budget, or are handing off. Defines the one question, the seven checks, what `done` is not, and why stopping early is a handoff rather than a completion.
---

> **Doc status:** `live` · category `instruction` · last verified `2026-09-21` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md)

# Closing out — how a session knows it is done

> Adopted 2026-09-21 on operator direction: *"I want to make sure it's clear —
> guidelines for a session to follow for closing out, so that a session can know
> when it's done, and not just drop things in the middle because it's missing a
> clear instruction."*

⚠️ **THIS IS NOT A RESURRECTION OF THE RETIRED `session-handoff` /
`session-receipt` SKILLS**, and the difference is the whole point. Those wrote
*observations into registers* — a receipt, a session row, a coordination post —
and the registers are archived. **This writes nothing new.** It is a completion
test against the surfaces that already exist: the checklist, the pipeline, and
git. If following this makes you want to create a register, you have
misread it.

---

## The one question

> **Could this session end right now — without warning — and nothing be lost,
> and nothing be silently dropped?**

If the answer is no, you are not done. That is the entire contract. The seven
checks below are how you answer it honestly instead of optimistically.

⚠️ **"I have stopped working" and "I am done" are different states**, and
conflating them is the drop. A session that ends mid-task having *said* so is
fine. A session that ends mid-task silently is the failure this exists for.

---

## The seven checks

Run all of them, every time, including when you are confident.

### 1. Nothing exists only in this container

Working tree clean · branch pushed · PR open.

⚠️ **A patch file in the scratchpad is NOT saved work.** The scratchpad
directory dies with the container, and so does an uncommitted working tree and
an unpushed commit. This was violated in the very session that wrote this rule:
finished, tested code sat as a `.patch` in `/tmp` for ~40 minutes to avoid
restarting a CI cycle. The trade is real but it is the wrong one — **push the
branch and let CI sort itself out.** A wasted CI cycle costs minutes; a dead
container costs the work.

### 2. Every PR you opened is merged — or you have said, in one place, why not

- **Red CI on a PR you opened is not done.** Fix it, or establish it is not
  yours and say so in one comment on that PR.
- **An open PR you own is not done.** Merge it or state the blocker.
- A merged PR is the only form of work that cannot be lost.

### 3. Every row you touched has a TRUE state

- No row says `in_flight` if nobody is working it. **Check this explicitly** —
  a stale `in_flight` tells the next session a lane is live when it is not, and
  it will route around work that is actually free.
- `landed_unproven` ≠ `done`. If you merged it but did not observe the effect,
  it is `landed_unproven`, **and the row must name the specific observation that
  would close it** — not "verify later". Name the endpoint, the command, the
  file, the date.
- `killed` needs a reason. So does `dropped`.

### 4. Every finding is fixed, filed, or flagged — and *filed* means the pipeline

A finding is filed when it is in `docs/claude/work/PIPELINE.jsonl` with an
`origin.rerun` and a `due_when`, or when it is a checklist row.

⚠️ **These are NOT filed**, however carefully written:

| not filed | why |
|---|---|
| a chat message | the conversation ends with the session |
| a PR comment | nothing reads PR comments on a schedule |
| a code comment | it is documentation, not a queue |
| a new `.md` memo | this repo has 404 of those and one ever actioned |

### 5. Every claim you could not verify is withdrawn or labelled

Go back over what you asserted this session. Anything you inferred rather than
checked gets corrected in place or marked as unverified — **in the artifact,
not just in chat.** A wrong claim that merged is now the repo's position.

⚠️ This check catches the most, and it catches it late. Expect to find
something.

### 6. Push, then answer

The Workflow page is the artifact the operator keeps; your message is not.
Answering first hands them a chat reply and a page that disagrees with it.

### 7. Say what the next session picks up first, and why

One line. Not a list of everything outstanding — **the first thing**, and the
reason it is first. A successor that has to re-derive the ordering will pick
differently, and the ordering usually encodes something you learned.

---

## Stopping early is legitimate. It is a HANDOFF, not a completion.

Running out of budget, context or time is a normal way for a session to end.
**It is not a reason to skip the seven checks — it is the reason they exist.**

When you stop early, say all three of these plainly:

1. **What is done and merged.**
2. **What is in flight and exactly where it stands** — branch, PR number, CI
   state.
3. **What you were about to do next.**

⚠️ **Do the checks BEFORE you are out of room, not after.** A session that
budgets nothing for close-out will discover at the boundary that it cannot
afford to land what it built. Treat close-out as part of the work, not as
something that happens after it.

---

## What "done" is NOT

| claim | what it actually is |
|---|---|
| *"I ran out of budget"* | a handoff — say where things stand |
| *"CI is still running"* | not done. Wait, or say what is pending and where |
| *"I filed it"* | **filed where?** See check 4 |
| *"I mentioned it"* | a chat message is not a disposition |
| *"It was already broken"* | not a disposition. Fix, file, or flag |
| *"The code is written"* | written ≠ pushed ≠ merged ≠ observed |
| *"It's on the branch"* | an unpushed branch dies with the container |
| *"The next session can pick it up"* | only if check 4 and check 7 are done |

---

## Merged ≠ deployed ≠ observed

The repo's most expensive recurring mistake, and close-out is where it usually
gets made. State **which one** you established:

- **Merged** — it is on `main`. You can prove this with a sha.
- **Deployed** — the running system has it. `ict-git-sync` pulls `main` every
  ~5 min; a service may need a reload.
- **Observed** — you looked at the running system and saw the effect.

**Re-reading the file you just changed proves none of the three.**
