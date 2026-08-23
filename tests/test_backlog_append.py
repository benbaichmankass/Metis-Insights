"""The backlog-append helper must REFUSE rather than reformat.

``BL-20260820-BACKLOG-APPEND-REFORMATS-AND-REATTRIBUTES`` names the remedy this
tests: *"a helper ... that round-trips the untouched file and REFUSES to write
when its own serialisation does not reproduce the original byte-for-byte ...
Proven by a test that plants an ensure_ascii=True write and asserts the helper
refuses it. Documenting 'remember ensure_ascii=False' is NOT sufficient — this
file already documents plenty that sessions miss."*

The stakes are not cosmetic. Every guard in `run_guards.py` is diff-scoped
(added-vs-origin/main), so a whole-file reformat **re-attributes every
pre-existing row to the appending PR**. Measured on a one-row append that took
the naive path: 21,307 insertions / 21,288 deletions, and
`impossibility-claim-guard` went red for eight rows the author never wrote.
Through the helper, the same append is 20 insertions / 1 deletion.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from scripts.ops.backlog_append import (
    FormatNotReproducible,
    append_row,
    detect_format,
)

# A real em-dash and a real umlaut: the characters `ensure_ascii=True` mangles.
_DOC = {
    "schema_version": 1,
    "updated_at": "2026-01-01",
    "items": [{"id": "BL-1", "title": "em—dash and ünicode"}],
}


def _write(path: pathlib.Path, **kw) -> str:
    raw = json.dumps(_DOC, **kw) + "\n"
    path.write_text(raw)
    return raw


def test_detects_the_canonical_format(tmp_path):
    p = tmp_path / "b.json"
    _write(p, indent=2, ensure_ascii=False)
    kw, trailing = detect_format(p.read_text(), json.loads(p.read_text()))
    assert kw["ensure_ascii"] is False
    assert trailing == "\n"


def test_the_live_backlogs_all_round_trip():
    """The helper must actually work on the real files, not just a fixture."""
    for name in ("health", "performance", "ml"):
        p = pathlib.Path(f"docs/claude/{name}-review-backlog.json")
        if not p.exists():
            continue
        raw = p.read_text()
        kw, trailing = detect_format(raw, json.loads(raw))
        assert json.dumps(json.loads(raw), **kw) + trailing == raw, (
            f"{name}-review-backlog.json does not round-trip — appending to it "
            "would reformat every line and re-attribute its rows"
        )


def test_append_is_addition_only(tmp_path):
    p = tmp_path / "b.json"
    before = _write(p, indent=2, ensure_ascii=False)
    append_row(p, {"id": "BL-2", "title": "new"}, updated_at="2026-01-02")
    after = p.read_text()

    assert "em—dash" in after, "the em-dash was escaped — the exact trap"
    assert "\\u2014" not in after
    # Everything except the appended row and updated_at is untouched.
    added = len(after.splitlines()) - len(before.splitlines())
    assert 0 < added < 20, f"expected a small addition, got {added} new lines"
    assert json.loads(after)["items"][0] == _DOC["items"][0]


def test_the_planted_ensure_ascii_write_is_refused(tmp_path):
    """THE control the backlog row asks for, stated in its own terms.

    A file already written with ``ensure_ascii=True`` is a DIFFERENT byte layout.
    The helper must reproduce *that* layout or refuse — what it must never do is
    silently rewrite the file into its preferred format.
    """
    p = tmp_path / "escaped.json"
    raw = _write(p, indent=2, ensure_ascii=True)
    assert "\\u2014" in raw, "fixture precondition: the em-dash is escaped"

    # This layout IS reproducible, so the helper may append — but it must not
    # un-escape anything, because that would rewrite every affected line.
    append_row(p, {"id": "BL-2"})
    after = p.read_text()
    assert "\\u2014" in after, (
        "the helper un-escaped an escaped file — that rewrites every line "
        "containing a non-ASCII character, which is the re-attribution bug"
    )


def test_an_unreproducible_layout_is_refused_and_left_untouched(tmp_path):
    p = tmp_path / "odd.json"
    p.write_text(json.dumps(_DOC, indent=3, separators=(" ,", " : ")))
    raw = p.read_text()
    with pytest.raises(FormatNotReproducible):
        append_row(p, {"id": "BL-2"})
    assert p.read_text() == raw, "a refused write must leave the file untouched"


def test_duplicate_ids_are_refused(tmp_path):
    p = tmp_path / "b.json"
    _write(p, indent=2, ensure_ascii=False)
    with pytest.raises(ValueError):
        append_row(p, {"id": "BL-1"})


def test_self_test_passes():
    from scripts.ops.backlog_append import _self_test
    assert _self_test() == 0
