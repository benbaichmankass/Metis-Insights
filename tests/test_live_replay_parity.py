"""Tests for scripts/ops/live_replay_parity.py.

The planted-defect cases are the mandated positive control: a fabricated live
record that is missing a replay signal MUST be flagged. Each planted test is
paired with an un-planted twin over the SAME candles that must come back clean,
so a check that flags everything cannot pass either.
"""
from __future__ import annotations

import copy
import math

import pytest

pd = pytest.importorskip("pandas")

import scripts.ops.live_replay_parity as P  # noqa: E402

TF_S = 14400
START = 1_790_000_000 // TF_S * TF_S  # a 4h boundary
N_BARS = 260
BREAK_BAR = N_BARS - 3  # the bar that breaks out
SINCE = START + (N_BARS - 12) * TF_S
UNTIL = START + (N_BARS - 1) * TF_S


def _bars():
    """Flat-ish channel 100..110, then a decisive up-breakout on BREAK_BAR."""
    rows = []
    for i in range(N_BARS):
        mid = 105 + 4 * math.sin(i / 3.0)
        o = mid - 0.5
        c = mid + 0.5
        h, lo = max(o, c) + 0.8, min(o, c) - 0.8
        if i >= BREAK_BAR:
            o, c, h, lo = 110.0, 140.0, 141.0, 109.5
        rows.append({"time": START + i * TF_S, "open": o, "high": h, "low": lo,
                     "close": c, "volume": 10.0})
    return rows


def _fine(tf_rows):
    """16 x 15m candles per 4h bar, for the last 20 bars, path open→close."""
    out = []
    for b in tf_rows[-20:]:
        for k in range(16):
            frac0, frac1 = k / 16, (k + 1) / 16
            o = b["open"] + (b["close"] - b["open"]) * frac0
            c = b["open"] + (b["close"] - b["open"]) * frac1
            out.append({"time": b["time"] + k * 900, "open": o, "close": c,
                        "high": max(o, c) + 0.01, "low": min(o, c) - 0.01, "volume": 1.0})
    return out


def _leg(cfg_over=None):
    import yaml
    cfg = yaml.safe_load(open(P.REPO_ROOT / "config/strategies.yaml"))
    cfg = (cfg.get("strategies", cfg))["trend_donchian_eth_4h"]
    cfg = {**cfg, **(cfg_over or {}), "decision_bar": "forming"}
    return P.Leg(name="trend_donchian_eth_4h", symbol="ETHUSDT", timeframe="4h",
                 unit="trend_donchian", decision_bar="forming", execution="live",
                 accounts=["bybit_2"], real_money=True,
                 features={"side_filter": True, "decision_frame": True,
                           "us_session_gate": False, "long_only": False}, cfg=cfg)


def _bundle(evals, packages=None, trades=None):
    tf = _bars()
    return {"since": P.iso(SINCE), "until": P.iso(UNTIL),
            "candles": {"ETHUSDT|4h": {"candles": tf, "error": None},
                        "ETHUSDT|15m": {"candles": _fine(tf), "error": None}},
            "signals": {"trend_donchian_eth_4h": {"rows": evals, "error": None}},
            "order_packages": {"rows": packages or [], "error": None},
            "trades": {"rows": trades or [], "error": None}}


