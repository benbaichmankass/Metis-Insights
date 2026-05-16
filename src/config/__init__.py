"""Canonical readers for files under ``config/``.

Read-only consumers (dashboards, runtime status writers, ops scripts,
reconciler helpers) must use the loaders here instead of calling
``yaml.safe_load`` on the config files directly. The CI guard
``scripts/check_canonical_config_loaders.py`` enforces this for
``config/accounts.yaml`` — see that script for the exact allowlist.

The production object-builders (``src.units.accounts.load_accounts``,
``src.strategy_registry``) build typed objects on top of these raw
readers and are unaffected.
"""
