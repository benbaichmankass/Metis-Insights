"""M28 P1 — tests for the non-FRED value-source adapter + backfill wiring.

No network: candle download + Shiller urlopen are injected. Verifies the two
previously-honest-null metrics (equity risk premium, gold/silver ratio) resolve
once their sources are supplied, point-in-time.
"""

from __future__ import annotations

import importlib.util
import os

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_DIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


macro_sources = _load("macro_sources")
backfill = _load("valuation_snapshot_backfill")


# ---- fakes ----------------------------------------------------------------

class _Series:
    def __init__(self, pairs):
        self._p = pairs

    def items(self):
        return iter(self._p)


class _OneColDF:
    def __init__(self, pairs):
        self.columns = ["X"]
        self._s = _Series(pairs)

    class _IL:
        def __init__(self, s):
            self._s = s

        def __getitem__(self, k):
            return self._s

    @property
    def iloc(self):
        return _OneColDF._IL(self._s)


class _DLDF:
    def __init__(self, pairs):
        self._pairs = pairs
        self.empty = not pairs

    def __getitem__(self, k):
        assert k == "Close"
        return _OneColDF(self._pairs)


class _Resp:
    def __init__(self, t):
        self._t = t

    def read(self):
        return self._t.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_SHILLER = (
    "Date,SP500,Dividend,Earnings,CPI\n"
    "2019-10-01,3000,1,150,1\n"        # ey = 0.05 → lagged to 2020-01
    "2019-11-01,3000,1,180,1\n"        # ey = 0.06 → lagged to 2020-02
    "2026-06-01,7400,0,0,1\n"          # not-yet-reported → skipped
)


# ---- parse ----------------------------------------------------------------

def test_shiller_parse_skips_zero_and_lags():
    ey = macro_sources.parse_shiller_earnings_yield(_SHILLER, lag_months=3)
    assert ey == [("2020-01-01", 0.05), ("2020-02-01", 0.06)]   # 3-mo publication lag


def test_price_source_symbol():
    assert macro_sources._price_source_symbol("price_gld") == "GLD"
    assert macro_sources._price_source_symbol("sp500_earnings_yield") is None


# ---- fetch_source_series_dated (injected) ---------------------------------

_CFG = {
    "instruments": {
        "GLD": {"asset_class": "commodity", "metrics": [
            {"metric": "gold_silver_ratio",
             "inputs": {"gold": {"source": "price_gld"}, "silver": {"source": "price_slv"}},
             "higher_is_cheaper": True},
        ]},
        "SPY": {"asset_class": "equity", "metrics": [
            {"metric": "equity_risk_premium",
             "inputs": {"earnings_yield": {"source": "sp500_earnings_yield"},
                        "real_yield": {"series": "DFII10"}},
             "higher_is_cheaper": True},
        ]},
    },
}


def _candle_dl(sym_to_pairs):
    def dl(s):
        return _DLDF(sym_to_pairs.get(s, []))
    return dl


def test_fetch_source_series_resolves_prices_and_earnings_yield():
    prices = {
        "GLD": [("2020-01-02", 150.0), ("2020-01-03", 151.0)],
        "SLV": [("2020-01-02", 20.0), ("2020-01-03", 20.5)],
    }
    out = macro_sources.fetch_source_series_dated(
        _CFG,
        candle_download=_candle_dl(prices),
        shiller_urlopen=lambda url, timeout=None: _Resp(_SHILLER),
    )
    assert out["price_gld"] == prices["GLD"]
    assert out["price_slv"] == prices["SLV"]
    assert out["sp500_earnings_yield"] == [("2020-01-01", 0.05), ("2020-02-01", 0.06)]


# ---- backfill with sources merged → ERP + gold/silver resolve -------------

def test_backfill_with_sources_resolves_erp_and_gold_silver():
    # DFII10 (FRED) via injected urlopen; prices + earnings yield via injected source fetchers.
    fred_csv = "DATE,VALUE\n" + "".join(f"2020-0{m}-01,1.{m}\n" for m in range(1, 7))

    def fred_urlopen(url, timeout=None):
        return _Resp(fred_csv)

    prices = {
        "GLD": [(f"2020-0{m}-01", 150.0 + m) for m in range(1, 7)],
        "SLV": [(f"2020-0{m}-01", 20.0 + m) for m in range(1, 7)],
    }
    shiller = "Date,SP500,Dividend,Earnings,CPI\n" + "".join(
        f"2019-0{m}-01,3000,1,{120 + 10*m},1\n" for m in range(1, 7)
    )
    summary = backfill.run_backfill(
        out_path="/tmp/_bf_sources_test.jsonl", cadence_days=15,
        urlopen=fred_urlopen,
        source_fetchers={
            "candle_download": _candle_dl(prices),
            "shiller_urlopen": lambda url, timeout=None: _Resp(shiller),
        },
    )
    import json
    rows = [json.loads(x) for x in open("/tmp/_bf_sources_test.jsonl")]
    by = {}
    for r in rows:
        by.setdefault(r["symbol"], set()).add(r["metric"])
    # Both previously-null metrics are now present as real reads on some date.
    gsr = [r for r in rows if r["metric"] == "gold_silver_ratio" and r["value"] is not None]
    erp = [r for r in rows if r["metric"] == "equity_risk_premium" and r["value"] is not None]
    assert gsr, "gold/silver ratio should resolve once metal prices are supplied"
    assert erp, "equity risk premium should resolve once earnings yield is supplied"
    assert summary["rows"] > 0
