"""S-046 T3 — canonical-path stub test for ``src/units/db/data_loader.py``.

The missing-test audit found that no test file imports
``src.units.db.data_loader`` directly via the canonical path. The
module IS exercised through the legacy ``src.data_layer.data_loader``
shim in ``tests/test_data_loader.py``, but per CLAUDE.md
§ Architecture rules every unit should have at least one test that
references its canonical home.

This stub asserts:
  1. The module imports cleanly via ``src.units.db.data_loader``.
  2. The public API (``DataLoader`` + ``load_data``) is reachable
     from the canonical path.
  3. The legacy shim and the canonical path resolve to the SAME
     module object — guarding against future shim drift.

Coverage tests for the loader's behaviour live in
``tests/test_data_loader.py`` (legacy path) — this file is a
presence guard, not a coverage extension.
"""
from __future__ import annotations


def test_canonical_path_imports():
    from src.units.db import data_loader as canonical
    assert hasattr(canonical, "DataLoader")
    assert hasattr(canonical, "load_data")
    assert callable(canonical.DataLoader)
    assert callable(canonical.load_data)


def test_legacy_shim_resolves_to_canonical():
    from src.data_layer import data_loader as legacy
    from src.units.db import data_loader as canonical
    assert legacy is canonical
