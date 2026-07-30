"""The dangling-tracking-reference guard.

A doc saying "tracked by BL-X" where BL-X was never filed is worse than no reference: it
reads as tracked, so nobody re-checks it. Four such ids existed on 2026-07-30, including
BL-20260730-M1-PRICE-JOIN-DEAD -- the canonical example in the binding "Green is not
evidence" rule, cited from four workflows, resolving to nothing.

The guard is DIFF-SCOPED on purpose (~109 pre-existing dangling refs repo-wide); failing on
all of them would make an alarm every session walks past, which the rules name as itself a
P1 bug. `TestScopedNotGlobal` pins that choice so a later "improvement" to fail globally
has to argue with it.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "ops"))

cbr = pytest.importorskip("check_backlog_refs")


def _backlog(root, ids):
    d = root / "docs" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "health-review-backlog.json").write_text(
        json.dumps({"items": [{"id": i} for i in ids]}), encoding="utf-8")
    return root


class TestRefPattern:
    def test_matches_the_three_prefixes(self):
        for s in ("BL-20260730-FOO", "MB-20260730-FOO-BAR", "FU-20260511-001"):
            assert cbr.REF.findall(s) == [s]

    def test_does_not_match_a_bare_trailing_dash(self):
        """A partial match inside prose must not masquerade as an id -- that artefact
        produced false 'dangling' hits in the first measurement."""
        assert cbr.REF.findall("BL-20260616-LTMGMT-lowercase") == ["BL-20260616-LTMGMT"]

    def test_ignores_non_ids(self):
        assert cbr.REF.findall("BL-2026-FOO") == []
        assert cbr.REF.findall("PR-20260730-FOO") == []


class TestDangling:
    def test_unfiled_id_is_dangling(self):
        assert cbr.dangling({"BL-20260730-X": {"a.md"}}, {"BL-20260730-Y"})

    def test_filed_id_is_clean(self):
        assert cbr.dangling({"BL-20260730-X": {"a.md"}}, {"BL-20260730-X"}) == {}

    def test_a_row_in_another_rows_refs_does_not_count_as_filed(self, tmp_path):
        """The exact shape of the M1-PRICE-JOIN-DEAD miss: cited in other rows' `refs`
        for weeks, never filed as a row of its own."""
        d = tmp_path / "docs" / "claude"
        d.mkdir(parents=True)
        (d / "health-review-backlog.json").write_text(json.dumps(
            {"items": [{"id": "BL-1", "refs": ["BL-20260730-M1-PRICE-JOIN-DEAD"]}]}),
            encoding="utf-8")
        assert "BL-20260730-M1-PRICE-JOIN-DEAD" not in cbr.filed_ids(tmp_path)


class TestFiledIds:
    def test_reads_ids_from_backlog(self, tmp_path):
        _backlog(tmp_path, ["BL-A", "BL-B"])
        assert cbr.filed_ids(tmp_path) == {"BL-A", "BL-B"}

    def test_malformed_backlog_does_not_crash(self, tmp_path):
        d = tmp_path / "docs" / "claude"
        d.mkdir(parents=True)
        (d / "x-backlog.json").write_text("{not json", encoding="utf-8")
        assert cbr.filed_ids(tmp_path) == set()


class TestScopedNotGlobal:
    def test_full_sweep_is_report_only(self, tmp_path):
        """--all must exit 0. It reports pre-existing debt; failing on ~109 historical
        refs would create the walked-past alarm the rules call a P1 bug."""
        _backlog(tmp_path, [])
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs" / "a.md").write_text("see BL-20260730-NOPE", encoding="utf-8")
        assert cbr.main(["--repo-root", str(tmp_path), "--all"]) == 0

    def test_requires_a_base_when_not_sweeping(self, tmp_path):
        _backlog(tmp_path, [])
        assert cbr.main(["--repo-root", str(tmp_path)]) == 1


class TestThisRepo:
    def test_the_four_late_filed_ids_now_resolve(self):
        filed = cbr.filed_ids()
        for i in ("BL-20260730-M1-PRICE-JOIN-DEAD",
                  "BL-20260730-RESEARCH-VENUE-FEE",
                  "BL-20260730-EXITHEAD-REPLAY-SINGLE-THRESHOLD",
                  "BL-20260730-CITED-BUT-UNFILED-BACKLOG-IDS"):
            assert i in filed, f"{i} must stay filed"
