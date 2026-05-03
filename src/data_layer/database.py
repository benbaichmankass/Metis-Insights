"""Back-compat shim — the canonical home is now ``src/units/db/database.py``.

S-035 (architecture-audit-2026-05-02 P2-10). Aliases the legacy
``src.data_layer.database`` module path to the canonical DB unit
module so monkeypatch fixtures + ``from src.data_layer.database
import Database`` call sites resolve to the same module object.
"""
import sys

from src.units.db import database as _canonical

sys.modules[__name__] = _canonical
