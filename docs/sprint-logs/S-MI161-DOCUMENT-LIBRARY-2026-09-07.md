# Sprint Log: S-MI161-DOCUMENT-LIBRARY-2026-09-07

> **Doc status:** `historical` · category `history` · last verified `2026-09-07` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md)

## Date Range

2026-09-07 (single session, `session_01NriFh691pHJFiVc7agPHnC`, work session under manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`).

## Objective

Build the document library the operator asked for on 2026-09-07:

> *"we need a library for our documents. Like, we're having a hard time keeping
> track of things, and you're right that a lot of things probably look fine if
> you just open them on their own. So maybe, like, one centralized table where
> we keep track of everything, and everything has to be organized correctly and
> logged and categorized."*

The motivating failure, measured the same day: a manager reported roadmap status
**to the operator** off `docs/research/WORKPLAN-2026-08-14.md`, which declares
`Status: ACTIVE` in its own header while being **three generations superseded**
(08-14 → 08-21 → 08-26 → 08-29). Twelve work-plan documents exist, `ROADMAP.md`
pointed at the wrong one, and `docs/claude/CYCLE-PRIORITY.json` — newer than
every work plan — is what actually drives sessions.

**It looked fine opened on its own.** That is the operator's own diagnosis and it
is why this ships two artifacts (a table *and* a per-document header stamp), not
one. An index alone would not have prevented the failure, because the manager
never opened one.

## Tier

**Tier-1** — docs, CI and tooling only. Self-landing (`.github/pr-landing/mi161-document-library-20260907.json`
+ `.github/pr-automerge-requests/mi161-document-library-20260907.txt`;
`check_pr_landing.py --base origin/main` → `state=declared_self_land`).

No `config/`, no `src/`, no order path, no strategy parameter, no risk cap, no
deploy or unit file. Nothing in the diff can place, modify or refuse an order.

## Starting Context

- The dispatch work object `WO-20260907-A-DOCUMENT-LIBRARY-ONE-CENTRALIZED-TABLE-WHERE`
  was **absent from `origin/main`** (`git cat-file -e` failed) — its dispatch PR
  (#11244) was still in the merge queue. Worked from the dispatch brief, as
  instructed. Stated rather than re-diagnosed.
- MI-159 (PR #11241) was **open and unmerged**, working `ROADMAP.md` and the
  `WORKPLAN-*` family concurrently.

## Repo State Checked

- `git ls-files --cached --others --exclude-standard` over `docs/**/*.md`,
  `ROADMAP*.md`, `CLAUDE.md`, `.claude/skills/**/*.md` → **969 documents**
  (968 before this sprint log itself was written). This is the population; every
  count below is against it.
- **Existence check before building** (`CLAUDE.md` § "Before `cat >` … check it
  exists first"; `RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`). Three candidates
  inspected and all three are different concerns, so nothing was rebuilt:
  - `docs/claude/INDEX.md` — a hand-written narrative index of one directory
    (179 lines), no status, no `superseded_by`, not machine-checkable.
  - `scripts/ci/check_canonical_doc_coherence.py` — enforces *content* claims
    over a 12-doc `ACTIVE_DOCS` list; says nothing about registration or status.
  - `scripts/ci/check_skills_index.py` — skills-only name coverage.
- Guard conventions read from `run_guards.py` (`GUARDS` registry, `when`
  semantics) and `guard_selftests.py` before writing anything.

## Files and Systems Inspected

- `CLAUDE.md`, `docs/CLAUDE-RULES-CANONICAL.md` (§ RULE ONE, § Always state the
  population, § Collapsed states, § Green is not evidence).
- `scripts/ci/{run_guards,guard_selftests,check_canonical_doc_coherence,check_skills_index,check_pr_landing}.py`.
- `scripts/ops/{constraint_readout,render_due_list,render_daily_brief,render_session_brief}.py`
  — to find which markdown is **generated** and must not be stamped.
- The 968-document population, measured for: YAML frontmatter (**32 files**, all
  `SKILL.md`, whose frontmatter the harness parses) and existing status
  declarations (**201 of 968** declare something status-like in their first 15
  lines — measured before this sprint log was added).

## Work Completed

| artifact | what it is |
|---|---|
| `docs/DOCUMENT-INDEX.md` | the centralized table — 968 rows; closed `category` and `status` vocabularies defined in the file; a `basis` column naming what established every row |
| header stamps | a visible one-line status in **967** documents (2 generated ones exempt) |
| `scripts/ops/document_index.py` | builds the table and stamps the headers; `--census` reports without writing; idempotent |
| `scripts/ci/check_document_index.py` | the guard (R1–R5) with a planted-positive control |
| `scripts/ci/run_guards.py` | registers `document-index-guard`, ungated |
| `docs/claude/health-review-backlog.json` | one new row — see § Contradictions or Drift Found |

**Category — a closed set of six.** `instruction` (must obey) · `architecture`
(a declared contract) · `plan` (a forward commitment) · `evidence` (a
measurement that may be cited) · `history` (a record of what happened) ·
`lookup` (consulted for a fact). The brief's three are kept apart because
conflating them is how a superseded plan gets obeyed; **`plan` is its own
category for exactly the same reason** — on 2026-09-07 a *plan* was read as an
*instruction*. The set says `lookup`, not `reference`, because `reference` is
already a **status** value and one word meaning two things is how a vocabulary
stops being closed.

**The census, population stated.** 969 in population, 969 registered.

| category | n | | status | n |
|---|---|---|---|---|
| `history` | 366 | | `unknown` | **563** |
| `evidence` | 323 | | `historical` | 366 |
| `architecture` | 79 | | `live` | 38 |
| `plan` | 60 | | `closed_unfinished` | 2 |
| `lookup` | 44 | | `superseded` | 0 |
| `instruction` | 41 | | `reference` | 0 |
| **`unknown`** | **56** | | | |

**56 could not be categorised; 563 statuses could not be established.** Both are
marked `unknown` rather than guessed. An invented status is worse than an absent
one because it reads as checked — which is exactly how 08-14 fooled a manager.
`unknown` rows say so in their own header too: *"nobody has verified this
document's status — do not act on it as current"*.

**`superseded` is 0, and that is not an oversight.** The one known superseded
chain in this repo is the `WORKPLAN-*` family, deliberately deferred to MI-159.

**How a status is established.** `live` only on real evidence —
`ci:canonical-doc-coherence-ACTIVE_DOCS` (a guard already fails on that
document's drift; the list is **imported** from that guard, never copied, so a
second "which docs are live" list cannot drift from the one CI enforces) or
`harness:skill-is-loaded-and-invocable`. `historical` from a record directory.
Everything else `unknown`, basis `not-assessed`.

**The self-declaration asymmetry, deliberate.** A document declaring itself
*dead* is recorded on that declaration; a document declaring itself *alive* is
`unknown`. A self-declared death is rarely wrong in the dangerous direction; a
self-declared life is precisely the claim that misled a manager. The 201
pre-existing declarations (of 968, measured before this log existed) use an
entirely uncontrolled vocabulary — including
`tier`, `scope`, `a proposal`, `measured` and `credentialfree pipeline built`.
There was no controlled status vocabulary in this repo before this sprint.

**Deconfliction with MI-159.** `ROADMAP*.md` and the `WORKPLAN-*` family are
registered `unknown` with basis `deferred:MI-159`. PR #11241 was open and
unmerged at index build; two sessions independently deciding which plan is live
would reproduce the exact defect both are fixing. Scoped so it does not
overreach — a sprint log named `S-ROADMAP-WORKPLAN-REVIEW-*.md` is a record of a
session and stays `history`; deferring it would overstate MI-159's scope while
leaving a resolvable row unresolved.

## Validation Performed

**The guard has been observed FIRING, not merely passing.** The rule engine is a
pure function over `(population, rows, headers, generated, categories,
statuses)`, so the policy is arguable in tests rather than against the live
tree. `--self-test` runs on every CI invocation:

```
PASS  R0 clean input produces NO findings
PASS  R1 fires on an unregistered document
PASS  R2 fires on a row whose file is gone
PASS  R3 fires when header and index disagree
PASS  R3 fires when a document carries no stamp at all
PASS  R3 fires when a document could not be read
PASS  R3 is WAIVED for a generated document
PASS  R4 fires on superseded with no successor
PASS  R4 does NOT fire when a successor is named
PASS  R5 fires on a category outside the closed set
PASS  R5 fires on a status outside the closed set
PASS  R5 does NOT fire on the required `unknown` category
PASS  R5 does NOT fire on the required `unknown` status
document-index self-test: OK — 13 planted cases, every rule observed FIRING and every clean case observed SILENT.
```

Clean inputs must stay silent, so **a rule that always fires is caught too**. It
also fired twice for real: against the live tree before the index existed
(`FAIL — docs/DOCUMENT-INDEX.md does not exist`) and against the first build
(`FAIL — 56 finding(s)`), which is how both defects below were found.

Other validation:

- `run_guards.py --all` → **PASS 89 · FAIL 0**.
- **The five initial failures were attributed, not assumed.** They were
  `trainer-capture-watch`, `artifact-validity`, `operator-owed` (all
  `No module named pytest`), `layer-guard` (`lint-imports` exit 127) and
  `ruff-lint`. `ruff` reported exactly **one** error and it was **mine** (unused
  `Optional`) — fixed. The other four were missing tooling in this container;
  `pytest` and `import-linter` were installed and re-run, **144 tests pass**.
  This mattered rather than being a formality: `test_check_research_index.py`
  and `test_check_backlog_refs.py` validate **doc references**, and the stamps
  add a link to every document — so "probably fine" was not good enough.
- `canonical-doc-coherence`, `skills-index`, `guard-glob-coverage` all pass on
  the stamped tree.
- **YAML frontmatter preserved** — the insertion point is computed to land after
  frontmatter, else after the H1, else at the top; verified on `SKILL.md`.
- `--write` is **idempotent** — a second run produces no diff.
- `check_pr_landing.py --base origin/main` → `state=declared_self_land`.

## Documentation Updated

- `docs/DOCUMENT-INDEX.md` — new; self-documenting (vocabularies, bases,
  waivers, and the rebuild command all live in the file).
- 967 documents gained a status header.
- This sprint log.

## Contradictions or Drift Found

**1. Two defects the guard found in this sprint's own work.**

- `docs/research/WORKPLAN-2026-08-14.md` — the file at the heart of the incident
  — was first categorised **`evidence`**, because its *directory* rule
  (`docs/research/`) beat its *filename*. It is a **plan**. Filing a plan as a
  measurement is a sibling of the very failure being fixed. Resolution order is
  now record-directories → strong filename rules → directory → weak filename
  rules → `unknown`, and the ordering is documented in the source as
  load-bearing.
- **R5 rejected `unknown`**, the required honest state — which would have pushed
  the builder to invent a category to make CI green, manufacturing exactly the
  false confidence the register exists to remove. Fixed, and pinned open by two
  self-test cases.

**1c. A third defect, found by running the guard against a genuinely new
document rather than reasoning about it.** `population()` used plain
`git ls-files`, which lists **tracked files only** — so this very sprint log was
invisible while unstaged: the guard read its directory, reported `OK`, and the
count never moved off 968. That is the clean negative this repo has a rule
about — "nothing unregistered" asserted over a population that silently excluded
the file being added. Fixed with `--cached --others --exclude-standard`, which
keeps gitignored scratch out so the original intent survives. Verified by
re-running: `R1 UNREGISTERED: docs/sprint-logs/S-MI161-DOCUMENT-LIBRARY-2026-09-07.md`,
`population=969 registered=968`, then `OK` after a rebuild.

**2. ⚠️ The MANDATORY coordination board is FULL — filed as
`BL-20260907-COORDINATION-BOARD-IS-FULL-AT-GITHUBS-2500-COMMENT-CAP-AND-BOTH-POST-PATHS-ARE-DEAD`.**

I could not post the mandatory `▶️ START`. `add_issue_comment` returned the
documented 403, so I fell back to `board-post.yml` exactly as `CLAUDE.md`
instructs — **and the relay failed too, with a different cause**:

```
FAILED: GraphQL: Commenting is disabled on issues with more than 2500 comments (addComment)
```

Verified directly rather than inferred from the relay error: `issue_read` on
**#6927** returns **`"comments": 2500`** — exactly GitHub's hard ceiling — with
`updated_at` 2026-09-07T11:28:18Z, so it filled about an hour before I hit it.
**The board is permanently closed to new comments on both paths, for every
session.** `CLAUDE.md` makes it mandatory for every session including every
review sub-session, and it is the only coordination mechanism not gated on
merging.

**The failure is silent in the dangerous direction:** reads still succeed, so the
board looks alive until a post is attempted, and the 403 path makes it look like
the already-resolved read-only-MCP problem rather than a new one.

Checked for duplicates: `backlog_search` returned 8 overlapping board rows and
**none** is this. The nearest,
`BL-20260820-NO-BOARD-POST-RELAY-FOR-READONLY-MCP` (resolved), is the 403, and
its remedy is the very relay that now also fails.

**3. Relay files were kept off this branch, deliberately.**
`BL-20260905-RELAY-WRITTEN-AUTOMATION-PATHS-MAKE-EVERY-RELAY-USING-PR-UNCERTIFIABLE-FOR-TIER1-SELF-LANDING`
records that `pr-landing-guard`'s `TIER1_SURFACE` excludes `automation/**`. Both
relay attempts were therefore pushed on a **separate** branch
(`claude/mi161-board-post-20260907`), which is also what keeps the relays'
bot results-commits from re-burying this PR's checks.

## Risks and Follow-Ups

- **563 statuses remain `unknown`.** This sprint ships the register and the
  mechanism that keeps it honest; establishing those statuses is per-document
  work needing a real basis. Saying otherwise would be the failure mode.
- **56 documents could not be categorised**, 54 of them under `docs/claude/`,
  which is genuinely mixed-purpose.
- **The board cap needs a successor issue**, and every surface naming 6927 must
  move together (`CLAUDE.md`, `docs/claude/coordination-board.md`,
  `docs/CLAUDE-RULES-CANONICAL.md`, `.claude/skills/session-coordination/SKILL.md`,
  `board-post.yml`). A half-applied rename would leave sessions posting into a
  dead issue and believing they had coordinated — worse than today's loud
  failure. It also needs a **cap watch**: the board filled in ~7 weeks, so a
  successor with no detector just resets the timer on the same outage.
- **Open PRs touching `docs/**` will see a one-line conflict** on the stamp;
  `python3 scripts/ops/document_index.py --write` regenerates it.
- When **#11241** lands, re-run `--write` so the deferred rows pick up MI-159's
  determination.

## Deferred Items

- Establishing the 563 unknown statuses.
- Categorising the 56 unknown documents.
- The board successor issue and its cap watch — filed, not fixed here; it is a
  separate change touching four canonical surfaces and belongs in its own PR.
- Deciding which work plan is live — **owned by MI-159**, deliberately not
  touched.

## Next Recommended Sprint

The board successor + cap watch (`BL-20260907-COORDINATION-BOARD-IS-FULL-...`),
as its own PR. It is currently blocking the mandatory START for **every**
session, which makes it higher-value than draining the `unknown` rows.

## Wrap-Up Check

- [x] Population stated for every quantitative claim.
- [x] Guard registered in `run_guards.py` and observed FIRING (13 planted cases
      + two real failures).
- [x] Landing declaration + automerge request both present; `check_pr_landing.py
      --base origin/main` passes.
- [x] Full guard suite green (89/0) with the five initial failures attributed to
      missing tooling, not assumed.
- [x] Finding filed through `scripts/ops/backlog_append.py::append_row` (13-line
      diff, no reformat).
- [x] Concurrent session (MI-159) deconflicted rather than raced.
- [ ] **Board `▶️ START` / `✅ DONE` NOT posted — impossible, see above.** Recorded
      here and in the backlog rather than left as a silent protocol lapse.
