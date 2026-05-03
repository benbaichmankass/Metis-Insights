"""Back-compat shim — the canonical home is now ``src/units/ui/``.

S-035 (architecture-audit-2026-05-02 P2-10) moved the UI unit to
``src/units/ui/`` to satisfy CLAUDE.md § Architecture rules § 1.
Existing call sites + test fixtures keep working through the
per-module shims under this package; new code should import from
``src.units.ui.*`` directly.
"""
