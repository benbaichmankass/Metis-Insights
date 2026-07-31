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
