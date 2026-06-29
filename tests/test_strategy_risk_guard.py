"""Tests for scripts/check_strategy_risk_field_in_diff.py — the strategy-risk
guard (per-strategy risk removal, 2026-06-29). A clean diff passes; a re-added
`risk_pct:` in config/strategies.yaml or a `strategy_risk_pct` token in src/
trips it; the `# allow-strategy-risk:` override and tests/docs paths are exempt.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_strategy_risk_field_in_diff",
    Path(__file__).resolve().parents[1] / "scripts" / "check_strategy_risk_field_in_diff.py",
)
guard = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(guard)  # type: ignore


def _diff(path: str, added: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,0 +1,1 @@\n+{added}\n"


def test_clean_diff_passes():
    assert guard.scan_diff(_diff("src/runtime/intents.py", "    x = 1")) == []


def test_strategies_yaml_risk_pct_trips():
    findings = guard.scan_diff(_diff("config/strategies.yaml", "    risk_pct: 0.3"))
    assert findings and "risk_pct" in findings[0]


def test_src_strategy_risk_pct_token_trips():
    findings = guard.scan_diff(
        _diff("src/runtime/strategy_signal_builders.py",
              '            "strategy_risk_pct": float(cfg.get("risk_pct", 0.3)),')
    )
    assert findings and "strategy_risk_pct" in findings[0]


def test_accounts_yaml_risk_pct_is_allowed():
    # account-level risk_pct is the canonical basis — not flagged.
    assert guard.scan_diff(_diff("config/accounts.yaml", "      risk_pct: 0.015")) == []


def test_allow_override_exempts():
    line = "    risk_pct: 0.3  # allow-strategy-risk: special one-off"
    assert guard.scan_diff(_diff("config/strategies.yaml", line)) == []


def test_tests_and_docs_paths_exempt():
    assert guard.scan_diff(_diff("tests/test_x.py", '    meta = {"strategy_risk_pct": 1.0}')) == []
    assert guard.scan_diff(_diff("docs/research/x.md", "risk_pct: 0.3")) == []


def test_main_exit_codes(tmp_path, capsys):
    clean = tmp_path / "clean.diff"
    clean.write_text(_diff("src/runtime/intents.py", "    x = 1"))
    assert guard.main(["prog", str(clean)]) == 0
    bad = tmp_path / "bad.diff"
    bad.write_text(_diff("config/strategies.yaml", "    risk_pct: 0.3"))
    assert guard.main(["prog", str(bad)]) == 1
