"""Asset-class resolution for the ETF reporting buckets (S-EXPANSION wrap-up).

Regression for the 2026-06-20 asset_class fix: the explicit ``asset_class:``
overrides in ``config/instruments.yaml`` win over the heuristic, so the ETF
cells (SLV/USO commodity, TLT/IEF bond) must report their corrected buckets —
they were wrongly pinned ``equity`` while the gld/slv/uso heuristic already
classified them commodity. Adds the new ``bond`` class token.

Pure resolver test — no FastAPI/auth import. Calls ``reset_cache()`` first so
the lru-cached {symbol: class} table reflects the on-disk YAML.
"""

from __future__ import annotations

from src.web.api._asset_class import asset_class_for_symbol, reset_cache


def test_commodity_etfs_report_commodity():
    reset_cache()
    assert asset_class_for_symbol("GLD") == "commodity"
    assert asset_class_for_symbol("SLV") == "commodity"
    assert asset_class_for_symbol("USO") == "commodity"


def test_treasury_bond_etfs_report_bond():
    reset_cache()
    assert asset_class_for_symbol("TLT") == "bond"
    assert asset_class_for_symbol("IEF") == "bond"


def test_equity_etfs_stay_equity():
    reset_cache()
    assert asset_class_for_symbol("SPY") == "equity"
    assert asset_class_for_symbol("QQQ") == "equity"
    assert asset_class_for_symbol("IWM") == "equity"


def test_crypto_stays_crypto():
    reset_cache()
    assert asset_class_for_symbol("BTCUSDT") == "crypto"


def test_bond_root_heuristic_for_untagged_symbol():
    # A bond ETF NOT in instruments.yaml still buckets via _BOND_ROOTS.
    reset_cache()
    assert asset_class_for_symbol("AGG") == "bond"
    assert asset_class_for_symbol("LQD") == "bond"
