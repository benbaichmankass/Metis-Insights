"""Tests for `scripts/ops/audit_verification_checklist.py`
(S-AI-WS10 follow-up)."""
from __future__ import annotations

import io
import json
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts.ops import audit_verification_checklist as avc  # noqa: E402


_SAMPLE_DOC = textwrap.dedent("""\
    # Architecture — Canonical

    Some preamble.

    ## Some Other Section

    - [x] This line is outside the checklist section.

    ## Verification Checklist (current state)

    Confirmed against the repo on 2026-05-10:

    - [x] Runtime entrypoint: `src/main.py` → `src/runtime/pipeline.py`
    - [x] Risk manager: `src/units/accounts/risk.py`
    - [x] No paths on this line — just a milestone.
    - [x] Comms directory: `comms/` with `requests/`, `archive/`, `schema/`

    ## Some Later Section

    - [x] This line is again outside the checklist.
""")


def _build_synthetic_repo(tmp_path: Path, *, existing: list[str]) -> Path:
    """Make a synthetic repo with the given files / directories
    present. `existing` items ending in '/' are dirs; others are files.
    """
    repo = tmp_path / "synth"
    repo.mkdir()
    for entry in existing:
        p = repo / entry
        if entry.endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
    doc = repo / "docs" / "ARCHITECTURE-CANONICAL.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(_SAMPLE_DOC)
    return repo


def _capture(fn, *args, **kwargs) -> tuple[int, list[dict]]:
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = fn(*args, **kwargs)
    finally:
        sys.stdout = saved
    out = buf.getvalue().strip()
    payload = json.loads(out) if out else {}
    return rc, payload


class TestParseChecklist:
    def test_picks_up_only_checklist_section(self):
        items = avc.parse_checklist(_SAMPLE_DOC)
        # 4 [x] lines inside the checklist (3 with paths + 1 milestone),
        # 2 [x] lines outside should be ignored.
        assert len(items) == 4
        first = items[0]
        # (lineno, desc, paths)
        assert first[1].startswith("Runtime entrypoint")
        assert "src/main.py" in first[2]


class TestPathExists:
    def test_simple_path(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x")
        assert avc._path_exists(tmp_path, "src/main.py")

    def test_missing_path(self, tmp_path: Path):
        assert not avc._path_exists(tmp_path, "src/missing.py")

    def test_brace_expansion(self, tmp_path: Path):
        (tmp_path / "deploy").mkdir()
        (tmp_path / "deploy" / "ict-trader.service").write_text("x")
        # Brace pattern that matches one of the alternatives.
        assert avc._path_exists(tmp_path, "deploy/ict-*.{service,timer}")

    def test_directory(self, tmp_path: Path):
        (tmp_path / "deploy").mkdir()
        assert avc._path_exists(tmp_path, "deploy/")


class TestAudit:
    def test_all_paths_present_returns_no_missing(self, tmp_path: Path):
        repo = _build_synthetic_repo(tmp_path, existing=[
            "src/main.py",
            "src/runtime/pipeline.py",
            "src/units/accounts/risk.py",
            "comms/",
        ])
        report = avc.audit(repo, repo / "docs" / "ARCHITECTURE-CANONICAL.md")
        assert report["checked"] == 3  # 3 lines with paths, 1 milestone-only
        assert report["missing"] == []
        assert report["verified"] == 3

    def test_missing_path_reported(self, tmp_path: Path):
        # Skip src/units/accounts/risk.py.
        repo = _build_synthetic_repo(tmp_path, existing=[
            "src/main.py",
            "src/runtime/pipeline.py",
            "comms/",
        ])
        report = avc.audit(repo, repo / "docs" / "ARCHITECTURE-CANONICAL.md")
        assert report["checked"] == 3
        assert len(report["missing"]) == 1
        assert report["missing"][0]["path"] == "src/units/accounts/risk.py"
        assert "Risk manager" in report["missing"][0]["description"]
        assert report["verified"] == 2

    def test_only_first_backtick_path_per_line_checked(self, tmp_path: Path):
        """The 'Comms directory: `comms/` with `requests/` ...' line
        should pass when `comms/` exists, even though `requests/`
        doesn't exist at the repo root."""
        repo = _build_synthetic_repo(tmp_path, existing=[
            "src/main.py",
            "src/runtime/pipeline.py",
            "src/units/accounts/risk.py",
            "comms/",
        ])
        report = avc.audit(repo, repo / "docs" / "ARCHITECTURE-CANONICAL.md")
        # No drift even though `requests/` isn't at the repo root.
        assert report["missing"] == []

    def test_missing_doc_returns_error_envelope(self, tmp_path: Path):
        report = avc.audit(tmp_path, tmp_path / "no-such.md")
        assert report["error"] == "doc_missing"
        assert "audited_at_utc" in report


class TestMainEntrypoint:
    def test_main_emits_json(self, tmp_path: Path):
        repo = _build_synthetic_repo(tmp_path, existing=[
            "src/main.py",
            "src/runtime/pipeline.py",
            "src/units/accounts/risk.py",
            "comms/",
        ])
        rc, payload = _capture(
            avc.main,
            [
                "--doc", str(repo / "docs" / "ARCHITECTURE-CANONICAL.md"),
                "--repo-root", str(repo),
            ],
        )
        assert rc == 0
        assert payload["checked"] == 3
        assert payload["missing"] == []


class TestAgainstRealDoc:
    """Smoke test against the live repo's checklist to make sure
    the parser handles the real text and not just the synthetic
    sample."""

    def test_live_checklist_has_at_least_one_path(self):
        doc = _REPO_ROOT / "docs" / "ARCHITECTURE-CANONICAL.md"
        if not doc.is_file():
            return  # skip if doc not on this branch yet
        items = avc.parse_checklist(doc.read_text(encoding="utf-8"))
        items_with_paths = [i for i in items if i[2]]
        assert items_with_paths, "live doc has no [x] lines with paths"

    def test_live_repo_checklist_clean(self):
        doc = _REPO_ROOT / "docs" / "ARCHITECTURE-CANONICAL.md"
        if not doc.is_file():
            return
        report = avc.audit(_REPO_ROOT, doc)
        # The live repo SHOULD be in sync — this is the same check
        # the weekly workflow runs.
        assert report.get("missing") == [], (
            f"live doc drift: {report.get('missing')}"
        )
