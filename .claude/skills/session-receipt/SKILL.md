---
name: session-receipt
description: >
  The ONE canonical shape for a "session receipt" — the wrap-up summary a session
  hands the operator. Five sections (A DECISIONS · B SESSIONS SPAWNED · C THE
  SESSION'S OWN WORK · D TOTALS · E ERRORS), two binding rules (every number
  carries its population; "landed" is MEASURED from commit trailers and squash
  suffixes, never recalled), and a generator that produces the measured half so
  the format cannot drift per session. Use whenever the operator asks for a
  "session receipt", a wrap, a session summary, or "what did you get done" — and
  at the end of any manager session. Composes with session-handoff (which hands
  the NEXT session a prompt; this hands the OPERATOR the record) and
  doc-freshness (the session-end sweep). NOT a status update — that is the
  manager's checklist → recently done → next, and it opens a conversation rather
  than closing one.
---

> **Doc status:** `live` · category `instruction` · last verified `2026-09-17` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md)

# session-receipt — one shape, every session

## Why this is a skill and not a paragraph in a doc

Operator directive, 2026-09-17, verbatim:

> *"i want this format of summary cannonized so that this is what i get whenever
> i ask a session for a 'session receipt', and this is what should be presented
> on the web page"*

**The operator named the trigger phrase.** "Session receipt" has to resolve to
something a session actually reads, and the binding skill-first lookup
(`CLAUDE-RULES-CANONICAL.md` § Generation Discipline Rule 1) is the only
mechanism in this repo that makes a phrase resolve. A prose section in a doc
would reproduce exactly the failure
`OI-20260902-API-REFERENCE-SPLIT-OUT-OF-CLAUDE-MD-AND-NOT-YET-FOUND-AT-USE`
watches: reference material moved somewhere nobody opens is not a win.

## The contract, in one line

**A receipt is a RECORD, not a report.** It states what happened, with its
population, and it is checkable by someone who was not there.

---

## Step 1 — run the generator. Do not hand-derive the measured half.

```bash
python3 scripts/ops/session_receipt.py \
    --session-id <your session id> \
    --since <the date this session started> \
    --role "manager | lane:ops | lane:research | ..." \
    --pr 12072 --pr 12446 --pr 12447          # every PR you claim
```

It produces sections **C** and **D** as MEASUREMENTS and scaffolds **A**, **B**
and **E** for you to complete. Add `--json --write` to also land the durable
artifact (see step 4).

⚠️ **Two of the five sections cannot be recalled, and the 2026-09-17 session
proved it by getting both wrong before the queries corrected it** — an ancestry
probe misreported a landed squash, and a *"three edits still owed"* claim was
two. If you find yourself typing a sha or a PR outcome from memory, stop and run
the generator.

⚠️ **`git merge-base --is-ancestor` DOES NOT TEST WHETHER A PR LANDED here.**
This repo squash-merges, and a squashed branch's commits are not fetched into a
fresh clone at all — MEASURED 2026-09-17: PR #12072's branch head `09f76dcb` is
not present as an object, so the probe exits non-zero for *object not found* and
a caller reading that as "not an ancestor" books a merged PR as not landed. The
generator greps the squash suffix `(#N)` on `origin/main`, which is what is
actually there.

---

## Step 2 — the five sections. All five, in this order, every time.

### A. DECISIONS

Operator decisions taken this session, **with the answer recorded verbatim**, and
an **explicit note wherever the operator OVERRULED this session's
recommendation**.

The overrule note is not decoration. A receipt that records only the outcome
makes a session's judgement look better than it was, and the next session
inherits the wrong prior about what this system's recommendations are worth.

⚠️ An empty section A is a **claim that no decision was taken** — not a licence
to skip it.

### B. SESSIONS SPAWNED

Every session this one spawned, **itemized**. One row each:

