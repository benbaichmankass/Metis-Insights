"""A workflow GitHub cannot parse fails every run, silently, forever.

A workflow file can be valid YAML and still be invalid to Actions. When it is,
GitHub fails EVERY run at startup — zero jobs, zero duration — and reports the
workflow's `name` as its PATH, because it never read a `name:` out of the file.

MEASURED 2026-09-17: `.github/workflows/ranking-key-ab.yml` had **3,393**
completed runs, the newest 100 all `failure` with `total_jobs: 0`, ~38 an hour,
and NOTHING paged on it — `claude-run-failure-alert.yml` scopes to CRON'D
workflows and this one declares no `schedule:`. It was found only because
`src_red_main_runs` happened to surface it.

⚠️ THE FALSE-POSITIVE GUARD IS THE HARD PART, and most of the assertions below
are about it. GitHub reports a path-valued `name` in TWO unrelated situations:
when the file declares no `name:` (ordinary and correct) and when GitHub could
not parse the file (the defect). Only the local declaration separates them, and
a source that skipped that step would raise a loud row for every name-less
workflow in the repo.
"""
from __future__ import annotations

import datetime
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))

R = pytest.importorskip("render_due_list")
pytest.importorskip("yaml")
TODAY = datetime.date.today()


def _repo(tmp_path, files: dict[str, str]) -> pathlib.Path:
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")
    return tmp_path


def _api(monkeypatch, workflows, *, pages=None, boom=False):
    def gh(path, token):
        if boom:
            raise OSError("network")
        if pages is not None:
            import re
            # ⚠️ `[?&]` is load-bearing: a bare `page=(\d+)` matches
            # `per_page=100` first and every request reads as page 100.
            m = re.search(r"[?&]page=(\d+)", path)
            n = int(m.group(1)) if m else 1
            return {"workflows": pages[n - 1] if n <= len(pages) else []}
        return {"workflows": workflows}
    monkeypatch.setattr(R, "_gh", gh)


NAMED = "name: alpha\non:\n  push:\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
UNNAMED = "on:\n  push:\njobs:\n  a:\n    runs-on: ubuntu-latest\n"


class TestItFindsTheRealCondition:
    def test_a_path_named_workflow_whose_file_declares_a_name_is_a_loud_row(self, tmp_path, monkeypatch):
        root = _repo(tmp_path, {"alpha.yml": NAMED})
        _api(monkeypatch, [{"path": ".github/workflows/alpha.yml",
                            "name": ".github/workflows/alpha.yml"}])
        r = R.src_unparseable_workflows(root, TODAY, token="t")
        assert r.state == "read" and len(r.rows) == 1
        assert r.rows[0]["loud"] is True
        assert "never parsed it" in r.rows[0]["why_due"]

    def test_a_normally_parsed_workflow_is_not_a_row(self, tmp_path, monkeypatch):
        root = _repo(tmp_path, {"alpha.yml": NAMED})
        _api(monkeypatch, [{"path": ".github/workflows/alpha.yml", "name": "alpha"}])
        assert R.src_unparseable_workflows(root, TODAY, token="t").rows == []

    def test_the_row_does_not_claim_a_cause(self, tmp_path, monkeypatch):
        """Three hypotheses are refuted with positive controls; a fourth guess
        dressed as a cause would be worse than the open question."""
        root = _repo(tmp_path, {"alpha.yml": NAMED})
        _api(monkeypatch, [{"path": ".github/workflows/alpha.yml",
                            "name": ".github/workflows/alpha.yml"}])
        why = R.src_unparseable_workflows(root, TODAY, token="t").rows[0]["why_due"]
        assert "not established" in why


class TestTheFalsePositiveGuard:
    def test_a_workflow_declaring_NO_name_is_never_flagged(self, tmp_path, monkeypatch):
        """GitHub correctly reports the path for a name-less workflow. Flagging
        it would raise a loud row for ordinary, healthy files."""
        # A named file sits beside it deliberately: a repo of ONLY name-less
        # workflows has no denominator at all and correctly grades
        # could_not_read, which would test something else entirely.
        root = _repo(tmp_path, {"alpha.yml": NAMED, "bare.yml": UNNAMED})
        _api(monkeypatch, [
            {"path": ".github/workflows/alpha.yml", "name": "alpha"},
            {"path": ".github/workflows/bare.yml", "name": ".github/workflows/bare.yml"},
        ])
        r = R.src_unparseable_workflows(root, TODAY, token="t")
        assert r.state == "read"
        assert r.rows == [], "a name-less workflow was flagged as unparseable"
        assert "1 skipped because the file declares NO name" in r.note, r.note

    def test_the_two_cases_are_separated_in_one_repo(self, tmp_path, monkeypatch):
        root = _repo(tmp_path, {"alpha.yml": NAMED, "bare.yml": UNNAMED})
        _api(monkeypatch, [
            {"path": ".github/workflows/alpha.yml", "name": ".github/workflows/alpha.yml"},
            {"path": ".github/workflows/bare.yml", "name": ".github/workflows/bare.yml"},
        ])
        rows = R.src_unparseable_workflows(root, TODAY, token="t").rows
        assert [x["id"] for x in rows] == [".github/workflows/alpha.yml"]

    def test_a_workflow_github_has_no_record_for_is_skipped(self, tmp_path, monkeypatch):
        root = _repo(tmp_path, {"alpha.yml": NAMED})
        _api(monkeypatch, [])
        assert R.src_unparseable_workflows(root, TODAY, token="t").rows == []


