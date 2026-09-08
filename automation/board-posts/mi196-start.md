▶️ **START** — MI-196 · branch `claude/mi196-due-readout-consolidation`

**Scope I am about to touch:**
- `scripts/ops/constraint_readout.py` (adding a section; no change to §1–§4 semantics)
- `docs/claude/READOUT.md` + `docs/claude/CONSTRAINT.json` (regenerated output)
- `tests/` (new test file for the ported section)
- `docs/claude/work/objects/WO-20260901-PHASE-D.yaml` (recording the operator decision)
- `docs/claude/health-review-backlog.json` (append-only, for what I find but do not fix)

**What:** operator decision `consolidate_into_the_readout` — carry the due-list's
source classes into the constraint readout. **This PR ports only.** `DUE.md` is
untouched and keeps rendering; its retirement is a SEPARATE later PR, so an
incomplete port cannot take the session-facing due list to zero.

**Two corrections to the brief I was given, measured this session:**
1. `render_due_list.py` has **NINE** source functions, not five — `open_items`,
   `soaks`, `operator_owed`, `probes`, `research_queue`, `red_crons`,
   `unlanded_automation`, `error_feed`, `sunset_dispositions`. Only
   `operator_owed` has a readout counterpart, so **8 classes are missing, not 4**.
2. The **recurrence ledger is NOT a due-list source.** `WO-20260901-PHASE-D`'s
   note says it is; the only `RECURRENCE-LEDGER` mention in `render_due_list.py`
   is line 15, a docstring reference to its sibling `render_session_brief.py`.
   Positive control: `PROBES` appears 13× in the same file, and every real source
   has a `def src_*`. Field beats comment.

**This does NOT improve the readout's coverage** and I will not report it as
doing so. `insufficient_basis` stays — folding in more sources adds ROWS, not
assessed `blocked_on` BASIS.

Not touching: `src/`, `config/`, any order path, `render_due_list.py`'s own
source logic.
