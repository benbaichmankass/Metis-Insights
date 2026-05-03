"""Tests for ``processor.render_per_account_collapsable``.

S-telegram-format Phase 4 introduced this generic helper used by
``/balance``, ``/trades``, and ``/log`` to wrap each account's
existing per-account formatter output in a collapsable HTML section.
The contract pinned here:

- Empty input yields the empty-state envelope (no per-account
  sections, just the placeholder).
- One section per account; account_id is in the default summary line.
- ``body_fn`` exceptions don't crash the renderer — the body becomes
  an inline ``⚠️ <type>: <msg>`` so the operator still sees the
  failing account in the layout.
- ``summary_fn`` overrides the default summary line.
- ``extra_top_lines`` ride above the per-account sections (used by
  ``/balance`` for the duplicate-key warning).
"""
from __future__ import annotations

from src.units.ui.processor import render_per_account_collapsable


_ACCOUNTS = [
    {"account_id": "main"},
    {"account_id": "alpha"},
    {"account_id": "beta"},
]


def test_renders_one_section_per_account_with_default_summary():
    body = render_per_account_collapsable(
        _ACCOUNTS,
        body_fn=lambda acc: f"💰 *{acc['account_id']} Balance*\nUSDT: 1234.56",
        header="💰 Account balances",
    )
    # Header + 3 account sections → 3 blockquotes.
    assert "<b>💰 Account balances</b>" in body
    assert body.count("<blockquote expandable>") == 3
    # Default summary line is the body's first non-empty line, with
    # markdown asterisks and backticks stripped.
    assert "main Balance" in body
    assert "alpha Balance" in body
    assert "beta Balance" in body


def test_empty_accounts_renders_empty_message_envelope():
    body = render_per_account_collapsable(
        [],
        body_fn=lambda acc: "",
        header="📊 Open positions",
        empty_message="No accounts configured.",
    )
    assert "<b>📊 Open positions</b>" in body
    assert "No accounts configured." in body
    # No per-account sections.
    assert body.count("<blockquote expandable>") == 1


def test_body_fn_exception_isolated_per_account():
    """A raising body_fn must NOT crash the whole render — the
    failing account becomes one section with the error in its body.
    Otherwise one bad account would hide the others' status."""
    def boom_for_alpha(acc):
        if acc["account_id"] == "alpha":
            raise RuntimeError("alpha exploded")
        return f"USDT: {acc['account_id']}"

    body = render_per_account_collapsable(
        _ACCOUNTS,
        body_fn=boom_for_alpha,
        header="💰 Account balances",
    )
    # All three accounts still rendered.
    assert body.count("<blockquote expandable>") == 3
    assert "RuntimeError: alpha exploded" in body
    # Other accounts unaffected.
    assert "USDT: main" in body
    assert "USDT: beta" in body


def test_summary_fn_override_used_for_summary_line():
    body = render_per_account_collapsable(
        _ACCOUNTS,
        body_fn=lambda acc: f"raw body for {acc['account_id']}",
        summary_fn=lambda acc, body_text: f"📊 {acc['account_id']} — custom",
        header="📊 Open positions",
    )
    assert "<b>📊 main — custom</b>" in body
    assert "<b>📊 alpha — custom</b>" in body
    assert "<b>📊 beta — custom</b>" in body


def test_extra_top_lines_render_above_account_sections():
    body = render_per_account_collapsable(
        _ACCOUNTS,
        body_fn=lambda acc: f"USDT: {acc['account_id']}",
        header="💰 Account balances",
        extra_top_lines=[
            "⚠️ Duplicate key fingerprint detected: …abcd",
        ],
    )
    # Top "Notes" section is the FIRST blockquote (by priority=1).
    notes_pos = body.index("Duplicate key fingerprint")
    main_pos = body.index("USDT: main")
    assert notes_pos < main_pos


def test_summary_fn_exception_falls_back_to_account_id():
    """If summary_fn itself raises, the renderer falls back to the
    account_id so the section still appears."""
    body = render_per_account_collapsable(
        _ACCOUNTS,
        body_fn=lambda acc: "ok",
        summary_fn=lambda acc, body: 1 / 0,
        header="📊 Hdr",
    )
    # All three sections rendered, summaries bear the account_id.
    assert body.count("<blockquote expandable>") == 3
    assert "<b>main</b>" in body
    assert "<b>alpha</b>" in body
    assert "<b>beta</b>" in body
