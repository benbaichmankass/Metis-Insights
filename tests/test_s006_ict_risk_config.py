"""
Tests for S-006 M3: ICT_RISK_PCT config additions.

Validates that the risk template and .env.example contain the expected ICT
risk profile values added after the synthetic backtest (PF=2.04 → GO).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# master-secrets.template.yaml
# ---------------------------------------------------------------------------


def test_template_has_ict_risk_section():
    text = (REPO_ROOT / "config" / "master-secrets.template.yaml").read_text()
    assert "ict:" in text


def test_template_ict_risk_per_trade_value():
    text = (REPO_ROOT / "config" / "master-secrets.template.yaml").read_text()
    # 0.4% expressed as decimal "0.004"
    assert 'risk_per_trade: "0.004"' in text


def test_template_ict_has_max_open_positions():
    text = (REPO_ROOT / "config" / "master-secrets.template.yaml").read_text()
    lines = text.splitlines()
    in_ict = False
    for line in lines:
        if line.strip() == "ict:":
            in_ict = True
        elif in_ict and line.strip().startswith("max_open_positions:"):
            assert '"1"' in line
            return
        elif in_ict and line and not line.startswith(" ") and not line.startswith("\t"):
            break
    assert False, "max_open_positions not found in ict risk section"


def test_template_ict_section_references_synthetic_backtest():
    text = (REPO_ROOT / "config" / "master-secrets.template.yaml").read_text()
    # The comment must mention the synthetic validation context
    assert "S-006" in text or "synthetic" in text.lower()
    assert "PF=" in text or "2.04" in text


# ---------------------------------------------------------------------------
# .env.example
# ---------------------------------------------------------------------------


def test_env_example_has_ict_risk_pct():
    text = (REPO_ROOT / ".env.example").read_text()
    assert "ICT_RISK_PCT" in text


def test_env_example_ict_risk_pct_value():
    text = (REPO_ROOT / ".env.example").read_text()
    assert "ICT_RISK_PCT=0.4" in text


def test_env_example_ict_risk_pct_has_comment():
    text = (REPO_ROOT / ".env.example").read_text()
    for line in text.splitlines():
        if "ICT_RISK_PCT" in line:
            assert "#" in line or any(
                "#" in ln and "ICT" in ln
                for ln in text.splitlines()
            ), "ICT_RISK_PCT line should have an inline comment"
            return
    assert False, "ICT_RISK_PCT not found in .env.example"
