---
name: duty
description: >
  The DUTY PASS — the short, bounded session that gives every detected signal an
  OWNER. Reads the one generated due-list (docs/claude/DUE.md, rendered by
  scripts/ops/render_due_list.py from every structured register) and drives each
  row to a written DISPOSITION: acted / filed / escalated / not-due. It is
  deliberately NOT a review: it does not grade trades, propose tweaks, or drain
  backlogs. Use it as a standalone short session, at the start of any long
  session, and whenever a cron-failure alert fires. Composes with
  session-coordination (board START before acting), health-review /
  performance-review / ml-review (where a row is ROUTED, not resolved), and
  backlog-drain (where a filed row is later worked).
---

# duty — give every detected signal an owner

## Why this exists (read once; it is the whole point)

The 2026-08-31 operations audit measured **17 separate work registers** and no
surface answering *"what is due right now?"*. The finding was not that this
system misses signals — it detects extremely well — it is that **a detected
signal has no owner**.

The proof is `replay-pregate-nightly`. It failed identically on **2026-08-13**
(3/3 nights, trainer SSH timeout at head 9/22) and again on **2026-08-31**
(same shape, model 10/22). The response in August was *to add it to the alert
list*. Detection was raised twice and disposition never happened once.

The corroborating proof is the `/system-review` retrospective of 2026-08-31,
which completed 25 of 37 checklist items. All four **not-started** items were
generative (trade grades, proposed tweaks, experiments, new-work compliance);
all four **in-progress** items were backlog bookkeeping. When budget runs out a
review drops the thinking and keeps the bookkeeping.

So this pass is **short and bounded on purpose**. It must fit in the budget that
is actually available, or it will be dropped exactly like the generative half of
a review.

## The contract

- **It decides nothing.** Every row is a pointer for a human-shaped judgement.
  Operator directive 2026-08-31: *actions are autonomous, decisions are the
  operator's* — so autonomous Tier-1 fixes are made here without asking, and
  anything that is a DECISION is put to the operator rather than resolved.
