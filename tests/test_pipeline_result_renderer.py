"""Tests for the multi-account dispatch renderer in _pipeline_result_sections.

Pin that result dicts using the coordinator's actual key names
(``name``, ``error``, ``sized_qty``) are rendered correctly — not
the old stale keys (``account``/``account_id``, ``status``, ``qty``)
that caused ``?: ?`` rows in the Telegram "Accounts dispatched" section.

BUG: pre-fix the renderer used ``r.get("account") or r.get("account_id")``
and ``r.get("status")`` but ``multi_account_execute`` returns
``{"name": ..., "error": ..., "sized_qty": ...}``.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock


# Stub heavy deps before pipeline import.
for _mod in (
    "pandas",
    "numpy",
    "src.runtime.signal_notifications",
    "src.runtime.signal_writer",
    "src.runtime.notify",
    "src.utils.signal_audit_logger",
):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime.pipeline_result import _pipeline_result_sections  # noqa: E402


def _sections(multi_results, status="dispatched"):
    signal = {"side": "buy", "strategy": "vwap", "confidence": 0.85}
    result = {
        "status": status,
        "multi_account_results": multi_results,
    }
    return _pipeline_result_sections(signal=signal, result=result, strategy="vwap")


def _dispatch_section(sections):
    return next((s for s in sections if "dispatched" in s.summary.lower()), None)


class TestAccountsDispatchedRenderer:
    def test_name_field_rendered_not_question_mark(self):
        """Account name comes from 'name' key, not 'account'/'account_id'."""
        secs = _sections([
            {"name": "bybit_2", "error": None, "sized_qty": 0.001},
        ])
        sec = _dispatch_section(secs)
        assert sec is not None
        assert "bybit_2" in sec.body
        assert "?" not in sec.body.split(":")[0]

    def test_ok_outcome_for_none_error(self):
        secs = _sections([
            {"name": "bybit_2", "error": None, "sized_qty": 0.001},
        ])
        sec = _dispatch_section(secs)
        assert "ok" in sec.body

    def test_error_string_shown_for_failure(self):
        secs = _sections([
            {"name": "bybit_1", "error": "skipped_not_assigned: ...", "sized_qty": 0.0},
        ])
        sec = _dispatch_section(secs)
        assert "bybit_1" in sec.body
        assert "skipped_not_assigned" in sec.body

    def test_three_mixed_outcome_accounts(self):
        """Full 3-account scenario: one ok, one skipped, one zero-balance."""
        secs = _sections([
            {"name": "bybit_1", "error": "skipped_not_assigned: vwap not in strategies", "sized_qty": 0.0},
            {"name": "bybit_2", "error": None, "sized_qty": 0.001},
            {"name": "ib_paper", "error": "zero_balance: gate_balance=0.00 USD (no funds available to size against)", "sized_qty": 0.0},
        ])
        sec = _dispatch_section(secs)
        assert sec is not None
        # Count header shows 3
        assert "3" in sec.summary
        lines = sec.body.strip().split("\n")
        assert len(lines) == 3
        # Each line has a recognisable name
        assert any("bybit_1" in ln for ln in lines)
        assert any("bybit_2" in ln and "ok" in ln for ln in lines)
        assert any("ib_paper" in ln for ln in lines)
        # No residual '?' placeholders
        assert not any(ln.startswith("?:") for ln in lines)

    def test_ok_row_includes_qty(self):
        secs = _sections([
            {"name": "bybit_2", "error": None, "sized_qty": 0.001},
        ])
        sec = _dispatch_section(secs)
        assert "qty=0.001" in sec.body

    def test_error_row_does_not_include_qty(self):
        """Rejected rows shouldn't clutter the output with qty=0.0."""
        secs = _sections([
            {"name": "bybit_1", "error": "skipped_not_assigned: x", "sized_qty": 0.0},
        ])
        sec = _dispatch_section(secs)
        assert "qty" not in sec.body

    def test_backward_compat_account_key_still_works(self):
        """If some caller still emits 'account' key, it should not break."""
        secs = _sections([
            {"account": "legacy_acct", "error": None, "sized_qty": 0.001},
        ])
        sec = _dispatch_section(secs)
        assert sec is not None
        assert "legacy_acct" in sec.body
