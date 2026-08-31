---
name: backlog-drain
description: A DEDICATED session whose only job is CLOSING backlog rows — not reviewing, not filing. Use when the operator says "/backlog-drain", "drain the backlog", "work the backlog down", or when a review's burn-down shows the pile growing. Owns the selection strategy (class-first), the evidence bar for a close, and the burn-down accounting. NOT /system-review (which reviews and files); this is the counterweight that empties what reviews fill.
---

# /backlog-drain — a session that CLOSES rows

Operator, 2026-08-31: *"maybe what we need is also have some dedicated backlog
draining sessions in order to make sure that we're getting things done correctly
and actually cutting into the backlog ... it's okay for you to have added more
things than you resolved, but what we're missing here seems to be workflow."*

**Filing more than you close is NOT a failure** — a review that finds real
defects SHOULD file them. What was missing is a session type on the other side
of the ledger. This is it.

## The one rule

**A row leaves this session CLOSED, or with a concrete reason it cannot be.**
"Re-validated, still open" is what the last several reviews produced and it
moves nothing. If a row cannot be closed, the output is either a fix, or a named
blocker, or a merge into the class row that supersedes it.

## Selection — class first, never newest-first

1. **Fix CLASSES, not rows.** `/system-review`'s `backlog_classes` names them
   with >=2 verified members and a structural fix. One change that retires a
   class closes every member together — the ONLY move whose arithmetic beats the
   filing rate. Measured 2026-08-31: opened/closed ran 43/8, 231/94, 249/175,
   536/326, so one-at-a-time closing has never once caught up.
2. **Then supersession.** Rows that restate one another collapse into the
   sharpest one; the rest close as `superseded` citing it. This is bookkeeping,
   not progress — do it, but do not count it as draining.
3. **Then the shortlist**, `scripts/ops/backlog_drain_candidates.py` — read its
   limits below before trusting it.
4. **Then oldest-first among `severity: high`.** Age is the signal that a row is
   never going to be picked up incidentally.

## The evidence bar

Identical to a review's: **`done` requires evidence, and shipped is not
exercised.** A criterion satisfied by "the code now exists" is satisfied only if
someone RAN it. Two rows closed on 2026-08-31 were closed by *calling the live
route*, not by reading the diff that added it.

State plainly which clauses of a criterion you verified and which you inferred —
a close carrying one inferred clause is fine and honest; a close that hides one
is the "reported done, never verified" state the backlog exists to prevent.

## Automation does NOT drain this backlog — measured, not assumed

`scripts/ops/backlog_drain_candidates.py` shortlists rows whose criteria may
already be met. **Its first version claimed 108 of 542 and was almost entirely
wrong.** It matched bare identifiers and test names in criteria prose: a row
about a stray DB matched because the word `signals` appears somewhere in the
tree; a row needing a live Bybit verification matched because `tp_order_id`
exists in code. A six-row hand check found ~none closable, and
`identifier_present` alone carried 75 of the 108.

After cutting the noisy signals and making the path signal refuse on a shallow
clone (CI and every sandbox session have one — `git log --diff-filter=A` then
reports the shallow boundary, so an ancient doc reads as "added last week"), the
shortlist is **3**, and **532 of 542 rows (98%) are `not_checkable`**.

**That 98% IS the finding.** Backlog criteria overwhelmingly demand a live
measurement or a judgement, so no tool will drain this pile. Human-driven,
class-first sessions are the mechanism. Use the shortlist as a cheap first pass,
never as the plan.

## The process fix that compounds

**Write criteria a future session can check mechanically**, wherever the defect
allows it: name the diag route, the test id, the guard, the exact field. The 3
rows the shortlist does find are precisely the ones whose author named a
concrete surface. Every row filed that way is a row that can be closed cheaply
later; every row filed as prose needs a full session's attention forever. This
is the highest-leverage change available and it costs nothing at filing time.

## Accounting — report burn-down, not activity

End with the numbers, via
`scripts/ops/system_review_checklist.py::backlog_burndown()`:

    rows CLOSED this session / rows FILED this session / net
    open count before and after
    which CLASS (if any) was retired, and how many members it took with it

A session that touched forty rows and closed none has moved nothing, and the
report must make that visible rather than describe the touching.

## What this session does NOT do

Not a review — it does not hunt for new defects. If it finds one while fixing,
file it (that is correct, and it is why net-positive is acceptable) and carry on
closing. Tier-3 changes are proposed, never merged, exactly as in a review.
