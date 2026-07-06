"""Evidence-layer tests for scripts/backtest_system.py (Designs A/B).

Hermetic: synthetic candles + a monkeypatched signal stream — no network,
no ML registry, no datasets. Verifies the regime/vol-axis stamping and the
``--vol-verdict`` / ``--regime-router`` / ``--conviction-sizing`` evidence
knobs added for the A/B backtest plans, AND that with none of them the harness
behaves exactly as before.

The ``ml`` vol-verdict path can't resolve a real advisory head offline, so the
test asserts the **graceful-fallback** contract (available=False, frozen used
for every bar) rather than requiring a head — the live trainer run is where
``ml`` scores real heads.
"""
from __future__ import annotations

import importlib

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

bs = importlib.import_module("scripts.backtest_system")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _trending_base5m(n: int = 1200, start_px: float = 20_000.0) -> pd.DataFrame:
    """A clean up-trend on a 5m grid so detect_regime → 'trending' and a long
    breakout is profitable. n 5m bars ≈ n/12 hours."""
    ts = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    px = start_px + np.arange(n, dtype=float) * 5.0  # +$5/bar steady climb
    return pd.DataFrame({
        "timestamp": ts,
        "open": px,
        "high": px + 8.0,
        "low": px - 8.0,
        "close": px + 4.0,
        "volume": np.full(n, 10.0),
    })


def _inject_stream(monkeypatch, *, side="long", at_bars=(3, 4), conf=0.8,
                   stop_frac=0.01):
    """Monkeypatch generate_signal_stream so `trend_donchian` emits a
    deterministic long signal on the given clock-grid timestamps. Entry = the
    bar close; SL below by stop_frac; TP far so the SL/monitor governs the exit.

    Returns the captured-intents list (filled by the patched aggregate_intents).
    """
    base = _trending_base5m()
    clock = bs._date_filter(bs._resample(base, bs._PANDAS_TF["15m"]), None, None)
    clock = clock.reset_index(drop=True)

    rows = []
    for b in at_bars:
        c = float(clock["close"].iloc[b])
        rows.append({
            "ts": clock["timestamp"].iloc[b],
            "side": side,
            "entry": c,
            "sl": c * (1 - stop_frac) if side == "long" else c * (1 + stop_frac),
            "tp": c * (1 + 5 * stop_frac) if side == "long" else c * (1 - 5 * stop_frac),
            "confidence": conf,
            "meta_json": "{}",
        })
    stream = pd.DataFrame(
        rows, columns=["ts", "side", "entry", "sl", "tp", "confidence", "meta_json"])

    def _fake_stream(name, base5m, *, start, end, overrides, refresh=False,
                     symbol="BTCUSDT"):
        if name == "trend_donchian":
            return stream
        return pd.DataFrame(
            columns=["ts", "side", "entry", "sl", "tp", "confidence", "meta_json"])

    monkeypatch.setattr(bs, "generate_signal_stream", _fake_stream)
    return base


def _capture_intents(monkeypatch):
    """Wrap aggregate_intents to record the intents it is handed each tick."""
    captured = []
    real = bs.aggregate_intents

    def _spy(intents, *, symbol="BTCUSDT"):
        intents = list(intents)
        captured.extend(intents)
        return real(intents, symbol=symbol)

    monkeypatch.setattr(bs, "aggregate_intents", _spy)
    return captured


def _run(base, **kw):
    return bs.run_system_backtest(
        base, roster=["trend_donchian"], start=None, end=None,
        initial_balance=10_000.0, risk_pct=0.3, daily_loss_pct=3.0,
        signal_ttl_bars=2, overrides={}, refresh=True, clock_tf="15m",
        **kw)


