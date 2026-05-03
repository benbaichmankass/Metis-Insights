"""Back-compat shim — the canonical home is now
``src/units/ui/data_loaders.py``.

S-032 first relocated this file out of ``src/bot/`` (where it didn't
do any Telegram work) into the UI unit. S-035 then moved the UI unit
itself into ``src/units/ui/`` to satisfy CLAUDE.md § Architecture
rules § 1 ("every unit lives under src/units/").

This shim aliases the legacy module path to the canonical
``src.units.ui.data_loaders`` module so that:

  * ``from src.bot import data_loaders as dl`` resolves to the same
    module object as ``from src.units.ui import data_loaders``.
  * ``monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", …)`` /
    ``monkeypatch.setattr("src.bot.data_loaders.account_last_trade",
    …)`` fixtures keep mutating the single source of truth.
  * Re-imports through either path return the same module — no name
    drift, no two-different-loggers bug.

New code should ``from src.units.ui.data_loaders import …`` directly.
"""
import sys

from src.units.ui import data_loaders as _canonical

sys.modules[__name__] = _canonical
