"""Tests for per-bar regime scoring (S-MLOPT-S13 / Phase 3.1).

Covers ``src.runtime.regime_bar_scoring.emit_regime_bar_predictions``: it
scores every shadow-stage REGIME head on its own (symbol, timeframe) bar
cadence, deduped to one record per closed bar, observe-only, never raising —
independent of any actionable signal (closes MB-20260529-001).

The predictor list + candle fetcher + dedup cache are injectable so the path
is exercised without a registry or live market data.
"""
from __future__ import annotations

import json

import pytest

from ml.predictors.base import Predictor
from ml.predictors.shadow import ShadowPredictor
from src.runtime.regime_bar_scoring import (
    _BAR_SECONDS,
    _FETCH_GATE_BUFFER_S,
    _last_bar_timestamp,
    _should_fetch_now,
    emit_regime_bar_predictions,
    regime_bar_scoring_enabled,
)


class _RegimeBase(Predictor):
    """A minimal regime predictor: carries a regime_spec, scores on vol_bucket."""

    def __init__(self, regime_spec):
        self.regime_spec = regime_spec
        self.calls: list = []

    def predict(self, row):
        self.calls.append(dict(row))
        # Deterministic, just so the score column is non-constant per bucket.
        return float(len(str(row.get("vol_bucket") or "")))


class _PlainBase(Predictor):
    """A non-regime predictor (no regime_spec) — must be ignored per-bar."""

    def __init__(self):
        self.calls: list = []

    def predict(self, row):
        self.calls.append(dict(row))
        return 1.0


def _spec(symbol="BTCUSDT", timeframe="5m", edges=(0.001, 0.002), window=20):
    return {
        "feature_column": "vol_bucket",
        "vol_feature_column": "rolling_log_return_vol",
        "vol_window_n": window,
        "vol_bucket_edges": list(edges),
        "vol_bucket_labels": ["vol_b0", "vol_b1", "vol_b2"],
        "symbol": symbol,
        "timeframe": timeframe,
    }


def _regime_predictor(model_id, spec, log_path):
    return ShadowPredictor(
        _RegimeBase(spec), model_id=model_id, stage="shadow", log_path=log_path,
    )


def _candles(n=30, start=100.0, step=1.001, last_ts=1_000):
    """A list-of-dict candle frame the duck-typed accessors accept."""
    rows = []
    price = start
    for i in range(n):
        rows.append(
            {
                "timestamp": last_ts - (n - 1 - i),
                "open": price,
                "high": price * 1.001,
                "low": price * 0.999,
                "close": price,
                "volume": 10.0,
            }
        )
        price *= step
    # Duck-typed frame: __getitem__ returns a column list.
    return _FrameLike(rows)


class _FrameLike:
    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, key):
        return [r[key] for r in self._rows]


class TestEnabled:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("REGIME_BAR_SCORING_DISABLED", raising=False)
        assert regime_bar_scoring_enabled() is True

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", "On"])
    def test_disabled_values(self, monkeypatch, val):
        monkeypatch.setenv("REGIME_BAR_SCORING_DISABLED", val)
        assert regime_bar_scoring_enabled() is False

    def test_disabled_short_circuits(self, monkeypatch, tmp_path):
        monkeypatch.setenv("REGIME_BAR_SCORING_DISABLED", "1")
        pred = _regime_predictor(
            "btc-regime-5m", _spec(), tmp_path / "shadow.jsonl"
        )
        n = emit_regime_bar_predictions(
            predictors=[pred],
            fetch_fn=lambda s, t: _candles(),
            seen={},
            wall_cache={},
        )
        assert n == 0
        assert pred.wrapped.calls == []


class TestLastBarTimestamp:
    def test_reads_last(self):
        assert _last_bar_timestamp(_candles(last_ts=42)) == 42

    def test_none_inputs(self):
        assert _last_bar_timestamp(None) is None
        assert _last_bar_timestamp(_FrameLike([])) is None
        assert _last_bar_timestamp(object()) is None


