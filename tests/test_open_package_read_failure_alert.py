"""ORDER-AUDIT-3: open-package read failures.

* PI-20260930-MEK2XMWT-0004 -- a PERSISTENT read failure refuses every entry
  (both gates fail closed) and nothing paged. It now pages ONCE, through the
  existing ``outcomes.report`` path, after 3 consecutive failed rounds; a clean
  read resets the streak. The gate itself is unchanged: still blocks, still
  retries every tick (no halt).
* PI-20260930-MEK2XMWT-0005 -- ``_monocle_gate`` (allocator path) reported a
  ``journal_unreadable:...`` read failure as ``open_package_exists``.
"""
from __future__ import annotations

import pytest

from src.runtime import pipeline as pl
from src.runtime import strategy_monocle as sm


def _sig():
    return {"symbol": "SPY", "side": "buy", "meta": {"strategy_name": "spy_trend"}}


@pytest.fixture
def pages(monkeypatch):
    out = []
    monkeypatch.setattr(pl, "_OPEN_PKG_READ_FAIL_STREAK", 0, raising=False)
    monkeypatch.setattr(pl, "report",
                        lambda *a, **k: out.append((a, k)))
    monkeypatch.setattr("src.runtime.alert_cooldown.cooldown_admits",
                        lambda *a, **k: True)
    monkeypatch.setattr(pl, "log_signal", lambda row: None)
    return out


def _verdict(read_error):
    return {"action": "block", "scope": None, "excluded": {}, "pair_coupled": {},
            "order_package_id": None, "read_error": read_error}


def test_a_persistent_read_failure_pages_once_at_the_threshold(monkeypatch, pages):
    monkeypatch.setattr(pl, "_open_package_scope",
                        lambda *a, **k: _verdict("OperationalError: database is locked"))
    for _ in range(2):
        gate, _ = pl._open_package_round(_sig(), None)
        assert gate["reason"] == "open_package_read_failed"
    assert pages == []
    pl._open_package_round(_sig(), None)
    assert len(pages) == 1
    (component, outcome), kw = pages[0]
    assert (component, outcome) == ("open_package_gate", "read_failed")
    assert kw["level"] == pl.Level.ERROR
    assert "database is locked" in kw["reason"]


def test_a_clean_read_resets_the_streak(monkeypatch, pages):
    seq = iter([_verdict("boom"), _verdict("boom"),
                {"action": "pass", "scope": None},
                _verdict("boom"), _verdict("boom")])
    monkeypatch.setattr(pl, "_open_package_scope", lambda *a, **k: next(seq))
    for _ in range(5):
        pl._open_package_round(_sig(), None)
    assert pages == []


def test_a_latched_repeat_is_downgraded_not_silenced(monkeypatch, pages):
    monkeypatch.setattr("src.runtime.alert_cooldown.cooldown_admits",
                        lambda *a, **k: False)
    monkeypatch.setattr(pl, "_open_package_scope", lambda *a, **k: _verdict("boom"))
    for _ in range(3):
        pl._open_package_round(_sig(), None)
    assert [kw["level"] for _, kw in pages] == [pl.Level.WARN]


def test_monocle_gate_names_a_read_failure_as_one(monkeypatch, pages):
    unreadable = f"{sm.UNREADABLE_PACKAGE_PREFIX}OperationalError"
    monkeypatch.setattr(pl, "_has_open_package_for_strategy",
                        lambda s, symbol=None: unreadable)
    gate = pl._monocle_gate(_sig(), {})
    assert gate["status"] == "skipped"          # still blocks (fail closed)
    assert gate["reason"] == "open_package_read_failed"
    assert gate["cause"] == unreadable


def test_monocle_gate_real_open_package_is_unchanged(monkeypatch, pages):
    monkeypatch.setattr(pl, "_has_open_package_for_strategy",
                        lambda s, symbol=None: "pkg-123")
    assert pl._monocle_gate(_sig(), {})["reason"] == "open_package_exists"
