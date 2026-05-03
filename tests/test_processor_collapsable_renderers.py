"""Tests for the collapsable Telegram renderers added under
S-telegram-format follow-up (rolling the unified formatter out to
high-traffic bot commands).

Pin shape, not pixels:

- ``get_health_summary(use_html=True)`` produces a header, a
  Services section, a Data freshness section, all wrapped in
  ``<blockquote expandable>``.
- ``render_accounts_status_collapsable`` produces a header naming
  the configured/healthy/halted counts and one expandable section
  per account, with the existing per-account HTML body inside
  (NOT escaped — operator must see ``<b>``/``<code>`` rendered).
- The legacy Markdown rendering of ``get_health_summary`` is
  preserved (default ``use_html=False``) so existing snapshot tests
  + bot callers that haven't migrated stay green.
"""
from __future__ import annotations

from src.units.ui.processor import (
    get_health_summary,
    render_accounts_status_collapsable,
)


# ---------------------------------------------------------------------------
# /health — HTML collapsable mode
# ---------------------------------------------------------------------------


def _fake_get_service_status(unit: str) -> str:
    if unit.endswith("trader-live"):
        return "active"
    if unit.endswith("telegram-bot"):
        return "failed"
    return "unknown"


def test_health_html_mode_wraps_services_and_data_freshness_in_collapsable_sections(
    tmp_path,
):
    body = get_health_summary(
        get_service_status=_fake_get_service_status,
        repo_root=str(tmp_path),
        use_html=True,
    )
    # Header.
    assert "<b>🩺 ICT Trading Bot — health</b>" in body
    # Two summary sections.
    assert body.count("<blockquote expandable>") == 2
    assert "<b>Services — " in body
    assert "<b>Data freshness — " in body
    # Service summary counts the icons correctly.
    assert "1 up" in body and "1 down" in body
    # Per-service detail lives inside the blockquote, not at the top
    # level (i.e. the operator can collapse it).
    assert "active" in body and "failed" in body


def test_health_legacy_markdown_default_unchanged(tmp_path):
    """Default ``use_html=False`` keeps the legacy Markdown render so
    callers that haven't migrated still get the same string."""
    body = get_health_summary(
        get_service_status=_fake_get_service_status,
        repo_root=str(tmp_path),
    )
    assert "<blockquote" not in body
    assert "🩺 *ICT Trading Bot — health*" in body
    assert "*Services*" in body
    assert "*Data freshness*" in body


# ---------------------------------------------------------------------------
# /accounts_status — collapsable per-account renderer
# ---------------------------------------------------------------------------


def _account_status(name: str, halted: bool = False, balance: float | None = 1000.0):
    return {
        "name": name,
        "exchange": "bybit",
        "account_type": "personal",
        "halted": halted,
        "live_balance_usdt": balance,
        "live_balance_error": None,
        "daily_pnl": 0.0,
        "max_daily_loss_usd": 100.0,
        "max_pos_size_usd": 500.0,
        "open_positions": 0,
        "strategies": ["vwap"],
        "api_key_fingerprint": "abcd",
    }


def test_accounts_status_collapsable_renders_one_section_per_account():
    statuses = [
        _account_status("alice"),
        _account_status("bob", halted=True),
        _account_status("carol", balance=None),
    ]
    body = render_accounts_status_collapsable(statuses)
    # Three sections, three blockquotes.
    assert body.count("<blockquote expandable>") == 3
    assert "alice" in body and "bob" in body and "carol" in body
    # Header counts.
    assert "3 configured" in body
    assert "2 healthy" in body
    assert "1 halted" in body


def test_accounts_status_collapsable_summary_carries_balance_or_error():
    statuses = [_account_status("alice", balance=12345.67)]
    body = render_accounts_status_collapsable(statuses)
    # Summary line carries the balance figure.
    assert "alice — $12,345.67" in body


def test_accounts_status_collapsable_preserves_inner_html():
    """The per-account body comes from ``format_account_status_block``
    which already produces ``<b>``/``<code>`` HTML. The wrapper must
    keep that HTML intact (NOT escape it) — otherwise the operator
    sees raw markup tags instead of bold/monospaced text."""
    statuses = [_account_status("alice")]
    body = render_accounts_status_collapsable(statuses)
    # Per-account renderer emits <b>name</b> and <code>exchange</code>;
    # the wrapper must NOT have escaped those into &lt;b&gt; etc.
    assert "<b>alice</b>" in body
    assert "<code>bybit</code>" in body
    assert "&lt;b&gt;" not in body


def test_accounts_status_collapsable_handles_empty_list():
    body = render_accounts_status_collapsable([])
    assert "📋 Accounts Status" in body
    assert "No accounts configured" in body
