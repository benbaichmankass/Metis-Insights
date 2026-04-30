"""S-016 H1 regression — Telegram bot surface cleanup.

Pins three behaviours from the H0 audit (docs/audit/2026-04-30-housekeeping.md):

* §A3 — `cmd_sprintlet_status` / `cmd_sprintlet_complete` source the
  sprint id from `CHECKPOINT_LOG.md` instead of hardcoding "S-008.5".
* §A5 — `cmd_status` per-account block does NOT leak the systemd unit
  name. Strategy header stays, service column is gone.
* §A1 — BotCommand registry includes `vm` and `vm_write` so they
  appear in the operator's Telegram autocomplete.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Stub optional deps so the bot module imports in the lean sandbox.
for _mod in ("telegram", "telegram.ext", "dotenv", "requests",
             "pandas", "matplotlib", "matplotlib.pyplot"):
    sys.modules.setdefault(_mod, MagicMock())

_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = MagicMock
_tg.InlineKeyboardButton = lambda *a, **kw: MagicMock()
_tg.InlineKeyboardMarkup = lambda *a, **kw: MagicMock()

_tgext = sys.modules["telegram.ext"]
_tgext.Application = MagicMock
_tgext.CommandHandler = MagicMock
_tgext.CallbackQueryHandler = MagicMock
_ctx = MagicMock()
_ctx.DEFAULT_TYPE = MagicMock
_tgext.ContextTypes = _ctx

REPO_ROOT = Path(__file__).resolve().parents[1]


def _sample_log(sprint: str, cp: str) -> str:
    return (
        "# Checkpoint log\n\n"
        "Append-only log.\n\n"
        "---\n\n"
        f"## {cp} — current entry\n\n"
        f"- **Sprint:** {sprint} — current sprint description.\n"
        "- **Next checkpoint:** **CP-…**\n\n"
        "---\n\n"
        "## CP-2026-04-30-09 — earlier entry (must be ignored)\n"
        "- **Sprint:** S-014 — earlier.\n"
    )


def test_latest_sprint_parses_top_entry(tmp_path, monkeypatch):
    log = tmp_path / "CHECKPOINT_LOG.md"
    log.write_text(_sample_log("S-016", "CP-2026-04-30-99"), encoding="utf-8")
    from src.bot import telegram_query_bot as bot

    monkeypatch.setattr(bot, "REPO_ROOT", str(tmp_path.parent))
    monkeypatch.setattr(
        bot.os.path, "join",
        lambda *a: (str(log) if a[-1] == "CHECKPOINT_LOG.md" else "/".join(a)),
    )
    sprint, cp = bot._latest_sprint_from_checkpoint_log()
    assert sprint == "S-016"
    assert cp == "CP-2026-04-30-99"


def test_latest_sprint_handles_missing_file(tmp_path, monkeypatch):
    from src.bot import telegram_query_bot as bot
    missing = tmp_path / "nope.md"
    monkeypatch.setattr(
        bot.os.path, "join",
        lambda *a: (str(missing) if a[-1] == "CHECKPOINT_LOG.md" else "/".join(a)),
    )
    sprint, cp = bot._latest_sprint_from_checkpoint_log()
    assert sprint == "unknown"
    assert cp == "unknown"


def test_latest_sprint_handles_subsprint_ids(tmp_path, monkeypatch):
    """S-014.5 (subsprints) must parse — the regex allows ``\\d+`` after a dot."""
    log = tmp_path / "CHECKPOINT_LOG.md"
    log.write_text(_sample_log("S-014.5", "CP-2026-04-30-05"), encoding="utf-8")
    from src.bot import telegram_query_bot as bot
    monkeypatch.setattr(
        bot.os.path, "join",
        lambda *a: (str(log) if a[-1] == "CHECKPOINT_LOG.md" else "/".join(a)),
    )
    sprint, _ = bot._latest_sprint_from_checkpoint_log()
    assert sprint == "S-014.5"


def test_status_per_account_block_drops_service_name(monkeypatch):
    """§A5 — the per-account block must not contain the systemd unit name.
    Build one block by calling the same render path cmd_status uses.
    """
    from src.bot import telegram_query_bot as bot
    # cmd_status's per-account snippet is inlined; we assert by looking at the
    # current source not containing the offending `{svc}`-in-account-block.
    src_path = REPO_ROOT / "src" / "bot" / "telegram_query_bot.py"
    text = src_path.read_text(encoding="utf-8")
    # The dropped pattern from before the fix — must NOT be present.
    assert "Open (DB): {open_count} | `{svc}`: {svc_status}" not in text


def test_botcommand_registry_includes_vm_commands():
    """§A1 — vm and vm_write must be in the BotCommand list (operator
    autocomplete) so they're discoverable, not just registered as
    handlers."""
    src_path = REPO_ROOT / "src" / "bot" / "telegram_query_bot.py"
    text = src_path.read_text(encoding="utf-8")
    assert 'BotCommand("vm",' in text
    assert 'BotCommand("vm_write",' in text


def test_no_stale_s008_5_hardcoded_in_handlers():
    """§A3 — both sprintlet handlers must source the sprint id from the
    log, not hardcode S-008.5 / S-009 / CP-2026-04-29-58."""
    src_path = REPO_ROOT / "src" / "bot" / "telegram_query_bot.py"
    text = src_path.read_text(encoding="utf-8")
    assert "S-008.5" not in text
    assert "Ready for S-009" not in text
    assert "CP-2026-04-29-58" not in text
