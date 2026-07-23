"""M28 P1 — tests for the seed-universe candle fetcher (no network / no pandas)."""

from __future__ import annotations

import importlib.util
import os

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "macro", "fetch_macro_candles.py",
)
_spec = importlib.util.spec_from_file_location("fetch_macro_candles", _PATH)
fmc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fmc)


# ---- fakes mimicking the pandas frames yfinance returns -------------------

class _Series:
    """Minimal Series: .items() yields (date, scalar)."""
    def __init__(self, pairs):
        self._p = pairs

    def items(self):
        return iter(self._p)


class _OneColDF:
    """A 1-column DataFrame — what `df['Close']` is for a SINGLE-symbol yfinance
    download (MultiIndex columns). Has `.columns` + `.iloc[:, 0]` → the Series.
    This is the shape that broke the first run (1 garbage row/symbol)."""
    def __init__(self, pairs):
        self.columns = ["GLD"]
        self._series = _Series(pairs)

    class _ILoc:
        def __init__(self, s):
            self._s = s

        def __getitem__(self, key):
            return self._s          # [:, 0] → the only column's Series

    @property
    def iloc(self):
        return _OneColDF._ILoc(self._series)


class _DownloadDF:
    """A yfinance download frame: `df['Close']` → a 1-col DataFrame (MultiIndex)."""
    def __init__(self, pairs):
        self._pairs = pairs
        self.empty = not pairs

    def __getitem__(self, k):
        assert k == "Close"
        return _OneColDF(self._pairs)


# ---- pure parsers ---------------------------------------------------------

def test_yf_close_pairs_squeezes_multiindex_column():
    # The regression: df["Close"] is a 1-col DataFrame → must squeeze to the Series
    # and yield real (date, value) rows, NOT one row keyed by the column name.
    df = _DownloadDF([("2020-01-02", 100.0), ("2020-01-03", 101.5)])
    assert fmc.yf_close_pairs(df) == [("2020-01-02", 100.0), ("2020-01-03", 101.5)]


def test_stooq_close_pairs_parses_ohlcv_csv():
    body = "Date,Open,High,Low,Close,Volume\n2020-01-02,1,2,0,100.0,10\n2020-01-03,1,2,0,101.5,11\n"
    assert fmc.stooq_close_pairs(body) == [("2020-01-02", 100.0), ("2020-01-03", 101.5)]


def test_seed_symbols_are_instrument_keys():
    cfg = {"instruments": {"TLT": {}, "GLD": {}}, "context": {"term_structure": {}}}
    assert fmc.seed_symbols(cfg) == ["GLD", "TLT"]          # sorted, context excluded


# ---- fetch_candles: yfinance path + Stooq fallback ------------------------

def _rows(csv_path):
    return csv_path.read_text().strip().splitlines()


def test_fetch_writes_real_dates_from_yfinance(tmp_path):
    def dl(s):
        return _DownloadDF([("2020-01-02", 100.0), ("2020-01-03", 101.5)])

    res = fmc.fetch_candles(["GLD"], tmp_path, download=dl, min_rows=1)
    assert res == {"GLD": 2}
    rows = _rows(tmp_path / "GLD.csv")
    assert rows[0] == "date,close"
    assert rows[1] == "2020-01-02,100.0"          # a real date, not "GLD"


def test_fetch_falls_back_to_stooq_when_yfinance_short(tmp_path):
    def dl_short(s):
        return _DownloadDF([("2020-01-02", 100.0)])   # only 1 row → below min_rows

    class _Resp:
        def __init__(self, t):
            self._t = t

        def read(self):
            return self._t.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def stooq(url, timeout=None):
        body = "Date,Open,High,Low,Close,Volume\n" + "".join(
            f"2020-01-{d:02d},1,2,0,{100+d}.0,9\n" for d in range(1, 6)
        )
        return _Resp(body)

    res = fmc.fetch_candles(["TLT"], tmp_path, download=dl_short, stooq_urlopen=stooq, min_rows=3)
    assert res == {"TLT": 5}                          # Stooq's 5 rows won over yfinance's 1
    assert _rows(tmp_path / "TLT.csv")[1].startswith("2020-01-01,")


def test_fetch_empty_both_sources_is_zero_not_fatal(tmp_path):
    def dl_empty(s):
        return _DownloadDF([])

    def stooq_empty(url, timeout=None):
        raise RuntimeError("stooq blocked")

    res = fmc.fetch_candles(["NOPE"], tmp_path, download=dl_empty, stooq_urlopen=stooq_empty, min_rows=3)
    assert res == {"NOPE": 0}
    assert not (tmp_path / "NOPE.csv").exists()
