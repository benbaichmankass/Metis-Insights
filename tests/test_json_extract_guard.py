"""The `json-extract-guard` must actually FIRE.

A guard that has never been proven to catch anything is indistinguishable from
a broken one — and false assurance from an unexercised signal is precisely the
failure this whole workstream is about (`exit_price_source` was written in 12
files and read in none; reviewers saw the field and assumed something acted on
it).

So these tests are mostly POSITIVE: given a real violation, does it fire? The
negative cases then pin the two ways it could become useless — crying wolf on
prose (which gets a guard waivered) or missing a legitimately-guarded call.

Background: SQLite's ``json_extract`` RAISES ``malformed JSON`` on an
unparseable argument rather than returning NULL, and one bad row aborts the
WHOLE statement. ``COALESCE(json_extract(...), '')`` LOOKS null-safe and is not
— that is the trap, and it is why review alone does not catch this.
"""
from __future__ import annotations

import importlib.util


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_json_extract_guarded", "scripts/ci/check_json_extract_guarded.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()


# ------------------------------------------------------------ it must FIRE
def test_bare_json_extract_is_caught():
    src = 'sql = "SELECT json_extract(t.notes, \'$.k\') FROM trades t"\n'
    hits = G.scan_source("x.py", src)
    assert len(hits) == 1
    assert hits[0][0] == 1


def test_coalesce_wrapping_is_NOT_accepted():
    """THE trap. `COALESCE(json_extract(...), '')` reads null-safe but the
    extract still raises before COALESCE ever sees a value."""
    src = (
        'sql = "SELECT COALESCE(json_extract(t.notes, \'$.k\'), \'\') '
        'FROM trades t"\n'
    )
    assert len(G.scan_source("x.py", src)) == 1


def test_the_exact_inv2_regression_shape_is_caught():
    """The predicate I very nearly shipped on 2026-07-30."""
    src = (
        '_NOT_DECLARED = (\n'
        '    "COALESCE(json_extract(t.notes, \'$.pnl_source\'), \'\') != "\n'
        '    "\'unmeasured\'"\n'
        ')\n'
    )
    assert G.scan_source("scripts/check_db_integrity.py", src)


def test_multiple_violations_all_reported():
    src = (
        'a = "json_extract(notes, \'$.x\')"\n'
        + "\n" * 30
        + 'b = "json_extract(notes, \'$.y\')"\n'
    )
    assert len(G.scan_source("x.py", src)) == 2


def test_shell_file_violation_is_caught():
    src = "sqlite3 db \"SELECT json_extract(notes,'\\$.k') FROM trades\"\n"
    assert len(G.scan_source("x.sh", src)) == 1


# --------------------------------------------------- it must NOT cry wolf
def test_guarded_call_passes():
    src = (
        'sql = (\n'
        '    "COALESCE(CASE WHEN json_valid(t.notes) "\n'
        '    "THEN json_extract(t.notes, \'$.k\') END, \'\')"\n'
        ')\n'
    )
    assert G.scan_source("x.py", src) == []


def test_guard_may_sit_a_few_lines_away():
    """A SQL string split across adjacent literals legitimately separates the
    guard from the extract."""
    src = (
        'sql = (\n'
        '    "SELECT id "\n'
        '    "FROM trades t "\n'
        '    "WHERE json_valid(t.notes) "\n'
        '    "  AND something "\n'
        '    "  AND other "\n'
        '    "  AND json_extract(t.notes, \'$.k\') = \'x\'"\n'
        ')\n'
    )
    assert G.scan_source("x.py", src) == []


def test_module_docstring_prose_is_not_a_violation():
    """A line-prefix heuristic only catches a docstring's FIRST line, so
    continuation lines leaked through and read as code — that false positive is
    how a guard gets waivered."""
    src = (
        '"""Notes.\n'
        '\n'
        'One malformed row made ``json_extract(notes, \'$.closed_at\')`` raise\n'
        '"malformed JSON" and abort the report.\n'
        '"""\n'
        'x = 1\n'
    )
    assert G.scan_source("x.py", src) == []


def test_function_docstring_prose_is_not_a_violation():
    src = (
        'def f():\n'
        '    """Doc.\n'
        '\n'
        '    Uses json_extract(notes, \'$.k\') under a guard.\n'
        '    """\n'
        '    return 1\n'
    )
    assert G.scan_source("x.py", src) == []


def test_comment_prose_is_not_a_violation():
    src = "# json_extract(notes, '$.k') raises on malformed JSON\nx = 1\n"
    assert G.scan_source("x.py", src) == []


def test_shell_comment_prose_is_not_a_violation():
    src = "# json_extract(notes,'$.k') would raise here\necho hi\n"
    assert G.scan_source("x.sh", src) == []


def test_unparseable_python_still_scans_and_does_not_suppress():
    """On a syntax error the prose set degrades to (at most) comments — the
    guard must fail LOUD rather than silently allow a real hit through."""
    src = 'def broken(:\n    sql = "json_extract(notes, \'$.k\')"\n'
    assert G.scan_source("x.py", src)


# ------------------------------------------------------------- repo state
def test_repo_is_currently_clean():
    """Pins the sweep result: every json_extract in src/scripts/ml is guarded."""
    assert G.main([]) == 0