# ---------------------------------------------------------------------------
# (a) default behaviour unchanged — stamping present, router off → same trades
# ---------------------------------------------------------------------------
def test_default_stamps_regime_but_does_not_change_trades(monkeypatch):
    base = _inject_stream(monkeypatch)
    captured = _capture_intents(monkeypatch)
    out = _run(base)  # all evidence knobs default

    # Trades happened (the injected long on a clean uptrend).
    assert out["total_trades"] >= 1
    baseline_trades = out["total_trades"]

    # Regime axis is stamped on the intents the aggregator saw (Design A wiring).
    stamped = [i for i in captured if i.regime is not None]
    assert stamped, "expected at least one intent stamped with an ADX regime"
    assert stamped[0].adx_14 is not None
    # On a clean monotone uptrend the ADX label resolves to 'trending'.
    assert any(i.regime == "trending" for i in captured)

    # Router OFF → the stamping is observe-only: trade count is the baseline.
    out2 = bs.run_system_backtest(
        base, roster=["trend_donchian"], start=None, end=None,
        initial_balance=10_000.0, risk_pct=0.3, daily_loss_pct=3.0,
        signal_ttl_bars=2, overrides={}, refresh=True, clock_tf="15m",
        regime_router="off")
    assert out2["total_trades"] == baseline_trades

    # Per-cell (strategy|trend|vol|side) attribution populates from the stamped
    # closed trades — the 2-D vol-split that authors evidence-based OFF-cells.
    cells = out.get("per_cell_attribution")
    assert isinstance(cells, dict) and cells, "expected per-cell attribution"
    k = next(iter(cells))
    assert k.count("|") == 3, f"cell key shape owner|trend|vol|side, got {k!r}"
    assert k.startswith("trend_donchian|")
    # Net PnL across cells equals the per-strategy total (no trades dropped).
    cell_pnl = round(sum(c["pnl"] for c in cells.values()), 2)
    strat_pnl = round(sum(a["pnl"] for a in out["per_strategy_attribution"].values()), 2)
    assert abs(cell_pnl - strat_pnl) < 0.05


# ---------------------------------------------------------------------------
# (b) --vol-verdict frozen stamps a vol_regime onto the intents
# ---------------------------------------------------------------------------
def test_frozen_vol_verdict_stamps_vol_regime(monkeypatch):
    base = _inject_stream(monkeypatch)
    captured = _capture_intents(monkeypatch)
    # Offline there is no frozen vol-spec, so force the frozen detector to a
    # concrete label to exercise the stamping path deterministically.
    monkeypatch.setattr(bs, "_frozen_vol_regime_for_window",
                        lambda *a, **k: "calm")
    out = _run(base, vol_verdict="frozen")

    assert out["evidence"]["vol_verdict"] == "frozen"
    assert out["evidence"]["intents_stamped_with_vol"] >= 1
    assert any(i.vol_regime == "calm" for i in captured)


# ---------------------------------------------------------------------------
# (c) --conviction-sizing changes the sized qty vs flat
# ---------------------------------------------------------------------------
def test_conviction_sizing_changes_qty(monkeypatch):
    base = _inject_stream(monkeypatch, at_bars=(3,), conf=0.5)
    flat = _run(base)
    conv = _run(base, conviction_sizing=True)

    assert flat["total_trades"] >= 1
    assert conv["total_trades"] >= 1
    assert conv["evidence"]["conviction_sizing"] is True
    assert conv["evidence"]["conviction_sized_opens"] >= 1

    # The conviction-sized book is materially different from the flat-risk book
    # (different qty → different net PnL). Same signals, only the sizing differs.
    assert flat["net_pnl"] != conv["net_pnl"]


# ---------------------------------------------------------------------------
# (d) --regime-router on with a local OFF-cell policy drops the intents
# ---------------------------------------------------------------------------
def test_regime_router_on_local_policy_drops_intents(monkeypatch, tmp_path):
    base = _inject_stream(monkeypatch)
    # Local policy that OFF-cells trend_donchian in the 'trending' regime (the
    # label the clean uptrend produces). Never the live config.
    policy = tmp_path / "local_policy.yaml"
    policy.write_text(
        "trending:\n"
        "  trend_donchian:\n"
        "    long: off\n"
        "    short: off\n"
    )

    gated = _run(base, regime_router="on", regime_policy_path=str(policy))
    ungated = _run(base, regime_router="off")

    assert ungated["total_trades"] >= 1
    # The hard gate drops every trend_donchian intent in 'trending' → no trades.
    assert gated["total_trades"] == 0

    # Env restored after the run (no leak of the in-process flags). The router
    # is baseline-on, so an `off` run sets REGIME_ROUTER_DISABLED=1 for the
    # duration and must restore it; the `on` run sets REGIME_ROUTER_ENABLED=1.
    import os
    assert os.environ.get("REGIME_ROUTER_ENABLED") is None
    assert os.environ.get("REGIME_ROUTER_DISABLED") is None
    assert os.environ.get("REGIME_POLICY_PATH") is None


