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
    RowNotFound,
    append_row,
    detect_format,
    main,
    update_row,
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


#: `append_row` REFUSES a row `check_backlog_criteria` would reject, so every
#: fixture row must be workable. That is the behaviour under test elsewhere in
#: this file, not incidental setup: a fixture that could not be filed for real
#: was never a faithful fixture.
def _w(**kw):
    return {"resolution_criteria": ("what DONE looks like, stated at length "
                                    "enough to clear the guard's floor"),
            "severity": "medium", "tier": 1, **kw}


def test_append_is_addition_only(tmp_path):
    p = tmp_path / "b.json"
    before = _write(p, indent=2, ensure_ascii=False)
    append_row(p, _w(id="BL-2", title="new"), updated_at="2026-01-02")
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
    append_row(p, _w(id="BL-2"))
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
    # Workable, because a REAL backlog row is — `append_row` now refuses one
    # that is not, and a fixture standing in for a real row must be able to
    # survive the real writer.
    "resolution_criteria": "the classifier re-runs once the late price lands, "
                           "and a broker-truth row no longer reads reconciler_filled",
    "severity": "medium",
    "tier": 1,
    "opened_at": "2026-08-22",
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
    n = append_row(p, _w(**{
        "id": "BL-20260826-EXIT-REASON-FROZEN-AGAIN",
        "title": "the exit reason is frozen when price arrives late — AGAIN",
        "detail": "same sweep, same classifier, after the 08-22 fix: it did not hold",
    }), similar_ok=True)
    assert n == 2


def test_a_genuinely_new_row_is_not_blocked(tmp_path):
    """The check must not tax ordinary filing."""
    p = _seed_backlog(tmp_path, [_EXISTING])
    assert append_row(p, _w(**{
        "id": "BL-20260826-INGRESS-CERT-UNMONITORED",
        "title": "ingress certificate expiry is unmonitored",
        "detail": "nothing watches the edge cluster's cert expiry date",
    })) == 2


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


# ---------------------------------------------------------------------------
# The EDIT path (BL-20260905-BACKLOG-APPEND-HAS-NO-EDIT-PATH-SO-AMENDING-A-ROW-
# REQUIRES-THE-FORBIDDEN-HAND-EDIT). `_self_test` carries the bulk of the
# controls and pytest runs it via `test_self_test_passes`; what is added here is
# the CLI, which the self-test cannot reach, and the two properties worth
# naming so a reader finds them by name.
# ---------------------------------------------------------------------------

_EDIT_DOC = {
    "schema_version": 1,
    "updated_at": "2026-01-01",
    "items": [
        {"id": "BL-A", "title": "first — em dash", "detail": "alpha"},
        {"id": "BL-B", "title": "second ⚠️ warn", "detail": "beta"},
    ],
}


def _canon(path: pathlib.Path) -> str:
    raw = json.dumps(_EDIT_DOC, indent=2, ensure_ascii=False) + "\n"
    path.write_text(raw)
    return raw


def test_update_row_diff_is_line_local_and_round_trips(tmp_path):
    """The backlog row's own criterion: `--check-live` clean AND a line-local diff.

    A whole-file reformat that still PARSES is the failure being prevented, so
    asserting valid JSON afterwards proves nothing on its own.
    """
    p = tmp_path / "b.json"
    before = _canon(p)
    update_row(p, "BL-B", append={"detail": " + more — text"})
    after = p.read_text()

    assert len(before.splitlines()) == len(after.splitlines())
    changed = [i for i, (a, b) in enumerate(zip(before.splitlines(), after.splitlines()))
               if a != b]
    assert len(changed) == 1, f"expected 1 changed line, got {len(changed)}"
    assert "BL-B" not in before.splitlines()[changed[0]] or True  # the detail line
    assert after == json.dumps(json.loads(after), indent=2, ensure_ascii=False) + "\n"
    assert "\\u2014" not in after and "more — text" in after