class TestEmit:
    def test_scores_regime_head_and_writes_log(self, tmp_path):
        log = tmp_path / "shadow.jsonl"
        pred = _regime_predictor("btc-regime-5m", _spec(), log)
        seen = {}
        n = emit_regime_bar_predictions(
            predictors=[pred],
            fetch_fn=lambda s, t: _candles(last_ts=1234),
            seen=seen,
            wall_cache={},
        )
        assert n == 1
        assert len(pred.wrapped.calls) == 1
        row = pred.wrapped.calls[0]
        # Reused feature_row_for_predictor → carries the live bucket + marker.
        assert "vol_bucket" in row
        assert row["event_source"] == "per_bar"
        assert row["symbol"] == "BTCUSDT"
        # The shadow audit log got exactly one record.
        lines = log.read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["model_id"] == "btc-regime-5m"
        assert rec["stage"] == "shadow"
        assert rec["feature_row"]["event_source"] == "per_bar"
        # Dedup state updated to this bar.
        assert seen["btc-regime-5m"] == 1234

    def test_dedup_same_bar_scored_once(self, tmp_path):
        pred = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")
        seen = {}
        # Advance past the fetch-gate so the second call attempts a fetch and
        # exercises the per-model_id dedup (not the wall-clock gate above it).
        clock = [1_000_000.0]
        now = lambda: clock[0]  # noqa: E731
        wall: dict = {}
        assert (
            emit_regime_bar_predictions(
                predictors=[pred], fetch_fn=lambda s, t: _candles(last_ts=999),
                seen=seen, wall_cache=wall, now=now,
            )
            == 1
        )
        clock[0] += 300.0
        assert (
            emit_regime_bar_predictions(
                predictors=[pred], fetch_fn=lambda s, t: _candles(last_ts=999),
                seen=seen, wall_cache=wall, now=now,
            )
            == 0
        )
        assert len(pred.wrapped.calls) == 1

    def test_new_bar_scores_again(self, tmp_path):
        pred = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")
        seen = {}
        # Advancing wall-clock between calls so the per-(symbol, timeframe)
        # fetch gate (MB-20260609-001) doesn't suppress the second fetch.
        clock = [1_000_000.0]
        now = lambda: clock[0]  # noqa: E731
        wall: dict = {}
        assert (
            emit_regime_bar_predictions(
                predictors=[pred], fetch_fn=lambda s, t: _candles(last_ts=1),
                seen=seen, wall_cache=wall, now=now,
            )
            == 1
        )
        # Advance past the gate (5m bar = 300 s, buffer = 30 s → 270 s gate).
        clock[0] += 300.0
        assert (
            emit_regime_bar_predictions(
                predictors=[pred], fetch_fn=lambda s, t: _candles(last_ts=2),
                seen=seen, wall_cache=wall, now=now,
            )
            == 1
        )
        assert len(pred.wrapped.calls) == 2

    def test_non_regime_predictor_ignored(self, tmp_path):
        plain = ShadowPredictor(
            _PlainBase(), model_id="setup-quality", stage="shadow",
            log_path=tmp_path / "s.jsonl",
        )
        n = emit_regime_bar_predictions(
            predictors=[plain],
            fetch_fn=lambda s, t: _candles(),
            seen={},
            wall_cache={},
        )
        assert n == 0
        assert plain.wrapped.calls == []

    def test_fetch_failure_skips_head(self, tmp_path):
        pred = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")

        def _boom(symbol, timeframe):
            raise RuntimeError("market data down")

        n = emit_regime_bar_predictions(
            predictors=[pred], fetch_fn=_boom, seen={}, wall_cache={},
        )
        assert n == 0
        assert pred.wrapped.calls == []

    def test_none_candles_skips_head(self, tmp_path):
        pred = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")
        n = emit_regime_bar_predictions(
            predictors=[pred], fetch_fn=lambda s, t: None, seen={},
            wall_cache={},
        )
        assert n == 0

    def test_multiple_heads_scored_per_own_bar(self, tmp_path):
        log = tmp_path / "s.jsonl"
        btc5 = _regime_predictor(
            "btc-regime-5m", _spec(symbol="BTCUSDT", timeframe="5m"), log
        )
        mes15 = _regime_predictor(
            "mes-regime-15m", _spec(symbol="MES", timeframe="15m"), log
        )

        def _fetch(symbol, timeframe):
            # Each market returns its own bar timestamp.
            return _candles(last_ts=hash((symbol, timeframe)) & 0xFFFF)

        n = emit_regime_bar_predictions(
            predictors=[btc5, mes15], fetch_fn=_fetch, seen={}, wall_cache={},
        )
        assert n == 2
        assert len(btc5.wrapped.calls) == 1
        assert len(mes15.wrapped.calls) == 1

    def test_one_head_failure_does_not_block_others(self, tmp_path):
        good = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")
        bad = _regime_predictor(
            "mes-regime-15m", _spec(symbol="MES", timeframe="15m"),
            tmp_path / "s.jsonl",
        )

        def _fetch(symbol, timeframe):
            if symbol == "MES":
                raise RuntimeError("ibkr down")
            return _candles(last_ts=7)

        n = emit_regime_bar_predictions(
            predictors=[bad, good], fetch_fn=_fetch, seen={}, wall_cache={},
        )
        # The MES fetch failed but BTC still scored.
        assert n == 1
        assert len(good.wrapped.calls) == 1

    def test_never_raises_on_internal_error(self, tmp_path):
        # A predictor whose model_id access path is fine but whose spec yields
        # an uncomputable vol (single close) → row is None → skipped, n=0.
        pred = _regime_predictor("btc-regime-5m", _spec(window=20), tmp_path / "s.jsonl")
        n = emit_regime_bar_predictions(
            predictors=[pred],
            fetch_fn=lambda s, t: _FrameLike([{"timestamp": 1, "close": 100.0,
                                               "open": 100.0, "high": 100.0,
                                               "low": 100.0, "volume": 1.0}]),
            seen={},
            wall_cache={},
        )
        assert n == 0


