---
name: doc-freshness
description: Session-end (and on-demand) check that the canonical instruction docs do not contradict each other, the code/config on disk, or the changes this session made — AND that this session's material decisions actually landed in every durable surface they belong in (roadmap + sprint log + the right review backlog), so nothing flows through the cracks. Use at the end of every session per docs/CLAUDE-RULES-CANONICAL.md, when the operator says "/doc-freshness" or "check the docs are up to date", or whenever you suspect documentation drift. Fixes Tier-1 doc contradictions + missing roadmap/sprint-log records in place; logs minor leftovers to the health-review backlog; flags anything needing a code/config change for the operator.
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
   - The **instruction hierarchy** in `CLAUDE.md` § "Instruction hierarchy"
     is identical (same docs, same order) to
     `docs/CLAUDE-RULES-CANONICAL.md` § "Document Priority". They must mirror.
   - The **VM topology** is single-sourced: only `ARCHITECTURE-CANONICAL.md`
     § "VM topology" (and its `CLAUDE.md` § "VM authority split" mirror) state
     VM IPs/shapes; no other operating doc, skill, or script hardcodes a VM
     IP, and the terminated micro `158.178.210.252` never appears as the
     *current* live VM.
   - No **removed gate** is described as live. The removed set:
     `MULTI_SYMBOL_ENABLED`, `NEWS_ENABLED`, `NAKED_POSITION_AUTOPROTECT`,
     `MONITOR_RECONCILE_ENABLED`, `POSITION_NETTING_GUARD_ENABLED`,
     `POSITION_NETTING_GUARD_ACCOUNTS` — each may only appear flagged as
     removed/historical, never as an active toggle.
   - The **ML deployment ladder** is the 3-stage `candidate → shadow →
     advisory` everywhere; no skill/command/active-doc presents the old
     7-stage ladder unflagged (legacy stage names must carry an "aliases to"
     note).
   - No re-introduced "banned phrase" lists and no "operator does X manually /
     SSHes / runs X" framing — the model is autonomous Claude + tier-based
     approval. Any operator-facing manual step must be one of the three
     genuine hand-offs (secret-value origination, OCI console/CAPTCHA,
     one-time sudoers bootstrap) — everything else routes through a
     `system-actions` workflow.
   - References to renamed or removed things resolve (e.g. `system-actions`,
     not `operator-actions`; no Telegram-ping / Colab-key-rotation workflows
     presented as current).
   - `docs/claude/INDEX.md` lists the files that actually exist; no dangling
     doc links.

   **Mechanical scans (run these every time — they are what the
   `canonical-doc-coherence` CI guard also runs; do them by hand here so you
   catch drift before the PR, not after):**

   ```bash
   # Fast guard: run the CI checker locally over the working tree.
   python scripts/ci/check_canonical_doc_coherence.py

   # Or the raw greps it wraps, if you want to eyeball:
   #  - terminated micro IP presented as live (should only match historical lines):
   rg -n '158\.178\.210\.252' CLAUDE.md docs/ .claude/ scripts/ | rg -v -i 'terminat|retir|histor|pre-2026-06-14|migration source|decommiss|supersed|old x86|former'
   #  - removed gates presented as live (should only match removal/historical lines):
   rg -n 'MULTI_SYMBOL_ENABLED|NEWS_ENABLED|NAKED_POSITION_AUTOPROTECT|MONITOR_RECONCILE_ENABLED|POSITION_NETTING_GUARD_(ENABLED|ACCOUNTS)' CLAUDE.md docs/ .claude/ | rg -v -i 'remov|retir|supersed|histor|ignored|baseline|no longer|legacy|deprecat|example|stranded'
   #  - stale 7-stage ladder in the skill/command catalog (should be empty):
   rg -n -i '7[- ]stage' .claude/skills/ .claude/commands/
   ```
   A non-empty result from any of these (after the allow-list filter) is drift
   to fix in this session, not to defer.
5. **Decision-landing completeness — did this session's material decisions get
   recorded in EVERY surface they belong in?** Consistency (steps 2-4) checks
   that what IS written doesn't contradict; this step checks that what was
   DECIDED isn't *missing* from a surface it should be in. This is the
   "stuff flows through the cracks" guard: a material decision/finding/outcome
   must be categorized into ALL of its required durable surfaces, not just one.
   For each material item this session produced (a shipped/abandoned
   initiative, a strategy/account/risk change, a milestone status change, a
   validated/rejected research finding, a live-VM action), confirm it landed
   in every required surface below — and if a surface is missing it, add it now
   (docs are Tier-1):

   | Decision/outcome type | ROADMAP.md (milestone record) | `docs/sprint-logs/<ID>.md` (execution record) | review backlog (follow-ups) | other |
   |---|---|---|---|---|
   | Milestone/sprint completed or status-changed | **required** (row or status update) | **required** | follow-ups only | — |
   | Research initiative concluded (incl. honest negatives) | **required** (incl. ON-HOLD/abandoned + why) | **required** | open follow-ups | results doc under `docs/research/` |
   | Strategy/account/risk Tier-3 change (proposed or shipped) | **required** | **required** | — | the PR + `config/*` |
   | Live-VM action (deploy/mode-flip/restart) | if it changes a milestone's state | **required** | — | the system-action issue/audit |
   | Per-trade / strategy-perf finding | — | if part of a sprint | `performance-review-backlog.json` | `claude_strategy_scores.jsonl` |
   | ML experiment outcome | M14 row if it moves a sprint | if part of a sprint | `ml-review-backlog.json` | manifest/registry |

   The two surfaces that most often get skipped are **ROADMAP.md** (a decision
   lands in a sprint log or a research doc but the centralized record never
   learns the milestone moved) and the **sprint log** (work ships but no
   execution record is written). Treat a material decision that exists in only
   one surface as drift to fix here. **An ON-HOLD / abandoned / negative
   outcome is a decision too** — it must be recorded (with the reason) in the
   roadmap + sprint log, not just dropped, or a future session re-litigates it.
   (Use the `sprint-format` skill for the sprint-log shape. This table says
   THAT a decision must land in ROADMAP.md; for WHICH section — new
   milestone, existing phase table, Items Under Consideration, or backlog
   only — see [`research-driver`](../research-driver/SKILL.md) Step 6's
   landing decision tree.)

6. **Triage minor leftovers.** Anything real but too small to fix now goes
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
- **Decision-landing** — for each material decision this session, the surfaces
  it should be in vs where it actually is; any gaps you filled (e.g. "added
  missing ROADMAP row", "wrote the sprint log"). "All material decisions landed
  in roadmap + sprint log + backlog" is a complete result.
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
