"""The compat-matrix run lands its verdicts, and the assertion can FAIL.

`gld-compat-matrix.yml` ended at `upload-artifact`, which a PM-side session
cannot download — one of the eighteen in
`BL-20260827-EIGHTEEN-EVIDENCE-WORKFLOWS-UPLOAD-AND-LAND-NOTHING`. It is also
the first job an armed research-queue cron fires, and `RQ-20260827-001` declares
a `lands.store` that had never existed on `main`.

The load-bearing test is `test_the_landing_assertion_is_run_scoped`: on a
CUMULATIVE store an assertion keyed on a field the store already holds is
satisfied by history and can never fail — the e35 vacuity fixed in #10487.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
WF = REPO / ".github/workflows/gld-compat-matrix.yml"

from scripts.research.gld_compat_extract import rows_from  # noqa: E402


def _steps() -> list[dict]:
    return yaml.safe_load(WF.read_text())["jobs"]["compat"]["steps"]


def _named(frag: str) -> dict:
    hits = [s for s in _steps() if frag.lower() in str(s.get("name", "")).lower()]
    assert len(hits) == 1, f"expected one step matching {frag!r}, got {len(hits)}"
    return hits[0]


def test_extractor_selftest_passes():
    r = subprocess.run([sys.executable, "scripts/research/gld_compat_extract.py",
                        "--selftest"], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_producer_key_is_renamed_at_the_boundary():
    """`account_compat_matrix.py` writes `account`; the corpus keys `account_id`.

    The queue job's `assert_field` is `account_id`, and every other store in the
    repo keys accounts that way. The rename happens once, here.
    """
    row = rows_from({"generated_at": "t", "rows": [{"account": "bybit_2", "verdict": "ROUTE"}]})[0]
    assert row["account_id"] == "bybit_2"
    assert "account" not in row


def test_an_undateable_payload_is_refused():
    """A row that cannot be dated cannot be scoped to its run."""
    with pytest.raises(ValueError):
        rows_from({"rows": [{"account": "x"}]})


def test_an_empty_scan_exits_2_rather_than_reporting_success(tmp_path):
    """A silent zero-row success is how a broken producer reads as a quiet one."""
    r = subprocess.run([sys.executable, "scripts/research/gld_compat_extract.py",
                        "--in-dir", str(tmp_path), "--store", str(tmp_path / "s.jsonl")],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr


def test_the_workflow_lands_and_asserts():
    names = " | ".join(str(s.get("name", "")) for s in _steps())
    assert "Land the corpus on main" in names
    assert _named("Land the corpus on main")["uses"] == "./.github/actions/commit-to-main"
    assert "Assert the rows actually landed" in names


def test_the_landing_assertion_is_run_scoped():
    """THE load-bearing one. Scoped on this run's stamp, not on stored history."""
    body = _named("Assert the rows actually landed")["run"]
    assert "--field run_generated_at" in body
    assert 'run-stamp: //p' in body, "the stamp must come from the extractor's output"
    for stored in ("--field account_id", "--field verdict", "--field strategy"):
        assert stored not in body, (
            f"{stored} is satisfied by rows already in the cumulative store — "
            "the assertion could never fail"
        )


def test_the_job_checks_out_with_a_pat():
    """commit-to-main's own contract: a GITHUB_TOKEN PR never triggers checks."""
    co = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout")][0]
    assert "BRANCH_PROTECTION_TOKEN" in str(co.get("with", {}).get("token", ""))


def test_round_trip_through_the_store(tmp_path):
    """Positive control for the refusals above: a real payload IS written."""
    d = tmp_path / "in"; d.mkdir()
    (d / "compat_gld.json").write_text(json.dumps({
        "generated_at": "2026-08-30T10:00:00+00:00", "strategy": "gld_pullback_1h",
        "rows": [{"account": "alpaca_portfolio", "verdict": "ROUTE"}]}))
    store = tmp_path / "s.jsonl"
    r = subprocess.run([sys.executable, "scripts/research/gld_compat_extract.py",
                        "--in-dir", str(d), "--store", str(store)],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "run-stamp: 2026-08-30T10:00:00+00:00" in r.stdout
    assert json.loads(store.read_text().strip())["account_id"] == "alpaca_portfolio"
