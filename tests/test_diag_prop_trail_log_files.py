"""TRAIL-OBSERVABILITY: the prop trail's latch and replay state are readable.

The entries must resolve to the SAME path the writer uses
(``scripts/prop/prop_executor_tick.py::default_state_dir``), and every
``type: prop`` account in config/accounts.yaml must have both.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from scripts.prop.prop_executor_tick import default_state_dir
from src.prop.prop_trail import ROLLOUT_FILE, STATE_FILE
from src.web.api.routers import diag

ROOT = Path(__file__).resolve().parents[1]


def _prop_accounts() -> list:
    doc = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())
    accts = doc.get("accounts", doc)
    names = sorted(k for k, v in accts.items()
                   if isinstance(v, dict) and v.get("type") == "prop")
    assert names, "probe broken: no type: prop accounts parsed"
    return names


def test_every_prop_account_has_latch_and_state_entries(monkeypatch):
    for acct in _prop_accounts():
        assert f"prop_trail_latch_{acct}" in diag._LOG_FILES, acct
        assert f"prop_trail_state_{acct}" in diag._LOG_FILES, acct


def test_entries_resolve_to_the_writers_state_dir(monkeypatch):
    monkeypatch.delenv("PROP_BROWSER_BASE", raising=False)
    for acct in _prop_accounts():
        want = default_state_dir(acct)
        assert diag._LOG_FILES[f"prop_trail_latch_{acct}"] == want / ROLLOUT_FILE
        assert diag._LOG_FILES[f"prop_trail_state_{acct}"] == want / STATE_FILE
