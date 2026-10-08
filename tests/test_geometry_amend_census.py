"""Fixture test for scripts/research/geometry_amend_census.py."""
import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "geometry_amend_census",
    Path(__file__).resolve().parents[1] / "scripts" / "research" / "geometry_amend_census.py",
)
gac = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gac)

CFG = {"bybit_2": "real_money", "bybit_1": "paper", "bybit_portfolio": "paper",
       "tradeify_1": "prop"}


def _plan(sl, tp):
    return json.dumps({"stop": {"price": sl}, "final": {"price": tp} if tp else None})


def _pkg(pid, sl, tp, plan_sl, plan_tp, has_plan=True):
    return {"order_package_id": pid, "sl": sl, "tp": tp,
            "exit_plan": _plan(plan_sl, plan_tp) if has_plan else None}


def _trade(i, acct, pid, status="closed", ts="2026-09-10T00:00:00+00:00", leg="leg_a"):
    return {"id": i, "account_id": acct, "account_class": None, "order_package_id": pid,
            "status": status, "timestamp": ts, "strategy_name": leg}


PKGS = [
    _pkg("p1", 99.0, 120.0, 98.0, 120.0),            # SL moved 1%, TP unmoved
    _pkg("p2", 100.0, 120.0, 100.0, 120.0),          # nothing moved
    _pkg("p3", 100.02, 120.0, 100.0, 120.0),         # 0.02% < tolerance: not an amend
    _pkg("p4", 100.0, 130.0, 100.0, 120.0),          # TP moved (comparator must see it)
    _pkg("p5", 90.0, 120.0, 100.0, None, has_plan=False),  # no exit_plan: excluded
]
TRADES = [
    _trade(1, "bybit_2", "p1"),
    _trade(2, "bybit_2", "p2"),
    _trade(3, "bybit_portfolio", "p3"),
    _trade(4, "bybit_1", "p4"),
    _trade(5, "bybit_1", "p5"),
    _trade(6, "bybit_1", "missing"),                  # no package: unmatched
    _trade(7, "bybit_1", "p1", ts="2026-08-01T00:00:00+00:00"),  # before --since
    _trade(8, "bybit_1", "p1", status="open"),
    _trade(9, "tradeify_1", None, status="rejected"),  # prop rows exist, none closed
]


def _run(trades=TRADES, packages=PKGS, **kw):
    data = {"trades": {"rows": trades, "total_rows": len(trades)},
            "order_packages": {"rows": packages, "total_rows": len(packages)}, "prop": None}
    return gac.run(data, "2026-09-01", 5e-4, CFG, "2026-10-07T00:00:00Z")


def test_counts_per_class():
    r = _run()
    real, mirror, paper = (r["classes"][k] for k in ("real_money", "mirror", "paper"))
    assert (real["closed_in_window"], real["sl_amended"], real["sl_denominator"],
            real["tp_amended"], real["tp_denominator"]) == (2, 1, 2, 0, 2)
    assert (mirror["sl_amended"], mirror["sl_denominator"]) == (0, 1)   # 0.02% under tolerance
    assert paper["closed_in_window"] == 3 and paper["unmatched_package"] == 1
    assert paper["no_exit_plan"] == 1 and paper["tp_amended"] == 1 and paper["tp_denominator"] == 1
    assert r["fleet"]["sl_amended"] == 1 and r["fleet"]["sl_denominator"] == 4


def test_read_states_distinguish_absent_from_zero():
    r = _run()
    assert r["classes"]["real_money"]["read_state"] == "measured"
    assert r["classes"]["prop"]["read_state"] == "no_closed_rows"
    assert r["classes"]["prop"]["sl_amended"] is None      # not 0: nothing was measured
    assert r["classes"]["prop"]["window_status_counts"] == {"rejected": 1}
    assert r["classes"]["unclassified"]["read_state"] == "absent"
    # a real zero stays a zero
    assert r["classes"]["mirror"]["tp_amended"] == 0
    assert r["classes"]["mirror"]["tp_amended_rate"] == 0.0


def test_incomplete_pull_is_unreadable_not_zero():
    data = {"trades": {"rows": TRADES[:3], "total_rows": len(TRADES)},
            "order_packages": {"rows": PKGS, "total_rows": len(PKGS)}, "prop": None}
    r = gac.run(data, "2026-09-01", 5e-4, CFG, "2026-10-07T00:00:00Z")
    assert r["pull"]["complete"] is False
    assert all(c["read_state"] == "unreadable" and c["sl_amended"] is None
               for c in r["classes"].values())


def test_from_dir_roundtrip_and_markdown(tmp_path):
    (tmp_path / "trades.json").write_text(json.dumps({"rows": TRADES, "total_rows": len(TRADES)}))
    (tmp_path / "order_packages.json").write_text(json.dumps({"rows": PKGS, "total_rows": len(PKGS)}))
    out = tmp_path / "out"
    rc = gac.main(["--from-dir", str(tmp_path), "--out-dir", str(out), "--date", "2026-10-07"])
    assert rc == 0
    doc = json.loads((out / "geometry-amend-census-2026-10-07.json").read_text())
    assert doc["provenance"] == "MEASURED" and doc["classes"]["real_money"]["sl_amended"] == 1
    md = (out / "geometry-amend-census-2026-10-07.md").read_text()
    assert "| prop | no_closed_rows" in md and "1 / 2" in md
