"""`pytest-run`'s short-circuit must not skip a tree the suite asserts over.

`BL-20260813-PYTEST-RUN-SHORTCIRCUITS-SO-MAIN-MERGES-UNVERIFIED`.

pytest-run skips the pip-install + test steps when a PR's diff touches nothing
"relevant", to save CI minutes (MB-20260706-CI-MINUTES). The saving is real. The
hazard is that a skipped run reports the SAME green tick as an executed one, so
at the merge button they are indistinguishable — "green is not evidence".

That makes the filter's completeness load-bearing, and it has now failed FOUR
times, each time by omitting a tree the suite reads:

  1. config/            BL-20260707-FASTCI-CONFIG-DRIFT — #5850 and #5851 both
                        merged green while reddening main's full suite.
  2. .github/workflows/ same fix.
  3. scripts/           2026-08-13 — PR #8994 changed ONLY
                        scripts/ops/purge_vm_runner.sh, short-circuited to a
                        NINE-SECOND green pytest-run (a real run is 7-9 min over
                        ~10,600 tests) and merged to main having executed no
                        tests. deploy/ and comms/ were open the same way.
  4. docs/             2026-08-14 — PR #9208 changed ONLY
                        docs/research/exit-refinement-coverage.json, which
                        tests/test_exit_head_per_leg.py reads AS COMMITTED,
                        short-circuited to a TEN-SECOND green pytest-run, merged,
                        and left main red.
                        (BL-20260814-COVERAGE-MATRIX-SHORTCIRCUITS-THE-SUITE-THAT-READS-IT)

Enumerating the trees by hand is what keeps failing, so this test asserts the
filter against the directories the tests demonstrably read. A new tree of
assertions must either be covered or be excluded ON PURPOSE, here, with a
reason.

⚠️ THE HAND-WRITTEN TABLES BELOW ARE STILL THE OLD SHAPE, and instance 4 is what
that costs: every fix so far added the ONE tree just proven, and the next tree
was found by an incident rather than by a check. So the docs/ half is now
DERIVED from the tests — see `test_docs_committed_readers_are_all_covered` at
the bottom. Prefer extending that mechanism over extending the tables.

⚠️ AND NOTE WHAT NONE OF THE FOUR FIXES DID. Instance 3's backlog row required
"pytest-run cannot report a green tick without having executed the suite (or
reports a visibly distinct state when it skips)" and was marked RESOLVED without
that — closed by adding three trees, leaving the green-tick ambiguity that IS the
hazard fully intact. A skipped run and a 10,677-test run still report the same
tick. Until that changes, this class can recur through any tree the allowlist
does not name, and these tables are mitigation, not a fix.
"""
from __future__ import annotations

import ast
import pathlib
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "pytest-run.yml"


def _filter_regex() -> str:
    """The `grep -Eq '...'` pattern that decides relevance."""
    text = WORKFLOW.read_text()
    m = re.search(r"grep -Eq '([^']+)'", text)
    assert m, "could not find the relevance grep in pytest-run.yml"
    return m.group(1)


def _matches(path: str) -> bool:
    return bool(re.search(_filter_regex(), path))


