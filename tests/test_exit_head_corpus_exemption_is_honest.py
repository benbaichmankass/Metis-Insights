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
    """The exemption states 2 disagreements. Recompute and hold it to that.

    A number quoted in a comment and never recomputed is how the original reason
    went stale in the first place. If a round is added or a status is re-graded,
    this fails and the comment gets updated with it — the count is evidence, not
    decoration.

    ⚠️ THE STATUS IS NORMALISED THROUGH `guard._base_status`, AND THAT IS LOAD-
    BEARING (fixed 2026-08-23). This test used to match the status by EXACT
    membership against the literal ``"blocked"``. `blocked:<reason>` is the repo
    convention rather than an exception — 51 of the 53 blocked cells in the
    matrix carry a reason, across nine distinct ones — so the exact test matched
    only the single bare ``blocked`` cell and missed every qualified one. It went
    unnoticed because this column happened to have no qualified-blocked cells
    until three ict_scalp cells gained ``blocked:no_lever_consumer_in_unit``,
    at which point they read as three fresh disagreements and the count appeared
    to jump 10 -> 13.

    RECORDING WHY A CELL IS BLOCKED MUST NOT FLIP WHETHER IT AGREES. A qualified
    state read as an unknown state is the collapsed-state class, and raising the
    stated count to 13 would have enshrined a parsing artifact as evidence —
    precisely the stale-number failure this file exists to prevent, inverted.

    The guard itself was never wrong: `find_stale_blocks` has always normalised
    via `_base_status`. Importing that same helper rather than re-deriving the
    rule here is deliberate — two copies of "what counts as blocked" is how they
    drift apart.

    ⚠️ 9 -> 2 ON 2026-09-06 (MI-145), AND THE DELTA IS REAL — this test's own
    failure message says to check that first, so here is the check. All nine
    were `candidate` (round) vs `honest_negative` (matrix). Seven moved to
    `blocked:no_lever_consumer_in_unit` because their monitor unit implements no
    exit head, established by CALLING `monitor_unit_for` and
    `exit_mechanism_coverage.module_implements`. That is a change of
    DISPOSITION, not a `blocked` cell gaining a reason suffix — the artifact
    guarded against two paragraphs up — and `candidate offline` genuinely does
    not contradict `unshippable for want of a consumer`. The two survivors,
    trend_donchian_{ada,xrp}_4h, run on a unit that DOES implement the head, so
    their contradiction is live; keeping them visible is the point of lowering
    the number rather than leaving it at 9.

    ⚠️ 2 -> 4 ON 2026-09-06 (MI-150, PR #11140), AND THE RISE IS REAL — it is
    the MI-145 paragraph above EXPIRING, not new evidence. That re-grade's
    compatibility argument holds only *while no consumer exists*: MI-150 makes
    the `ict_scalp` unit implement the exit head, so
    `blocked:no_lever_consumer_in_unit` became factually false for its legs and
    the cells were corrected. Three keep a still-compatible blocked reason
    (`blocked:no_scalp_artifact_published` — every published head is 1h against
    their 5m/15m), ict_scalp_eth_15m agrees `honest_negative` with its round,
    and ict_scalp_{avax_5m,xrp_15m} land `honest_negative` against a `candidate`
    round — the trend_donchian_{ada,xrp}_4h shape exactly, on a unit that now
    DOES implement the head. So two of MI-145's seven were parked behind an
    excuse this PR removes, and part of the 9 -> 2 drop was always temporary.
    ⚠️ THIS IS THE ONE DIRECTION THE COUNT MAY RISE WITH NO NEW ROUND. It is
    NOT a promotion: no cell moved toward `shipped`/`passed_unshipped`, the
    2026-08-23 SHIP BLOCKED operator decision stands, and MI-150 lands
    annotate-only and disarmed.
    """
    rows = [json.loads(x) for x in ROUNDS.read_text().splitlines() if x.strip()]
    matrix = json.loads(MATRIX.read_text())
    by_strategy: dict[str, dict] = {}
    for r in matrix["rows"]:
        by_strategy.setdefault(r.get("strategy"), r)

    disagree = []
    for rd in rows:
        cell = (by_strategy.get(rd["leg"], {}) or {}).get("exit_head_ml") or {}
        # `blocked:<reason>` -> `blocked`. See the docstring: the guard's own
        # helper, never a second copy of the rule.
        st, v = guard._base_status(cell.get("status")), rd.get("verdict")
        ok = ((v == "candidate"
               and st in {"shipped", "passed_unshipped", "pending", "blocked"})
              or (v == "honest_negative"
                  and st in {"honest_negative", "shipped_gate_failed",
                             "blocked", "pending"}))
        if not ok:
            disagree.append(rd["leg"])

    reason = guard.CORPUS_EXEMPT_LEVERS["exit_head_ml"]
    assert "4 DISAGREE" in reason, "the exemption no longer states a count"
    assert len(disagree) == 4, (
        f"the exemption says 4 disagreements; recomputing over the committed "
        f"evidence gives {len(disagree)}: {sorted(disagree)}. Update the "
        "exemption text — a stale count is how this exemption went wrong before. "
        "But FIRST check whether the delta is real: a cell gaining a "
        "`blocked:<reason>` suffix is the same disposition with its cause "
        "recorded, and must NOT move this count (that is what `_base_status` "
        "above is for).")