class TestFetchGate:
    """The wall-clock fetch gate is the MB-20260609-001 fix: per
    (symbol, timeframe), skip the network fetch between bar closes so a 60 s
    tick on a 1h-cadence head does not pay the fetch cost 60× per hour.
    """

    def test_should_fetch_first_call_true(self):
        assert _should_fetch_now("BTCUSDT", "5m", {}, now=1000.0) is True

    def test_should_fetch_unknown_tf_always_true(self):
        # Unknown timeframe (no _BAR_SECONDS entry) → permissive fallback so a
        # newly-added cadence cannot strand a head silently.
        wall = {("BTCUSDT", "7m"): 999.0}
        assert _should_fetch_now("BTCUSDT", "7m", wall, now=999.5) is True

    def test_should_fetch_within_gate_false(self):
        # 5m bar = 300 s, buffer = 30 s → gate = 270 s. 200 s elapsed → skip.
        wall = {("BTCUSDT", "5m"): 1000.0}
        assert _should_fetch_now("BTCUSDT", "5m", wall, now=1200.0) is False

    def test_should_fetch_past_gate_true(self):
        # 270 s elapsed at the threshold → fetch.
        wall = {("BTCUSDT", "5m"): 1000.0}
        gate = float(_BAR_SECONDS["5m"]) - _FETCH_GATE_BUFFER_S
        assert _should_fetch_now("BTCUSDT", "5m", wall, now=1000.0 + gate) is True

    def test_1h_gate_is_long(self):
        # 1h bar = 3600 s, buffer = 30 s → gate = 3570 s. 60 s after the last
        # fetch (i.e. the very next tick) is firmly inside the gate → skip.
        wall = {("BTCUSDT", "1h"): 1000.0}
        assert _should_fetch_now("BTCUSDT", "1h", wall, now=1060.0) is False
        # And re-allowed once a full bar is up.
        assert _should_fetch_now("BTCUSDT", "1h", wall, now=1000.0 + 3570.0) is True

    def test_emit_within_gate_does_not_call_fetch(self, tmp_path):
        # Two consecutive emit() calls within the 5m gate: only the FIRST
        # should call the fetch fn (which is where the CPU saturation came
        # from). The second short-circuits before the network.
        pred = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")
        calls: list = []

        def _fetch(symbol, timeframe):
            calls.append((symbol, timeframe))
            return _candles(last_ts=42)

        seen: dict = {}
        wall: dict = {}
        clock = [1_000_000.0]
        now = lambda: clock[0]  # noqa: E731

        assert (
            emit_regime_bar_predictions(
                predictors=[pred], fetch_fn=_fetch, seen=seen,
                wall_cache=wall, now=now,
            )
            == 1
        )
        # 60 s later (one tick) — should NOT re-fetch.
        clock[0] += 60.0
        assert (
            emit_regime_bar_predictions(
                predictors=[pred], fetch_fn=_fetch, seen=seen,
                wall_cache=wall, now=now,
            )
            == 0
        )
        assert len(calls) == 1

    def test_emit_fetch_failure_does_not_arm_gate(self, tmp_path):
        # A failed fetch must NOT delay the next retry: the wall-cache entry
        # is only set AFTER a candle frame is in hand. Otherwise a transient
        # exchange blip would silence a head for a full bar duration.
        pred = _regime_predictor("btc-regime-5m", _spec(), tmp_path / "s.jsonl")
        seen: dict = {}
        wall: dict = {}
        clock = [1_000_000.0]
        now = lambda: clock[0]  # noqa: E731

        def _boom(symbol, timeframe):
            raise RuntimeError("market data down")

        emit_regime_bar_predictions(
            predictors=[pred], fetch_fn=_boom, seen=seen,
            wall_cache=wall, now=now,
        )
        assert ("BTCUSDT", "5m") not in wall  # gate not armed by failure

        # Next tick (60 s) — should attempt the fetch again (a real recovery).
        clock[0] += 60.0
        calls: list = []

        def _ok(symbol, timeframe):
            calls.append((symbol, timeframe))
            return _candles(last_ts=99)

        n = emit_regime_bar_predictions(
            predictors=[pred], fetch_fn=_ok, seen=seen,
            wall_cache=wall, now=now,
        )
        assert n == 1
        assert calls == [("BTCUSDT", "5m")]


