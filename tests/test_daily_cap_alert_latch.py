"""Latching daily-cap notification — one ping per transition.

`note_account_cap_state` must:
  * ping ``exhausted`` once when an account first crosses into "capped",
  * stay silent while it remains capped (the latch),
  * ping ``resumed`` once when it crosses back out,
  * NOT ping ``resumed`` on the very first observation (no prior episode).
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def cap_mod(tmp_path, monkeypatch):
    # Redirect runtime_logs_dir to a tmp dir so the state file + pending
    # pings land in isolation, then (re)import the module fresh.
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(tmp_path))
    import src.utils.paths as paths
    monkeypatch.setattr(paths, "runtime_logs_dir", lambda: tmp_path)
    mod = importlib.import_module("src.runtime.daily_cap_alert")
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_state_path", lambda: tmp_path / "state.json")
    return mod


def _pings(tmp_path):
    d = tmp_path / "pending_pings"
    return sorted(d.glob("*-dailycap.json")) if d.exists() else []


def test_latch_emits_once_per_transition(cap_mod, tmp_path, monkeypatch):
    sent = []
    import src.runtime.execution_diagnostics as ed
    monkeypatch.setattr(
        ed, "enqueue_daily_cap_alert",
        lambda **kw: sent.append((kw["account"], kw["kind"])) or None,
    )

    acct = "bybit_1"
    # First observation: not exhausted → no ping (no prior episode).
    assert cap_mod.note_account_cap_state(acct, exhausted=False) is None
    # Cross into capped → one 'exhausted' ping.
    assert cap_mod.note_account_cap_state(acct, exhausted=True, daily_pnl=-13_700,
                                          cap_usd=13_700) == "exhausted"
    # Still capped on subsequent ticks → silent (latch holds).
    assert cap_mod.note_account_cap_state(acct, exhausted=True) is None
    assert cap_mod.note_account_cap_state(acct, exhausted=True) is None
    # Cross back out (e.g. 00:00 UTC reset) → one 'resumed' ping.
    assert cap_mod.note_account_cap_state(acct, exhausted=False, daily_pnl=0.0,
                                          cap_usd=13_700) == "resumed"
    # Stays clear → silent.
    assert cap_mod.note_account_cap_state(acct, exhausted=False) is None

    assert sent == [("bybit_1", "exhausted"), ("bybit_1", "resumed")]


def test_first_observation_exhausted_pings(cap_mod, monkeypatch):
    sent = []
    import src.runtime.execution_diagnostics as ed
    monkeypatch.setattr(
        ed, "enqueue_daily_cap_alert",
        lambda **kw: sent.append(kw["kind"]) or None,
    )
    # If the very first time we see an account it is already capped, that
    # IS a real transition worth announcing.
    assert cap_mod.note_account_cap_state("ib_paper", exhausted=True) == "exhausted"
    assert sent == ["exhausted"]
