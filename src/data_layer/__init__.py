"""Back-compat shim — the canonical home is now ``src/units/db/``.

S-035 (architecture-audit-2026-05-02 P2-10) moved the DB unit to
``src/units/db/`` to satisfy CLAUDE.md § Architecture rules § 1
("every unit lives under src/units/"). This package preserves the
legacy ``src.data_layer`` import path so existing call sites + test
fixtures (``sys.modules["src.data_layer.database"]``,
``monkeypatch.setattr("src.data_layer.database.Database", …)``) keep
working without any churn.

New code should import from ``src.units.db.*`` directly.
"""
