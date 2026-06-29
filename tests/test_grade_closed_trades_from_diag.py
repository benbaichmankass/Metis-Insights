"""Web-session grading path (grade_closed_trades_from_diag).

Confirms the diag-fed grader (a) grades CLOSED real-money trades only by default,
(b) reproduces the canonical _grade_package rubric, and (c) writes append-style
JSONL rows the API join can consume.
"""
from __future__ import annotations
import json
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "g", os.path.join("scripts", "ops", "grade_closed_trades_from_diag.py"))
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)
grader = g._load_grader()

TRADES = [
  {"id":3010,"order_package_id":"pkg-8ba3","strategy_name":"htf_pullback_trend_2h","symbol":"BTCUSDT","direction":"long","status":"closed","is_backtest":0,"account_class":"real_money","entry_price":60229.1,"stop_loss":59230.65,"take_profit_1":66191.78,"exit_price":59226.5,"pnl":-1.0515,"exit_reason":"sl","created_at":"2026-06-27T22:37:18+00:00","closed_at":"2026-06-28T19:47:50+00:00","notes":json.dumps({"confidence":0.3155})},
  {"id":3025,"order_package_id":"pkg-9f79","strategy_name":"htf_pullback_trend_2h","symbol":"BTCUSDT","direction":"long","status":"closed","is_backtest":0,"account_class":"real_money","entry_price":59822.7,"stop_loss":58764.36,"take_profit_1":65745.15,"exit_price":59794.1,"pnl":-0.0679,"exit_reason":"reconciler_filled","created_at":"2026-06-28T22:03:49+00:00","closed_at":"2026-06-29T11:52:34+00:00","notes":json.dumps({"confidence":0.1862})},
  {"id":3024,"order_package_id":"pkg-open","strategy_name":"htf_pullback_trend_2h","symbol":"BTCUSDT","direction":"long","status":"open","is_backtest":0,"account_class":"paper","entry_price":59822.7,"stop_loss":58764.36,"take_profit_1":65745.15,"created_at":"2026-06-28T22:03:50+00:00"},
  {"id":3009,"order_package_id":"pkg-paper","strategy_name":"htf_pullback_trend_2h","symbol":"BTCUSDT","direction":"long","status":"closed","is_backtest":0,"account_class":"paper","entry_price":60215.5,"stop_loss":59230.65,"take_profit_1":66191.78,"exit_price":59383.5,"pnl":-673.92,"exit_reason":"reconciler_filled","created_at":"2026-06-27T22:37:19+00:00","closed_at":"2026-06-27T22:37:18+00:00","notes":json.dumps({"confidence":0.3155})},
]

def test_grades_closed_real_money_only():
    recs = g.grade(TRADES, grader, source="t", since=None, include_paper=False)
    ids = {r["order_package_id"] for r in recs}
    assert ids == {"pkg-8ba3","pkg-9f79"}  # open + paper excluded
    by = {r["order_package_id"]: r for r in recs}
    assert by["pkg-8ba3"]["decision_grade"] == "C"   # conf .32 late, sl_appropriate
    assert by["pkg-9f79"]["decision_grade"] == "D"   # conf .19 should_skip
    for r in recs:
        assert r["status"] == "closed" and r["reviewer"] == "claude"
        assert r["exit_quality"] in ("sl_appropriate","tp_appropriate","premature_exit","unknown")

def test_include_paper_and_since():
    recs = g.grade(TRADES, grader, source="t", since="2026-06-28T00:00:00+00:00", include_paper=True)
    ids = {r["order_package_id"] for r in recs}
    # paper pkg-paper closed 06-27 → before since; open excluded; real two in-window
    assert ids == {"pkg-8ba3","pkg-9f79"}

def test_append_writes_jsonl(tmp_path):
    out = tmp_path/"scores.jsonl"
    out.write_text("")
    rc = g.main([_write(tmp_path, TRADES), "--out", str(out), "--source","unit"])
    assert rc == 0
    lines = [json.loads(ln) for ln in out.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2


def _write(tmp_path, obj):
    p = tmp_path / "trades.json"
    p.write_text(json.dumps(obj))
    return str(p)
