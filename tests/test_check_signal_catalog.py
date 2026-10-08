"""signal-catalog-guard: passes on the committed catalog, fails on a synthetic
uncatalogued input of each family (SIGNAL-CATALOG)."""
import copy
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))
import check_signal_catalog as g  # noqa: E402


def _cat():
    return yaml.safe_load(g.CATALOG.read_text())


def test_committed_catalog_passes_with_nonzero_denominators():
    fails, dens = g.check(_cat())
    assert fails == [], fails
    assert all(not d.endswith(": 0") for d in dens), dens


def test_uncatalogued_ml_feature_fails():
    cat = _cat()
    cat["inputs"] = [e for e in cat["inputs"] if e.get("name") != "vpin"]
    fails, _ = g.check(cat)
    assert "F1 uncatalogued ml_feature: vpin" in fails


def test_new_candle_column_or_feed_import_fails():
    cat = copy.deepcopy(_cat())
    cat["strategy_modules"]["vwap"]["candle_columns"] = []
    cat["strategy_modules"]["vwap"]["runtime_imports"] = []
    fails, _ = g.check(cat)
    assert any(f.startswith("F2 vwap reads uncatalogued candle_columns") for f in fails)
    assert any(f.startswith("F2 vwap reads uncatalogued runtime_imports") for f in fails)


def test_synthetic_strategy_module_without_block_fails(tmp_path):
    root = tmp_path
    (root / "src/units/strategies").mkdir(parents=True)
    (root / "src/units/strategies/new_leg.py").write_text(
        'import pandas as pd\nfrom src.runtime.market_data import fetch_candles\n'
        'def f(df):\n    return df["close"] - df["volume"]\n')
    got = g.strategy_modules(root)
    assert got["new_leg"]["candle_columns"] == ["close", "volume"]
    assert got["new_leg"]["runtime_imports"] == ["src.runtime.market_data"]


def test_uncatalogued_config_id_fails():
    cat = _cat()
    cat["inputs"] = [e for e in cat["inputs"] if e["id"] != "cross_asset.ETHUSDT"]
    fails, _ = g.check(cat)
    assert "F3 uncatalogued config id: cross_asset.ETHUSDT" in fails


def test_stale_module_block_fails():
    cat = _cat()
    cat["strategy_modules"]["ghost_leg"] = {"candle_columns": [], "runtime_imports": []}
    fails, _ = g.check(cat)
    assert "F2 catalog block for missing module: ghost_leg" in fails


def test_empty_catalog_self_test():
    assert g.self_test() == 0
