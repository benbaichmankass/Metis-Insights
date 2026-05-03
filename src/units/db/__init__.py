"""DB unit (S-035 — architecture-audit-2026-05-02 P2-10).

Per CLAUDE.md § Architecture rules § 1 every unit lives under
``src/units/``. Pre-S-035 the DB unit lived at ``src/data_layer/``
which broke the convention. This package is the canonical home;
``src/data_layer/`` is preserved as a back-compat shim that aliases
the legacy import path to this one.

Owns three logs (Rule 4): trades, order_packages, signals.
"""
