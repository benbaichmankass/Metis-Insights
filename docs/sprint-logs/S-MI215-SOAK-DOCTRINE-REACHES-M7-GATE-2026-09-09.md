# Sprint Log: S-MI215-SOAK-DOCTRINE-REACHES-M7-GATE-2026-09-09

> **Doc status:** `historical` · category `history` · last verified `2026-09-11` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md)

## Date Range
2026-09-09 (single session, `session_01MrTjuoRpBb7spyQLydXmmT`). Work object `WO-20260901-PHASE-F`, checklist item MI-215. Manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`.

## Objective
Establish whether `scripts/check_soak_doctrine.py` reaches the M7 strategy-review decision surface, close the hole with a positive control, and re-point the strategy edge verdict at offline evidence.

## Tier
**Tier-1** as shipped (CI guard, tests, docs, registers). The repair of the seven offending branches is **Tier-3** and is PROPOSED only — `scripts/ml/strategy_review_packet.py` is byte-identical to `main`.

## Starting Context
The M7 packet graded **52 of 52 legs `hold`** on every committed day, withholding every verdict for want of live in-window closes (`MIN_CLOSED_FOR_ACTION = 20` over 7 days). A prior session put four window/floor options to the operator; all four were rejected as malformed, because they presumed live closes are the evidence basis for grading a strategy.

## Repo State Checked
`a77afb8e` (base). Shallow clone deepened first — `git log -p` is untrustworthy at depth 50 and would have returned a plausible wrong answer.

## Files and Systems Inspected
- `scripts/check_soak_doctrine.py` — read all 132 lines
- `scripts/ml/strategy_review_packet.py::decide` — read, not modified
- `docs/CLAUDE-RULES-CANONICAL.md` § "Promotion evidence — offline edge, live mechanics"
- `src/runtime/evidence_horizon.py`, `ml/promotion/oos_edge.py`, `scripts/ml/replay_pregate*.py`
- All 7 committed `comms/strategy_reviews/*/INDEX.json` and all 52 full packets of 2026-09-01

## Work Completed
1. **Established the stronger finding.** The guard opens exactly three paths (`CLAUDE-RULES-CANONICAL.md`, `new-strategy/SKILL.md`, `backtesting/SKILL.md`); **no code path reached the packet** — not merely "does not name it". It enforced the doctrine's prose and reached none of the deciding code.
2. **Shipped check D.** AST scan of registered gate surfaces; resolves local aliases of live/calendar-accrual names to a fixpoint; fails when a verdict assignment is control-dependent on one. Found **seven** branches in `decide()`, including **Override 4's `shadow_soak_days < 14`** — the literal "N days at stage" construct clause 3 forbids by name, independent of the floor and not previously called out.
3. **Ratchet ledger** at `docs/claude/soak-doctrine-exceptions.json`, exact-match count 7, `backlog_row` VERIFIED against a real row.
4. **Filed the Tier-3 proposal** `docs/design/strategy-leg-offline-edge-basis-PROPOSAL.md`.
5. **Acted on the operator's mid-session correction** (below).

## Validation Performed
- **Positive control, observed:** an 8th branch planted in the real packet under a **new** name (`trades_banked = headline.n_closed`, in no seed list) → guard exit **1**, naming `line 1137 (decide, reads: trades_banked)`, 8-vs-7 against the ledger. Removed → exit **0**. Packet restored **byte-identical** (`git diff` empty). Catching an *alias* is what proves it is a detector and not a keyword matcher — the real gate already does `n = headline.n_closed`, so a textual check would have missed six of seven.
- `tests/test_soak_doctrine_guard.py` — **12 passing**, including a negative control and the alias/transitive-alias cases.
- `scripts/ci/run_guards.py --base main` → **PASS 68 · FAIL 1**; the one is `lint-imports` exit **127** (not installed in this sandbox), verified identical on clean `main`.
- CI: `guards`, `pytest-collect`, `repo-inventory` **success** on `ca5fbc45`.

## Documentation Updated
`docs/design/strategy-leg-offline-edge-basis-PROPOSAL.md` (new, registered), `docs/DOCUMENT-INDEX.md` (one row, by hand), `docs/claude/soak-doctrine-exceptions.json` (new), `docs/claude/health-review-backlog.json` (1 row resolved with evidence, 1 kept open and now carried, 2 new), `docs/claude/OPEN-ITEMS.json` (observation corrected).

## Contradictions or Drift Found
1. **The premise handed to this unit was wrong, measured.** The offline infra named as the fix target is **head-scoped, not leg-scoped**: all four `replay_pregate*` modules say "shadow regime heads (RG1)" / "22 heads" in their own docstrings; `oos_edge.py` is manifest+dataset scoped. The packet's only offline hook carries **no edge number by design**, and across all 52 committed packets **51 read `backtest_anchor: null`** (the exception, `turtle_soup`, points at a 2026-06-09 sweep). **There is no per-leg offline edge artifact to re-point at** — that absence is the root cause, and the producer is the precondition for any re-pointing.
2. **`OPEN-ITEMS.json` carried a conclusion that had been overtaken the same day** — it argued the window/floor decision was the binding item. Corrected in place by raw-text splice (the file has inconsistent hand-edited indentation; `backlog_append` correctly REFUSED to rewrite it).
3. **`document_index.py --write` rewrote 430 files** for one new doc and advanced `CLAUDE-RULES-CANONICAL.md`'s `last verified` to today — **asserting a verification nobody made**. Reverted; filed.
4. **`pr-landing` `HOLD_REASONS`** has no value for Tier-1 work on a path `TIER1_SURFACE` cannot certify, so the typed reason contradicts the `tier` field. Filed.
5. **Self-correction:** this session's own guard docstring said "eight consecutive days"; only **seven** indexes are committed. Corrected to the measured number with the discrepancy stated.

## Operator correction received mid-session, and what changed
The operator retracted the prohibition the brief was built around: *"you keep adding guards in the shape of forbidding questions from being asked - this is incorrect. the problem isn't the question, it's the underlying misunderstanding of infra and processes... that's what we need to fix here, not the question."*

This session had **reproduced that shape in four of its own artifacts**. All four were rewritten to carry the model and to *derive* the window/floor conclusion from it rather than decree it: edge is established **offline**; live data establishes **mechanics only** (1–2 executions, not calendar time); **slow live accrual is a FEATURE of this system, not a barrier**, so `none_gradeable` on a live window is not a defect; fewer legs does not raise trades-per-leg. Check D itself needed no change — it already enforces the rule structurally rather than banning a phrase.

**Pushback returned to the manager:** the correction asked the guard to enforce the *positive* form ("must source edge from offline evidence"). That is unsatisfiable today — no per-leg artifact exists, so it would fail 100% of surfaces on day one, the `check_pr_queue_watch.py` failure mode. The negative form is enforceable now; the ratchet is the bridge; the positive check should land the day the first per-leg edge record does.

## Risks and Follow-Ups
- The **seven branches remain live**. The guard makes them countable and un-growable; it does not repair them. Tier-3, awaiting the §6 decision.
- Check D reaches **only the surfaces in `GATE_SURFACES`**. A gate absent from that map is unreached — a clean run does not cover it, and the docstring says so.

## Deferred Items
The `OPEN-ITEMS.json` serialisation hazard was **deliberately not filed**: six open rows already say it (`BL-20260901`, `BL-20260902`, `BL-20260903`, `BL-20260905`, `BL-20260906` ×2). Six rows in six days for one unfixed defect is itself the finding; a seventh is the duplication `backlog_append`'s refusal exists to prevent.

Observed and **not** filed (outside claim, pre-existing): `.github/workflows/ranking-key-ab.yml` concludes `failure` on **every** push — runs 160–165 across four branches, two of them not this session's.

## Next Recommended Sprint
Build the per-leg offline edge producer in § 4 of the proposal, then add the positive form of check D in the same change. Until it exists, decide whether the packet keeps emitting a verdict or declares `no_offline_evidence`.

## Wrap-Up Check
Board `START` + `DONE` posted (#11336). PR **#11529** open, not draft, `landing: hold` — `pr-landing` R5 will not certify the `scripts/` root, and the guard was **not** relocated to clear that. Merge slot released (the one briefly held was stale). Needs one merge click.