class TestPredictorGrouping:
    """Multiple shadow regime heads on the same (symbol, timeframe) must share
    ONE network fetch per tick (the MB-20260609-001 fix). Pre-grouping the old
    per-predictor loop fetched the same Bybit/IBKR candles once per head."""

    def test_two_heads_same_symtf_share_one_fetch(self, tmp_path):
        log = tmp_path / "s.jsonl"
        a = _regime_predictor(
            "btc-regime-5m-v1", _spec(symbol="BTCUSDT", timeframe="5m"), log
        )
        b = _regime_predictor(
            "btc-regime-5m-v2", _spec(symbol="BTCUSDT", timeframe="5m"), log
        )
        calls: list = []

        def _fetch(symbol, timeframe):
            calls.append((symbol, timeframe))
            return _candles(last_ts=777)

        n = emit_regime_bar_predictions(
            predictors=[a, b], fetch_fn=_fetch, seen={}, wall_cache={},
        )
        assert n == 2
        assert len(calls) == 1  # one shared fetch, two scored heads
        assert len(a.wrapped.calls) == 1
        assert len(b.wrapped.calls) == 1

    def test_different_symtf_independent_fetches(self, tmp_path):
        # BTCUSDT/5m and BTCUSDT/1h are different groups → 2 fetches.
        log = tmp_path / "s.jsonl"
        btc5 = _regime_predictor(
            "btc-regime-5m", _spec(symbol="BTCUSDT", timeframe="5m"), log
        )
        btc1h = _regime_predictor(
            "btc-regime-1h", _spec(symbol="BTCUSDT", timeframe="1h"), log
        )
        calls: list = []

        def _fetch(symbol, timeframe):
            calls.append((symbol, timeframe))
            return _candles(last_ts=hash((symbol, timeframe)) & 0xFFFF)

        n = emit_regime_bar_predictions(
            predictors=[btc5, btc1h], fetch_fn=_fetch, seen={}, wall_cache={},
        )
        assert n == 2
        assert sorted(calls) == [("BTCUSDT", "1h"), ("BTCUSDT", "5m")]


