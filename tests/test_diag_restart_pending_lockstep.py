"""OPS-AUDIT 2026-10-04 (OA-14): diag's restart_pending must use the deploy's own
non-runtime path set. #15830 widened scripts/deploy_pull_restart.sh's set and
the diag copy drifted, so /api/diag/version read restart_pending=true on a
research-only diff (observed: running 63bd0ce01 vs on-disk cd9dc2ed8).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _deploy_pattern() -> str:
    text = (REPO / "scripts" / "deploy_pull_restart.sh").read_text(encoding="utf-8")
    m = re.search(r"\| grep -vE '(\^\([^']+\))' \|\| true\)\"", text)
    assert m, "RUNTIME_CHANGES filter not found in deploy_pull_restart.sh"
    return m.group(1)


def _diag_pattern() -> str:
    text = (REPO / "src" / "web" / "api" / "routers" / "diag.py").read_text(encoding="utf-8")
    m = re.search(r"_NON_RUNTIME_PATHS_RE = re\.compile\(\s*((?:r\"[^\"]*\"\s*)+)\)", text)
    assert m, "_NON_RUNTIME_PATHS_RE not found in diag.py"
    return "".join(re.findall(r'r"([^"]*)"', m.group(1)))


def _deploy_in_process_pattern() -> str:
    text = (REPO / "scripts" / "deploy_pull_restart.sh").read_text(encoding="utf-8")
    m = re.search(r"\| grep -E '(\^scripts/research/[^']+)' \|\| true\)\"", text)
    assert m, "IN_PROCESS_CHANGES grep not found in deploy_pull_restart.sh"
    return m.group(1)


def _diag_in_process_pattern() -> str:
    text = (REPO / "src" / "web" / "api" / "routers" / "diag.py").read_text(encoding="utf-8")
    m = re.search(r"_IN_PROCESS_RUNTIME_PATHS_RE = re\.compile\(\s*((?:r\"[^\"]*\"\s*)+)\)", text)
    assert m, "_IN_PROCESS_RUNTIME_PATHS_RE not found in diag.py"
    return "".join(re.findall(r'r"([^"]*)"', m.group(1)))


def test_diag_non_runtime_paths_equal_the_deploy_filter():
    assert _diag_pattern() == _deploy_pattern()


def test_diag_in_process_exception_equals_the_deploy_exception():
    """OA-16(d): the scripts/research modules the brief imports in-process."""
    assert _diag_in_process_pattern() == _deploy_in_process_pattern()


def test_diag_classifier_matches_the_deploy_on_the_edge_paths():
    from src.web.api.routers.diag import _is_runtime_path
    assert not _is_runtime_path("comms/macro/valuation_snapshots.jsonl")
    assert not _is_runtime_path("scripts/research/regime_matrix.py")
    assert _is_runtime_path("scripts/research/queue_throughput.py")
    assert _is_runtime_path("comms/claude_strategy_scores.jsonl")
    assert not _is_runtime_path("")


def test_research_only_paths_are_non_runtime_and_src_is_not():
    pat = re.compile(_diag_pattern())
    for p in ("research/results/x.jsonl", "comms/research/RQ/1/verdict.json",
              "scripts/research/egress_chromium_landing_probe.py", "docs/a.md", "README.md"):
        assert pat.match(p), p
    for p in ("src/prop/prop_executor.py", "config/accounts.yaml", "scripts/ops/x.sh", "deploy/u.service"):
        assert not pat.match(p), p