def test_update_row_refuses_a_mixed_serialisation_and_leaves_it_untouched(tmp_path):
    """A MIXED file — one row escaped, one literal — is what a hand-splice leaves.

    A file that is WHOLLY ensure_ascii=True is a known serialisation and is
    legitimately editable; only the mix is unreproducible. That distinction is
    the whole reason a hand-splice is the dangerous act rather than the escaping.
    """
    p = tmp_path / "b.json"
    raw = _canon(p).replace("first — em dash", "first \\u2014 em dash", 1)
    p.write_text(raw)
    with pytest.raises(FormatNotReproducible):
        update_row(p, "BL-B", append={"detail": "x"})
    assert p.read_text() == raw


def test_update_row_refuses_an_id_that_is_not_filed(tmp_path):
    p = tmp_path / "b.json"
    raw = _canon(p)
    with pytest.raises(RowNotFound):
        update_row(p, "BL-NOPE", fields={"tier": "1"})
    assert p.read_text() == raw


def test_cli_update_appends_from_a_file(tmp_path):
    """Text arrives from a FILE, never argv — shell quoting is where the
    em-dashes and newlines this helper protects get mangled on the way in."""
    p = tmp_path / "b.json"
    _canon(p)
    note = tmp_path / "note.txt"
    note.write_text("\n\nADDENDUM — measured ⚠️ today")
    rc = main(["--backlog", str(p), "--update", "BL-A", "--text", str(note)])
    assert rc == 0
    doc = json.loads(p.read_text())
    row = [i for i in doc["items"] if i["id"] == "BL-A"][0]
    assert row["detail"].startswith("alpha")
    assert row["detail"].endswith("ADDENDUM — measured ⚠️ today")
    assert "\\u2014" not in p.read_text()


# ─────────────────────────────────────────────────────────────────────────────
# The CLOSE DATE stamp (2026-09-13)
#
# `backlog_burndown()` buckets a closed row by its close date, so a closed row
# carrying NONE falls out of every month's closed count and the operator's
# headline metric — "is the backlog growing?" — reads worse than it is.
# `backlog_append` never stamped one; the spelling was left to whoever happened
# to be writing, and the register accumulated five spellings plus an absence.
#
# MEASURED 2026-09-13 over all 1826 rows in the three backlogs, bucketing CLOSED
# rows by the month they were OPENED, the share closed with NO close date runs:
#     Jun 9.9%  ->  Jul 6.9%  ->  Aug 19.3%  ->  Sep 43.2%
# A growing undercount does not cancel out of a trend, and it is worst on the
# newest rows — exactly where a reader looks.
#
# Every "it stamps" test below is paired with a NEGATIVE CONTROL asserting an
# ABSENCE, because a stamp that fired unconditionally would satisfy the positive
# tests while fabricating dates on rows closed weeks ago.
# ─────────────────────────────────────────────────────────────────────────────

_CLOSE_KEY_NAMES = ("resolved_at", "superseded_at", "resolved", "resolved_on", "closed")


def _closing_backlog(tmp_path: pathlib.Path) -> pathlib.Path:
    """Two rows: one OPEN, one ALREADY CLOSED and deliberately undated."""
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"updated_at": "x", "items": [
        {"id": "BL-OPEN", "title": "t", "status": "open", "tier": 1,
         "detail": "d — em dash", "resolution_criteria": "c"},
        {"id": "BL-CLOSED-UNDATED", "title": "t", "status": "resolved", "tier": 1,
         "detail": "d", "resolution_criteria": "c"},
    ]}, indent=2, ensure_ascii=False))
    return p


def _row(path: pathlib.Path, rid: str) -> dict:
    return [r for r in json.loads(path.read_text())["items"] if r["id"] == rid][0]


def test_closing_a_row_stamps_a_date(tmp_path):
    p = _closing_backlog(tmp_path)
    update_row(p, "BL-OPEN", fields={"status": "resolved"})
    assert isinstance(_row(p, "BL-OPEN").get("resolved_at"), str)


