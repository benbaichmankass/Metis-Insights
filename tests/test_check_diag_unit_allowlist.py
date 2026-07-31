"""Tests for scripts/check_diag_unit_allowlist.py (2026-07-31 audit P2.4).

The guard's contract: every deploy/ unit is allowlisted or exempted-with-
reason; stale exemptions fail; an empty deploy/ is an absent result, not a
clean one. Run via subprocess in a fabricated tree so the failure paths are
genuinely exercised (a guard whose red path is never run is a green that
checked nothing).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_diag_unit_allowlist.py"


def _make_tree(tmp: Path, allow: list[str], deploy: list[str]) -> None:
    router = tmp / "src" / "web" / "api" / "routers"
    router.mkdir(parents=True)
    entries = "".join(f'    "{u}",\n' for u in allow)
    (router / "diag.py").write_text(
        f"_CANONICAL_UNITS: tuple[str, ...] = (\n{entries})\n", encoding="utf-8")
    dep = tmp / "deploy"
    dep.mkdir()
    for u in deploy:
        (dep / u).write_text("", encoding="utf-8")
    (tmp / "scripts").mkdir()
    shutil.copy(_SCRIPT, tmp / "scripts" / _SCRIPT.name)


def _run(tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tmp / "scripts" / _SCRIPT.name)],
        cwd=tmp, capture_output=True, text=True,
    )


def test_covered_tree_passes(tmp_path: Path):
    # Exempted units come from the REAL script's EXEMPT table; use one of them.
    _make_tree(tmp_path,
               allow=["ict-a.service", "ict-a.timer"],
               deploy=["ict-a.service", "ict-a.timer",
                       "ict-heartbeat.service", "ict-heartbeat.timer",
                       "ict-ib-gateway-reset.service", "ict-ib-gateway-reset.timer",
                       "ict-trainer-git-sync.service", "ict-trainer-git-sync.timer",
                       "ict-env-check.service", "ict-smoke-once.service",
                       "claude-vm-runner@.service"])
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_uncovered_unit_fails(tmp_path: Path):
    _make_tree(tmp_path,
               allow=["ict-a.service"],
               deploy=["ict-a.service", "ict-brand-new.timer",
                       "ict-heartbeat.service", "ict-heartbeat.timer",
                       "ict-ib-gateway-reset.service", "ict-ib-gateway-reset.timer",
                       "ict-trainer-git-sync.service", "ict-trainer-git-sync.timer",
                       "ict-env-check.service", "ict-smoke-once.service",
                       "claude-vm-runner@.service"])
    r = _run(tmp_path)
    assert r.returncode == 1
    assert "ict-brand-new.timer" in r.stdout


def test_stale_exemption_fails(tmp_path: Path):
    # Deploy tree WITHOUT ict-heartbeat.* — its exemption is now stale.
    _make_tree(tmp_path,
               allow=["ict-a.service"],
               deploy=["ict-a.service",
                       "ict-ib-gateway-reset.service", "ict-ib-gateway-reset.timer",
                       "ict-trainer-git-sync.service", "ict-trainer-git-sync.timer",
                       "ict-env-check.service", "ict-smoke-once.service",
                       "claude-vm-runner@.service"])
    r = _run(tmp_path)
    assert r.returncode == 1
    assert "STALE" in r.stdout


def test_empty_deploy_is_absent_not_clean(tmp_path: Path):
    _make_tree(tmp_path, allow=["ict-a.service"], deploy=[])
    r = _run(tmp_path)
    assert r.returncode == 1
    assert "scanned NOTHING" in r.stdout


def test_real_tree_is_currently_covered():
    r = subprocess.run([sys.executable, str(_SCRIPT)], cwd=_REPO_ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
