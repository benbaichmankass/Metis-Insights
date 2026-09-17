"""A register that reformats has no writer, and one of them is mandatory.

`update_row` re-serialises the whole document and REFUSES with
`FormatNotReproducible` when no candidate serialisation reproduces the file
byte-for-byte. That refusal is correct and must stay: a reformat re-attributes
every pre-existing row to whoever triggered it.

But MEASURED 2026-09-17 over all 20 committed `docs/claude/*.json` registers
above 200 bytes, probed against 20 candidate serialisations, THREE reproduce at
none of them — `OPEN-ITEMS.json` (779,305 bytes), `session-board.json`,
`strategy-refinement-queue.json` — and the first is the file CLAUDE.md requires
EVERY session to update at session end. So the mandated duty had no mandated
writer, and the only route left was the hand-edit the repo forbids.

`splice_row` replaces the exact bytes of one field inside one row and then
RE-PARSES to prove the document differs nowhere else. The assertions below are
mostly about its REFUSALS, because a writer that guesses which bytes were meant
is worse than a hand-edit a human reviewed.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))

BA = pytest.importorskip("backlog_append")


def _weird(tmp_path, items) -> pathlib.Path:
    """A register whose serialisation reproduces at NO json.dumps setting.

    Mixed indentation and a stray separator, which is what a file edited by hand
    across many sessions actually looks like — and exactly the case `update_row`
    refuses.
    """
    body = ",\n".join(
        "  " + json.dumps(r, ensure_ascii=False, indent=(1 if i % 2 else 3)).replace("\n", "\n  ")
        for i, r in enumerate(items)
    )
    p = tmp_path / "weird.json"
    p.write_text('{\n "items": [\n' + body + "\n ]\n}\n", encoding="utf-8")
    json.loads(p.read_text(encoding="utf-8"))          # it must still be valid JSON
    return p


ROWS = [
    {"id": "BL-A", "status": "open", "detail": "first", "n": 1},
    # ⚠️ The brace here is DELIBERATELY UNBALANCED. A balanced pair inside a
    # string does not break a brace matcher that ignores string literals, so a
    # fixture using `{...}` tests nothing — a planted mask-less matcher passed
    # against exactly that fixture before this was changed.
    {"id": "BL-B", "status": "open", "detail": "second } dangling { and \"quotes\"", "n": 2},
    {"id": "BL-C", "status": "open", "detail": "third", "n": 3},
]


class TestItWorksWhereUpdateRowCannot:
    def test_update_row_refuses_this_file(self, tmp_path):
        """The premise. If this ever stops raising, the splice writer is no
        longer needed for this shape and the test should say so loudly."""
        p = _weird(tmp_path, ROWS)
        with pytest.raises(BA.FormatNotReproducible):
            BA.update_row(p, "BL-B", fields={"status": "resolved"})

    def test_splice_amends_it(self, tmp_path):
        p = _weird(tmp_path, ROWS)
        row = BA.splice_row(p, "BL-B", fields={"status": "resolved"})
        assert row["status"] == "resolved"
        got = json.loads(p.read_text(encoding="utf-8"))["items"]
        assert [r["status"] for r in got] == ["open", "resolved", "open"]

    def test_it_changes_almost_nothing_byte_wise(self, tmp_path):
        p = _weird(tmp_path, ROWS)
        before = p.read_text(encoding="utf-8")
        BA.splice_row(p, "BL-B", fields={"status": "resolved"})
        after = p.read_text(encoding="utf-8")
        diff = sum(1 for a, b in zip(before.splitlines(), after.splitlines()) if a != b)
        assert diff == 1, "a splice touched more than the one line it had to"
        assert len(before.splitlines()) == len(after.splitlines())


class TestTheRowScopeIsTheCorrectnessArgument:
    def test_a_value_shared_by_every_row_is_changed_in_ONE(self, tmp_path):
        """`"status": "open"` appears in all three rows. Without brace-matched
        row scoping the writer would either refuse everything or hit the wrong
        row — and hitting the wrong row is silent."""
        p = _weird(tmp_path, ROWS)
        BA.splice_row(p, "BL-C", fields={"status": "kept_open"})
        got = {r["id"]: r["status"] for r in json.loads(p.read_text())["items"]}
        assert got == {"BL-A": "open", "BL-B": "open", "BL-C": "kept_open"}

    def test_braces_and_quotes_inside_prose_do_not_break_the_span(self, tmp_path):
        """These registers are full of prose containing braces; a brace counter
        that ignored string literals would find the wrong row boundary."""
        p = _weird(tmp_path, ROWS)
        BA.splice_row(p, "BL-B", fields={"n": 22})
        got = {r["id"]: r["n"] for r in json.loads(p.read_text())["items"]}
        assert got == {"BL-A": 1, "BL-B": 22, "BL-C": 3}

    def test_a_dangling_brace_BEFORE_the_id_does_not_become_the_row_start(self, tmp_path):
        """The BACKWARD half of the span scan, which the fixtures above cannot
        reach: they all put `id` first, so scanning back from it never crosses
        any prose. A row whose `id` comes AFTER a field containing an unbalanced
        `{` is the only shape that exercises it — without this, a mask-less
        backward scan passes every other test in this file.
        """
        rows = [
            {"detail": "prose with a { dangling open brace", "id": "BL-LATE",
             "status": "open", "n": 7},
            {"id": "BL-A", "status": "open", "detail": "first", "n": 1},
        ]
        p = tmp_path / "late.json"
        p.write_text(json.dumps({"items": rows}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        BA.splice_row(p, "BL-LATE", fields={"status": "resolved"})
        got = {r["id"]: r["status"] for r in json.loads(p.read_text())["items"]}
        assert got == {"BL-LATE": "resolved", "BL-A": "open"}

    def test_a_separator_with_no_space_still_matches(self, tmp_path):
        """A writer that hardcoded `": "` would find nothing here and fail by
        silence, which is the quiet direction."""
        p = tmp_path / "tight.json"
        p.write_text(json.dumps({"items": ROWS}, separators=(",", ":")), encoding="utf-8")
        BA.splice_row(p, "BL-B", fields={"status": "resolved"})
        assert json.loads(p.read_text())["items"][1]["status"] == "resolved"

    def test_non_ascii_values_round_the_other_spelling(self, tmp_path):
        rows = [dict(ROWS[0]), {"id": "BL-U", "status": "open", "detail": "⚠️ caution", "n": 9}]
        p = tmp_path / "esc.json"
        p.write_text(json.dumps({"items": rows}, ensure_ascii=True, indent=2), encoding="utf-8")
        BA.splice_row(p, "BL-U", append={"detail": " — appended"})
        assert json.loads(p.read_text())["items"][1]["detail"] == "⚠️ caution — appended"


class TestItRefusesRatherThanGuesses:
    def test_an_unknown_row_id(self, tmp_path):
        p = _weird(tmp_path, ROWS)
        with pytest.raises(BA.RowNotFound):
            BA.splice_row(p, "BL-NOPE", fields={"status": "resolved"})

    def test_appending_to_a_field_the_row_does_not_have(self, tmp_path):
        """Creating a field is `fields`, not `append` — and the difference is
        said rather than silently resolved."""
        p = _weird(tmp_path, ROWS)
        with pytest.raises(BA.RowNotFound):
            BA.splice_row(p, "BL-A", append={"observation": "x"})

    def test_appending_to_a_non_string(self, tmp_path):
        p = _weird(tmp_path, ROWS)
        with pytest.raises(ValueError):
            BA.splice_row(p, "BL-A", append={"n": "x"})

    def test_setting_a_field_that_has_no_bytes_to_replace(self, tmp_path):
        p = _weird(tmp_path, ROWS)
        with pytest.raises(BA.RowNotFound):
            BA.splice_row(p, "BL-A", fields={"brand_new": "x"})

    def test_a_duplicated_id_in_the_raw_text(self, tmp_path):
        """Two rows carrying one id is a register-id-guard failure, but this
        writer must not compound it by editing an arbitrary one of them."""
        p = _weird(tmp_path, ROWS + [{"id": "BL-B", "status": "open", "detail": "dupe", "n": 4}])
        with pytest.raises(BA.SpliceAmbiguous):
            BA.splice_row(p, "BL-B", fields={"status": "resolved"})

    def test_no_change_requested_at_all(self, tmp_path):
        p = _weird(tmp_path, ROWS)
        with pytest.raises(ValueError):
            BA.splice_row(p, "BL-A")

    def test_nothing_is_written_when_it_refuses(self, tmp_path):
        p = _weird(tmp_path, ROWS)
        before = p.read_bytes()
        for call in (lambda: BA.splice_row(p, "BL-NOPE", fields={"status": "x"}),
                     lambda: BA.splice_row(p, "BL-A", fields={"brand_new": "x"}),
                     lambda: BA.splice_row(p, "BL-A", append={"n": "x"})):
            with pytest.raises(Exception):
                call()
            assert p.read_bytes() == before, "a refusal still modified the file"


class TestTheProofIsLoadBearing:
    def test_a_splice_that_changed_more_is_refused_and_not_written(self, tmp_path, monkeypatch):
        """Without this check the writer IS the forbidden hand-edit with a nicer
        interface. Planted by making the locator point at the wrong bytes."""
        p = _weird(tmp_path, ROWS)
        before = p.read_bytes()

        def wrong(segment, field, old_value):
            return (0, len(segment), False)          # clobber the whole row
        monkeypatch.setattr(BA, "_locate_value", wrong)
        with pytest.raises(BA.SpliceWouldChangeMore):
            BA.splice_row(p, "BL-B", fields={"status": "resolved"})
        assert p.read_bytes() == before


class TestItIsWiredForTheRealRegister:
    def test_the_mandated_register_is_spliceable(self):
        """OPEN-ITEMS.json is the file CLAUDE.md requires every session to
        update. Read-only here: it locates the bytes without writing."""
        p = REPO / "docs" / "claude" / "OPEN-ITEMS.json"
        if not p.exists():
            pytest.skip("register absent")
        raw = p.read_text(encoding="utf-8")
        doc = json.loads(raw)
        items = doc["items"] if isinstance(doc, dict) else doc
        rid = items[0]["id"]
        start, end = BA._row_span(raw, rid)
        assert json.loads(raw[start:end])["id"] == rid
        a, b, _ea = BA._locate_value(raw[start:end], "id", rid)
        assert json.loads(raw[start:end][a:b]) == rid
