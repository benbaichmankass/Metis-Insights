"""Back-compat shim — the canonical home is now
``src/units/db/data_loader.py`` (S-035, architecture-audit-2026-05-02 P2-10).
"""
import sys

from src.units.db import data_loader as _canonical

sys.modules[__name__] = _canonical
