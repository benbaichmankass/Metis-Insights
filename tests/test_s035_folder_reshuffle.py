"""S-035 regression tests
(architecture-audit-2026-05-02 § P2-10).

Per CLAUDE.md § Architecture rules § 1 every unit lives under
``src/units/``. Pre-S-035 the DB unit lived at ``src/data_layer/``
and the UI unit lived at ``src/ui/`` — both broke the convention.
S-035 moves them to ``src/units/db/`` and ``src/units/ui/``
respectively, and leaves back-compat shims at the legacy paths so
existing imports + ``sys.modules`` test fixtures keep working.

Tests pin:
  1. Canonical locations resolve (`src.units.db.database`,
     `src.units.ui.processor`, `src.units.ui.data_loaders`).
  2. Every legacy path is a back-compat shim that resolves to the
     SAME module object as the canonical one (so monkeypatch
     fixtures hit a single source of truth).
  3. The bot data_loaders shim chain (`src.bot.data_loaders` →
     `src.units.ui.data_loaders`) is preserved.
"""
from __future__ import annotations


def test_canonical_db_module_imports():
    from src.units.db import database as canonical
    assert hasattr(canonical, "Database")


def test_canonical_ui_modules_import():
    from src.units.ui import processor as canonical_processor
    from src.units.ui import data_loaders as canonical_dl
    assert hasattr(canonical_processor, "get_account_balances")
    assert hasattr(canonical_processor, "get_price")
    assert hasattr(canonical_processor, "close_open_positions")
    assert hasattr(canonical_dl, "list_accounts")


def test_legacy_data_layer_path_resolves_to_canonical_module():
    """``from src.data_layer.database import Database`` must return
    the same object as ``src.units.db.database.Database`` so tests
    that monkeypatch either path mutate the same namespace."""
    from src.data_layer import database as legacy
    from src.units.db import database as canonical
    assert legacy is canonical


def test_legacy_ui_path_resolves_to_canonical_module():
    from src.ui import processor as legacy_processor
    from src.ui import data_loaders as legacy_dl
    from src.units.ui import processor as canonical_processor
    from src.units.ui import data_loaders as canonical_dl
    assert legacy_processor is canonical_processor
    assert legacy_dl is canonical_dl


def test_bot_data_loaders_shim_chain_preserved():
    """S-032 added the bot→ui shim; S-035 moved ui under units. The
    chain ``src.bot.data_loaders`` → ``src.units.ui.data_loaders``
    must still produce the same module object."""
    from src.bot import data_loaders as bot_dl
    from src.units.ui import data_loaders as canonical_dl
    assert bot_dl is canonical_dl


def test_module_attribute_writes_propagate_through_shim():
    """The shim aliases (sys.modules[__name__] = canonical) make
    write-through work. ``setattr`` on the legacy path must mutate
    the canonical module."""
    from src.data_layer import database as legacy
    from src.units.db import database as canonical
    sentinel = object()
    legacy._S035_SENTINEL = sentinel
    try:
        assert canonical._S035_SENTINEL is sentinel
    finally:
        del legacy._S035_SENTINEL
