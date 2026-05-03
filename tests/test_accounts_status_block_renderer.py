"""Velotrade phase-2b — /accounts_status block renderer.

Tests for ``src.units.ui.processor.format_account_status_block`` —
the helper that produces the HTML block per account for the
``/accounts_status`` Telegram command. Centralising it in the UI
processor (CLAUDE.md rule 5) means the bot stays a thin shell and
the renderer is testable without the python-telegram-bot import.

Coverage:
  - Configured account renders the standard 5-line block, no
    not-configured line, no prop-state block.
  - Not-configured account adds the "⚙️ Not configured: …" line
    with the env-var reason.
  - Prop account adds the phase + mission-progress block.
  - Prop + mission complete shows the 🏁 + ✅ icons.
  - Plain regular account skips the prop block even if some prop
    keys leaked into its dict.
  - HTML special chars (<, >, &, _) in dynamic fields are escaped
    or pass through harmlessly under HTML parse mode.
"""
from __future__ import annotations

from src.units.ui.processor import format_account_status_block


def _regular_status(**overrides):
    s = {
        "name": "bybit_1",
        "exchange": "bybit",
        "account_type": "regular",
        "halted": False,
        "daily_pnl": 1.23,
        "max_daily_loss_usd": 100.0,
        "max_pos_size_usd": 500.0,
        "open_positions": 0,
        "live_balance_usdt": 1234.56,
        "live_balance_error": None,
        "strategies": ["vwap"],
        "api_key_fingerprint": "ABCD",
        "configured": True,
        "configured_reason": None,
    }
    s.update(overrides)
    return s


def _prop_status(**overrides):
    s = _regular_status(
        name="prop_velotrade_1",
        exchange="velotrade",
        account_type="prop",
        strategies=[],
        api_key_fingerprint=None,
        configured=False,
        configured_reason="VELOTRADE_API_KEY_1 and/or VELOTRADE_API_SECRET_1 not set in env",
        live_balance_usdt=None,
        live_balance_error="missing API credentials",
        # PropRiskManager.report() fields:
        account_state="evaluation",
        target_profit_pct=0.05,
        min_active_days=4,
        cumulative_pnl_pct=0.0125,
        active_days=2,
        profit_target_met=False,
        active_days_met=False,
        mission_complete=False,
    )
    s.update(overrides)
    return s


# ---------------------------------------------------------------------------
# Regular account
# ---------------------------------------------------------------------------


class TestRegularAccountRender:
    def test_baseline_lines(self):
        block = format_account_status_block(_regular_status())
        assert "<b>bybit_1</b>" in block
        assert "<code>bybit</code>" in block
        assert "🎯 Strategy: vwap" in block
        assert "🔑 Key: …ABCD" in block
        assert "🔌 API: ✅ Balance $1,234.56 USDT" in block
        assert "💵 Daily PnL: $+1.23 / limit $100" in block
        assert "📦 Max pos: $500" in block
        # Configured account: no not-configured line.
        assert "Not configured" not in block
        # Regular account: no prop block.
        assert "Phase:" not in block
        assert "Mission PnL" not in block

    def test_no_strategies_renders_placeholder(self):
        block = format_account_status_block(_regular_status(strategies=[]))
        assert "🎯 Strategy: <i>(none assigned)</i>" in block

    def test_halted_icon(self):
        block = format_account_status_block(_regular_status(halted=True))
        assert block.startswith("🔴 ")

    def test_no_balance_falls_back_to_warning(self):
        block = format_account_status_block(_regular_status(
            live_balance_usdt=None, live_balance_error=None,
        ))
        assert "🔌 API: ⚠️ no balance returned" in block


# ---------------------------------------------------------------------------
# Not-configured account
# ---------------------------------------------------------------------------


class TestNotConfiguredRender:
    def test_not_configured_line_present(self):
        block = format_account_status_block(_regular_status(
            configured=False,
            configured_reason="BYBIT_API_KEY_1 and/or BYBIT_API_SECRET_1 not set in env",
        ))
        assert "⚙️ Not configured:" in block
        # Env var names render literally under HTML parse mode (no
        # underscore-escaping needed — the rule that bit BUG-009 / 030 /
        # 031 only applied to the legacy Markdown parser).
        assert "BYBIT_API_KEY_1" in block
        assert "BYBIT_API_SECRET_1" in block

    def test_not_configured_default_reason(self):
        block = format_account_status_block(_regular_status(
            configured=False, configured_reason=None,
        ))
        assert "⚙️ Not configured: credentials not set" in block

    def test_configured_true_omits_line(self):
        block = format_account_status_block(_regular_status(configured=True))
        assert "Not configured" not in block

    def test_configured_missing_key_treated_as_configured(self):
        # Backwards-compat: a status dict without a ``configured``
        # key (legacy callers / mocks) doesn't trigger the warning.
        s = _regular_status()
        s.pop("configured", None)
        block = format_account_status_block(s)
        assert "Not configured" not in block