# Trees the suite ASSERTS OVER using non-.py files. A change to any of these can
# redden the suite, so a diff touching them must not short-circuit.
COVERED = {
    "scripts/ops/purge_vm_runner.sh":
        "tests/ops/test_system_actions_workflow.py reads scripts/ops/*.sh (OPS_DIR)",
    "deploy/ict-trader-live.service":
        "tests/test_s012_service_consolidation.py asserts the deploy/*.service set",
    "comms/schema/health_review_response.template.json":
        "the suite validates against the comms/ schema templates",
    "config/strategies.yaml":
        "test_strategy_execution_gate reads config/strategies.yaml",
    ".github/workflows/system-actions.yml":
        "test_system_actions_workflow reads the workflow itself",
    # The docs/ files the suite reads AS COMMITTED, found by scanning
    # tests/ for a docs/ path joined onto the repo root — not by hand, since
    # hand-enumeration is what produced instances 1-4 above. 19 test files carry
    # a docs/ path literal; these are the ones not writing a tmp_path
    # fixture. See test_docs_committed_readers_are_all_covered below, which
    # re-derives this set from the tests on every run so it cannot go stale.
    "docs/sprint-logs":
        "test_conflict_marker_scope reads THE REAL sprint-log tree — level 4 of "
        "the instruction hierarchy, and the gap that decided the conflict-marker "
        "scope. A sprint-log-only PR must therefore run the suite: the tree is "
        "now asserted over, so a short-circuit would report a green having "
        "checked none of the 309 files the assertion covers",
    "docs/research/exit-refinement-coverage.json":
        "test_exit_head_per_leg reads THE REAL matrix (not a fixture); PR #9208 "
        "changed only this file, short-circuited to a TEN-SECOND green "
        "pytest-run, merged, and left main red",
    "docs/claude/impossibility-claim-baseline.json":
        "test_impossibility_claim_ratchet reads the REAL baseline; it records the "
        "ALLOWED per-file claim counts, so a docs-only PR RAISING them is exactly "
        "the diff that must not short-circuit into a green tick",
    "docs/research/m20-sweep-corpus.jsonl":
        "test_m20_regime_book_provenance asserts per-run axis agreement over the "
        "committed corpus",
    "docs/claude/system-actions.md":
        "tests/ops/test_system_actions_workflow reads DOC.read_text() and asserts "
        "the documented allowlist matches the workflow",
    "docs/ARCHITECTURE-CANONICAL.md":
        "test_audit_verification_checklist::test_live_repo_checklist_clean asserts "
        "the LIVE doc has no drift — and the only other check of that property is "
        "the WEEKLY doc-audit-weekly.yml, so without this a docs-only PR can break "
        "it and merge green. Costly (this doc is edited often) and included anyway",
    "docs/CLAUDE-RULES-CANONICAL.md":
        "test_status_enum_single_home reads the REAL canonical doc and asserts "
        "its status vocabulary matches the one home. The drift it catches IS a "
        "docs-only diff -- a status name edited here and nowhere else -- so "
        "without this the guard's own subject can change and merge on a green "
        "tick from a run that executed nothing. Costly (this is the #1 canonical "
        "doc and is edited on many PRs) and included anyway, the same accepted "
        "trade-off already recorded for ARCHITECTURE-CANONICAL.md just above",
    "docs/claude/work/SESSIONS.json":
        "test_register_field_loss plants a FIELD revert into the REAL 231-row "
        "register and asserts the guard goes red WHILE the union-by-id proof still "
        "reads CLEAN — a fixture cannot carry that premise. Costly (every manager "
        "spawn edits this file) and included anyway: a register write-back IS the "
        "diff that can revert a field, so excluding it would exempt exactly the "
        "change the guard exists for",
    "docs/claude/work/objects/WO-20260908-BUILD-THE-N-BOOK-RANKING-KEY-HARNESS.yaml":
        "test_decision_subject reads this REAL object AND globs the whole "
        "objects/ directory, asserting no live decision_request declares a "
        "`subject.kind` outside SUBJECT_KINDS. Declaring one IS an object-only "
        "diff, so the assertion is falsifiable by exactly the change that would "
        "short-circuit past it. ⚠️ COSTLY AND MEASURED RATHER THAN GUESSED: 11 "
        "of the last 200 commits on main (5.5%) touch this tree, against 1.0% "
        "for docs/claude/work/SESSIONS.json, which is covered above on the same "
        "reasoning -- a write to the register IS the diff the assertion is "
        "about, so excluding it would exempt precisely that change. ⚠️ THE "
        "CHEAPER FIX IS NOT TAKEN HERE AND IS NOT PRETENDED AWAY: give the "
        "subject-kind property a guard-side owner (as "
        "`work-digest-source-coverage` does for the digest glob) and this tree "
        "could be excluded with a TRUE premise instead of covered. Nothing in "
        "scripts/ validates SUBJECT_KINDS today -- verified by grepping for the "
        "symbol, which appears only in src/runtime/decision_subject.py and this "
        "one test -- so excluding it now would be the assumed premise "
        "BL-20260814 was filed about. Filed as the follow-up rather than "
        "half-built here",
    "docs/claude/SUNSET-DISPOSITIONS.json":
        "test_phase_g_sunset_and_pull::test_the_live_register_and_the_live_pass_agree "
        "runs check_sunset_dispositions.audit over the REAL register. A row claiming "
        "`retired` for a file that still exists is a DOCS-ONLY diff, so without this "
        "the retirement register could assert a removal that never happened and merge "
        "on a green tick — the exact shape of PR #9208, in the mechanism built to stop "
        "things being left behind",
    "docs/claude/CONSTRAINT.json":
        "test_phase_g_sunset_and_pull::test_the_live_constraint_is_the_one_the_guard_"
        "grades reads the REAL readout, which check_capability_pull BRANCHES on "
        "(enforcing / advisory / unknown). It is GENERATED, so it moves whenever the "
        "work store does — a real CI cost, accepted because the alternative is a shape "
        "change breaking the enforcement grading behind a green tick. ⚠️ If it proves "
        "noisy the honest move is DELIBERATELY_EXCLUDED with the `guards owns it` "
        "reason (guards runs the reader unconditionally), never deleting the check",
    "docs/research/m20-exit-head-rounds.jsonl":
        "test_exit_head_round_emits_evidence::test_the_emitted_schema_matches_the_"
        "committed_evidence_file reads THE REAL evidence file, so a change to it "
        "must run the suite that validates its schema. Added 2026-08-14 as the "
        "FIFTH committed docs/ reader — found by this file's derived check on the "
        "same day the file was created, which is the mechanism working: instances "
        "1-4 were each found by an incident instead",
    "docs/claude/diag-relay.md":
        "test_trainer_diag_relay::test_docs_point_a_relay_bound_session_here reads "
        "the REAL doc and asserts it names the trainer relay and its request path. A "
        "relay a 403-bound session cannot find in the doc it reads is, for that "
        "session, identical to no relay — the exact gap the relay was built to close. "
        "Removing those references is a DOCS-ONLY diff, so without this it merges on a "
        "green tick from a run that executed nothing and silently re-opens the gap",
    # The four found 2026-09-12 by fixing the two scans below, which had been
    # blind to them for different reasons -- the line scan dropped the
    # one-string join spelling, the AST scan had all of docs/ excluded by a
    # per-file entry widening to its top-level directory. Both now see them,
    # and the two agree, which is the cross-check.
    "docs/claude/session-board.json":
        "test_claim_merge_slot asserts over the REAL board at BYTE level -- that "
        "a splice rewrites only the merge_slot span, that `merge_slot` is found "
        "at top level and NOT in the `_doc` prose or the nested `schema` "
        "description that both mention it, and that a whole-file re-serialisation "
        "still blows the size budget. Every one of those premises is falsifiable "
        "by a docs-only edit to this file, so excluding it would exempt exactly "
        "the diff the tests are about. ⚠️ COSTLY AND SAID SO: 27 workflows write "
        "this file and every shared-slot claim touches it, so this really does "
        "run the full suite often. If that proves unaffordable the honest move is "
        "an EXCLUDED_TREES/DELIBERATELY_EXCLUDED entry with the reasoning, never "
        "deleting the check",
    "docs/claude/work/objects/WO-20260901-PHASE-G.yaml":
        "test_phase_g_sunset_and_pull reads this REAL work object; a fixture "
        "cannot carry the premise that the LIVE object and the LIVE pass agree",
    "docs/research/e35-bracket-corpus.jsonl":
        "test_e35_achieved_oos_count counts achieved OOS folds out of the REAL "
        "corpus -- the count IS the file's contents, so a corpus-only diff is "
        "precisely what can change the answer",
    "docs/research/research-disposition-ledger.jsonl":
        "test_research_disposition reads the REAL ledger and compares before/after; "
        "a ledger row is a docs-only diff and is the whole subject of the test",
    "docs/api-tier-policy.md":
        "tests/test_check_api_tier_policy.py reads the REAL doc three times and "
        "asserts its STATED coverage claim equals the computed one. \u26a0\ufe0f THE HAZARD IS "
        "MEASURED, NOT ARGUED: on 2026-09-17 the doc said 109/109 while the code "
        "said 110/110 and the suite went red -- so a PR restating that line and "
        "nothing else is precisely a docs-only diff that reddens main, PR "
        "#9208's shape. \u26a0\ufe0f AND IT WAS UNCOVERED THE WHOLE TIME, surfaced by "
        "accident: that test spells the join `os.path.join(REPO, \"docs/...\")`, a "
        "THIRD spelling BOTH scans below are blind to -- the line regex wants the "
        "segmented join and the AST walk wants a division chain, and the "
        "example is deliberately NOT spelled out here because the line scan "
        "would read it as a real read (false-positive class 3, in the comment "
        "describing the blind spot) -- so neither "
        "reported it. It became visible only because an unrelated test added the "
        "segmented spelling for the same file. Population: 1 of the 1188 files "
        "under tests/ uses that spelling today. Filed as "
        "BL-20260917-THE-PYTEST-RUN-COMMITTED-READER-SCANS-ARE-BLIND-TO-THE-OS-PATH-JOIN-SPELLING-SO-AN-UNCOVERED-READER-WAS-FOUND-BY-ACCIDENT",
    "docs/claude/work/MANAGER-CHECKLIST.json":
        "tests/ops/test_merge_json_register.py::test_real_register_merge_produces_valid_json "
        "reads THIS REAL file via os.path.join(ROOT, ...) — the Call-node "
        "spelling that motivated the fix above — and feeds it through the real "
        "merge driver, asserting the output still parses. Found while grading "
        "that same row: the general AST scan below now resolves Call-node "
        "joins and reported this as newly uncovered.",
    "docs/claude/health-review-backlog.json":
        "tests/ops/test_merge_json_register.py::test_round_trip_is_byte_identical "
        "(parametrized over REGISTERS) and tests/test_backlog_append.py both read "
        "THIS REAL file. ⚠️ A FOURTH BLIND SPOT, not caught by either AST scan: "
        "the parametrized read joins os.path.join(ROOT, rel) where `rel` is a "
        "LOOP VARIABLE bound to the REGISTERS list, not a string literal — no "
        "AST walk over one call site can resolve that without dataflow analysis. "
        "Found by reading the real test file while grading "
        "BL-20260917-THE-PYTEST-RUN-COMMITTED-READER-SCANS-ARE-BLIND-TO-THE-OS-PATH-JOIN-SPELLING-SO-AN-UNCOVERED-READER-WAS-FOUND-BY-ACCIDENT, "
        "not detected by any scan. This is the file nearly every backlog-drain "
        "PR touches ALONE, so it was short-circuiting the exact test that "
        "proves the merge driver still round-trips it.",
    "docs/claude/OPEN-ITEMS.json":
        "same test_round_trip_is_byte_identical parametrization as the entry "
        "above, same fourth blind spot (REGISTERS is a loop variable, not a "
        "literal) — OPEN-ITEMS.json is not byte-reproducible through json.dumps "
        "(a literal vs escaped em-dash), which is the exact case this test "
        "exists to catch a regression in.",
    "docs/claude/work/OPEN-PRS.json":
        "same test_round_trip_is_byte_identical parametrization, same fourth "
        "blind spot as the two entries above.",
    "src/runtime/order_monitor.py": "python",
    "requirements.txt": "dependency pin",
}

