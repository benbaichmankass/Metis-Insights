# Shrinking the mandatory boot read — measured proposal

> **Status:** slice 1 EXECUTED (the API payload reference, 2026-09-02). Slices 2–5
> are PROPOSED and not done; each carries its own resolution criteria below.
> **Tier:** 1 (docs + guard registration) for every slice here. Nothing in this
> document touches `src/`, `config/`, an order path, or a VM.
> **Dispatched by:** the manager session, MI-41 item B (operating-model plan).

---

## 1 · The problem, measured

**MEASURED 2026-09-02**, population = the five files a session is instructed to
read before acting (`CLAUDE.md` § "Every session" + `CLAUDE-RULES-CANONICAL.md`
§ "Session-start documentation read"). Byte counts from `wc -c` at
`origin/main` `d8aac5c6`; token figures are an **estimate** at ~3.6 B/token and
are marked as such — they are not a measurement.

| file | bytes | ~tokens (est.) | share |
|---|--:|--:|--:|
| `ROADMAP.md` | 648,407 | ~180k | **42.1%** |
| `CLAUDE.md` | 430,453 | ~120k | 28.0% |
| `docs/claude/OPEN-ITEMS.json` | 191,955 | ~53k | 12.5% |
| `docs/ARCHITECTURE-CANONICAL.md` | 168,495 | ~47k | 10.9% |
| `docs/CLAUDE-RULES-CANONICAL.md` | 99,222 | ~28k | 6.4% |
| **total** | **1,538,532** | **~427k** | |

The operating-model design measured ~403k tokens on 2026-09-01 and named it a
root cause. One day later it is ~427k: `CLAUDE.md` grew ~13% and
`OPEN-ITEMS.json` ~26% in that single day.

⚠️ **The headline that matters is NOT "CLAUDE.md is huge".** `ROADMAP.md` is
the largest file in the set — 648,407 B against `CLAUDE.md`'s 430,453 B, over
the five-file population in the table above — and no slice of `CLAUDE.md` alone
can fix the boot read. Slice 1 — the largest clean cut available inside `CLAUDE.md` —
removes **160,944 B of reference material** from that file — 33.8% of it and 8.7% of the boot read as measured at `5aa9f9de`, though see §3 on why the percentage drifts and the byte count does not. Anyone reporting this
work as "the boot-read problem is solved" would be quoting the wrong
denominator.

## 2 · The test for what stays

`CLAUDE.md` is **the only surface that reaches a session before it acts** —
project hooks do not run on Claude Code on the web (verified 2026-08-26) and CI
guards fire at merge, which is after the wrong work is already built. So the
test is not "is this important?" (all of it is) but:

> **Does reading this before the first tool call change what the session does?**

- A **binding rule**, a **capability limit**, or a **closed decision a session
  would otherwise re-litigate** → passes. It changes the plan.
- A **lookup answer** — what does this endpoint return, what does this env var
  default to — → fails. The session has that question only once it is already
  in the code, at which point a pointer serves it better than pre-loading
  100 KB it mostly will not use.

Two corollaries that constrain every slice:

1. **Moving 300 KB into a file nobody opens is not a win.** The point is
   putting content where it is found **at the moment of use** — so every slice
   ships an index that says *when* to open the target, and leaves behind the
   few facts that bind independently of touching the subject.
2. **A row and its correction travel together.** Much of the bulk is ⚠️ blocks
   recording that an earlier version of the same row was stale *in the
   dangerous direction*. A session that reads a table without its warning is
   worse off than one that reads neither, so rows move **verbatim**, never
   summarised.

## 3 · Slice 1 — EXECUTED: the API payload reference

**Moved verbatim** to [`docs/reference/bot-api-reference.md`](../reference/bot-api-reference.md):
`## Dashboard REST API (S-014)` (the per-route table), `### BotStats shape`,
`### Position shape`, `## CORS`, and the `## Diagnostic API (S-051)` route
table.

| | bytes |
|---|--:|
| `CLAUDE.md` before (`d8aac5c6`) | 430,453 |
| **moved out verbatim** — the stable figure | **160,944** |
| pointer + index added back | 4,062 |
| `CLAUDE.md` after (at `5aa9f9de`) | 284,917 (−33.8%) |

