---
name: doc-freshness
description: Session-end (and on-demand) check that the canonical instruction docs do not contradict each other, the code/config on disk, or the changes this session made. Use at the end of every session per docs/CLAUDE-RULES-CANONICAL.md, when the operator says "/doc-freshness" or "check the docs are up to date", or whenever you suspect documentation drift. Fixes Tier-1 doc contradictions in place; logs minor leftovers to the health-review backlog; flags anything needing a code/config change for the operator.
---

# /doc-freshness — keep the instruction corpus internally consistent

This skill protects the single most important property of the rule set: that a
fresh Claude can read it top-to-bottom and never get contradictory
instructions. Run it before you close a session.

It is a **read + reconcile + fix-docs** routine. It never changes code, config,
or live state — when a contradiction can only be resolved by a code/config
change, you log it and tell the operator, you don't fix it here.

## Scope

Always check these (the canonical set, highest precedence first):

1. `CLAUDE.md` (root) — the operating contract + instruction hierarchy.
2. `docs/CLAUDE-RULES-CANONICAL.md` — the canonical rules.
3. `docs/ARCHITECTURE-CANONICAL.md` — architecture + contracts.
4. `ROADMAP.md` — the centralized milestone/sprint record.

Then, scoped to what changed this session:

5. Any `docs/claude/*` page covering a code/config area the session touched.
6. Any skill under `.claude/skills/` the session touched or that references a
   thing the session renamed/removed.
7. The dashboard repo's `CLAUDE.md` pointer (it must defer to this repo, not
   restate rules).

## Procedure

1. **Enumerate the session's changes.** List the files, config fields, and
   behaviours you changed. This is what you reconcile the docs against.
2. **Re-read the canonical set** and check for three failure modes:
   - **Doc-vs-doc:** two docs assert different things about the same rule
     (e.g. one says "one switch", another says "two gates").
   - **Doc-vs-reality:** a doc describes code/config that no longer matches
     what's on disk (verify against the actual file, not memory).
   - **Precedence violation:** a lower-precedence doc states something a
     higher one forbids. The higher one wins; the lower one is the bug.
3. **Resolve each contradiction:**
   - If it's a documentation-only fix (Tier 1), fix it now, in this session,
     so the corpus is consistent before you close.
   - If resolving it requires a code/config change (Tier 2/3), **do not**
     change code here. Record the exact contradiction, the file + line on
     both sides, and the change that would resolve it, and raise it with the
     operator.
4. **Check the structural invariants** (common drift vectors):
   - The **two execution gates** (account `mode:` + strategy `execution:`)
     are described consistently everywhere; nothing still says `mode:` is the
     only gate, and nothing introduces a hidden third gate.
   - The **permission tiers** match across CLAUDE.md and canonical.
   - No re-introduced "banned phrase" lists and no "operator does X manually /
     SSHes / runs X" framing — the model is autonomous Claude + tier-based
     approval.
   - References to renamed or removed things resolve (e.g. `system-actions`,
     not `operator-actions`; no Telegram-ping / Colab-key-rotation workflows
     presented as current).
   - `docs/claude/INDEX.md` lists the files that actually exist; no dangling
     doc links.
5. **Triage minor leftovers.** Anything real but too small to fix now goes
   into the appropriate review backlog as a new `open` item so a future
   review drains it. Don't silently walk past it. Pick the right bin
   (three-way split 2026-05-26):
   - System / pipeline / doc-drift → `docs/claude/health-review-backlog.json`
     (drained by `/health-review`).
   - Strategy / trading follow-ups → `docs/claude/performance-review-backlog.json`
     (drained by `/performance-review`).
   - AI / ML experiment follow-ups → `docs/claude/ml-review-backlog.json`
     (drained by `/ml-review`).
   When in doubt for a doc-drift leftover (the common case for this skill),
   use the health backlog.

## Output

A short report, no padding:

- **Docs checked** — the list.
- **Contradictions found** — each with file + line on both sides, and which one
  is wrong (by precedence or by reality).
- **Fixed this session** — the doc edits you made.
- **Logged to backlog** — item ids you appended.
- **Needs operator** — contradictions that require a code/config change, with
  the proposed resolution.
- If everything is consistent, say so plainly — "no contradictions found across
  the canonical set."

## Honesty

Only report a contradiction you actually verified by reading **both** sides.
Don't infer drift from a filename or a memory of how something used to be —
open the files and confirm. "I checked X, Y, Z and found nothing" is a complete
and useful result; a fabricated finding is worse than none.