# ---------------------------------------------------------------------------
# Prop account block
# ---------------------------------------------------------------------------


class TestPropAccountRender:
    def test_evaluation_phase_in_progress(self):
        block = format_account_status_block(_prop_status())
        # Header line carries the prop account_type.
        assert "(<code>velotrade</code> / prop)" in block
        # Phase + mission flag.
        assert "🏷️ Phase: <code>evaluation</code>" in block
        assert "mission_complete=⏳" in block
        # Mission PnL formatted as percent: cumulative 0.0125 → +1.25%.
        assert "+1.25%" in block
        # Target 0.05 → 5.00%.
        assert "5.00%" in block
        # Active days x/y.
        assert "Active days: 2/4" in block

    def test_mission_complete_flag_and_icon(self):
        block = format_account_status_block(_prop_status(
            cumulative_pnl_pct=0.06,
            active_days=5,
            mission_complete=True,
        ))
        assert "🏁" in block
        assert "mission_complete=✅" in block
        assert "+6.00%" in block

    def test_funded_state_shown(self):
        block = format_account_status_block(_prop_status(
            account_state="funded",
            mission_complete=True,
        ))
        assert "<code>funded</code>" in block

    def test_not_configured_and_prop_block_coexist(self):
        # The real prop_velotrade_1 ships not-configured by default.
        block = format_account_status_block(_prop_status())
        assert "⚙️ Not configured:" in block
        assert "🏷️ Phase:" in block
        # Order matters: not-configured before the prop block before
        # the API line, so the operator sees the inertness reason
        # before the mission progress.
        idx_cfg = block.index("Not configured:")
        idx_phase = block.index("🏷️ Phase:")
        idx_api = block.index("🔌 API:")
        assert idx_cfg < idx_phase < idx_api

    def test_negative_cumulative_pnl(self):
        block = format_account_status_block(_prop_status(
            cumulative_pnl_pct=-0.0234,
        ))
        # Sign-aware: the cumulative line shows the leading minus.
        assert "-2.34%" in block

    def test_prop_keys_on_regular_account_ignored(self):
        # Defensive: even if a regular account dict somehow carried
        # PropRiskManager fields (it shouldn't), the renderer skips
        # the prop block because account_type != "prop".
        s = _regular_status(
            account_state="evaluation",
            cumulative_pnl_pct=0.10,
            mission_complete=False,
        )
        block = format_account_status_block(s)
        assert "Phase:" not in block
        assert "Mission PnL" not in block

    def test_account_type_prop_without_account_state_skips_block(self):
        # Defensive: a prop account whose status() somehow lost the
        # PropRiskManager fields (e.g. reload race) doesn't render
        # garbage — the prop block requires "account_state" to fire.
        s = _prop_status()
        s.pop("account_state", None)
        block = format_account_status_block(s)
        assert "Phase:" not in block


# ---------------------------------------------------------------------------
# HTML escaping
# ---------------------------------------------------------------------------


class TestHTMLEscaping:
    def test_special_chars_in_name_escaped(self):
        block = format_account_status_block(_regular_status(name="weird<name>"))
        assert "&lt;name&gt;" in block
        # Raw < / > don't appear in dynamic content (only in our own tags).
        assert "weird<name>" not in block

    def test_ampersand_in_error_escaped(self):
        block = format_account_status_block(_regular_status(
            live_balance_usdt=None,
            live_balance_error="auth failed: token & key mismatch",
        ))
        assert "token &amp; key" in block

    def test_underscores_pass_through_under_html(self):
        block = format_account_status_block(_regular_status(
            configured=False,
            configured_reason="env var FOO_BAR_BAZ not set",
        ))
        # Underscores need no escaping under HTML parse mode.
        assert "FOO_BAR_BAZ" in block
        # And no backslash-escapes leaked in (the previous Markdown
        # band-aid produced visible "\\_").
        assert "\\_" not in block
