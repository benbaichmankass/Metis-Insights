"""CP-2026-05-02-02 — every .github/workflows/*.yml file must be valid YAML.

Background: ``.github/workflows/hf-cron.yml`` was committed as a
single-line shorthand that wasn't valid YAML — every scheduled run
since it landed failed silently in the GitHub Actions UI, hiding any
real CI failures behind a flood of red. Lightweight regression guard:
parse every workflow file at test time and assert the minimum shape
GitHub Actions requires (a top-level ``jobs:`` mapping plus an ``on:``
trigger). This catches the same shape of bug as soon as it's
committed instead of only when the cron fires.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _workflow_files() -> list[Path]:
    if not WORKFLOWS.exists():
        return []
    return sorted(p for p in WORKFLOWS.iterdir()
                  if p.suffix in {".yml", ".yaml"} and p.is_file())


@pytest.mark.parametrize("path", _workflow_files(),
                         ids=lambda p: p.name)
def test_workflow_yaml_parses(path: Path):
    """Each workflow file must parse as YAML and have the GitHub
    Actions minimum shape (``on``/``True`` trigger key + ``jobs``
    mapping)."""
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert isinstance(data, dict), (
        f"{path.name}: top-level YAML must be a mapping, got {type(data).__name__}"
    )
    # PyYAML parses bare ``on:`` (no quotes) as the boolean ``True``
    # because YAML 1.1 normalises ``on`` to a bool. Either form is
    # acceptable here — we only care that some trigger is declared.
    assert "on" in data or True in data, (
        f"{path.name}: missing top-level 'on:' trigger"
    )
    assert "jobs" in data and isinstance(data["jobs"], dict) and data["jobs"], (
        f"{path.name}: missing or empty top-level 'jobs:' mapping"
    )
    for job_name, job in data["jobs"].items():
        assert isinstance(job, dict), (
            f"{path.name}:{job_name}: job must be a mapping"
        )
        assert "runs-on" in job or "uses" in job, (
            f"{path.name}:{job_name}: job missing 'runs-on' (or 'uses' for reusable)"
        )
