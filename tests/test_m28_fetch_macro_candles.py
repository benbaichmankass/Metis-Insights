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


def test_resolve_fetchers_degrades_to_stooq_without_yfinance(monkeypatch):
    """A host without yfinance (e.g. the trainer VM) must degrade to the Stooq (urllib)
    fallback, not hard-fail the whole off-VM fetch (the value-sleeve grade blocker)."""
    import sys

    monkeypatch.setenv("ICT_OFFVM_BUILD_HOST", "1")
    monkeypatch.setitem(sys.modules, "yfinance", None)   # `import yfinance` → ImportError
    download, stooq = fmc._resolve_fetchers(None, None, "2005-01-01")
    assert download is None                              # yfinance skipped, not fatal
    assert stooq is not None                             # Stooq (urllib) still wired as fallback


# --- Stooq ticker resolution (BL-20260730-M1-PRICE-JOIN-DEAD) ----------------
# The fetcher used a single `?s={sym}.us` template, appending the US-EQUITY suffix
# to every symbol. For a yfinance futures ticker like `NG=F` that produced
# `s=ng=f.us` — a literal `=` inside the value plus an equity suffix on a futures
# symbol — so the Stooq fallback could never return a bar. With yfinance also
# absent from the econ-event-study runner, that is why the M1 study reported
# `price_bars: 0` on every run of its life while the workflow stayed green.

def test_stooq_symbol_maps_futures_to_dot_f():
    assert fmc.stooq_symbol("NG=F") == "ng.f"
    assert fmc.stooq_symbol("CL=F") == "cl.f"
    assert fmc.stooq_symbol("GC=F") == "gc.f"
    assert fmc.stooq_symbol("HG=F") == "hg.f"
    assert fmc.stooq_symbol("ES=F") == "es.f"


def test_stooq_symbol_keeps_us_suffix_for_equities_and_etfs():
    assert fmc.stooq_symbol("SPY") == "spy.us"
    assert fmc.stooq_symbol("UNG") == "ung.us"
    assert fmc.stooq_symbol("GLD") == "gld.us"


def test_stooq_symbol_unmapped_future_still_gets_dot_f_not_dot_us():
    """An unrecognised `*=F` must not be handed an equity suffix it can never
    resolve under — the exact shape of the original bug."""
    assert fmc.stooq_symbol("ZZ=F") == "zz.f"


def test_stooq_url_for_a_future_has_no_stray_equals_or_us_suffix():
    url = fmc._STOOQ_URL.format(sym=fmc.stooq_symbol("NG=F"))
    assert "s=ng.f" in url
    assert "ng=f" not in url          # the malformed form
    assert ".us" not in url           # equity suffix on a future


def test_futures_fetch_requests_the_dot_f_ticker(tmp_path):
    """End-to-end: the URL actually handed to urlopen carries the futures form."""
    seen = []

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def stooq(url, timeout=None):
        seen.append(url)
        return _Resp(
            "Date,Open,High,Low,Close,Volume\n2020-01-02,1,1,1,2.75,10\n"
        )

    fmc.fetch_candles(["NG=F"], tmp_path, download=None, stooq_urlopen=stooq, min_rows=1)
    assert seen and "s=ng.f" in seen[0], seen