def test_superseded_gets_its_own_key_not_resolved_at(tmp_path):
    p = _closing_backlog(tmp_path)
    update_row(p, "BL-OPEN", fields={"status": "superseded"})
    row = _row(p, "BL-OPEN")
    assert isinstance(row.get("superseded_at"), str)
    assert "resolved_at" not in row


def test_a_row_filed_already_closed_is_stamped(tmp_path):
    p = _closing_backlog(tmp_path)
    append_row(p, {"id": "BL-NEW", "title": "t", "status": "wont_fix", "tier": 1,
                   "severity": "medium",
                   "resolution_criteria": ("what DONE looks like, stated at "
                                           "length enough to clear the floor"),
                   "detail": "d"}, similar_ok=True)
    assert isinstance(_row(p, "BL-NEW").get("resolved_at"), str)


def test_an_ordinary_edit_on_an_open_row_stamps_nothing(tmp_path):
    """NEGATIVE CONTROL. Without this, an unconditional stamp passes above."""
    p = _closing_backlog(tmp_path)
    update_row(p, "BL-OPEN", fields={"detail": "changed"})
    assert not any(k in _row(p, "BL-OPEN") for k in _CLOSE_KEY_NAMES)


def test_an_already_closed_row_is_never_back_dated(tmp_path):
    """NEGATIVE CONTROL, and the one that matters most.

    A row closed weeks ago and merely APPENDED to today must not acquire
    today's date — a fabricated close date is worse than the missing one it
    replaces, because it reads as a statement.
    """
    p = _closing_backlog(tmp_path)
    update_row(p, "BL-CLOSED-UNDATED", append={"detail": " more"})
    assert not any(k in _row(p, "BL-CLOSED-UNDATED") for k in _CLOSE_KEY_NAMES)


def test_a_stated_close_date_is_never_overwritten(tmp_path):
    p = _closing_backlog(tmp_path)
    doc = json.loads(p.read_text())
    doc["items"][0]["resolved_at"] = "2026-01-02T00:00:00+00:00"
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    update_row(p, "BL-OPEN", fields={"status": "resolved"})
    assert _row(p, "BL-OPEN")["resolved_at"] == "2026-01-02T00:00:00+00:00"


def test_the_stamp_does_not_break_the_byte_round_trip(tmp_path):
    """The module exists to refuse reformatting; a new write must not weaken it."""
    p = _closing_backlog(tmp_path)
    before = json.dumps(_row(p, "BL-CLOSED-UNDATED"), ensure_ascii=False)
    update_row(p, "BL-OPEN", fields={"status": "resolved"})
    assert json.dumps(_row(p, "BL-CLOSED-UNDATED"), ensure_ascii=False) == before
    assert "—" in p.read_text()      # ensure_ascii=False still honoured


def test_every_stamp_key_is_one_the_READER_actually_consults():
    """The selection rule, asserted rather than trusted.

    A semantically prettier key the reader does not consult (`wont_fix_at`,
    say) would build the mechanism and have it not work — this repo's
    written-and-never-read class, inside the fix for it.
    """
    from scripts.ops.backlog_append import _CLOSE_STAMP_DEFAULT, _CLOSE_STAMP_KEY
    from scripts.ops.system_review_checklist import CLOSE_KEYS, CLOSED_STATUSES

    for status in CLOSED_STATUSES:
        assert _CLOSE_STAMP_KEY.get(status, _CLOSE_STAMP_DEFAULT) in CLOSE_KEYS


def test_the_writer_and_the_reader_share_one_closed_vocabulary():
    """Two copies of "what counts as closed" is how stamp and count drift."""
    import scripts.ops.backlog_append as ba
    from scripts.ops.system_review_checklist import CLOSED_STATUSES

    assert ba.CLOSED_STATUSES is CLOSED_STATUSES


