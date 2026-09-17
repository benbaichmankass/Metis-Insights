"""A MISSPELLED override must not be reported as a FABRICATED one.

`check_register_field_loss.py` accepts a declaration entry as long as it
carries a string `file`. An entry that names neither an `id` nor any `keys` —
the shape you get by writing `row_id` instead of `id` — is structurally unable
to cover any finding, so it lands in `phantom` and was reported as a FALSE
STATEMENT ABOUT THE DIFF: *"An override must be a TRUE statement about the
change, or it is a silencer."*

The declaration was true. It was misspelled. That is UNPROVENANCED DIAGNOSTIC
OUTPUT sub-class **A** in `CLAUDE.md` — a label naming a condition no code path
tested — and the remedy this repo prescribes is to branch on the actual
condition rather than reword the label.

⚠️ BOTH STATES STILL FAIL. Nothing here lets a malformed override excuse a
removal; only the message and its remedy differ.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import check_register_field_loss as rfl  # noqa: E402


def decl(**kw):
    base = {"file": "R.json", "id": None, "keys": [], "declared_in": "d.json",
            "entry_keys": ["file"]}
    base.update(kw)
    return base


class TestWhichDeclarationsAreStructurallyInert:
    @pytest.mark.parametrize("d,want", [
        (decl(id="s1", keys=[]), False),            # the whole row
        (decl(id="s1", keys=["observed"]), False),  # named fields of a row
        (decl(id=None, keys=["extra"]), False),     # a top-level key
        (decl(id=None, keys=[]), True),             # neither — inert
    ])
    def test_is_malformed(self, d, want):
        assert rfl.is_malformed(d) is want

    def test_the_inert_shape_covers_none_of_the_three_finding_kinds(self):
        """Derived, not asserted: `_covers` is the authority on what a
        declaration can excuse, and it says this one excuses nothing."""
        d = decl(id=None, keys=[])
        findings = [
            {"path": "R.json", "kind": rfl.FIELD_LOSS, "id": "s1", "key": "k"},
            {"path": "R.json", "kind": rfl.ROW_LOSS, "id": "s1", "key": None},
            {"path": "R.json", "kind": rfl.TOPLEVEL_LOSS, "id": None, "key": "x"},
        ]
        assert not any(rfl._covers(d, f) for f in findings)

    def test_a_well_formed_one_does_cover_its_finding(self):
        """The control: without it, 'covers nothing' would pass vacuously."""
        d = decl(id="s1", keys=[])
        assert rfl._covers(d, {"path": "R.json", "kind": rfl.ROW_LOSS,
                               "id": "s1", "key": None})


class TestTheMessageNamesTheRightFault:
    def _render(self, d):
        return rfl.render({"ok": False, "summary": "s", "phantom": [d],
                           "findings": [], "excused": [], "unreadable": []})

    def test_a_misspelled_entry_renders_as_malformed(self):
        out = self._render(decl(entry_keys=["file", "row_id", "why"]))
        assert "MALFORMED declaration" in out
        assert "PHANTOM declaration" not in out

    def test_it_does_not_call_a_misspelling_a_silencer(self):
        out = self._render(decl(entry_keys=["file", "row_id", "why"]))
        assert "it is a silencer" not in out
        assert "TRUE statement about the change" not in out

    def test_it_names_the_keys_the_entry_actually_carries(self):
        """The remedy is the spelling, so the spelling must be on screen."""
        out = self._render(decl(entry_keys=["file", "row_id", "why"]))
        assert "'row_id'" in out

    def test_it_states_the_schema(self):
        out = self._render(decl(entry_keys=["file", "row_id"]))
        assert '"id"' in out and '"keys"' in out

    def test_a_well_formed_declaration_that_matches_nothing_is_still_a_phantom(self):
        """The silencer check is what stops the directory being a blanket
        override. Nothing here may soften it."""
        out = self._render(decl(id="s1", keys=[],
                                entry_keys=["file", "id", "keys"]))
        assert "PHANTOM declaration" in out
        assert "MALFORMED" not in out
        assert "it is a silencer" in out


class TestBothStatesStillFail:
    def test_a_malformed_declaration_excuses_nothing(self):
        finding = {"path": "R.json", "kind": rfl.ROW_LOSS, "id": "s1",
                   "key": None, "why": "gone"}
        remaining, excused, phantom = rfl.apply_removals(
            [finding], [decl(entry_keys=["file", "row_id"])])
        assert remaining == [finding]
        assert excused == []
        assert len(phantom) == 1

    def test_the_verdict_is_not_ok_with_a_malformed_declaration(self):
        v = {"ok": False, "summary": "s", "phantom": [decl()],
             "findings": [], "excused": [], "unreadable": []}
        assert "OK — no shared register" not in rfl.render(v)


class TestTheSummaryCountsThemApart:
    def _summary(self, tmp_path, entries):
        """Drive the real `load_removals`, so the count is over what the guard
        would actually read rather than over a hand-built dict."""
        d = tmp_path / ".github" / "register-removals"
        d.mkdir(parents=True)
        (d / "z.json").write_text(json.dumps({"removals": entries}))
        return rfl.load_removals(tmp_path)

    def test_load_removals_records_the_entrys_own_keys(self, tmp_path):
        got = self._summary(tmp_path, [{"file": "R.json", "row_id": "s1"}])
        assert got[0]["entry_keys"] == ["file", "row_id"]
        assert rfl.is_malformed(got[0])

    def test_a_well_formed_entry_is_read_as_before(self, tmp_path):
        got = self._summary(tmp_path, [{"file": "R.json", "id": "s1",
                                        "keys": ["a"]}])
        assert got[0]["id"] == "s1" and got[0]["keys"] == ["a"]
        assert not rfl.is_malformed(got[0])

    def test_an_entry_with_no_file_is_still_dropped_entirely(self, tmp_path):
        """Unchanged behaviour: a declaration that does not even name a file
        is ignored, so the loss it meant to excuse is reported."""
        assert self._summary(tmp_path, [{"id": "s1", "keys": []}]) == []

    def test_the_summary_reports_the_two_counts_separately(self):
        v = rfl.check(None)          # no base: nothing compared
        assert "not a pass" in v["summary"]

    def test_the_rendered_summary_of_a_real_run_distinguishes_them(self):
        src = (REPO / "scripts/ci/check_register_field_loss.py").read_text()
        assert "malformed " in src and "phantom " in src, (
            "the summary line must count them apart, or `N phantom` still "
            "misnames a misspelling")


class TestTheGuardsOwnSelfTestCoversThis:
    def test_the_self_test_passes(self):
        """It returns `(ok, failures)`, not an exit code."""
        ok, failures = rfl._self_test()
        assert ok and failures == [], failures

    def test_it_asserts_the_rendered_string_rather_than_a_copy(self):
        src = (REPO / "scripts/ci/check_register_field_loss.py").read_text()
        i = src.index("MALFORMED is not PHANTOM")
        block = src[i:i + 2600]
        assert "render(" in block, (
            "a control that builds its own message cannot catch a reworded one")