| field | note |
|---|---|
| session id | |
| checklist item | `MI-…` |
| lane | |
| spawn time | |
| lifecycle state | read it from `get_session`, do not assume it is still running |
| **what it was SET TO DO** | the **mandate**, not a paraphrase |
| **what ACTUALLY LANDED** | **MEASURED** — PR numbers, verified |
| maps to | ROADMAP milestone / backlog row / ad-hoc investigation / regular-ops cadence |

⚠️ **Aborted and respawned sessions are listed, never silently dropped.** A
spawn that produced nothing is the most informative row in the table.

⚠️ **"Lifecycle state" is read, not remembered.** MEASURED 2026-09-17 over the 8
`in_flight` work objects: 6 carried a resolvable session-id owner and **6 of 6
were IDLE or ARCHIVED — zero were running**, five of them reporting
`status_category: completed` in their own summary. A receipt that copies a
remembered state reports dead lanes as working.

### C. THE SESSION'S OWN WORK

PRs merged **with their commit shas**, registers repaired, findings measured.
The generator fills this. Read its three landing states and do not flatten them:
`landed` · `not_landed` · **`could_not_look`** — a failed git read is not
evidence a PR did not land.

### D. TOTALS

Sessions spawned · PRs landed by spawned sessions · PRs landed by this session ·
**the attributed share of a STATED commit population**.

### E. ERRORS

**What this session got wrong and how it was corrected. Not optional.**

> A receipt with no errors section reads as a claim of none.

Every receipt written to date has had entries here. If yours genuinely has none,
say that in words rather than deleting the heading.

---

## Step 3 — the two binding rules

### Rule 1 — EVERY NUMBER CARRIES ITS POPULATION, structurally

The 2026-09-17 delivery read *"349 attributed of 545 commits on origin/main since
2026-09-11"*. A receipt that says **"349 commits"** is precisely what
`CLAUDE-RULES-CANONICAL.md` § "Always state the population" forbids.

This is enforced rather than requested: in the generator a count is
`{"value": N, "population": "…"}` and `counted()` **raises** on an empty
population, so a bare number is not representable. When you write the narrative
sections by hand, carry the same discipline — n, window, filter, instrument.

### Rule 2 — BACKLOG IDS ARE EMITTED VERBATIM, NEVER TYPED

```bash
python3 scripts/ops/session_receipt.py --resolve-id BL-20260917-RANKING-KEY-AB
```

Resolve every id you cite. The generator completes a unique prefix and
**refuses** a dangling or ambiguous one.

⚠️ **This is why the 2026-09-17 reference receipt could not be committed.** It
carried two shortened ids (`BL-20260917-RANKING-KEY-AB…` and
`BL-20260917-THE-OPEN-PR-HANDOFF-RECORD-COVERS-11-OF-67-OPEN-PRS…`) that resolve
to nothing, and `check_backlog_refs` fails any diff introducing a reference that
resolves to nothing — correctly, because *"tracked by BL-X"* where `BL-X` was
never filed reads as tracked, so nobody re-checks it.

⚠️ **Calibrate on how often this fires.** That single session hit it **four
times, from three different sources**: its own abbreviation (twice), `CLAUDE.md`'s
own prose carrying a truncated id, and the receipt. An author cannot be trusted
to type these. A generator can read them.

---

## Step 4 — the durable artifact and the web surface

```bash
python3 scripts/ops/session_receipt.py --session-id <id> --since <date> --json --write
```

writes **`docs/claude/work/receipts/<session_id>.json`**, which
`GET /api/bot/work/receipts` serves to the Workflow page beside the checklist.

**Two design decisions, recorded here because MI-290 asked for them to be said:**

1. **ONE FILE PER SESSION, not a shared register.** A receipt is written by
   exactly one session about itself, and several lanes wrap at once. The repo has
   already paid for the shared-mutable-array shape twice — `health-review-backlog.json`
   conflicts constantly, and `session-board.json::merge_slot` was measured at
   **39 of the last 40 commits touching that file moving the same field**, which
   sent every armed branch `dirty` and restarted its CI. The remedy there was a
   per-branch file under `.github/merge-slots/`; this is the same remedy taken
   before the same cost, and it matches `docs/claude/work/README.md`'s own
   principle: one file per object, so two sessions touching different work never
   conflict.