⚠️ **QUOTE THE MOVED-BYTES FIGURE, NOT THE PERCENTAGE — the percentage DRIFTS
and the moved figure does not.** <!-- population-ok: n is the byte table immediately above — CLAUDE.md 430,453 B at d8aac5c6 → 284,917 B at 5aa9f9de, 160,944 B moved out, 4,062 B added back. These three percentages are quoted to show they DRIFT against that fixed denominator; the sentence's point is to quote the moved-bytes figure INSTEAD of them. -->
Across three merges of `main` during this PR's
life the percentage read −34.9%, then −34.2%, then −33.8%, while the PR's own
content did not change once. Two causes, both outside this change:

1. **`main` kept editing rows this PR had moved** — `GET /api/bot/strategy-reviews`
   gained an `evidence.horizon` block (+2,374 B) and `GET /api/diag/log_file`
   gained the `close_wedge_standing` name (+2,086 B). Both were **carried into
   the reference verbatim**, so "moved out" grew 154,074 → 160,944.
2. **The generated `SESSION-BRIEF` block inside `CLAUDE.md` keeps growing** —
   24,642 B at `5aa9f9de`, and it is re-rendered from the registers on every
   merge. It is not this PR's content and it is not removable by any slice
   here.

**A percentage whose denominator is edited by other sessions several times a
day is not a stable claim**, which is why the table above leads with the byte
count. Any figure below that reads as a percentage is `MEASURED` against a
NAMED commit and is stale the moment `main` moves.

⚠️ **RE-MEASURED 2026-09-02 after merging `main`, and BOTH figures moved — do
not quote the earlier −34.9% / −9.7% pair.** Two independent causes, and the
second is the more interesting:

1. **`main` edited one moved row.** The `GET /api/bot/strategy-reviews` row
   gained an `evidence.horizon` block (+2,374 B) while this branch was open.
   That edit was **carried into the reference file verbatim** rather than
   dropped, so "moved verbatim" rose 154,074 → 156,448.
2. **`OPEN-ITEMS.json` grew 191,955 → 205,121 B (+13,166) in the same few
   hours** — five new rows, four of them `main`'s. So the boot-read reduction
   fell from 9.7% to 8.7% **without this PR changing at all**. The register
   grew faster than the split shrank `CLAUDE.md`. That is not an argument
   against the split; it is the strongest available evidence for §1's claim
   that a `CLAUDE.md`-only path cannot fix the boot read.

**What a session loses, and how it gets it back.** It loses the per-endpoint
payload contract at boot. It gets it back from a five-row index left in
`CLAUDE.md` under the same `## Dashboard REST API (S-014)` heading (so existing
references still land somewhere correct), which routes *"I need an endpoint's
shape"* → the reference and *"which tier is this route"* →
`docs/api-tier-policy.md`.

**Three facts were kept in `CLAUDE.md`** because they bind before you open
anything: CORS is load-bearing for the only live consumer; a new route needs a
row in `docs/api-tier-policy.md` or `api-tier-policy-guard` fails CI; and the
diag surface's 503-vs-401 contract plus its **read-only premise**, which the
closed token-rotation decision rests on.

**Governance did not move with the content — it was re-pointed.**
`docs/reference/bot-api-reference.md` is registered in `ACTIVE_DOCS` in
`scripts/ci/check_canonical_doc_coherence.py`. That was not optional: the
`POST /api/bot/prop/report` fail-CLOSED value contract reads text that lives in
the moved table, so omitting the registration would have **retired a live check
by moving the text it reads** — a guard that still passes because it stopped
looking. **Verified by positive control**, not assumed: planting the stale
`"token-gated … when set"` phrasing in the new file makes the guard FAIL, and
removing it makes it pass.

**One pre-existing defect surfaced and was fixed.** `check_backlog_refs.py`
exempts a tracking id "already cited in **this file** at base", so a verbatim
move into a **new** file defeated it and five long-standing dangling ids read
as newly introduced — all five already attributed to
`BL-20260730-CITED-BUT-UNFILED-BACKLOG-IDS`, the row that guard's own docstring
names as the home for that debt. The fix falls back to *cited anywhere in the
tree at base* for a path absent at base; an id cited nowhere at base still
fails. Pinned by four tests, and the split test is a **real instrument** — its
first version deleted the source file, which git rendered as a pure rename
emitting no `+` lines at all, so it passed with the fix disabled and proved
nothing. That was caught by running the negative control, not by reading the
test.

