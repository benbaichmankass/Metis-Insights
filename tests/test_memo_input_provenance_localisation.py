"""Can a `content_changed` verdict NAME the changed rows? (MI-278 U51)

⚠️ **THESE EXIST BECAUSE 64 GREEN CONTROLS WERE GREEN ON A DEFECT.** Every control
in `memo_input_provenance._selftest` compared two LIVE fingerprint dicts, which
always carry `row_digests`; the one that did reach the STANZA path checked the
IDENTITY case, where the row lists are empty whether or not the digests are
there. So the suite never exercised the only comparison a later session can make
— a fresh pull against a memo's declared stanza — on a case where the answer
differs.

MEASURED on that path before the fix, on two REAL declared memos (U32 and U38, a
genuine re-stamp: same ids, same `order_digest`, different `rowset_digest`):
`rows_changed` / `rows_added` / `rows_removed` all `[]` beside
`rows_not_localisable: 0`. Against a live pull instead: the ENTIRE population
reported as `rows_added`. Two confident falsehoods pointing at OPPOSITE causes —
a re-stamp rendered as "nothing changed", and a re-stamp rendered as "the whole
population is new", which is the third cause
(`BL-20260912-A-1000-ROW-TAIL-PULL-CHANGES-A-FANOUT-PACKAGES-MEMBERSHIP...`).
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "research" / "memo_input_provenance.py"
_spec = importlib.util.spec_from_file_location("memo_input_provenance", _SRC)
mip = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(mip)


def rows(n: int = 6) -> list[dict]:
    return [{"id": str(100 + i), "exit_price": 1.0 + i, "pnl": float(i)} for i in range(n)]


def restamped(i: int = 3) -> list[dict]:
    r = rows()
    r[i]["exit_price"] = 99.9
    return r


@pytest.fixture()
def fa():
    return mip.fingerprint(rows())


@pytest.fixture()
def fb():
    return mip.fingerprint(restamped())


# ---------------------------------------------------------------- the defect
def test_stanza_vs_live_does_not_report_the_whole_population_as_added(fa, fb):
    """The measured pre-fix output was `rows_added == every observed id`."""
    v = mip.compare(mip.parse_stanza(mip.render_stanza(fa)), fb)
    assert v["state"] == "content_changed"
    assert v["rows_added"] is None, "a derived list from an absent declared map is a fabrication"
    assert v["rows_changed"] is None
    assert v["rows_removed"] is None


def test_stanza_vs_stanza_does_not_report_agreement_on_a_real_change(fa, fb):
    """The measured pre-fix output was all-`[]` beside a `content_changed` state."""
    v = mip.compare(mip.parse_stanza(mip.render_stanza(fa)),
                    mip.parse_stanza(mip.render_stanza(fb)))
    assert v["state"] == "content_changed"
    assert v["localisation"] == "neither_side_carries_row_digests"
    assert (v["rows_changed"], v["rows_added"], v["rows_removed"]) == (None, None, None)


def test_rows_not_localisable_is_none_not_zero(fa, fb):
    """`0` asserts we looked and found none; we could not look at all."""
    v = mip.compare(mip.parse_stanza(mip.render_stanza(fa)), fb)
    assert v["rows_not_localisable"] is None


def test_the_why_string_carries_the_localisation_state(fa, fb):
    v = mip.compare(mip.parse_stanza(mip.render_stanza(fa)), fb)
    assert "CANNOT BE NAMED" in v["why"]
    assert v["localisation"] in v["why"]


# ------------------------------------------------- the capability that fixes it
def test_a_stanza_with_row_digests_names_the_changed_row(fa, fb):
    v = mip.compare(mip.parse_stanza(mip.render_stanza(fa, include_row_digests=True)), fb)
    assert v["localisation"] == "localised"
    assert v["rows_basis"] == "row_digests"
    assert v["rows_changed"] == ["103"]
    assert v["rows_added"] == [] and v["rows_removed"] == []


def test_the_declared_prefix_trims_the_observed_side(fa, fb):
    """A 16-hex declared map against full observed digests would differ on EVERY
    row — not a milder wrong answer, the same fabricated total turnover."""
    v = mip.compare(mip.parse_stanza(mip.render_stanza(fa, include_row_digests=16)), fb)
    assert v["row_digest_prefix"] == 16
    assert v["rows_changed"] == ["103"]


def test_full_length_is_declared_as_full_and_still_localises(fa, fb):
    st = mip.parse_stanza(mip.render_stanza(fa, include_row_digests=64))
    assert st["row_digest_prefix"] == "full"
    assert mip.compare(st, fb)["rows_changed"] == ["103"]


def test_stanza_without_digests_is_unchanged_by_default(fa):
    """The opt-in must not silently grow every existing memo's stanza."""
    assert "row_digests:" not in mip.render_stanza(fa)
    assert "row_digest_prefix:" not in mip.render_stanza(fa)