2. **`docs/claude/work/receipts/`, and the directory level is load-bearing.**
   `check_manager_scope`'s `MANAGER_SURFACE` admits `docs/claude/work/**` and does
   **not** admit `docs/claude/receipts/**`. A **manager** is the primary author of
   receipts, so a path off the manager surface makes the mechanism unusable by the
   one role that needs it most. That is not hypothetical: on 2026-09-17 the
   reference receipt was refused by R2 at `docs/claude/receipts/`, and one
   directory level up the block disappears.

### ⚠️ EDITING A WRITTEN RECEIPT — re-run the generator, or match its serialization exactly

A receipt is a section-E artifact, so it is normal to append an error row after
the file is already written. **The generator writes with `json.dumps(receipt,
indent=2)` — the `ensure_ascii` DEFAULT, i.e. escaped.** Hand-editing with
`ensure_ascii=False` re-encodes every em-dash and emoji in the file, turning a
one-row append into a whole-file rewrite.

⚠️ **THIS IS NOT A STYLE POINT AND IT IS NOT HYPOTHETICAL.** It happened on
2026-09-17, in this format's own first receipt, appending the row that recorded
the SAME class of mistake against `OPEN-ITEMS.json` earlier the same session: a
one-row append produced **20 insertions / 16 deletions**, against **8 / 0** once
the serialization matched. A rewritten register destroys the reviewability of
the diff and, on a shared file, re-attributes everyone else's lines to you.

⚠️ **AND THE GENERATOR CANNOT REFUSE IT.** `scripts/ops/backlog_append.py`
round-trips a register and raises `FormatNotReproducible` rather than write a
reformatted file; `session_receipt.py` has **no such check**. The blast radius is
genuinely smaller here — one file per session, authored by that session, which
is part of why one-file-per-session was chosen — but smaller is not absent, so
the discipline is yours and not the tool's. Prefer re-running the generator; if
you must edit in place, read, mutate, and write back with `indent=2` and no
`ensure_ascii` override, then **check `git diff --stat` shows insertions only.**

---

## ⚠️ ONE THING THIS SKILL DOES NOT DECIDE

**Whether a receipt is generated ON DEMAND or written AT SESSION END is an open
operator decision** (`DEC-20260917-SESSION-RECEIPT-CADENCE` on
`WO-20260917-OPS-LANE-CANONIZE-THE-SESSION-RECEIPT-THEN-REVIEW-AND-TRIAGE`).

- **On demand** can measure a live session, and produces nothing if nobody asks.
- **At session end** survives the session's death, and cannot be produced by a
  session that died — which is the case where the record is worth most.

Both were true of the 2026-09-17 delivery and it did not have to choose. Until
the operator answers, **generate on demand and also `--write` when you wrap** —
that is the union, it is cheap, and it pre-empts neither answer. Do not quietly
settle this by building only one.

---

## What a receipt is NOT

| not this | that is |
|---|---|
| a status update | the manager's **checklist → recently done → next** (`CLAUDE.md` § manager duties). A status update OPENS a conversation; a receipt CLOSES one. |
| a handoff prompt | the `session-handoff` skill — that hands the NEXT SESSION a paste-ready prompt; this hands the OPERATOR the record. Run both when wrapping. |
| a sprint log | `sprint-format` / `docs/sprint-logs/` — the per-sprint execution record. |
| a doc-freshness sweep | the `doc-freshness` skill, which is a separate session-end duty and is not replaced by writing a receipt. |

## Composes with

- **`session-handoff`** — run at the same cut point; receipt for the operator, handoff prompt for the successor.
- **`doc-freshness`** — the session-end reconciliation sweep, still owed.
- **`session-coordination`** — post the board `✅ DONE` when you wrap; the receipt is not a substitute for releasing your claim.