# Excluded ON PURPOSE — large, change on nearly every PR, and their assertions
# belong to the separate `guards` job, which does NOT short-circuit.
DELIBERATELY_EXCLUDED = {
    # VERIFIED 2026-08-14, not assumed: every test that touches this path writes
    # it under a `tmp_path` fixture (test_check_allow_degraded,
    # test_check_backlog_refs, test_check_allow_degraded) or only names it in a
    # docstring — none reads the committed file. So the "guards owns it"
    # premise genuinely held HERE, THEN.
    #
    # ⚠️ THE PREMISE STOPPED HOLDING, AND THIS TABLE'S OWN NEXT PARAGRAPH SAYS
    # WHAT TO DO ABOUT THAT: "re-checking it is the point of this table." Found
    # 2026-09-18 while grading
    # BL-20260917-THE-PYTEST-RUN-COMMITTED-READER-SCANS-ARE-BLIND-TO-THE-OS-PATH-JOIN-SPELLING-SO-AN-UNCOVERED-READER-WAS-FOUND-BY-ACCIDENT:
    # tests/ops/test_merge_json_register.py::test_round_trip_is_byte_identical,
    # added after the 2026-08-14 verification, reads THE REAL committed
    # `docs/claude/health-review-backlog.json` (parametrized over REGISTERS,
    # not a tmp_path fixture and not a docstring) and asserts the merge driver
    # reproduces it byte-for-byte. That is a real read the `guards` job does
    # NOT carry, so the per-file entry that used to sit here is FALSE today —
    # a PR touching only this file would short-circuit past the one test that
    # would catch the driver mangling it. Per-file entry REMOVED (not
    # narrowed further — there is no narrower true statement left to make for
    # this specific file) and the path moved to COVERED above. The
    # DIRECTORY-level exclusion below is untouched: it is about a different
    # property (which files EXIST here), still true, still owned by
    # `work-digest-source-coverage` in `guards`.
    #
    # It did NOT hold for the exit-coverage matrix either, which is why that
    # file moved to COVERED above; the premise is per-file and re-checking it
    # is the point of this table.
    # ⚠️ THE KEY IS THE DIRECTORY, AND THAT IS THE WHOLE POINT — it excuses the
    # DIRECTORY-LEVEL read and nothing under it. `_scan_excluded` matches
    # DELIBERATELY_EXCLUDED by EXACT key, so every per-file read beneath this
    # stays graded.
    #
    # The first draft of this change put `docs/claude` in EXCLUDED_TREES
    # instead, and a planted revert caught it: dropping
    # docs/claude/system-review-checklist.json from the grep reddened NOTHING,
    # because the tree exclusion had silently excused every per-file read under
    # docs/claude/ — re-creating, for that tree, the exact blind spot the
    # file/tree split was built to close, in the change that built it.
    #
    # What is excused: tests/test_work_digest.py GLOBS this directory for
    # `*-review-backlog.json` and asserts every one on disk is declared in
    # work_digest.SOURCES, so its dependency is WHICH FILES EXIST here, not any
    # file's contents. The filter matches on PATH and cannot tell an ADD from an
    # EDIT, so covering that dependency means matching the whole tree — measured
    # 2026-09-12 at 330 committed files, touched by every backlog append.
    #
    # ⚠️ THE "guards OWNS IT" PREMISE IS TRUE HERE AND WAS MADE TRUE IN THE SAME
    # CHANGE, not assumed. The property's one owner — work_digest.py's own
    # self-test check 11 — is now a step in the `work-digest-source-coverage`
    # guard, and `guards` does not short-circuit. Before that it ran only on
    # schedule / push-to-main / dispatch, so a PR adding a review backlog was
    # graded by nobody until after it merged. BL-20260814 is the row recording
    # that this premise had never been checked per-file; this is the check.
    "docs/claude":
        "the DIRECTORY-level glob read only (see above) — per-file reads under "
        "it are still graded, and `work-digest-source-coverage` in the guards "
        "job carries the property this excuses",
    "data/some_fixture.csv": "bulk data, not asserted structurally by pytest",
    # The rest of docs/research/ stays excluded: the matrix is the one file
    # there the suite reads as-committed, and widening to the tree would pull in
    # every research memo for no assertion.
    "docs/research/exit-refinement-notes.md":
        "prose; no suite assertion reads it",
}