def _consistent_evals(leg):
    """Live evals every 2 min that say exactly what the replay robustly says."""
    probe = P.analyse_leg(leg, _bundle([]), SINCE, UNTIL)
    robust = {w.split("|")[0]: w.split("|")[1] for w in probe["replay_signal_windows"]}
    rows = []
    for t in range(SINCE + 40, UNTIL, 120):
        fs = P.iso(t // 900 * 900)
        side = robust.get(fs, "none")
        rows.append({"event": "trend_donchian_eth_4h_eval", "logged_at_utc": P.iso(t),
                     "strategy": "trend_donchian_eth_4h",
                     "side": {"long": "buy", "short": "sell"}.get(side, "none"),
                     "reason": "x", "adx_14": None})
    return rows, robust


def test_replay_finds_the_breakout():
    leg = _leg()
    res = P.analyse_leg(leg, _bundle([]), SINCE, UNTIL)
    assert res["replay_signal_windows"], "fixture must contain a replay signal"


def test_consistent_live_record_has_zero_missed_or_side_divergence():
    leg = _leg()
    rows, robust = _consistent_evals(leg)
    assert robust
    sig_bars = sorted({P.to_epoch(w) // TF_S * TF_S for w in robust})
    pk = [{"order_package_id": f"p{b}", "strategy_name": leg.name,
           "created_at": P.iso(b + 1900)} for b in sig_bars]
    tr = [{"id": n, "order_package_id": p["order_package_id"], "account_id": "bybit_2",
           "status": "open"} for n, p in enumerate(pk)]
    res = P.analyse_leg(leg, _bundle(rows, pk, tr), SINCE, UNTIL)
    assert res["state"] == "checked"
    bad = {k: v for k, v in res["by_class"].items()
           if v and k in ("missed_signal", "side_mismatch", "lost_bar", "dropped_no_package",
                          "dropped_no_order", "candle_mismatch")}
    assert not bad, res["divergence_detail"]
    assert res["matched_signal_bars"]


def test_planted_defect_live_none_where_replay_signals_is_flagged():
    """THE mandated control: fabricated live 'no signal' over a replay signal."""
    leg = _leg()
    rows, robust = _consistent_evals(leg)
    planted = [dict(r, side="none") for r in rows]
    res = P.analyse_leg(leg, _bundle(planted), SINCE, UNTIL)
    assert res["by_class"]["missed_signal"] > 0
    assert res["divergences"] > 0


def test_planted_lost_bar_is_flagged():
    leg = _leg()
    rows, _ = _consistent_evals(leg)
    b0 = START + (N_BARS - 6) * TF_S
    lost = [r for r in rows if not (b0 <= P.to_epoch(r["logged_at_utc"]) < b0 + TF_S)]
    res = P.analyse_leg(leg, _bundle(lost), SINCE, UNTIL)
    assert any(d["class"] == "lost_bar" and d["bar"] == P.iso(b0) for d in res["divergence_detail"])


def test_planted_live_signal_replay_cannot_produce_is_flagged():
    leg = _leg()
    rows, _ = _consistent_evals(leg)
    b0 = START + (N_BARS - 8) * TF_S  # quiet channel bar
    fake = copy.deepcopy(rows)
    for r in fake:
        if b0 <= P.to_epoch(r["logged_at_utc"]) < b0 + 900:
            r["side"] = "buy"
    res = P.analyse_leg(leg, _bundle(fake), SINCE, UNTIL)
    assert res["by_class"]["live_only_signal"] > 0


def test_signal_without_package_or_named_gate_is_dropped_downstream():
    leg = _leg()
    rows, _ = _consistent_evals(leg)
    res = P.analyse_leg(leg, _bundle(rows), SINCE, UNTIL)
    assert res["by_class"]["dropped_no_package"] > 0
    # ...and a named gate explains it
    gate_t = None
    for r in rows:
        if r["side"] != "none":
            gate_t = r["logged_at_utc"]
            break
    gated = rows + [{"event": "open_package_blocked", "logged_at_utc": gate_t,
                     "strategy": leg.name, "side": "buy"}]
    res2 = P.analyse_leg(leg, _bundle(gated), SINCE, UNTIL)
    assert res2["by_class"]["dropped_no_package"] == 0


def test_candle_mismatch_on_printed_close_outside_the_15m_candle():
    leg = _leg()
    rows, _ = _consistent_evals(leg)
    for r in rows[:3]:
        r["reason"] = "no breakout on the latest bar (close=999.0 within channel [1, 2])"
    res = P.analyse_leg(leg, _bundle(rows), SINCE, UNTIL)
    assert res["by_class"]["candle_mismatch"] >= 1


def test_could_not_check_is_null_never_zero():
    leg = _leg()
    b = _bundle([])
    b["candles"]["ETHUSDT|15m"] = {"candles": [], "error": "fetch_failed: boom"}
    res = P.analyse_leg(leg, b, SINCE, UNTIL)
    assert res["state"] == "could_not_check"
    assert res["divergences"] is None and res["bars_checked"] is None


def test_run_headline_says_could_not_check_before_divergences():
    leg = _leg()
    b = _bundle([])
    b["signals"]["trend_donchian_eth_4h"] = {"rows": [], "error": "signals_table_absent"}
    doc = P.run(b, [leg], SINCE, UNTIL)
    assert doc["status"] == "could_not_check"
    assert doc["totals"]["divergences_real_money"] is None
    assert "COULD NOT CHECK" in doc["headline"]
    assert "COULD NOT CHECK" in P.summary_line(None)


def test_planted_defect_control_passes_in_run():
    leg = _leg()
    rows, _ = _consistent_evals(leg)
    doc = P.run(_bundle(rows), [leg], SINCE, UNTIL)
    pd_ = doc["controls"]["planted_defect"]
    first = pd_["plants"][0]
    assert first["plant"] == "replay_signal_live_none" and first["flagged"] is True


def test_liveness_needs_a_placed_order():
    b = _bundle([], [{"order_package_id": "p", "strategy_name": "x",
                      "created_at": P.iso(SINCE + 10)}],
                [{"order_package_id": "p", "account_id": "bybit_1", "status": "rejected"}])
    assert P.liveness(b, SINCE)["state"] == "fail"
    b["trades"]["rows"][0]["status"] = "closed"
    assert P.liveness(b, SINCE)["state"] == "pass"


def test_findings_file_one_pipeline_row_each_and_never_twice(tmp_path):
    leg = _leg()
    rows, _ = _consistent_evals(leg)
    planted = [dict(r, side="none") for r in rows]
    doc = P.run(_bundle(planted), [leg], SINCE, UNTIL)
    store = tmp_path / "pipeline"
    first = P.file_pipeline(doc, store)
    assert first, "a planted divergence must become a pipeline row"
    from scripts.ops import pipeline
    items = pipeline.read_log(store).items
    for pid in first:
        it = items[pid]
        assert it["due_when"]["kind"] == "observation" and it["origin"]["rerun"]
    assert P.file_pipeline(doc, store) == []  # open row already carries it