A second, separate defect in the same guard was fixed alongside it: `REF`
matches `BL`/`MB`/**`FU`** while `filed_ids` read only
`docs/claude/*backlog*.json`, so **every `FU-` citation dangled by
construction** — the rows live in `comms/follow_ups.json`. **MEASURED
2026-09-02**, population = every `FU-` id cited across the guard's own
`SEARCH_DIRS`: **12 of 13 are genuinely filed** there and now resolve; the
thirteenth is cited and never filed and still dangles, and is the live positive
control in `tests/test_check_backlog_refs.py`. So the change is demonstrably
*more accurate* rather than quieter. (The id is named in the test and not here:
`tests/` is outside the guard's `SEARCH_DIRS`, and writing a deliberately-
unresolvable id into a scanned path makes the guard report it — a false-positive
class recorded in the backlog row below.) What remains open — whether that register is
live or retired — is `BL-20260902-FU-IDS-CAN-NEVER-RESOLVE-IN-CHECK-BACKLOG-REFS`.

## 4 · Slices 2–5 — PROPOSED, not done

### Slice 2 — `## Environment Variables` → `docs/reference/env-vars.md`

**131,620 B, 46.9% of `CLAUDE.md` as it now stands** — the largest remaining
block by a factor of six, and the highest-value remaining cut.

*Shape:* a table of runtime knobs, one row per var, each carrying its default,
its kill-switch semantics, and its ⚠️ live-value corrections.

*Why it is lookup:* the table's own header calls itself *"a curated subset of
operator-relevant toggles"*, and several rows say outright **"do not read the
live value from this row — read it with `get-env` against
`/proc/<MainPID>/environ`"**. A row you must not trust at boot is not boot
reading.

*What a session loses:* the ability to recognise, unprompted, that a knob it is
reasoning about has a dangerous default or an armed live value.

*How it gets it back:* an index in `CLAUDE.md` that keeps the **classes**
rather than the rows — (a) the `*_MODE` `off`/`annotate`/`apply` shape is the
sanctioned gate and a default-off `*_ENABLED` in front of a required capability
is forbidden (already canonical in § "The two execution gates", so this is a
cross-reference not a copy); (b) **an empty `*_ACCOUNTS` allowlist means ALL
for some knobs and NONE for others, deliberately** — this is the single most
dangerous fact in the table and belongs in the index verbatim; (c) a live value
is read with `get-env` from `/proc`, never from the `.env` and never from prose.

*Risk:* higher than slice 1. Rows here describe **armed live gates on real
money**. Mitigations: move verbatim, and register in `ACTIVE_DOCS`.

**MEASURED 2026-09-02 — which guards actually read `CLAUDE.md`'s *content*, so
this slice does not repeat slice 1's near-miss.** Exactly two do:
`scripts/ci/check_canonical_doc_coherence.py` (via `ACTIVE_DOCS`, and
separately `_extract_hierarchy` on § "Instruction hierarchy") and
`scripts/ops/render_session_brief.py` (the generated block). `arch_doc_guard.py`
and `run_guards.py` name `CLAUDE.md` only in a **relevance glob** — they never
open it. **`env-gate-guard` does NOT read it** (`scripts/check_env_gate_in_diff.py`
contains no reference), so moving the env table takes no input away from it.
That leaves `ACTIVE_DOCS` registration as the whole of the governance work for
this slice — but confirm it with a positive control rather than on the strength
of this paragraph.

*Resolution criteria:* `docs/reference/env-vars.md` exists carrying the block
verbatim; it is in `ACTIVE_DOCS`; a positive control shows a planted stale
value being caught there; `CLAUDE.md` ≤ 150,000 B.

### Slice 3 — `ROADMAP.md` § "Historical Sprint Ledger" → `docs/roadmap-history.md`

**179,606 B, 27.7% of `ROADMAP.md`** — and the cleanest cut in the whole
boot-read set, because `CLAUDE-RULES-CANONICAL.md` § "Historical Notes Policy"
already says historical material is *"useful for context, but not
authoritative"*. A session is being asked to read 180 KB of finished work
before it acts.

⚠️ **THIS ONE HAS A CONSUMER AND MUST NOT BE MOVED BLIND.**
`src/web/api/routers/roadmap.py` maps each sprint to its milestone using **the
Historical Sprint Ledger's `M-mapping` column** as one of three resolution
paths, and serves it at `GET /api/bot/roadmap` → the SPA's Roadmap tab. Moving
the ledger without updating that parser breaks a live consumer. `Slice 3 is
therefore Tier-1 docs work with a Tier-1 code change attached`, and it is not
the next slice to take.

*Resolution criteria:* the ledger lives in its own file; `roadmap.py` reads it
from there; `GET /api/bot/roadmap` returns the **same** sprint→milestone
mapping before and after, verified by comparing the two payloads — not by the
route returning 200.

### Slice 4 — stop double-reading `OPEN-ITEMS.json` (no bytes move at all)

**MEASURED 2026-09-02:** `render_session_brief.py` renders **11 of the
register's 32 rows** into `CLAUDE.md`'s SESSION BRIEF, and the rendered
`summary` + `clears_when` are **byte-identical to the register's** for all 11
(22/22 field matches). Those 11 rows are **58,189 B** of `OPEN-ITEMS.json`; the
brief block is **24,982 B**. A session that follows the boot instruction reads
that content **twice**.

*The remedy is not to stop rendering the brief* — it is the only channel that
arrives in time, which is exactly why it was built. The remedy is that the boot
instruction is now wrong: it says read `OPEN-ITEMS.json` **first**, written
before the brief existed. It should say *the brief above already carries every
row that is DUE; open the register for the rows it did not render, and for the
fields it does not show.*

*What a session loses:* nothing, if the brief's coverage is stated. ⚠️ **The
brief renders a SUBSET of each row's fields** — `observation`, `updates` and
`refs` are not rendered — so the instruction must say *which* fields are
missing, or a session will believe it has read a row it has only skimmed.

*Resolution criteria:* the boot instruction in `CLAUDE.md` § "Every session"
names the brief as the DUE-row channel and the register as the
everything-else channel, and states which fields the brief omits;
`session-brief-guard` still passes.

### Slice 5 — `## Important Notes` → the runbooks it already duplicates

**17,823 B.** Watchdog/heartbeat/gateway operational detail that largely
restates `docs/runbooks/liveness-watchdog.md` and
`docs/runbooks/ib-integration.md`, which it cites. Smallest and lowest
priority; listed so it is not mistaken for covered.

*Resolution criteria:* each paragraph is either (a) shown to be present in the
named runbook and replaced by a pointer, or (b) kept with the reason it is not
lookup written down. **Not** deleted on the assumption the runbook says the
same thing — *field beats comment*, and the runbook is the comment here.

### Explicitly NOT proposed for moving

**`## PM-side session capabilities` (22,470 B) stays.** It is the densest
boot-binding content in the file: what a session's tools can and cannot do,
which failure is a transient MCP drop versus a real scope denial, that
`issue_write` 403s and the `pr-opener`/`board-post` relays exist, and the
zero-check-runs trap. Every one of those changes what a session does before it
touches anything, and one of them — the relays being undocumented — has a
**measured** cost: a session on 2026-09-01 concluded no board path existed and
found both relays only by reading `.github/workflows/` after every documented
path had failed. That section is the fix for that class, not a candidate for
removal.

## 5 · Where this ends up

| after | `CLAUDE.md` | boot-read total | vs. today |
|---|--:|--:|--:|
| slice 1 (done) | 284,917 | ~1,406,000 | ~−8.6% |
| + slice 2 | ~149,000 | ~1,257,000 | −18.3% |
| + slice 3 | ~149,000 | ~1,078,000 | −29.9% |
| + slice 5 | ~131,000 | ~1,060,000 | −31.1% |

⚠️ **Every row below slice 1 is a PROJECTION, not a measurement** — it assumes
each block moves whole and the index costs roughly what slice 1's did
(4,062 B). **INFERRED from** the byte counts in §1 and §4 and slice 1's
measured index cost.

⚠️ **Even at −31% the boot read is ~294k tokens (est.), and `ROADMAP.md`
§ "Milestone Roadmap" — 347,909 B — is untouched by every slice here.** It is
live status, it has a CI guard (`roadmap-status-glyph-guard`) and a live API
consumer, and splitting *closed* milestones out of it is a real option that
this document deliberately does **not** propose, because nobody has yet
measured what fraction of it is closed. **That measurement is the next thing
worth doing**, and stating it as unknown is more useful than guessing.