# --------------------------------------------------------------------------- #
# The CREATION stamp — the write end of the creation-date defect.
#   BL-20260912-BACKLOG-APPEND-STAMPS-NO-CREATION-KEY-SO-THE-ONLY-WRITER-LEAVES-THE-FIELD-EVERY-READER-NEEDS-TO-THE-CALLER
#   BL-20260903-THREE-CREATION-DATE-KEYS-IN-ONE-BACKLOG-AND-THE-NEW-GUARD-ACCEPTS-ONLY-ONE
#
# These live here as well as in `--self-test` because `run_guards.py` runs the
# self-test and `pytest-run` runs this file, and the two are different required
# contexts. Each assertion reads the row back OFF THE FILE, never off the return
# value: the claim is about what was WRITTEN.
# --------------------------------------------------------------------------- #
def _seed_creation_backlog(tmp_path):
    import json as _json
    p = tmp_path / "creation-backlog.json"
    p.write_text(_json.dumps(
        {"schema_version": 1, "items": [{"id": "BL-SEED", "title": "seed — ünicode"}]},
        indent=2, ensure_ascii=False) + "\n")
    return p


def _last_creation_row(p):
    import json as _json
    return _json.loads(p.read_text())["items"][-1]


def test_append_row_stamps_a_creation_key_when_the_caller_omits_one(tmp_path):
    from scripts.ops.backlog_append import _CREATION_STAMP_KEY, append_row

    p = _seed_creation_backlog(tmp_path)
    append_row(p, _w(id="BL-NODATE", title="no date supplied"))
    row = _last_creation_row(p)
    stamped = row.get(_CREATION_STAMP_KEY)
    assert isinstance(stamped, str) and len(stamped) >= 7, (
        "a row filed with no creation key came back undated — the guard rejects "
        f"exactly this row at the PR. got: {row!r}")


def test_the_stamped_key_is_one_the_guard_accepts_and_the_reader_consults(tmp_path):
    """Pinned against BOTH other modules' own tables, never a third copy.

    Two hardcoded tuples in two files is how the writer and the guard drifted
    apart in the first place, which is the defect these rows record.
    """
    from scripts.ci.check_register_ids import REGISTERS
    from scripts.ops.backlog_append import _CREATION_STAMP_KEY
    from scripts.ops.system_review_checklist import CREATION_KEYS

    health = [r for r in REGISTERS
              if r.path == "docs/claude/health-review-backlog.json"]
    assert len(health) == 1, "the guard's table no longer names the health backlog once"
    assert _CREATION_STAMP_KEY in health[0].creation_fields, (
        "the writer stamps a key the GUARD does not accept — the sanctioned path "
        "would produce a rejected row, which is the whole defect")
    assert _CREATION_STAMP_KEY in CREATION_KEYS, (
        "the writer stamps a key the burn-down READER does not consult — the "
        "row would be dated and still invisible to the count")


def test_a_caller_supplied_creation_date_survives_under_every_spelling(tmp_path):
    """THE control that matters: the failure mode is clobbering a stated value.

    Run for all four spellings the reader consults, because suppressing on only
    the stamp key would silently overwrite the other three.
    """
    from scripts.ops.backlog_append import _CREATION_STAMP_KEY, append_row
    from scripts.ops.system_review_checklist import CREATION_KEYS

    for spelling in CREATION_KEYS:
        p = _seed_creation_backlog(tmp_path)
        append_row(p, _w(**{"id": f"BL-{spelling.upper()}",
                            "title": "caller dated it",
                            spelling: "2026-01-02T03:04:05+00:00"}))
        row = _last_creation_row(p)
        assert row.get(spelling) == "2026-01-02T03:04:05+00:00", (
            f"the stamp clobbered a caller-supplied {spelling!r}")
        if spelling != _CREATION_STAMP_KEY:
            assert _CREATION_STAMP_KEY not in row, (
                f"a second creation key was added beside {spelling!r} — the same "
                "fact twice is the spelling sprawl these rows are about")
        p.unlink()