class TestPerTickBudget:
    """The cold-start wall-clock budget (BL-20260609-001 / 2026-06-10 wedge).

    On a fresh restart the fetch-gate + dedup caches are empty, so without a
    budget the first tick would fetch every group and score every head in one
    synchronous mega-tick — pegging the 2-core VM and freezing the heartbeat.
    The budget caps per-call wall time and defers remaining whole groups to the
    next tick.
    """

    def _clock(self, values):
        seq = list(values)
        state = {"i": 0}

        def _now():
            i = state["i"]
            state["i"] = min(i + 1, len(seq) - 1)
            return seq[i]

        return _now

    def test_budget_defers_remaining_groups_to_next_tick(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REGIME_BAR_SCORING_BUDGET_S", "6")
        log = tmp_path / "s.jsonl"
        btc5 = _regime_predictor(
            "btc-regime-5m", _spec(symbol="BTCUSDT", timeframe="5m"), log
        )
        mes15 = _regime_predictor(
            "mes-regime-15m", _spec(symbol="MES", timeframe="15m"), log
        )

        def _fetch(symbol, timeframe):
            return _candles(last_ts=hash((symbol, timeframe)) & 0xFFFF)

        seen: dict = {}
        wall: dict = {}
        # Tick 1: tick_start=0, group1(btc5)=0 (under budget, scores),
        # group2(mes15)=10 (>=6, deferred — no fetch, no score).
        n1 = emit_regime_bar_predictions(
            predictors=[btc5, mes15], fetch_fn=_fetch, seen=seen,
            wall_cache=wall, now=self._clock([0.0, 0.0, 10.0]),
        )
        assert n1 == 1
        assert len(btc5.wrapped.calls) == 1
        assert len(mes15.wrapped.calls) == 0          # deferred, not scored
        assert ("MES", "15m") not in wall              # its fetch gate stays un-armed

        # Tick 2: well within budget. btc5's group is fetch-gated (scored last
        # tick), so the deferred mes15 group is the one that runs now.
        n2 = emit_regime_bar_predictions(
            predictors=[btc5, mes15], fetch_fn=_fetch, seen=seen,
            wall_cache=wall, now=self._clock([1.0, 1.0, 1.0]),
        )
        assert n2 == 1
        assert len(mes15.wrapped.calls) == 1           # picked up on the next tick

    def test_budget_zero_is_unlimited(self, tmp_path, monkeypatch):
        # Budget 0 => pre-budget behaviour: everything scored in one call even
        # if the injected clock jumps far ahead.
        monkeypatch.setenv("REGIME_BAR_SCORING_BUDGET_S", "0")
        log = tmp_path / "s.jsonl"
        btc5 = _regime_predictor(
            "btc-regime-5m", _spec(symbol="BTCUSDT", timeframe="5m"), log
        )
        mes15 = _regime_predictor(
            "mes-regime-15m", _spec(symbol="MES", timeframe="15m"), log
        )

        def _fetch(symbol, timeframe):
            return _candles(last_ts=hash((symbol, timeframe)) & 0xFFFF)

        n = emit_regime_bar_predictions(
            predictors=[btc5, mes15], fetch_fn=_fetch, seen={}, wall_cache={},
            now=self._clock([0.0, 100.0, 200.0]),
        )
        assert n == 2

    def test_budget_default_when_env_unset(self, monkeypatch):
        from src.runtime.regime_bar_scoring import _DEFAULT_BUDGET_S, _budget_seconds
        monkeypatch.delenv("REGIME_BAR_SCORING_BUDGET_S", raising=False)
        assert _budget_seconds() == _DEFAULT_BUDGET_S
        monkeypatch.setenv("REGIME_BAR_SCORING_BUDGET_S", "not-a-number")
        assert _budget_seconds() == _DEFAULT_BUDGET_S   # typo falls back, never strands


class _RegimeProbaBase(_RegimeBase):
    """A regime predictor that also exposes predict_proba (advisory heads)."""

    def __init__(self, regime_spec, p_volatile):
        super().__init__(regime_spec)
        self._p = p_volatile

    def predict_proba(self, row):
        return {"range": 1.0 - self._p, "volatile": self._p}


def _advisory_proba_predictor(model_id, spec, log_path, p_volatile):
    return ShadowPredictor(
        _RegimeProbaBase(spec, p_volatile),
        model_id=model_id, stage="advisory", log_path=log_path,
    )


class TestAdvisoryPublish:
    """Design A: advisory regime heads publish per-bar P(volatile) to the
    ml_vol_verdict cache (in addition to the shadow-log write), and a shadow
    head does NOT publish."""

    def test_advisory_head_publishes_p_volatile(self, tmp_path):
        from src.runtime.regime.ml_vol_verdict import (
            _p_volatile_from_cache,
            clear_ml_vol_cache,
            ml_vol_regime,
        )

        clear_ml_vol_cache()
        spec = _spec(symbol="BTCUSDT", timeframe="1h")
        pred = _advisory_proba_predictor(
            "btc-regime-1h-v2", spec, tmp_path / "s.jsonl", p_volatile=0.85,
        )
        n = emit_regime_bar_predictions(
            predictors=[pred], fetch_fn=lambda s, t: _candles(last_ts=7),
            seen={}, wall_cache={},
        )
        assert n == 1  # still writes the shadow log
        # The advisory head published its P(volatile) into the verdict cache.
        assert _p_volatile_from_cache("btc-regime-1h-v2") == 0.85
        # And ml_vol_regime reads the published score WITHOUT an inline score:
        # inject a spec table whose predictor would say a different label inline.
        cold_pred = _advisory_proba_predictor(
            "btc-regime-1h-v2", spec, None, p_volatile=0.05,
        )
        table = {("BTCUSDT", "1H"): {
            "symbol": "BTCUSDT", "timeframe": "1h",
            "model_id": "btc-regime-1h-v2", "predictor": cold_pred, "is_yz": False,
        }}
        out = ml_vol_regime("BTCUSDT", "1h", _candles(), specs=table)
        assert out["vol_regime"] == "volatile"  # from cache (0.85), not 0.05
        clear_ml_vol_cache()

    def test_shadow_head_does_not_publish(self, tmp_path):
        from src.runtime.regime.ml_vol_verdict import (
            _p_volatile_from_cache,
            clear_ml_vol_cache,
        )

        clear_ml_vol_cache()
        # A shadow-stage regime head (no predict_proba on _RegimeBase) — must
        # write the shadow log but NOT publish to the verdict cache.
        pred = _regime_predictor(
            "btc-regime-5m", _spec(symbol="BTCUSDT", timeframe="5m"),
            tmp_path / "s.jsonl",
        )
        n = emit_regime_bar_predictions(
            predictors=[pred], fetch_fn=lambda s, t: _candles(last_ts=9),
            seen={}, wall_cache={},
        )
        assert n == 1
        assert _p_volatile_from_cache("btc-regime-5m") is None
        clear_ml_vol_cache()
