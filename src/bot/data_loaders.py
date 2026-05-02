"""Back-compat shim — the canonical home is now ``src/ui/data_loaders.py``.

S-032 (architecture-audit-2026-05-02 P1-7): the file used to live under
``src/bot/`` but did no Telegram work — it loaded data from the DB,
YAML, exchange, signal log. Per CLAUDE.md § Architecture rules § 1 and
§ 5 it belongs to the UI unit.

This shim aliases the legacy module path to the canonical UI module so
that:

  * ``from src.bot import data_loaders as dl`` resolves to the same
    module object as ``from src.ui import data_loaders``.
  * ``monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", …)`` /
    ``monkeypatch.setattr("src.bot.data_loaders.account_last_trade", …)``
    fixtures that pre-date this PR keep mutating the same namespace.
  * Re-imports through either path return the same module — no name
    drift, no two-different-loggers bug.

New code should ``from src.ui.data_loaders import …`` directly.
"""
import sys

from src.ui import data_loaders as _canonical

# Replace this module entry in sys.modules with the canonical one so
# every attribute access + monkeypatch lands on the single source of
# truth. After this line, future ``import src.bot.data_loaders`` or
# ``from src.bot import data_loaders as dl`` calls receive the
# ``src.ui.data_loaders`` module object.
sys.modules[__name__] = _canonical