# --------------------------------------------------------- states stay apart
@pytest.mark.parametrize(
    "declared_digests,observed_digests,expected",
    [
        (True, True, "localised"),
        (False, True, "declared_carries_no_row_digests"),
        (True, False, "observed_carries_no_row_digests"),
        (False, False, "neither_side_carries_row_digests"),
    ],
)
def test_every_localisation_state_is_reachable(fa, fb, declared_digests, observed_digests, expected):
    d = mip.parse_stanza(mip.render_stanza(fa, include_row_digests=declared_digests))
    o = (fb if observed_digests
         else mip.parse_stanza(mip.render_stanza(fb, include_row_digests=False)))
    assert mip.compare(d, o)["localisation"] == expected


def test_the_declared_vocabulary_is_exactly_what_compare_can_emit(fa, fb):
    emitted = set()
    for dd in (True, False):
        for oo in (True, False):
            d = mip.parse_stanza(mip.render_stanza(fa, include_row_digests=dd))
            o = fb if oo else mip.parse_stanza(mip.render_stanza(fb, include_row_digests=False))
            emitted.add(mip.compare(d, o)["localisation"])
    assert emitted == set(mip.LOCALISATION_STATES)


# ------------------------------------------- the sound cases must stay sound
def test_reproduces_reports_empty_lists_without_row_digests(fa):
    """Entailed by `rowset_digest` equality — no per-row map needed, and saying
    `None` here would be the opposite error: refusing an answer we have."""
    v = mip.compare(mip.parse_stanza(mip.render_stanza(fa)), mip.fingerprint(rows()))
    assert v["state"] == "reproduces"
    assert v["rows_changed"] == []
    assert v["rows_basis"] == "rowset_digest_equality"


def test_reordered_reports_empty_lists_without_row_digests(fa):
    v = mip.compare(mip.parse_stanza(mip.render_stanza(fa)),
                    mip.fingerprint(list(reversed(rows()))))
    assert v["state"] == "reordered"
    assert v["rows_changed"] == []
    assert v["rows_basis"] == "rowset_digest_equality"


def test_live_vs_live_is_byte_for_byte_unchanged(fa, fb):
    v = mip.compare(fa, fb)
    assert v["rows_changed"] == ["103"] and v["rows_added"] == [] and v["rows_removed"] == []
    assert v["rows_not_localisable"] == 0


# --------------------------------------------------------------- parse hygiene
def test_a_malformed_row_digest_entry_is_counted_never_guessed(fa):
    st = mip.render_stanza(fa, include_row_digests=True).replace(
        "row_digests: ", "row_digests: junkwithoutequals,")
    parsed = mip.parse_stanza(st)
    assert parsed["row_digests_malformed_entries"] == 1
    assert "junkwithoutequals" not in parsed["row_digests"]


def test_row_digests_are_not_a_required_stanza_key():
    """Every memo written before this change must still grade `declares_input`;
    making them required would retroactively un-declare all five."""
    assert "row_digests" not in mip.REQUIRED_STANZA_KEYS


def test_the_five_memos_that_declare_today_still_parse_complete():
    """A live positive control over the real corpus, not a fixture."""
    root = pathlib.Path(__file__).resolve().parents[1] / "docs" / "research"
    if not root.is_dir():
        pytest.skip("docs/research not present")
    declaring = [r for r in mip.census(str(root))["rows"] if r["state"] == "declares_input"]
    assert declaring, "positive control: at least one memo must declare an input"
    for r in declaring:
        st = mip.parse_stanza(pathlib.Path(r["path"]).read_text(errors="replace"))
        assert st is not None and not st.get("incomplete")
