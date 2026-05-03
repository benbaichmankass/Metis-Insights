"""Back-compat shim — the canonical home is now
``src/units/ui/processor.py`` (S-035, architecture-audit-2026-05-02 P2-10).
"""
import sys

from src.units.ui import processor as _canonical

sys.modules[__name__] = _canonical