# ---------------------------------------------------------------------------
# (e) --vol-verdict ml degrades gracefully offline (no advisory head)
# ---------------------------------------------------------------------------
def test_ml_vol_verdict_graceful_fallback(monkeypatch):
    base = _inject_stream(monkeypatch)
    out = _run(base, vol_verdict="ml")
    ev = out["evidence"]
    assert ev["vol_verdict"] == "ml"
    # Offline: no advisory regime head resolves → unavailable, never crashes.
    assert ev["ml_vol_available"] is False
    assert ev["ml_vol_reason"] is not None
    assert ev["ml_vol_scored_bars"] == 0
    # Default stage is advisory (the live verdict source).
    assert ev["ml_vol_stage"] == "advisory"


def test_ml_stage_shadow_threads_to_evidence(monkeypatch):
    """--ml-stage shadow resolves from the shadow registry stage (option-2
    evidence lever). Offline no head resolves, but the chosen stage must be
    reported so the run is self-describing, and the fallback reason names it."""
    base = _inject_stream(monkeypatch)
    out = _run(base, vol_verdict="ml", ml_stage="shadow")
    ev = out["evidence"]
    assert ev["vol_verdict"] == "ml"
    assert ev["ml_vol_stage"] == "shadow"
    # Offline: degrades to frozen, never crashes; the reason is stage-scoped.
    assert ev["ml_vol_available"] is False
    assert ev["ml_vol_scored_bars"] == 0
    # The footer surfaces the stage + (None offline) head id without throwing.
    text = bs._fmt(out)
    assert "stage=shadow" in text


def test_ml_model_id_pin_overrides_stage_discovery(monkeypatch):
    """--ml-model-id pins one exact head. Offline it still can't resolve, but
    the resolver must attempt only the pinned id (no crash) and the run stays
    self-describing."""
    base = _inject_stream(monkeypatch)
    out = _run(base, vol_verdict="ml", ml_stage="shadow",
               ml_model_id="btc-regime-15m-lgbm-v2")
    ev = out["evidence"]
    assert ev["ml_vol_available"] is False  # offline, no registry
    assert ev["ml_vol_stage"] == "shadow"


# ---------------------------------------------------------------------------
# Backward-compat: default _fmt output carries no evidence footer
# ---------------------------------------------------------------------------
def test_default_fmt_has_no_evidence_footer(monkeypatch):
    base = _inject_stream(monkeypatch)
    out = _run(base)
    text = bs._fmt(out)
    assert "evidence layer" not in text


def test_active_fmt_shows_evidence_footer(monkeypatch):
    base = _inject_stream(monkeypatch)
    out = _run(base, conviction_sizing=True)
    text = bs._fmt(out)
    assert "evidence layer" in text


# ---------------------------------------------------------------------------
# (h) --regime-router on enforces the 2-D trend_vol OFF-cells on the STAMPED
#     vol label (BL-20260706-VOLGATE-REPLAY). Post-#4896 the live hard gate
#     vol-enforces only on a live-resolved ML label, which never exists
#     offline — the replay must trust the label the run stamped instead, or
#     every vol-cell A/B arm silently equals ungated (the 2026-07-06 ETH/SOL
#     evidence-session regression).
# ---------------------------------------------------------------------------
def test_regime_router_on_enforces_trend_vol_cell_on_stamped_label(
        monkeypatch, tmp_path):
    base = _inject_stream(monkeypatch)
    # Force a concrete stamped vol label (the synthetic climb yields 'unknown'
    # from the real frozen detector, which is correctly never gated).
    monkeypatch.setattr(bs, "_frozen_vol_regime_for_window",
                        lambda *a, **k: "calm")
    policy = tmp_path / "local_policy.yaml"
    policy.write_text(
        "schema_version: 2\n"
        "trend_vol:\n"
        "  trending:\n"
        "    calm:\n"
        "      trend_donchian:\n"
        "        long: off\n"
    )

    ungated = _run(base, vol_verdict="frozen")
    gated = _run(base, regime_router="on", regime_policy_path=str(policy),
                 vol_verdict="frozen")

    assert ungated["total_trades"] >= 1
    # The vol cell must drop every trending|calm|long trend_donchian intent.
    assert gated["total_trades"] == 0

    # Teardown restored the in-process gate hooks + env.
    import os

    import src.runtime.intents as im
    assert os.environ.get("REGIME_ML_VERDICT_MODE") is None
    assert im._decision_vol_regime.__name__ == "_decision_vol_regime"
    assert im._emit_ml_vol_shadow_rows.__name__ == "_emit_ml_vol_shadow_rows"
