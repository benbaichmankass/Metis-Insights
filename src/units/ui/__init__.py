"""UI unit (S-035 — architecture-audit-2026-05-02 P2-10).

Per CLAUDE.md § Architecture rules § 1 every unit lives under
``src/units/``. Pre-S-035 the UI unit lived at ``src/ui/`` which
broke the convention. This package is the canonical home;
``src/ui/`` is preserved as a back-compat shim that aliases the
legacy import path to this one.
"""
