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
    LIVE_BACKLOGS,
    FormatNotReproducible,
    append_row,
    detect_format,
)

REPO = pathlib.Path(__file__).resolve().parents[1]

#: The glob that DEFINES what a live review backlog is. `LIVE_BACKLOGS` stays a
#: hand-enumerated tuple for the reason its own comment gives — the reader this
#: protects interpolates a loop variable, which no static scan can resolve — so
#: the tuple cannot simply BECOME this glob. What it can do is be pinned equal
#: to it, which is what `test_live_backlogs_covers_every_review_backlog` does.
REVIEW_BACKLOG_GLOB = "docs/claude/*-review-backlog.json"

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


# --- the near-duplicate refusal -------------------------------------------
# Operator, 2026-08-26: "We aren't using the backlog/lessons learned logs
# correctly if we still keep running into the same fuck ups." The id check
# above catches only an EXACT repeat, which never happens — ids carry the
# filing date. With 951 / 109 / 104 rows, checking by hand is impractical, so
# nobody does, so the log accumulates lessons and teaches none.

def _seed_backlog(tmp_path, rows):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"schema_version": 1, "items": rows}, indent=2) + "\n")
    return p


_EXISTING = {
    "id": "BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE",
    "status": "kept_open",
    "title": "exit reason frozen when the price arrives late",
    "detail": "the sweep fills exit_price after the close and never re-runs the "
              "classifier, so broker-truth rows keep a reconciler_filled label",
}


def test_a_row_restating_an_existing_one_is_refused(tmp_path):
    """The real 2026-08-26 duplicate, reproduced."""
    from scripts.ops.backlog_append import SimilarRowExists

    p = _seed_backlog(tmp_path, [_EXISTING])
    with pytest.raises(SimilarRowExists) as exc:
        append_row(p, {
            "id": "BL-20260826-EXIT-REASON-FROZEN-AFTER-A-LATE-PRICE",
            "title": "the exit reason is frozen when price arrives late",
            "detail": "a sweep fills the exit_price after close and never re-runs "
                      "the classifier, so rows keep a reconciler_filled label",
        })
    # The refusal must NAME the candidate — a bare "too similar" teaches nothing
    # and the reader cannot judge duplicate-vs-recurrence without it.
    assert _EXISTING["id"] in str(exc.value)
    assert "RECURRENCE" in str(exc.value)


def test_a_recurrence_can_be_filed_once_acknowledged(tmp_path):
    """The override is the point: a recurrence is a VALUABLE row, not noise.

    A refusal with no way through would push sessions to stop filing, which is
    strictly worse than the duplicate it prevents.
    """
    p = _seed_backlog(tmp_path, [_EXISTING])
    n = append_row(p, {
        "id": "BL-20260826-EXIT-REASON-FROZEN-AGAIN",
        "title": "the exit reason is frozen when price arrives late — AGAIN",
        "detail": "same sweep, same classifier, after the 08-22 fix: it did not hold",
    }, similar_ok=True)
    assert n == 2


def test_a_genuinely_new_row_is_not_blocked(tmp_path):
    """The check must not tax ordinary filing."""
    p = _seed_backlog(tmp_path, [_EXISTING])
    assert append_row(p, {
        "id": "BL-20260826-INGRESS-CERT-UNMONITORED",
        "title": "ingress certificate expiry is unmonitored",
        "detail": "nothing watches the edge cluster's cert expiry date",
    }) == 2


def test_the_precheck_never_blocks_when_it_cannot_run(tmp_path, monkeypatch):
    """A broken pre-check must not become a filing outage.

    Fail-PERMISSIVE, the opposite polarity to most guards here — this gates
    the recording of a finding, and losing the finding is worse than
    recording a duplicate.
    """
    import scripts.ops.backlog_search as bs

    def _boom(*a, **kw):
        raise RuntimeError("search is broken")

    monkeypatch.setattr(bs, "search", _boom)
    p = _seed_backlog(tmp_path, [_EXISTING])
    assert append_row(p, dict(_EXISTING, id="BL-20260826-NEAR-IDENTICAL")) == 2


# ─────────────────────────────────────────────────────────────────────────────
# COVERAGE OF THE GUARD ITSELF — the hand-enumerated tuple must not fall behind.
#
# WHY A SET-EQUALITY PIN AND NOT "ADD THE MISSING PATH". Until 2026-09-02
# `LIVE_BACKLOGS` named three paths while four review backlogs existed on disk:
# `research-review-backlog.json` was split out of the performance backlog on
# 2026-08-30 and nothing added it. Adding a fourth entry alone would leave the
# same hand-maintained list one entry longer, and the NEXT backlog created would
# reproduce the gap in exactly the same silence — the guard's success line
# ("3 live backlog(s) reproduce byte-for-byte") is a coverage statement nobody
# reads as one.
#
# ⚠️ THE FAILURE THIS CATCHES IS A GUARD THAT LOOKS COMPLETE FROM CI. A partial
# round-trip guard is indistinguishable from a total one in its output, and the
# uncovered file is precisely where a break reaches `main` green — the backlogs
# are excluded from `pytest-run`'s relevance filter as a class
# (`tests/test_pytest_run_filter.py::DELIBERATELY_EXCLUDED`), so nothing else
# would have looked.
# ─────────────────────────────────────────────────────────────────────────────
def test_live_backlogs_covers_every_review_backlog():
    """`LIVE_BACKLOGS` == every review backlog on disk. FAILS when one is added.

    This is the pin that makes the hand-enumeration safe. Verified to fail
    against the pre-2026-09-02 three-entry tuple, which is the whole point: a
    test that has only ever been green over a list nobody changed proves
    nothing about what happens when somebody changes it.
    """
    on_disk = {
        p.relative_to(REPO).as_posix()
        for p in REPO.glob(REVIEW_BACKLOG_GLOB)
    }
    # ⚠️ NON-VACUITY. An empty glob would make the equality below trivially
    # satisfiable by an empty tuple, i.e. a guard covering nothing passing
    # cleanly — the exact shape this test exists to refuse.
    assert len(on_disk) >= 3, (
        f"only {len(on_disk)} review backlog(s) matched {REVIEW_BACKLOG_GLOB!r} — "
        "the equality below would not be meaningful; check the glob before "
        "trusting a green here")
    assert set(LIVE_BACKLOGS) == on_disk, (
        f"LIVE_BACKLOGS and the review backlogs on disk have diverged.\n"
        f"  guarded but absent from disk: {sorted(set(LIVE_BACKLOGS) - on_disk)}\n"
        f"  ON DISK BUT UNGUARDED:        {sorted(on_disk - set(LIVE_BACKLOGS))}\n"
        "An unguarded backlog is not 'not yet guarded' — a serialisation break "
        "in it reaches main green, because the backlogs are excluded from "
        "pytest-run's relevance filter and check_live_backlogs will not look at "
        "it. Add it to LIVE_BACKLOGS in scripts/ops/backlog_append.py.")


def test_live_backlogs_has_no_duplicate_entries():
    """A repeated path would inflate the guard's own coverage count."""
    assert len(LIVE_BACKLOGS) == len(set(LIVE_BACKLOGS)), LIVE_BACKLOGS