- **It closes nothing it did not observe.** A passing test is not an observation
  of a live mechanism (`OPEN-ITEMS.json`'s own rule).
- **Every row leaves this pass with a disposition.** "Noted" is not one.

## Step 0 — read the list, and read its VERDICT first

```bash
python3 scripts/ops/render_due_list.py --markdown
```

Read `verdict` **before** reading any row:

| verdict | what the list means |
|---|---|
| `all_sources_read` | complete. An empty section means nothing is due. |
| `partial` | **a LOWER BOUND.** At least one source could not be read and is named. An empty section may mean nobody looked. |
| `no_sources_read` | meaningless. Do not act on it; fix the read first. |

⚠️ From a web sandbox the two GitHub-backed sources are 403'd by design, so
`partial` is the NORMAL local verdict — say so in your summary rather than
reporting the list as complete. This is the `curl … || echo '{}'` failure class
(`CLAUDE.md` § "Diagnostic provenance", sub-class **C**): an unread source that
renders as an empty section is a confident wrong answer.

If you need the GitHub-backed sources, run the workflow instead of guessing:
open an issue labelled `due-list-now`.

### The `error_feed` source — the trader's live error feed

Operator ask, 2026-09-02: *"can the error feed that's in the trader bot be fed
directly to the manager session, so you can decide what should be resolved
immediately vs. backlogged?"* **That decision is this pass's disposition**, so
the feed renders into the same list rather than a surface of its own.
`error-feed-digest.yml` (hourly, best-effort) runs
`scripts/ops/error_feed_digest.py` over `runtime_logs/operator_alerts.jsonl`
and `/api/bot/logs?level=error,warn`, groups by digit-normalised cause, and
commits `docs/claude/ERROR-FEED-DIGEST.json`. Three rules for reading it:

- **The list is CAPPED at the 10 largest error-level groups.**
  `ERROR-FEED-SUMMARY` states how many were left out and every warn group is
  omitted — a capped render is never the whole feed. Open the digest for the
  rest before concluding a condition is absent.
- **`ERROR-FEED-UNREACHABLE-*` and `ERROR-FEED-DIGEST-STALE` are `loud` rows
  and mean WE COULD NOT LOOK.** Neither is "the feed went quiet"; both make
  every group beside them a lower bound.
- **A big group is not a big problem.** Counts are volume, not severity: one
  un-latched alarm has been 202 of 376 CRITICALs in a window here. The
  disposition on a flood is usually *fix the alarm*, and that is itself a row.

⚠️ **The cadence is best-effort and unproven.** This repo has scheduled
workflows that fire ~4h50m late and once instead of daily (`probes.yml`, run
\#34). Read `generated_at` on the digest, not the declared hourly cron.

### The `sunset` rows — E3's retirement candidates (added 2026-09-02)

The list now carries a `sunset` source: every `retire_candidate` from the newest
`comms/sunset/<date>/INDEX.json` that has **no row in
`docs/claude/SUNSET-DISPOSITIONS.json`**. Nine were undispositioned when it
landed. It exists because E3's machinery all shipped — `sunset-pass.yml`, the
register, the CI guard — and **no role pack connected a session to any of it**,
so candidates accumulated on a cadence with nobody made to look.

Working one of these rows means recording a DISPOSITION, **not** retiring
anything:

- Retiring a strategy leg is **Tier-3**. `retire_proposed` is the furthest you
  may take one on your own; `operator_decision` is the operator's to fill.
- **"Keep" is a complete disposition.** Looking and deciding to keep clears the
  row exactly as legitimately as proposing removal — the gap this closes is that
  nobody looked, not that nothing was retired.
- The row clears by appearing in `SUNSET-DISPOSITIONS.json`, so it drops off the
  list by itself. Do not hand-edit `DUE.json` / `DUE.md`; they are generated.
- ⚠️ These rows are deliberately **not `loud`**. Ten permanently-loud rows would
  be restated in every closing summary until sessions learned to scroll past
  them — the desensitized-alarm failure this repo calls its own worst. They are
  due, which is enough.

## Step 1 — post a board START before you touch anything

Per `session-coordination`: GitHub issue #6927, naming the files/subsystems you
are about to touch. A duty pass that fixes something is a session like any other.

## Step 2 — work each row to a disposition

For **each** row in the list, in the order given (loud rows first, then oldest
first), record exactly one of:

| disposition | when | what it costs you |
|---|---|---|
| **acted** | Tier-1 and unambiguous, and you can verify the fix in-session | do it now, then state what you OBSERVED, not what you changed |
| **filed** | real, but bigger than this pass | one row via `scripts/ops/backlog_append.py::append_row` — never by hand; read the near-duplicate candidates it prints and say explicitly whether this is a DUPLICATE (drop it, update the existing row) or a RECURRENCE (`similar_ok=True`, and the fact that the earlier fix did not hold IS the finding) |
| **escalated** | it is a DECISION, or Tier-2/Tier-3 | put it to the operator with the exact change, not a summary of the area |
| **not-due** | the row's own `clears_when` / cadence says so | say which clause, not "looks fine" |

**A `monitoring` row in `OPEN-ITEMS.json` cannot be carried by doing nothing.**
To re-affirm it you must set `verified_at` to today **and** write what you
actually saw into `observation`. A claim of progress is not an observation.

**A `loud: true` row must be reported on in your closing summary** — checked and
stated, never silently carried.

## Step 3 — close the loop

- Update `docs/claude/OPEN-ITEMS.json` for anything you cleared or re-affirmed.
- Refresh the list so the next session inherits your work:
  `python3 scripts/ops/render_due_list.py --write` (or let the daily
  `due-list` workflow do it — do not hand-edit `DUE.json` / `DUE.md`; they are
  generated).
- Post the board `✅ DONE`.

## Closing summary — the required shape

State, in this order:

1. The **verdict** and, if not `all_sources_read`, which sources were unread.
2. Counts: rows seen, and the split across acted / filed / escalated / not-due.
   These must sum to the row count — a partition that does not sum is how a
   dropped row hides.
3. Every `loud` row by id, with its disposition.
4. Every **escalation**, with the exact decision being asked for.

## What this pass is NOT

It is not `/health-review`, `/performance-review`, `/ml-review` or
`/system-review`. It does not grade trades, propose strategy tweaks, recommend
promotions, or drain a backlog. Where a row belongs to one of those, the correct
disposition is **escalated** (routed), and naming the destination review IS the
work. Trying to do the review inside the duty pass is how the duty pass becomes
another thing that runs out of budget.