# Excluded as a WHOLE TREE, which is a different statement from the per-file
# entries above and must be made separately.
#
# ⚠️ THE TWO WERE ONE TABLE UNTIL 2026-09-12, AND THAT COLLAPSE BLINDED THE
# GENERAL SCAN OVER ALL OF `docs/`. `_excluded` below matched on a candidate's
# TOP-LEVEL component, so a single per-FILE entry excluded its entire tree:
# `data/some_fixture.csv` was meant to say "the bulk data tree" (and does), but
# the one `docs/...` entry -- a per-file decision, verified per-file, about a
# backlog whose readers all use tmp_path -- silently excluded `docs/` as well.
# MEASURED that day: 17 committed docs/ paths the AST scan finds are read by the
# suite, and it was reporting on NONE of them; four were genuinely uncovered by
# the filter. `docs/` is where five of the six recurrences of this class lived,
# so it was the worst possible tree to go blind over.
#
# A file entry now excludes THAT FILE. A tree entry excludes its subtree, and
# saying so is deliberately a separate line somebody has to write.
EXCLUDED_TREES = {
    "data": "bulk candle/fixture data. The three files under it the suite DOES "
            "read as committed are named individually in the filter (see the "
            "`_excluded` docstring below); the rest is bulk and pulling the "
            "tree in would run the full suite on every data refresh. This is "
            "the ONE tree the original top-level widening was actually about, "
            "and it keeps exactly the scope it had.",
}


@pytest.mark.parametrize("path,why", sorted(COVERED.items()))
def test_filter_covers_every_tree_the_suite_asserts_over(path, why):
    assert _matches(path), (
        f"pytest-run would SHORT-CIRCUIT on a diff touching {path!r} — but {why}. "
        "A green tick from a run that executed nothing is indistinguishable from "
        "a real pass at the merge button. Add the tree to the grep in "
        ".github/workflows/pytest-run.yml."
    )


@pytest.mark.parametrize("path,why", sorted(DELIBERATELY_EXCLUDED.items()))
def test_deliberate_exclusions_stay_excluded(path, why):
    """Pins the trade-off so it stays a DECISION.

    If someone widens the filter to these, CI minutes regress sharply and the
    short-circuit stops paying for itself. Failing here means: state the new
    reasoning, don't drift into it.
    """
    assert not _matches(path), (
        f"{path!r} is now matched by the pytest-run filter, but it was excluded "
        f"deliberately ({why}). Widening here costs CI minutes on nearly every "
        "PR — if that is intended, update this test with the new reasoning."
    )


def test_the_exact_regression_case_would_now_run():
    """PR #8994's real diff — the one that merged on a 9-second green."""
    assert _matches("scripts/ops/purge_vm_runner.sh")


def test_short_circuit_is_still_possible_at_all():
    """Negative control: if EVERYTHING matched, this test file would pass while
    the CI-minutes saving had been silently destroyed. A guard that cannot fail
    proves nothing."""
    assert not _matches("README.md"), (
        "nothing short-circuits any more — the filter has been widened to "
        "everything, so MB-20260706-CI-MINUTES's saving is gone"
    )


def test_workflow_still_parses():
    yaml.safe_load(WORKFLOW.read_text())


# ---------------------------------------------------------------------------
# The self-maintaining half (2026-08-14,
# BL-20260814-COVERAGE-MATRIX-SHORTCIRCUITS-THE-SUITE-THAT-READS-IT).
#
# The COVERED table above is still hand-written, and hand-enumeration is exactly
# what produced instances 1-4 in this file's header: each was fixed by adding
# the one proven tree, and the NEXT tree was found by an incident rather than by
# a check. So this derives the docs/ half from the tests themselves. A new test
# that reads a committed docs/ file fails here until the filter covers it —
# which is the difference between a list someone must remember to update and a
# property CI enforces.
# ---------------------------------------------------------------------------

# A path joined onto a repo-root-ish name. `tmp_path`/fixture roots are excluded
# by NAME (they are locals like `repo`, `root`, `r`, `tmp_path`), so this matches
# the module-level REPO / REPO_ROOT / _REPO_ROOT idiom the real readers use.
_REPO_DOCS_JOIN = re.compile(
    r'\b_?REPO(?:_ROOT)?\s*/\s*["\']docs["\']'
    r'|\b_?REPO(?:_ROOT)?\s*/\s*["\']docs/'
)
# `REPO / "docs" / "research" / "exit-refinement-coverage.json"` -> the path.
#
# ⚠️ `/` IS IN THE CHARACTER CLASS, AND IT WAS NOT UNTIL 2026-09-12. Readers
# spell the join two ways and this scan only ever saw one of them: the
# SEGMENTED form (one quoted string per path component) was seen, and the form
# that puts the whole path in ONE quoted string was dropped.
#
# The drop was silent and, worse, it looked handled: `_REPO_DOCS_JOIN` above
# carries a dedicated alternation for the one-string form, so the line MATCHED
# and then the extractor returned [] (no `/` in the class), the `"docs" not in
# segs` guard skipped it, and that alternation was dead code.
#
# MEASURED on the real tree the day it was fixed: FOUR committed docs/ files
# read as-committed by the suite, every one of them uncovered by the filter,
# every one invisible here -- the merge-slot board read by the claim tests, a
# work object read by the phase-G tests, and two research corpora. They are
# not named literally in this comment on purpose: this scan is a LINE REGEX,
# and an earlier draft that spelled them out was duly picked up as a real
# reader of one of them. That is false-positive class 3 in the AST section
# below, reproduced by the very comment describing the fix.
_SEGMENTS = re.compile(r'["\']([A-Za-z0-9_\-./]+)["\']')