class TestTheDenominatorIsNotSilentlyTruncated:
    def test_it_paginates_past_one_hundred(self, tmp_path, monkeypatch):
        """The repo carries ~142 workflow files against a 100-per-page API. A
        single page would grade two thirds of them and report the rest clean."""
        files = {f"w{i}.yml": NAMED.replace("alpha", f"w{i}") for i in range(150)}
        root = _repo(tmp_path, files)
        p1 = [{"path": f".github/workflows/w{i}.yml", "name": f"w{i}"} for i in range(100)]
        p2 = [{"path": f".github/workflows/w{i}.yml",
               "name": (".github/workflows/w{}.yml".format(i) if i == 140 else f"w{i}")}
              for i in range(100, 150)]
        _api(monkeypatch, None, pages=[p1, p2])
        r = R.src_unparseable_workflows(root, TODAY, token="t")
        assert "150 workflow(s) known to GitHub" in r.note, r.note
        assert [x["id"] for x in r.rows] == [".github/workflows/w140.yml"], \
            "the finding on page 2 was missed — the denominator truncated"


class TestSilenceIsNeverGreen:
    def test_no_token(self, tmp_path):
        r = R.src_unparseable_workflows(_repo(tmp_path, {"a.yml": NAMED}), TODAY, token="")
        assert r.state == "could_not_read" and r.rows == []

    def test_an_api_failure(self, tmp_path, monkeypatch):
        root = _repo(tmp_path, {"alpha.yml": NAMED})
        _api(monkeypatch, [], boom=True)
        assert R.src_unparseable_workflows(root, TODAY, token="t").state == "could_not_read"

    def test_no_local_file_declares_a_name_is_could_not_read_not_clean(self, tmp_path, monkeypatch):
        """Zero declarations is a MISSING DENOMINATOR. Reporting it as a clean
        repo is the unasserted-denominator defect this family exists to stop."""
        root = _repo(tmp_path, {"bare.yml": UNNAMED})
        _api(monkeypatch, [{"path": ".github/workflows/bare.yml", "name": "x"}])
        r = R.src_unparseable_workflows(root, TODAY, token="t")
        assert r.state == "could_not_read"
        assert "missing denominator" in r.note

    def test_an_unreadable_local_file_is_counted_not_dropped(self, tmp_path, monkeypatch):
        root = _repo(tmp_path, {"alpha.yml": NAMED, "broken.yml": "name: [unclosed\n"})
        _api(monkeypatch, [{"path": ".github/workflows/alpha.yml", "name": "alpha"}])
        r = R.src_unparseable_workflows(root, TODAY, token="t")
        assert "1 local file(s) unreadable as YAML" in r.note, r.note


class TestItIsActuallyWired:
    def test_registered_in_sources(self):
        assert R.src_unparseable_workflows in R.SOURCES

    def test_collect_passes_it_the_token(self):
        """It takes a token, so `collect` must dispatch it in the token branch —
        otherwise it reports could_not_read forever, which looks like a quiet
        source rather than a source that was never given what it needs."""
        src = (REPO / "scripts" / "ops" / "render_due_list.py").read_text(encoding="utf-8")
        i = src.index("if fn in (src_red_crons,")
        assert "src_unparseable_workflows" in src[i:i + 260], src[i:i + 260]

    def test_the_note_always_states_the_population(self, tmp_path, monkeypatch):
        root = _repo(tmp_path, {"alpha.yml": NAMED})
        _api(monkeypatch, [{"path": ".github/workflows/alpha.yml", "name": "alpha"}])
        note = R.src_unparseable_workflows(root, TODAY, token="t").note
        for token in ("known to GitHub", "declare a name", "gradeable"):
            assert token in note, note
