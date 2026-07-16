"""BL-20260716-PAIRS-EXEC — the merge-gate for the pairs sizing basis.

Two layers:

1. Self-test for the ``pairs-sizing-basis`` CI guard
   (``scripts/ci/check_pairs_sizing_basis.py``): a planted `risk_budget_usd`
   config key and a planted config-read of it in the executor must fail; a
   commented mention, the `pairs_risk_fraction` key, an allow-marked line, and a
   clean derive must pass; ripping the derive tokens out must fail.

2. The live invariant: the real `config/pairs.yaml` + `pairs_executor.py`
   currently pass, AND the executor still derives from the canonical basis
   (references `_fetch_balance` + `risk_pct`, no `risk_budget_usd` key read).
"""
from __future__ import annotations

from pathlib import Path

from scripts.ci.check_pairs_sizing_basis import (
    PAIRS_CONFIG,
    PAIRS_EXECUTOR,
    find_config_violations,
    find_executor_violations,
    scan,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


# --- guard self-test ---------------------------------------------------------

def test_config_hardcode_key_flagged():
    assert find_config_violations("risk_budget_usd: 20.0\n", PAIRS_CONFIG)


def test_config_commented_mention_is_clean():
    assert find_config_violations("# risk_budget_usd: legacy note\n", PAIRS_CONFIG) == []


def test_config_fraction_key_is_clean():
    assert find_config_violations("pairs_risk_fraction: 1.0\n", PAIRS_CONFIG) == []


def test_config_allow_marker_suppresses():
    src = "risk_budget_usd: 20.0  # pairs-sizing-allow: reviewed fixture\n"
    assert find_config_violations(src, PAIRS_CONFIG) == []


def test_executor_key_read_flagged():
    bad = 'x = cfg.get("risk_budget_usd", 20.0)\n_fetch_balance\nrisk_pct\n'
    hits = find_executor_violations(bad, PAIRS_EXECUTOR)
    assert any("reads `risk_budget_usd`" in m for _, m in hits)


def test_executor_clean_derive_passes():
    good = "b = _fetch_balance(client, cfg)\nrisk_pct = cfg['risk']['risk_pct']\n"
    assert find_executor_violations(good, PAIRS_EXECUTOR) == []


def test_executor_missing_derive_tokens_flagged():
    hits = find_executor_violations("budget = 20.0\n", PAIRS_EXECUTOR)
    assert any("no longer references" in m for _, m in hits)


# --- live invariant ----------------------------------------------------------

def test_live_pairs_sleeve_sizes_off_canonical_basis():
    violations = scan(_REPO_ROOT)
    assert violations == [], "pairs sizing-basis drift:\n" + "\n".join(violations)


def test_executor_still_derives_from_account_basis():
    text = (_REPO_ROOT / PAIRS_EXECUTOR).read_text(encoding="utf-8")
    assert "_fetch_balance" in text and "risk_pct" in text
    # the removed hardcode key must not be read back from config
    assert '"risk_budget_usd"' not in text and "'risk_budget_usd'" not in text
