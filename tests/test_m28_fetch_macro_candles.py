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


class _FakeCloses:
    def __init__(self, pairs):
        self._p = pairs

    def items(self):
        return iter(self._p)


class _FakeDF:
    def __init__(self, pairs):
        self._c = _FakeCloses(pairs)
        self.empty = not pairs

    def __getitem__(self, k):
        assert k == "Close"
        return self._c


def test_seed_symbols_are_instrument_keys():
    cfg = {"instruments": {"TLT": {}, "GLD": {}}, "context": {"term_structure": {}}}
    assert fmc.seed_symbols(cfg) == ["GLD", "TLT"]          # sorted, context excluded


def test_fetch_writes_per_symbol_csv(tmp_path):
    def fake_download(s):
        return _FakeDF([("2020-01-01", 100.0), ("2020-01-02", 101.5)])

    res = fmc.fetch_candles(["TLT"], tmp_path, download=fake_download)
    assert res == {"TLT": 2}
    body = (tmp_path / "TLT.csv").read_text().strip().splitlines()
    assert body[0] == "date,close"
    assert body[1] == "2020-01-01,100.0"
    assert body[2] == "2020-01-02,101.5"


def test_fetch_empty_is_zero_not_fatal(tmp_path):
    def fake_download(s):
        return _FakeDF([])                                  # delisted / no data

    res = fmc.fetch_candles(["NOPE"], tmp_path, download=fake_download)
    assert res == {"NOPE": 0}
    assert not (tmp_path / "NOPE.csv").exists()


def test_fetch_swallows_download_error(tmp_path):
    def boom(s):
        raise RuntimeError("network down")

    res = fmc.fetch_candles(["TLT"], tmp_path, download=boom)
    assert res == {"TLT": 0}                                # best-effort, never raises
