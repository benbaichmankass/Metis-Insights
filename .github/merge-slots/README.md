# `.github/merge-slots/` — the conflict-free R13 merge-slot claim

`check_pr_landing.py` **R13** requires a branch arming the landing route to carry
an **attributable, timestamped claim**. Arming is not a request to merge — it IS
the merge (`claude-pr-automerge.yml` enables auto-merge and GitHub lands the PR on
green with no further act by anybody), and the `PreToolUse` slot guard is
structurally blind to that route, so R13 is where the claim gets made.

**Write one with the tool, never by hand:**

```
python3 scripts/ops/claim_merge_slot.py --branch-claim \
  --branch claude/<your-branch> --held-by <session-id> --purpose "<what and which PR>"
```

## Why this exists beside `docs/claude/session-board.json::merge_slot`

The shared field is **one field in one file that every armed branch must
overwrite**, so every armed branch conflicts with `main` the moment anything else
claims it.

**Measured** — population: the last **40** commits to `main` touching
`session-board.json` (2026-09-11T23:12Z → 2026-09-12T09:30Z). **39 of 40 MOVED
`merge_slot.branch`.** Inter-move gaps (n=38): **min 3.3m · median 11.5m · max
61.8m**, with **26 of 38 under 16 minutes**. Resolving the conflict pushes a new
head and restarts CI, so **resolving faster does not help** — the clock restarts
each time.

Observed the same morning on one session's six PRs: **all four ARMED ones went
`dirty` together, and the only two that stayed CLEAN were the two declaring
`landing: "hold"`** — which write no claim at all. Two controls, and the claim is
the one difference between them.

⚠️ **This repo already fixed this exact class once, one layer over.**
`.github/pr-automerge-request` was a single shared file every requesting PR had to
modify; it collided by construction, hit live on 2026-08-21 (#10083 merged, #10086
came back `dirty` with that file as the **only** conflicted path), and was split
into `.github/pr-automerge-requests/<slug>.txt`. The claim is the same defect in
the same route, and takes the same remedy.

## ⚠️ Nothing is given up, because R13 never serialized

R13's own docstring says so: a committed claim reaches no other session until the
branch merges, so two branches can each arm, each write a valid claim, and never
see one another. The shared field bought **conflict** and no exclusion.
Concurrent-merge safety rests on branch-protection required checks, where it
already rested.

What R13 actually buys — **attribution and timestamping** — is preserved exactly,
and strengthened: the claim now names its branch in the **path** as well as in the
**body**.

⚠️ **The filename is not the claim.** A file copied from another branch would sit
at the wrong path, but could equally be renamed — so the branch is written inside
too, and R13 requires the two to agree.

## The legacy route still works, unchanged

`docs/claude/session-board.json::merge_slot` still satisfies R13, byte-for-byte.
`.github/actions/commit-to-main` and the 27 workflows behind it keep writing it
and keep passing. This only **adds** a way to satisfy R13; it removes none.

The shared field also remains the **durable mirror** of the live coordination
board claim (`docs/claude/coordination-board.md`). Posting the
`🔒 MERGE SLOT CLAIM` comment on the board is unchanged and is still the
authoritative live claim — this file is about R13, not about coordination.
