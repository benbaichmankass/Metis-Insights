"""The `exit_head_ml` corpus exemption must not outlive its stated reason.

`check_matrix_corpus_agreement.py` exempts three lever columns from the
matrix-vs-corpus cross-check. `exit_head_ml`'s exemption originally read
"Nothing is committed back, so no (leg, lever) row can exist here" — true when
written, and FALSE since `docs/research/m20-exit-head-rounds.jsonl` was
committed. An exemption whose justification has quietly become untrue is worse
than no exemption: the summary line still says "declared", so a reader sees a
deliberate carve-out rather than a stale one.

These tests make the staleness impossible to re-introduce silently. They do NOT
assert the exemption should be lifted — that is gated on a Tier-3 re-grade of
ten cells — only that its stated reason stays true while it stands.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import check_matrix_corpus_agreement as guard  # noqa: E402

ROUNDS = REPO / "docs" / "research" / "m20-exit-head-rounds.jsonl"
# Kept on ONE line, deliberately. `test_pytest_run_filter.py`'s derived check
# scans tests/ LINE BY LINE for a docs/ path joined onto the repo root, so a
# wrapped join truncates to the directory (`docs/research`) and fails that
# guard. Both files here are individually named in the pytest-run grep; the
# tree is NOT, and must not be widened to it (`exit-refinement-notes.md` is a
# pinned deliberate exclusion). Truncation there is fail-SAFE — a directory
# never matches the filename-scoped grep, so it errs strict — but the honest
# fix is to let the scanner see the real path, not to widen the filter.
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"


def test_the_exemption_does_NOT_ASSERT_that_nothing_is_committed() -> None:
    """The false sentence may be QUOTED as retracted, never asserted.

    A first version of this test banned the substring outright and failed
    immediately — because the corrected exemption quotes the old sentence in
    order to retract it, which is the most useful thing it can do with it. The
    test could not tell an assertion from a quoted retraction.

    That distinction is not novel here: `check_impossibility_claims.py` already
    separates a claim from the same claim inside a REFUTING block, and the
    `# provenance:` override is excluded from its own evidence for the same
    reason. So the rule is context, not presence — if the sentence appears, a
    retraction marker must appear with it.
    """
    reason = guard.CORPUS_EXEMPT_LEVERS["exit_head_ml"]
    claim = "Nothing is committed back, so no (leg, lever) row can exist"
    if claim in reason:
        assert "NO LONGER TRUE" in reason and "It read:" in reason, (
            "the exemption states the sentence without retracting it, while "
            f"{ROUNDS.name} holds "
            f"{len([x for x in ROUNDS.read_text().splitlines() if x.strip()])} "
            "rows. Quote it as retracted or drop it — do not assert it.")


def test_the_exemption_ACKNOWLEDGES_the_committed_evidence() -> None:
    """It must name the file whose existence contradicts the original reason."""
    reason = guard.CORPUS_EXEMPT_LEVERS["exit_head_ml"]
    assert "m20-exit-head-rounds.jsonl" in reason, (
        "the exemption does not mention the committed evidence file, so a "
        "reader cannot tell the carve-out is a staging step rather than a "
        "statement that no evidence exists")


def test_the_committed_evidence_actually_exists_and_is_non_trivial() -> None:
    """Anchors the claim above against the real file.

    If the rounds file is ever emptied or removed, the exemption's ORIGINAL
    reason becomes true again and this fires — pointing at the exemption rather
    than letting the two drift apart silently.
    """
    assert ROUNDS.is_file(), f"{ROUNDS} is gone — re-read the exemption's reason"
    rows = [json.loads(x) for x in ROUNDS.read_text().splitlines() if x.strip()]
    assert len(rows) >= 30, f"expected the committed round corpus, found {len(rows)} row(s)"
    assert all(r.get("lever") == "exit_head_ml" for r in rows)


def test_the_disagreement_count_in_the_exemption_is_still_accurate() -> None:
    """The exemption states 10 disagreements. Recompute and hold it to that.

    A number quoted in a comment and never recomputed is how the original reason
    went stale in the first place. If a round is added or a status is re-graded,
    this fails and the comment gets updated with it — the count is evidence, not
    decoration.
    """
    rows = [json.loads(x) for x in ROUNDS.read_text().splitlines() if x.strip()]
    matrix = json.loads(MATRIX.read_text())
    by_strategy: dict[str, dict] = {}
    for r in matrix["rows"]:
        by_strategy.setdefault(r.get("strategy"), r)

    disagree = []
    for rd in rows:
        cell = (by_strategy.get(rd["leg"], {}) or {}).get("exit_head_ml") or {}
        st, v = cell.get("status"), rd.get("verdict")
        ok = ((v == "candidate"
               and st in {"shipped", "passed_unshipped", "pending", "blocked"})
              or (v == "honest_negative"
                  and st in {"honest_negative", "shipped_gate_failed",
                             "blocked", "pending"}))
        if not ok:
            disagree.append(rd["leg"])

    reason = guard.CORPUS_EXEMPT_LEVERS["exit_head_ml"]
    assert "10 DISAGREE" in reason, "the exemption no longer states a count"
    assert len(disagree) == 10, (
        f"the exemption says 10 disagreements; recomputing over the committed "
        f"evidence gives {len(disagree)}: {sorted(disagree)}. Update the "
        "exemption text — a stale count is how this exemption went wrong before.")
