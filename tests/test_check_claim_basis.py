"""Tests for scripts/check_claim_basis.py (P2.5, claim-basis-guard).

The failure path is exercised for every claim shape the guard recognizes —
a guard whose red path is never run is a green that checked nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from check_claim_basis import check_new_rows  # noqa: E402


def _blog(rows: list[dict]) -> str:
    return json.dumps({"items": rows})


def _row(rid: str, description: str) -> dict:
    return {"id": rid, "title": "t", "description": description}


BASE = _blog([_row("BL-OLD", "an old row claiming 99% with no basis at all")])


class TestClaimDetection:
    def test_percentage_without_basis_fails(self):
        head = _blog([_row("BL-OLD", "an old row claiming 99% with no basis at all"),
                      _row("BL-NEW", "the axis reads calm 99% of the time")])
        fails = check_new_rows(BASE, head, "x.json")
        assert len(fails) == 1 and "BL-NEW" in fails[0]

    def test_r_figure_without_basis_fails(self):
        head = _blog([_row("BL-NEW", "transitional/calm cell shows +32.66R, promote it")])
        assert len(check_new_rows(_blog([]), head, "x.json")) == 1

    def test_dollar_total_without_basis_fails(self):
        head = _blog([_row("BL-NEW", "a -$36,018.60 exit leak was found")])
        assert len(check_new_rows(_blog([]), head, "x.json")) == 1

    def test_preexisting_row_is_grandfathered(self):
        # BL-OLD asserts 99% basis-less but exists in base — diff-scoped.
        fails = check_new_rows(BASE, BASE, "x.json")
        assert fails == []

    def test_row_without_numbers_passes(self):
        head = _blog([_row("BL-NEW", "the relay executed the cmd block twice")])
        assert check_new_rows(_blog([]), head, "x.json") == []


class TestBasisRecognition:
    def test_n_of_m_passes(self):
        head = _blog([_row("BL-NEW", "fabricated share 65.3% (206 of 829 closed rows)")])
        assert check_new_rows(_blog([]), head, "x.json") == []

    def test_slash_denominator_passes(self):
        head = _blog([_row("BL-NEW", "recovery is 7.3% (24/327)")])
        assert check_new_rows(_blog([]), head, "x.json") == []

    def test_n_equals_passes(self):
        head = _blog([_row("BL-NEW", "win rate 54% at n=118")])
        assert check_new_rows(_blog([]), head, "x.json") == []

    def test_count_noun_passes(self):
        head = _blog([_row("BL-NEW", "61% measured across 829 rows in the window")])
        assert check_new_rows(_blog([]), head, "x.json") == []

    def test_date_window_passes(self):
        head = _blog([_row("BL-NEW",
                           "fabricated PnL is -$36,018.60 over 2026-06-08 to 2026-07-30")])
        assert check_new_rows(_blog([]), head, "x.json") == []

    def test_since_date_passes(self):
        head = _blog([_row("BL-NEW", "coverage is 60.8% since 2026-07-30 deploy")])
        assert check_new_rows(_blog([]), head, "x.json") == []


class TestFieldOfView:
    """`_ROW_TEXT_FIELDS` is the guard's whole field of view, in BOTH directions.

    Measured 2026-08-20 across all three backlogs (940 rows): 198 rows carried
    a quantitative claim in a then-scanned field, and **65 more carried one
    ONLY in `detail`/`evidence`** — 24.7% of the 263 claim-bearing rows, never
    checked at all; 16 of those had no basis anywhere and would have failed.
    So the pre-2026-08-20 guard reported a clean negative over a population it
    could not see, which is the unasserted-denominator class (sub-class C) the
    repo's own diagnostic-provenance rule names.

    Each test below is a PLANTED CONTROL: it fails if the field is dropped
    from `_ROW_TEXT_FIELDS`, so the widening cannot silently regress.
    """

    # --- false-negative direction: claim hidden in an unscanned field -------
    def test_a_basisless_claim_in_detail_is_CAUGHT(self):
        head = _blog([{"id": "BL-NEW", "title": "t",
                       "detail": "the failure rate reached 47% on this account"}])
        out = check_new_rows(BASE, head, "x.json")
        assert out, (
            "a basis-less claim living only in `detail` passed unseen — the "
            "guard is blind to the field 226 health-backlog rows use"
        )

    def test_a_basisless_claim_in_evidence_is_CAUGHT(self):
        head = _blog([{"id": "BL-NEW", "title": "t",
                       "evidence": "fabricated PnL totalled $247,683.78"}])
        assert check_new_rows(BASE, head, "x.json")

    def test_negative_control_a_truly_unscanned_field_still_passes(self):
        """Proves the tests above discriminate on the FIELD, not the text.

        Same basis-less claim, parked in a field deliberately NOT in the
        tuple. It passes — so the two tests above are detecting field
        coverage, not merely 'the guard flags 47% wherever it appears'.
        """
        head = _blog([{"id": "BL-NEW", "title": "t",
                       "some_field_we_do_not_scan":
                           "the failure rate reached 47% on this account"}])
        assert check_new_rows(BASE, head, "x.json") == []

    # --- false-positive direction: basis present in an unscanned field ------
    def test_a_claim_in_title_whose_BASIS_is_in_detail_passes(self):
        """The false positive that surfaced this: title states 94%, detail
        states n=1. Failing that row teaches sessions to duplicate prose into
        `description` to appease a guard, which is worse than the gap."""
        head = _blog([{"id": "BL-NEW",
                       "title": "a 94%-full training box was invisible",
                       "detail": "ONE reading, n=1, not a series: 45G total"}])
        assert check_new_rows(BASE, head, "x.json") == [], (
            "a correct row stating its basis in `detail` was failed"
        )

    def test_the_field_tuple_is_declared_once_and_used(self):
        """No second hardcoded list — the drift this repo keeps paying for."""
        import check_claim_basis as cb
        assert "detail" in cb._ROW_TEXT_FIELDS
        assert "evidence" in cb._ROW_TEXT_FIELDS
        src = (_REPO_ROOT / "scripts" / "check_claim_basis.py").read_text(encoding="utf-8")
        assert src.count('"detail"') == 1, (
            "`detail` appears more than once in the guard — a second field "
            "list has been introduced and the two will drift"
        )

    def test_diff_scoping_still_shields_preexisting_rows(self):
        """Widening the view must not retro-fail the 16 rows it can now see.

        Without this, the widening would have turned every subsequent PR red
        on rows nobody in that PR wrote — the change would have been reverted
        and the blind spot restored.
        """
        old = {"id": "BL-OLD-HIDDEN", "title": "t",
               "detail": "an old row claiming 99% with no basis at all"}
        base = _blog([old])
        head = _blog([old, {"id": "BL-NEW", "title": "t",
                            "detail": "clean row, 5 of 9 dirs"}])
        assert check_new_rows(base, head, "x.json") == []
