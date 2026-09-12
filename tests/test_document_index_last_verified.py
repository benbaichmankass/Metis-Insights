"""`--write` re-dated `last verified` on every document in the repo.

`last verified` is the field a session reads to decide whether a document can
be trusted. Stamping it asserts a verification that provably did not happen —
no session verifies 1054 documents — and it destroys the signal in the
direction that cannot be recovered: a document last genuinely assessed in May
becomes indistinguishable from one assessed today, with no record of which was
which.

⚠️ AND THE GUARD PRESCRIBED IT. `document-index-guard`'s R1 failure names ONE
unregistered file and hands you `python3 scripts/ops/document_index.py --write`,
whose diff is three orders of magnitude larger. A session following the
documented remedy and not reading `git diff --stat` ships it. The precedent is
`backlog_append`, fixed by making the helper REFUSE rather than by documenting
the hazard.

MEASURED A/B — same starting tree (`origin/main` + ONE new document), same
command, only the writer swapped:

    shipped writer   472 files changed · 425 differing ONLY by that date
                     (CLAUDE.md, CLAUDE-RULES-CANONICAL.md and
                      ARCHITECTURE-CANONICAL.md among them — hierarchy
                      levels 1, 2 and 6)
    this change       47 files changed ·   0 differing only by that date

⚠️ THE ROW'S LITERAL CRITERION IS NOT THE MEASUREMENT THAT SEPARATES THEM, and
saying so matters more than claiming a pass. It asks for `git diff --name-only
| wc -l` to return **2**. Run from a fixed point re-stamped THE SAME DAY, the
SHIPPED writer returns 2 as well — because stamping `today` is idempotent
within a day — so that count cannot tell the defect from the fix. It is the
DATE-ONLY count that can, and the residual 47 here are pre-existing debt this
change surfaces rather than causes (39 rows whose committed status no longer
reproduces, plus genuinely unstamped documents).

Run: ``python3 -m pytest tests/test_document_index_last_verified.py``
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ops" / "document_index.py"


def _load():
    spec = importlib.util.spec_from_file_location("_docindex", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = _load()
TODAY = "2026-09-12"


def _computed(status="live", category="instruction", lv=TODAY):
    return {"status": status, "category": category, "superseded_by": "",
            "last_verified": lv}


def _stamped(tmp_path, monkeypatch, *, status="live", category="instruction",
             date="2026-05-04", name="d.md"):
    p = tmp_path / name
    p.write_text(
        f"> **Doc status:** `{status}` · category `{category}` · "
        f"last verified `{date}` · registered in [`docs/DOCUMENT-INDEX.md`]"
        "(docs/DOCUMENT-INDEX.md)\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(D, "REPO", tmp_path)
    return name


# ── THE FIX ─────────────────────────────────────────────────────────────────

def test_an_unchanged_document_keeps_the_date_it_already_had(tmp_path, monkeypatch):
    """The whole point: May stays May."""
    rel = _stamped(tmp_path, monkeypatch, date="2026-05-04")
    date, basis = D.carry_last_verified(rel, _computed(), TODAY)
    assert (date, basis) == ("2026-05-04", "carried")


def test_a_document_whose_STATUS_changed_is_re_dated(tmp_path, monkeypatch):
    """Carrying here would assert the old date describes a NEW assessment."""
    rel = _stamped(tmp_path, monkeypatch, status="live", date="2026-05-04")
    date, basis = D.carry_last_verified(rel, _computed(status="superseded"), TODAY)
    assert (date, basis) == (TODAY, "assessment_changed")


def test_a_document_whose_CATEGORY_changed_is_re_dated(tmp_path, monkeypatch):
    rel = _stamped(tmp_path, monkeypatch, category="instruction", date="2026-05-04")
    date, basis = D.carry_last_verified(rel, _computed(category="evidence"), TODAY)
    assert (date, basis) == (TODAY, "assessment_changed")


def test_a_document_with_no_stamp_gets_one(tmp_path, monkeypatch):
    p = tmp_path / "fresh.md"
    p.write_text("# fresh\n\nno stamp here\n", encoding="utf-8")
    monkeypatch.setattr(D, "REPO", tmp_path)
    date, basis = D.carry_last_verified("fresh.md", _computed(), TODAY)
    assert (date, basis) == (TODAY, "new")


def test_an_UNREADABLE_file_is_treated_as_unstamped_not_as_carried(tmp_path, monkeypatch):
    """`we could not read it` must not silently preserve a date we never saw."""
    monkeypatch.setattr(D, "REPO", tmp_path)
    date, basis = D.carry_last_verified("does-not-exist.md", _computed(), TODAY)
    assert (date, basis) == (TODAY, "new")


def test_never_is_CARRIED_because_it_is_a_deliberate_claim(tmp_path, monkeypatch):
    """`never` says nobody has assessed this. Replacing it with today's date
    would be the defect in its purest form."""
    rel = _stamped(tmp_path, monkeypatch, status="unknown", category="unknown",
                   date="never")
    date, basis = D.carry_last_verified(
        rel, _computed(status="unknown", category="unknown", lv="never"), TODAY)
    assert (date, basis) == ("never", "carried")


# ── THE OPT-IN, which must NOT be the documented remedy ────────────────────

def test_restamp_exists_as_an_explicit_flag_and_says_what_it_asserts():
    """The capability is not removed, only taken off the default path. Its help
    text has to say what running it CLAIMS, because that is the whole
    distinction between it and `--write`."""
    import inspect
    src = inspect.getsource(D.main)
    assert '"--restamp"' in src, "the deliberate re-verification path must exist"
    assert "re-verified" in src.lower() or "human act" in src.lower(), \
        "the flag must state that it asserts a verification somebody performed"


def test_the_guards_remedy_line_does_NOT_tell_a_session_to_restamp():
    """The row's whole point: the failure IS a documented instruction followed
    correctly. If the remedy ever grows `--restamp`, this class is back."""
    guard = (REPO / "scripts" / "ci" / "check_document_index.py").read_text(
        encoding="utf-8")
    assert "--restamp" not in guard, (
        "the guard must never prescribe re-dating every document; that is the "
        "trap this change exists to remove")


def test_build_rows_carries_by_default_and_restamps_only_when_asked():
    import inspect
    src = inspect.getsource(D.build_rows)
    assert "restamp" in src and "carry_last_verified" in src
    assert "restamp: bool = False" in src, "carrying must be the DEFAULT"


# ── THE STAMP PARSER, which is what makes the carry possible ───────────────

def test_the_parser_reads_all_three_fields_a_carry_decision_needs(tmp_path, monkeypatch):
    rel = _stamped(tmp_path, monkeypatch, status="superseded",
                   category="evidence", date="2026-06-01")
    got = D.existing_stamp(rel)
    assert got["status"] == "superseded"
    assert got["category"] == "evidence"
    assert got["last_verified"] == "2026-06-01"


def test_the_parser_returns_None_rather_than_a_blank_row_when_there_is_no_stamp(
        tmp_path, monkeypatch):
    p = tmp_path / "n.md"
    p.write_text("# n\n\nnothing\n", encoding="utf-8")
    monkeypatch.setattr(D, "REPO", tmp_path)
    assert D.existing_stamp("n.md") is None, \
        "None is 'no stamp to preserve'; a blank dict would read as a carryable one"


def test_the_two_regexes_agree_about_status_on_the_same_line(tmp_path, monkeypatch):
    """R3 reads STAMP_RE, the writer reads STAMP_FULL_RE. Two surfaces reading
    one line must not disagree about it."""
    rel = _stamped(tmp_path, monkeypatch, status="superseded", date="2026-06-01")
    text = (tmp_path / rel).read_text(encoding="utf-8")
    assert D.STAMP_RE.search(text).group("status") == \
        D.STAMP_FULL_RE.search(text).group("status") == "superseded"