def _committed_docs_readers() -> dict[str, set[str]]:
    """{docs path -> {test files that read it as committed}} across tests/."""
    found: dict[str, set[str]] = {}
    for path in sorted((REPO / "tests").rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not _REPO_DOCS_JOIN.search(line):
                continue
            # Flatten BEFORE testing for "docs": the one-string spelling
            # yields a single segment `docs/claude/session-board.json`, which
            # is not equal to "docs", so testing first is what dropped it.
            segs = [part
                    for seg in _SEGMENTS.findall(line)
                    for part in seg.split("/") if part]
            if "docs" not in segs:
                continue
            tail = segs[segs.index("docs"):]
            # Stop at the first segment carrying an extension. Anything after a
            # filename on the same line is a different string literal, not a
            # deeper path component -- an `encoding="utf-8"` kwarg appended a
            # `/utf-8` to a real finding while this scan was being fixed.
            for i, seg in enumerate(tail):
                if "." in seg:
                    tail = tail[:i + 1]
                    break
            joined = "/".join(tail)
            found.setdefault(joined, set()).add(
                path.relative_to(REPO).as_posix())
    return found


# ---------------------------------------------------------------------------
# THE GENERAL CASE. The docs/ scan below covers ONE tree. The class it belongs
# to (BL-20260813-...) has now recurred FIVE times, and every fix added the one
# tree just proven while the next was found by an incident. ml/configs/ was the
# fifth: tests/ml/_manifest_paths.py reads it AS COMMITTED and a manifest-only
# PR short-circuited to a 12-second green.
#
# So this derives the covered set from EVERY tree the suite reads, not just
# docs/. A new test that reads any committed path fails here until the filter
# covers it or it is excluded on purpose.
#
# WHY AST AND NOT A REGEX OVER SOURCE TEXT. Four false-positive classes were
# measured while building this, each of which a naive scan reports as a real
# finding:
#   1. on-disk vs COMMITTED — `Path.exists()` counts __pycache__/*.pyc, so
#      src/, tests/ and src/runtime/ all looked uncovered. `git ls-files` is
#      the only correct source of "a PR could change this".
#   2. directory vs file — the filter matches `^scripts/`, so the bare string
#      "scripts" does not match while every file under it does. A directory is
#      covered iff its committed children are.
#   3. DOCSTRINGS — a line-regex matched `_REPO_ROOT / "runtime_logs"` inside a
#      docstring in test_runtime_paths_alignment.py that was *describing the
#      historical bug*. The real code there calls runtime_logs_dir(). An AST
#      walk cannot match prose.
#   4. PREFIX sub-expressions — `ast.walk` visits the inner nodes of a
#      multi-segment join, so a three-segment path also yields its one- and
#      two-segment prefixes as separate hits, inflating the count. Only
#      MAXIMAL chains count.
#      (This comment deliberately does NOT spell that join out: the docs/ scan
#      below is a LINE REGEX, and an earlier draft that wrote the example
#      literally was picked up by it as a real reader of a file named "x" —
#      false-positive class 3, demonstrated by this very comment.)
# A check that over-reports gets muted; these four are why the number below is
# trustworthy.
# ---------------------------------------------------------------------------

# ⚠️ MEASURED FROM THE TREE, NOT GUESSED, AND IT WAS THREE NAMES UNTIL
# 2026-09-12. A test module binds its repo root to whatever name it likes, and
# this set decides which of those the AST walk can even see — so a name missing
# here is not a narrower scan, it is a silently blind one. Census over the 1145
# files under tests/, counting module-level assignments of a `__file__`-derived
# path: REPO 139, _REPO_ROOT 44, REPO_ROOT 38 (the three that were here), and
# then ROOT 17, _ROOT 14, _REPO 7 and REAL 1 — 39 files whose every committed
# read was invisible. Adding them surfaced FOUR more live short-circuits.
#
# ⚠️ THE SET IS NO LONGER MAINTAINED BY HAND ALONE. A census asserts it
# against the tree — see `test_every_repo_root_binding_name_is_recognised`
# below — because a name missing here is invisible from a green run, which
# is how six of them went unnoticed.
#
# The remaining names in that census (_SPEC, SCRIPT, OPS, WORKFLOW, ...) are
# NOT repo roots — they point at a specific script or workflow — so they are
# deliberately absent rather than forgotten, and `_chain` would resolve them to
# wrong paths if added.
_PATH_ROOTS = {"REPO", "REPO_ROOT", "_REPO_ROOT", "ROOT", "_ROOT", "_REPO",
                "REAL"}


def _tracked() -> set:
    """Committed files. NOT os.listdir — see false-positive class 1 above."""
    out = subprocess.run(["git", "ls-files"], cwd=REPO,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return set(out.stdout.split())


def _chain(node) -> list | None:
    """`REPO / "a" / "b"` -> ["a", "b"]; None if not rooted at a repo name.

    Also resolves the Call-node spelling of the same join — `os.path.join(REPO,
    "a", "b")` and `Path(REPO, "a", "b")` — via `_call_chain` below. A third
    spelling doing the same thing as the two this scan already covered is
    RECURRENCE-shaped, not a new case, so it is folded into this one function
    rather than given a parallel walk: one join resolver, two entry shapes.
    """
    if isinstance(node, ast.Call):
        return _call_chain(node)
    segs = []
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        right = node.right
        if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
            return None
        segs.append(right.value)
        node = node.left
    if isinstance(node, ast.Name) and node.id in _PATH_ROOTS:
        return list(reversed(segs))
    return None


def _call_chain(node: ast.Call) -> list | None:
    """`os.path.join(ROOT, "a", "b/c")` / `Path(ROOT, "a")` -> ["a", "b", "c"].

    Deliberately narrow: the Call's `func` must resolve to `os.path.join` or a
    bare `Path(...)` constructor, its FIRST argument must be a `Name` in
    `_PATH_ROOTS` (the same census `_chain` trusts for the `/` spelling — a
    name missing there is invisible from both entry shapes, not just one), and
    every remaining argument must be a string constant. A one-string argument
    (`"docs/api-tier-policy.md"`) is split on `/` into segments, same as the
    line-regex scan already does for the one-string `/` spelling — this is
    that fix's Call-node counterpart, not a new policy.

    Anything else — a keyword arg, a non-constant path piece, a first argument
    that is not a recognised repo-root name — returns None rather than
    guessing. A silent wrong guess here is worse than staying blind: it would
    report a covered-looking path that is not the one actually read.
    """
    func = node.func
    is_os_path_join = (
        isinstance(func, ast.Attribute) and func.attr == "join"
        and isinstance(func.value, ast.Attribute) and func.value.attr == "path"
        and isinstance(func.value.value, ast.Name) and func.value.value.id == "os"
    )
    is_path_ctor = isinstance(func, ast.Name) and func.id == "Path"
    if not (is_os_path_join or is_path_ctor):
        return None
    if node.keywords or not node.args:
        return None
    first, rest = node.args[0], node.args[1:]
    if not (isinstance(first, ast.Name) and first.id in _PATH_ROOTS):
        return None
    if not rest:
        return None
    segs: list[str] = []
    for a in rest:
        if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
            return None
        segs.extend(part for part in a.value.split("/") if part)
    return segs or None


def _committed_readers_any_tree() -> dict:
    """{committed path -> {test files that read it}} over the whole suite."""
    tracked = _tracked()
    found: dict = {}
    for path in sorted((REPO / "tests").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        inner = {id(n.left) for n in ast.walk(tree)
                 if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
                 and isinstance(n.left, ast.BinOp)}
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp):
                if id(node) in inner:
                    continue                  # class 4: maximal chains only
            elif not isinstance(node, ast.Call):
                continue
            segs = _chain(node)
            if not segs:
                continue
            rel = "/".join(segs)
            if rel in tracked or any(t.startswith(rel + "/") for t in tracked):
                found.setdefault(rel, set()).add(
                    path.relative_to(REPO).as_posix())
    return found


def _uncovered_children(rel: str, tracked: set) -> list:
    """Committed files under *rel* the filter would let short-circuit."""
    if rel in tracked:                        # class 2: file vs directory
        return [] if _matches(rel) else [rel]
    return sorted(t for t in tracked
                  if t.startswith(rel + "/") and not _matches(t))


def test_the_general_scan_finds_the_tree_that_motivated_it():
    """Negative control FIRST — a scan finding nothing is vacuously green.

    ml/configs/ is the fifth instance of the class and is known to be read by
    tests/ml/_manifest_paths.py, so the scan must see it. If this ever fails,
    the scan is broken and the test below proves nothing.
    """
    found = _committed_readers_any_tree()
    assert "ml/configs" in found, sorted(found)
    assert any("_manifest_paths" in f for f in found["ml/configs"])
    # class 3 control: a docstring mention must NOT be picked up as a read.
    assert "runtime_logs" not in found, (
        "runtime_logs is only named in a DOCSTRING and as a write target; "
        "picking it up means the scan is matching prose again")


def test_the_ast_scan_sees_the_call_node_spelling_of_a_join():
    """PLANTED: `os.path.join(ROOT, ...)` / `Path(ROOT, ...)` must be resolved.

    RECURRENCE of BL-20260912 (fixed the one-string `/` spelling) — a THIRD
    spelling, `os.path.join(REPO, "docs/api-tier-policy.md")`, hid a live
    reader (tests/test_check_api_tier_policy.py) from this exact scan.
    `_chain` only ever walked `BinOp`/`Div` chains; an `os.path.join(...)` or
    `Path(...)` Call is invisible to that walk regardless of how many
    committed files start reading a path that way.

    Asserted over SYNTHETIC paths that do not exist, same discipline as
    `test_the_line_scan_sees_both_spellings_of_a_docs_join` and for the same
    reason: a control that reads the live tree passes for as long as no
    reader happens to use the unseen spelling, which is exactly how this went
    unnoticed twice. `REPO` and `_tracked` are both monkeypatched so the
    control needs no real git-tracked fixture file.
    """
    import tempfile

    def _fake_tracked() -> set:
        return {"docs/planted/call.json", "docs/planted/ctor.json",
                "docs/planted/kw.json"}

    # ⚠️ ASSEMBLED AT RUNTIME — see the twin control above for why: a literal
    # `os.path.join(REPO, "docs/...")` written directly in this file's source
    # would itself be a live reader the OTHER control (docs/-scoped) or this
    # very scan could pick up.
    root = "RE" + "PO"
    call_join = f'A = os.path.join({root}, "docs/planted/call.json")'
    path_ctor = f'B = Path({root}, "docs", "planted", "ctor.json")'
    kwarg_call = f'C = os.path.join({root}, "docs/planted/kw.json", sep="/")'
    non_root_first_arg = 'D = os.path.join("elsewhere", "docs/planted/skip.json")'

    with tempfile.TemporaryDirectory() as td:
        fake = pathlib.Path(td) / "tests"
        fake.mkdir()
        (fake / "test_planted_calls.py").write_text(
            "\n".join([call_join, path_ctor, kwarg_call, non_root_first_arg]),
            encoding="utf-8")
        global REPO, _tracked
        saved_repo, REPO = REPO, pathlib.Path(td)
        saved_tracked, _tracked = _tracked, _fake_tracked
        try:
            found = _committed_readers_any_tree()
        finally:
            REPO = saved_repo
            _tracked = saved_tracked

    assert "docs/planted/call.json" in found, (
        "os.path.join(ROOT, \"one/string/path\") is invisible — the Call-node "
        f"resolver is not being reached: {sorted(found)}")
    assert any("test_planted_calls" in f for f in found["docs/planted/call.json"])
    assert "docs/planted/ctor.json" in found, (
        "Path(ROOT, \"a\", \"b\") (segmented args, not the os.path.join name) "
        f"is invisible: {sorted(found)}")
    assert "docs/planted/kw.json" not in found, (
        "a call carrying a keyword argument was resolved anyway — this walk "
        "must refuse rather than guess at an unrecognised call shape, the "
        f"same discipline `_chain` already applies to the `/` spelling: {sorted(found)}")
    assert "planted/skip.json" not in found and "docs/planted/skip.json" not in found, (
        "a first argument that is not a recognised repo-root name was "
        f"resolved anyway — that is a fabricated path, not a real read: {sorted(found)}")


def _scan_excluded(rel: str) -> bool:
    """Honour the exclusion tables rather than silently overriding them.

    A documented decision outranks a derived one UNTIL evidence falsifies it —
    and then the decision is narrowed, not deleted. That happened twice.
    `data/` carried "bulk data, not asserted structurally by pytest", but this
    scan measured three files under it that ARE read as committed; the filter
    now names those three and the bulk tree stays excluded, so the original
    rationale still holds for everything it was actually about.

    ⚠️ FILE AND TREE ARE SEPARATE STATEMENTS, and they were one until
    2026-09-12. This matched a candidate's TOP-LEVEL component against every
    entry, so a per-FILE `docs/...` entry excluded the whole `docs/` tree — 17
    committed paths the suite reads, four of them genuinely uncovered, in the
    tree where five of this class's six recurrences lived.

    ⚠️ AND IT IS AT MODULE SCOPE FOR A REASON. It was nested inside the test
    below, so the control written for the fix could not call it and
    reimplemented the rule instead — which meant the control passed against its
    OWN copy while the reverted production rule stayed green. Measured by
    planting exactly that revert. A predicate its control cannot reach is a
    predicate nothing proves.
    """
    if rel in DELIBERATELY_EXCLUDED:
        return True
    for tree in EXCLUDED_TREES:
        if rel == tree or rel.startswith(tree + "/"):
            return True
    return False


def test_every_committed_tree_the_suite_reads_is_covered():
    """No committed path the suite reads may short-circuit pytest-run.

    This is the general form of the docs/-only check below. It exists because
    the class recurred five times, each fix covering only the tree just proven.
    If this fails, add the tree to the grep in pytest-run.yml — do not delete
    the assertion, and do not narrow the scan.
    """
    tracked = _tracked()

    uncovered = {
        rel: (sorted(readers), _uncovered_children(rel, tracked))
        for rel, readers in _committed_readers_any_tree().items()
        if not _scan_excluded(rel)
    }
    uncovered = {k: v for k, v in uncovered.items() if v[1]}
    assert not uncovered, (
        "these COMMITTED paths are read by the suite but would SHORT-CIRCUIT "
        "pytest-run, so a PR touching only them merges having executed no "
        f"tests: { {k: (v[0], v[1][:3]) for k, v in uncovered.items()} }"
    )


def test_the_scan_finds_the_reader_we_already_know_about():
    """Negative control FIRST: a scan that finds nothing would make the test
    below vacuously green — the 'unasserted denominator' shape. The matrix
    reader is known to exist, so the scan must see it."""
    found = _committed_docs_readers()
    assert "docs/research/exit-refinement-coverage.json" in found, sorted(found)
    assert any("test_exit_head_per_leg" in f
               for f in found["docs/research/exit-refinement-coverage.json"])


def test_docs_committed_readers_are_all_covered():
    """EVERY committed docs/ file the suite reads must defeat the short-circuit.

    If this fails, a test is asserting over a docs/ file that a docs-only PR can
    change while `pytest-run` reports a green tick without executing anything.
    Fix by adding the path to the grep in .github/workflows/pytest-run.yml and
    to COVERED above — not by deleting the assertion.
    """
    uncovered = {
        path: sorted(readers)
        for path, readers in _committed_docs_readers().items()
        if not _matches(path)
    }
    assert not uncovered, (
        "these docs/ files are read as COMMITTED by the suite but would "
        f"SHORT-CIRCUIT pytest-run: {uncovered}. A green tick from a run that "
        "executed nothing is indistinguishable from a real pass at the merge "
        "button (this is how PR #9208 merged and left main red)."
    )


# ---------------------------------------------------------------------------
# THE TWO BLIND SPOTS, 2026-09-12. Both scans above were reporting CLEAN over
# four committed files the suite reads as-committed and the filter did not
# cover, each for its own reason. Neither blind spot is visible from a green
# run — that is the whole problem with them — so each gets a control that goes
# red the moment the fix is reverted.
# ---------------------------------------------------------------------------


def test_the_line_scan_sees_both_spellings_of_a_docs_join():
    """PLANTED: the one-string join form must be extracted, not dropped.

    Readers spell the join two ways and the scan only ever saw the segmented
    one. The drop LOOKED handled — `_REPO_DOCS_JOIN` carries an alternation
    for the one-string form, so the line matched and the extractor then
    returned nothing, making that alternation dead code.

    Asserted over SYNTHETIC lines, deliberately: a control that reads the live
    tree passes for as long as no reader happens to use the unseen spelling,
    which is exactly how this went unnoticed. The paths below do not exist.
    """
    import tempfile

    # ⚠️ ASSEMBLED AT RUNTIME, NEVER WRITTEN OUT. This scan is a LINE REGEX
    # over the source of every file under tests/ — including this one — so a
    # synthetic example spelled literally here IS picked up as a live reader of
    # a path that does not exist. That is false-positive class 3, and the first
    # draft of this control duly reddened the two live scans with its own
    # fixtures. Keeping the root and the tree name out of the source text is
    # what makes the control a control instead of a contaminant.
    root, tree = "RE" + "PO", "do" + "cs"
    segmented = f'A = {root} / "{tree}" / "planted" / "seg.json"'
    one_string = f'B = {root} / "{tree}/planted/one.json"'
    with_kwarg = (f'C = ({root} / "{tree}/planted/kw.json")'
                  '.read_text(encoding="utf-8")')

    with tempfile.TemporaryDirectory() as td:
        fake = pathlib.Path(td) / "tests"
        fake.mkdir()
        (fake / "test_planted.py").write_text(
            "\n".join([segmented, one_string, with_kwarg]), encoding="utf-8")
        global REPO
        saved, REPO = REPO, pathlib.Path(td)
        try:
            found = _committed_docs_readers()
        finally:
            REPO = saved

    assert "docs/planted/seg.json" in found, (
        "the SEGMENTED spelling is no longer seen — this scan is broken "
        f"outright, not merely narrow: {sorted(found)}")
    assert "docs/planted/one.json" in found, (
        "the ONE-STRING spelling was dropped. `_REPO_DOCS_JOIN` matches the "
        "line and the segment extractor must not then discard it — that "
        "combination is what hid four real readers: "
        f"{sorted(found)}")
    assert "docs/planted/kw.json" in found, (
        "a trailing kwarg literal on the same line broke the path. The walk "
        f"must stop at the filename segment: {sorted(found)}")
    assert not any(k.endswith("/utf-8") for k in found), (
        "an `encoding=` literal was appended as a path component — the path "
        f"is fabricated and will never match the filter: {sorted(found)}")


def test_a_per_file_exclusion_does_not_swallow_its_whole_tree():
    """PLANTED: the file/tree distinction, which was collapsed until 2026-09-12.

    `_excluded` matched a candidate's TOP-LEVEL component against every entry,
    so one per-file `docs/...` entry excluded all of `docs/` from the general
    scan — the tree where five of this class's six recurrences lived. A tree
    exclusion must now be WRITTEN as one.
    """
    tracked = _tracked()

    # ⚠️ THE PRODUCTION PREDICATE, NOT A COPY. The first draft of this control
    # reimplemented the rule locally, so it asserted over its own logic while
    # the real one stayed nested inside the test below — and planting the exact
    # revert left this control GREEN. That is what a control that cannot reach
    # its subject is worth.
    excluded = _scan_excluded

    file_entries = [e for e in DELIBERATELY_EXCLUDED if e not in EXCLUDED_TREES]
    assert file_entries, "nothing to test — the exclusion table is empty"

    for entry in file_entries:
        assert excluded(entry), f"{entry} no longer excludes itself"
        top = entry.split("/", 1)[0]
        if top in EXCLUDED_TREES:
            continue          # declared as a tree ON PURPOSE; that is the point
        sibling = f"{top}/__planted_sibling_of_{entry.split('/')[-1]}"
        assert not excluded(sibling), (
            f"the per-FILE entry {entry!r} is swallowing its whole {top}/ tree "
            f"({sibling!r} reads as excluded). A tree exclusion must be stated "
            "in EXCLUDED_TREES, with a reason — this collapse is what blinded "
            "the general scan over docs/")

    # And the converse, so the narrowing did not quietly drop a real tree
    # exclusion: `data/` is declared and must still cover its children.
    assert "data" in EXCLUDED_TREES, "the bulk data tree lost its exclusion"
    assert excluded("data/ohlcv/README.md"), (
        "EXCLUDED_TREES no longer covers a subtree — the narrowing went too "
        "far and the CI-minutes trade-off it protects is gone")
    assert any(t.startswith("data/") for t in tracked), (
        "no committed file under data/ — this control is vacuous")


# ⚠️ REMOVED 2026-09-21 by the operating reset: `test_the_two_scans_agree_on_the_live_tree`.
# It asserted a property of the LIVE retired guard registrations, which is archived under
# docs/archive/2026-09-21-operating-reset/. Its subject is gone, so the
# test cannot pass and cannot be made to pass — it is removed WITH its
# subject rather than skipped, because a permanently-skipped test is a
# control in name only. Its fixture-based siblings in this file are
# UNTOUCHED and still green: they test the CODE, which still exists.
# Restore it from git history if the register ever returns.


# ---------------------------------------------------------------------------
# THE ROOT-NAME CENSUS, 2026-09-12. `_PATH_ROOTS` decides which modules the AST
# walk can see at all, and it was three names against a tree that uses seven —
# 39 test files whose every committed read was invisible, four of them genuinely
# short-circuiting. A hand-maintained set that can fall behind unnoticed is the
# same defect this whole file exists to stop, one level up.
# ---------------------------------------------------------------------------

_ROOT_BINDING = re.compile(r"\.parents\[(\d+)\]$")


def _repo_root_binding_names() -> dict[str, set[str]]:
    """{name -> {test files}} for every module-level binding of the REPO ROOT.

    A binding counts only when the assigned expression is EXACTLY
    `...__file__....parents[N]` with N equal to the file's own depth below the
    repo. Both halves are load-bearing and were measured:

      * requiring the expression to END at `parents[N]` is what separates a
        repo root from a path to a specific file — without it `_SPEC`,
        `SCRIPT`, `OPS` and `WORKFLOW` (all `parents[1] / "scripts" / ...`)
        come back as roots, and adding them to `_PATH_ROOTS` would make
        `_chain` resolve real reads to fabricated paths;
      * requiring N to equal the file's depth is what stops a deliberate
        parent-of-the-repo or subdirectory handle counting as the root.
    """
    found: dict[str, set[str]] = {}
    for path in sorted((REPO / "tests").rglob("*.py")):
        depth = len(path.relative_to(REPO).parts) - 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            src = ast.unparse(node.value)
            if "__file__" not in src:
                continue
            m = _ROOT_BINDING.search(src)
            if not m or int(m.group(1)) != depth:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, set()).add(
                        path.relative_to(REPO).as_posix())
    return found


def test_every_repo_root_binding_name_is_recognised():
    """PLANTED: a root name the AST walk cannot see makes it silently blind.

    Not narrow — BLIND. Every committed path a module reads through an
    unrecognised root is absent from the scan's output, and absence renders
    exactly like coverage. Measured 2026-09-12 before this control existed:
    `_PATH_ROOTS` held three names, the tree used seven, and the four missing
    ones hid 39 files and four live short-circuits.
    """
    census = _repo_root_binding_names()
    assert len(census) >= 3, (
        f"the census found almost nothing ({sorted(census)}) — the detector is "
        "broken, and this control proves nothing in that state")
    # Positive control: the name the scan has always used must be found, and
    # found in bulk, or the discriminator has stopped discriminating.
    assert len(census.get("REPO", ())) > 50, (
        f"'REPO' should be the dominant root binding: {len(census.get('REPO', ()))}")
    # Negative control: a path to a SPECIFIC FILE must not read as a root.
    assert "_SPEC" not in census, (
        "`_SPEC` is `parents[N] / 'scripts' / ...`, a path to one script. If it "
        "reads as a repo root the expression-end rule has been dropped, and "
        "adding such names to _PATH_ROOTS makes _chain resolve to fabricated "
        "paths")

    unrecognised = {n: sorted(f)[:3] for n, f in census.items()
                    if n not in _PATH_ROOTS}
    assert not unrecognised, (
        "these module-level REPO-ROOT bindings are not in _PATH_ROOTS, so every "
        "committed path the modules below read is INVISIBLE to the general "
        f"scan: {unrecognised}. Add the name — do not narrow this census.")


# ⚠️ REMOVED 2026-09-21 by the operating reset: `test_the_work_digest_source_coverage_runs_on_a_pr`.
# It asserted a property of the LIVE retired guard registrations, which is archived under
# docs/archive/2026-09-21-operating-reset/. Its subject is gone, so the
# test cannot pass and cannot be made to pass — it is removed WITH its
# subject rather than skipped, because a permanently-skipped test is a
# control in name only. Its fixture-based siblings in this file are
# UNTOUCHED and still green: they test the CODE, which still exists.
# Restore it from git history if the register ever returns.