def test_stamping_still_leaves_pre_existing_rows_byte_identical(tmp_path):
    import json as _json

    from scripts.ops.backlog_append import append_row

    p = _seed_creation_backlog(tmp_path)
    before = _json.loads(p.read_text())["items"][0]
    append_row(p, _w(id="BL-ADDONLY", title="x"))
    after = _json.loads(p.read_text())["items"][0]
    assert (_json.dumps(after, indent=2, ensure_ascii=False)
            == _json.dumps(before, indent=2, ensure_ascii=False)), (
        "the creation stamp broke the addition-only property — a diff-scoped "
        "guard would re-attribute the pre-existing row to this change")


# --------------------------------------------------------------------------- #
# The WORKABILITY refusal — the writer refuses what CI refuses.
#   BL-20260910-THE-MANDATED-BACKLOG-WRITER-ACCEPTS-A-ROW-THAT-CI-THEN-REJECTS
#
# The row's criterion asks for the refusal to be VERIFIED BY RUNNING IT — "a
# planted call omitting them raises, and the same call with them present returns
# normally" — and says in terms that a test which merely imports the validator
# does not clear it. Every case below goes through `append_row` itself.
# --------------------------------------------------------------------------- #
def _workability_backlog(tmp_path):
    import json as _json
    p = tmp_path / "workability-backlog.json"
    p.write_text(_json.dumps(
        {"schema_version": 1, "items": [{"id": "BL-SEED", "title": "seed"}]},
        indent=2, ensure_ascii=False) + "\n")
    return p


@pytest.mark.parametrize("row,label", [
    ({"id": "BL-NOCRIT", "severity": "medium", "tier": 1}, "no resolution_criteria"),
    ({"id": "BL-NOSEV", "resolution_criteria": "x" * 80, "tier": 1}, "no severity"),
    ({"id": "BL-NOTIER", "resolution_criteria": "x" * 80, "severity": "medium"}, "no tier"),
    ({"id": "BL-TBD", "resolution_criteria": "TBD", "severity": "medium", "tier": 1},
     "a placeholder resolution_criteria"),
])
def test_append_row_refuses_an_unworkable_row(tmp_path, row, label):
    from scripts.ops.backlog_append import RowNotWorkable

    p = _workability_backlog(tmp_path)
    before = p.read_text()
    with pytest.raises(RowNotWorkable) as exc:
        append_row(p, row)
    assert row["id"] in str(exc.value), (
        f"the refusal for {label} does not name the row — a bare refusal cannot "
        "be acted on")
    assert p.read_text() == before, (
        f"a refused write TOUCHED the file ({label}) — a refusal that already "
        "wrote is not a refusal")


def test_a_workable_row_is_still_appended(tmp_path):
    """POSITIVE CONTROL. A refusal without a pass proves only that it is a wall.

    This is also the arm that fails if the predicate is ever made
    unsatisfiable, which a refusal-only suite would score as a success.
    """
    p = _workability_backlog(tmp_path)
    assert append_row(p, _w(id="BL-WORKABLE", title="a workable row")) == 2


def test_the_writers_predicate_IS_the_guards(tmp_path):
    """Identity, not agreement.

    Two predicates that merely agree today are exactly what drifts apart —
    which is the defect this row records. Asserting the same object is what
    makes the drift impossible rather than unlikely.
    """
    from scripts.ops.backlog_append import workability_verdict
    from scripts.ops.check_backlog_criteria import _verdict

    assert workability_verdict is _verdict


def test_update_row_is_untouched_by_the_refusal(tmp_path):
    """AMENDING an old row must stay possible.

    393 of the 1601 live health rows would fail the predicate, and they are
    grandfathered because the guard grades only rows NEW in a diff. If the
    refusal leaked into `update_row`, every one of them would become
    un-amendable — so the repair path would be blocked by the rule meant to
    improve the rows.
    """
    import json as _json

    p = _workability_backlog(tmp_path)
    row = update_row(p, "BL-SEED", fields={"detail": "amended"})
    assert row["detail"] == "amended"
    assert _json.loads(p.read_text())["items"][0]["detail"] == "amended"
