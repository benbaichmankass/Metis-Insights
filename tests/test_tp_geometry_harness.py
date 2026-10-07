"""GEOM-B1-HARNESS: can a backtest measure a take-profit that MOVES?

Two properties, in order of importance:

1. **Bit-for-bit at default.** Every prior trend / pullback / scalp verdict was
   produced with no TP-geometry lever. `test_defaults_reproduce_pre_lever_golden`
   compares the summary dict AND the emitted trade rows of each harness against a
   golden captured from the harnesses BEFORE the levers existed
   (`tests/fixtures/tp_geometry_default_golden.json`, regenerate only on purpose:
   ``python3 tests/test_tp_geometry_harness.py --capture``). A golden that
   captured zero trades would prove nothing, so each case asserts it traded.
2. **The levers measure what they claim** -- finite target, extension (decided by
   `src.runtime.target_expectation.evaluate_extension`, IMPORTED), the two
   retarget modes, and the refuse-rather-than-run-inert combinations.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
GOLDEN = _REPO / "tests" / "fixtures" / "tp_geometry_default_golden.json"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(_REPO / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TREND = _load("_tpg_trend", "scripts/backtest_trend.py")
PULLBACK = _load("_tpg_pullback", "scripts/backtest_pullback.py")
SCALP = _load("_tpg_scalp", "scripts/backtest_ict_scalp.py")


def _candles(rule="5min"):
    df = pd.read_csv(_REPO / "data" / "backtest_candles.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return (df.set_index("timestamp").resample(rule, label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index())


TREND_KW = dict(donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=3.0,
                timeout_bars=200, cooldown_bars=1, timeframe="5min", symbol="BTCUSDT")
PULLBACK_KW = dict(trend_lookback=40, pullback_lookback=10, pullback_frac=0.5,
                   atr_period=14, atr_stop_mult=2.5, trail_mult=5.0,
                   timeout_bars=200, cooldown_bars=1, timeframe="5min",
                   symbol="BTCUSDT")
SCALP_KW = dict(cfg_overrides={}, timeframe="5m", symbol="BTCUSDT", warmup_bars=50,
                timeout_bars=24, cooldown_bars=3)

# Each case: (id, runner). `tp_r=1.5` + a cap makes the trend/pullback books take
# profit, so the take_profit exit path is inside the golden, not just the trail.
CASES = {
    "trend_trail_only": ("trend", TREND_KW, {}),
    "trend_capped_tp": ("trend", TREND_KW, dict(tp_cap_pct=0.099, tp_r=1.5)),
    "pullback_trail_only": ("pullback", PULLBACK_KW, {}),
    "pullback_capped_tp": ("pullback", PULLBACK_KW, dict(tp_cap_pct=0.099, tp_r=1.5)),
    "scalp_default": ("scalp", SCALP_KW, {}),
}


def _run_case(case_id, tmp_path, extra=None):
    kind, base, over = CASES[case_id]
    emit = tmp_path / f"{case_id}.jsonl"
    if kind == "scalp":
        for mod in (SCALP,):
            mod.FEE_BPS_ROUNDTRIP = 7.5
            mod.SLIPPAGE_BPS_ROUNDTRIP = 3.0
            mod.FUNDING_BPS_PER_WINDOW = 1.0
        df = SCALP._load_candles(str(_REPO / "data" / "backtest_candles.csv")
                                 ).iloc[:4000].reset_index(drop=True)
        out = SCALP.run_backtest(df, emit_path=str(emit), **{**base, **over, **(extra or {})})
    else:
        mod = TREND if kind == "trend" else PULLBACK
        out = mod.run_backtest(_candles().copy(), emit_path=str(emit),
                               **{**base, **over, **(extra or {})})
    rows = [json.loads(line) for line in emit.read_text().splitlines()] if emit.exists() else []
    return json.loads(json.dumps(out, default=str)), rows


def _capture(tmp_path):
    golden = {}
    for cid in CASES:
        summary, rows = _run_case(cid, tmp_path)
        golden[cid] = {"summary": summary, "rows": rows}
    return golden


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden not captured")
@pytest.mark.parametrize("cid", list(CASES))
def test_defaults_reproduce_pre_lever_golden(cid, tmp_path):
    golden = json.loads(GOLDEN.read_text())[cid]
    summary, rows = _run_case(cid, tmp_path)
    assert golden["rows"], f"{cid}: golden holds no trades -- the equality would prove nothing"
    assert summary == golden["summary"]
    assert rows == golden["rows"]


# --------------------------------------------------------------------------- #
# The levers measure what they claim
# --------------------------------------------------------------------------- #
tpg = _load("_tpg_mod", "scripts/research/tp_geometry.py")
Spec = tpg.TPGeometrySpec

LEVER_CASES = ("trend_trail_only", "pullback_trail_only")


def _geom(cid, tmp_path, **spec):
    return _run_case(cid, tmp_path, extra=dict(tp_geometry=Spec(**spec)))


def test_decision_half_is_imported_not_reimplemented():
    from src.runtime import target_expectation as te
    assert tpg.evaluate_extension is te.evaluate_extension
    src = (_REPO / "scripts" / "research" / "tp_geometry.py").read_text()
    assert "def evaluate_extension" not in src


def test_default_spec_is_unarmed():
    assert not Spec().armed and not Spec().revises and Spec().params() == {}


def test_extension_decided_by_evaluate_extension():
    """Long, entry 100, risk 2, target 1.5R=103. Close at 102.8 (>= 85% of the
    span) with the thesis intact extends by extend_r R; thesis unknown never does."""
    def tracker(thesis):
        return tpg.TPTracker(Spec(target_r=1.5, extend_r=1.0, thesis=thesis),
                             anchor=100.0, sl=98.0, risk=2.0, is_long=True,
                             tp_cap_pct=0.0, target=103.0, atr0=1.0)
    t = tracker("always")
    t.on_bar_close(close=102.8, ext=102.9, bars_since_peak=0, atr_now=1.0, thesis_fn=None)
    assert (t.n_extends, t.target) == (1, 105.0) and t.final_target_r == 2.5
    t = tracker("unknown")
    t.on_bar_close(close=102.8, ext=102.9, bars_since_peak=0, atr_now=1.0, thesis_fn=None)
    assert (t.n_extends, t.target) == (0, 103.0)
    t = tracker("native")
    t.on_bar_close(close=102.8, ext=102.9, bars_since_peak=0, atr_now=1.0,
                   thesis_fn=lambda: False)
    assert t.n_extends == 0
    t = tracker("always")        # not approaching: 101 is 50% of the span
    t.on_bar_close(close=101.0, ext=101.5, bars_since_peak=0, atr_now=1.0, thesis_fn=None)
    assert t.n_extends == 0


def test_extension_is_bounded_and_clamped():
    t = tpg.TPTracker(Spec(target_r=1.5, extend_r=1.0, max_extends=1, thesis="always"),
                      anchor=100.0, sl=98.0, risk=2.0, is_long=True,
                      tp_cap_pct=0.0, target=103.0, atr0=1.0)
    t.on_bar_close(close=102.9, ext=102.9, bars_since_peak=0, atr_now=1.0, thesis_fn=None)
    t.on_bar_close(close=104.9, ext=104.9, bars_since_peak=0, atr_now=1.0, thesis_fn=None)
    assert t.n_extends == 1                      # max_extends binds
    c = tpg.TPTracker(Spec(target_r=1.5, extend_r=5.0, thesis="always"),
                      anchor=100.0, sl=98.0, risk=2.0, is_long=True,
                      tp_cap_pct=0.05, target=103.0, atr0=1.0)
    c.on_bar_close(close=102.9, ext=102.9, bars_since_peak=0, atr_now=1.0, thesis_fn=None)
    assert c.target == 105.0                     # venue clamp, not 103 + 5R


def test_stall_pull_in_only_tightens_and_atr_rescale_follows_vol():
    s = tpg.TPTracker(Spec(target_r=3.0, retarget_mode="stall_pull_in",
                           retarget_stall_bars=2, retarget_pull_r=0.5),
                      anchor=100.0, sl=98.0, risk=2.0, is_long=True,
                      tp_cap_pct=0.0, target=106.0, atr0=1.0)
    s.on_bar_close(close=102.0, ext=103.0, bars_since_peak=1, atr_now=1.0, thesis_fn=None)
    assert s.target == 106.0                     # not stalled yet
    s.on_bar_close(close=102.0, ext=103.0, bars_since_peak=2, atr_now=1.0, thesis_fn=None)
    assert s.target == 104.0                     # peak 103 + 0.5R
    s.on_bar_close(close=102.0, ext=103.0, bars_since_peak=9, atr_now=1.0, thesis_fn=None)
    assert s.target == 104.0                     # a longer stall never moves it out again
    assert s.n_retargets == 1
    a = tpg.TPTracker(Spec(target_r=2.0, retarget_mode="atr_rescale"),
                      anchor=100.0, sl=98.0, risk=2.0, is_long=True,
                      tp_cap_pct=0.0, target=104.0, atr0=1.0)
    a.on_bar_close(close=101.0, ext=101.0, bars_since_peak=0, atr_now=1.5, thesis_fn=None)
    assert a.target == pytest.approx(106.0)      # 2R * 1.5
    a.on_bar_close(close=101.0, ext=101.0, bars_since_peak=0, atr_now=0.5, thesis_fn=None)
    assert a.target == pytest.approx(102.0)      # 2R * 0.5, still ahead of the close


def test_short_side_mirrors_long():
    t = tpg.TPTracker(Spec(target_r=1.5, extend_r=1.0, thesis="always"),
                      anchor=100.0, sl=102.0, risk=2.0, is_long=False,
                      tp_cap_pct=0.0, target=97.0, atr0=1.0)
    t.on_bar_close(close=97.2, ext=97.1, bars_since_peak=0, atr_now=1.0, thesis_fn=None)
    assert (t.n_extends, t.target, t.final_target_r) == (1, 95.0, 2.5)


@pytest.mark.parametrize("cid", LEVER_CASES)
def test_unknown_thesis_equals_no_extension(cid, tmp_path):
    """The negative control: `unknown` must reproduce the finite-target run
    exactly -- an extension that fires without a thesis is not conditioned on one."""
    base, base_rows = _geom(cid, tmp_path, target_r=1.5)
    unk, unk_rows = _geom(cid, tmp_path, target_r=1.5, extend_r=1.0, thesis="unknown",
                          approach_frac=0.75)
    strip = lambda rows: [{k: v for k, v in r.items()} for r in rows]  # noqa: E731
    assert strip(unk_rows) == strip(base_rows)
    assert unk["tp_geometry"]["total_extends"] == 0


@pytest.mark.parametrize("cid", LEVER_CASES)
def test_extension_and_retarget_change_the_book(cid, tmp_path):
    base, _ = _geom(cid, tmp_path, target_r=1.5)
    ext, ext_rows = _geom(cid, tmp_path, target_r=1.5, extend_r=1.0, thesis="always",
                          approach_frac=0.75)
    assert ext["tp_geometry"]["total_extends"] > 0
    assert any(r["n_extends"] for r in ext_rows)
    assert ext_rows != _geom(cid, tmp_path, target_r=1.5)[1]
    stall, _ = _geom(cid, tmp_path, target_r=2.5, retarget_mode="stall_pull_in",
                     retarget_stall_bars=2)
    assert stall["tp_geometry"]["total_retargets"] > 0
    atr, _ = _geom(cid, tmp_path, target_r=1.5, retarget_mode="atr_rescale")
    assert atr["tp_geometry"]["total_retargets"] > 0


@pytest.mark.parametrize("cid", LEVER_CASES)
def test_armed_rows_carry_geometry_and_calibration_is_a_share(cid, tmp_path):
    out, rows = _geom(cid, tmp_path, target_r=1.5, extend_r=1.0, thesis="always")
    for r in rows:
        assert {"final_target_r", "n_extends", "n_retargets", "exit_r"} <= set(r)
        assert r["final_target_r"] >= 1.5
    g = out["tp_geometry"]
    assert g["read_state"] == "measured" and 0.0 <= g["calibration_share"] <= 1.0
    assert g["n_with_target"] == len(rows)


def test_scalp_levers_run_and_unknown_thesis_is_inert(tmp_path):
    base, base_rows = _geom("scalp_default", tmp_path, target_r=1.0)
    ext, ext_rows = _geom("scalp_default", tmp_path, target_r=1.0, extend_r=0.5,
                          thesis="always", approach_frac=0.5)
    unk, unk_rows = _geom("scalp_default", tmp_path, target_r=1.0, extend_r=0.5,
                          thesis="unknown", approach_frac=0.5)
    assert unk_rows == base_rows
    assert all("final_target_r" in r for r in ext_rows)
    for mode in ("atr_rescale", "stall_pull_in"):
        out, rows = _geom("scalp_default", tmp_path, target_r=1.0, retarget_mode=mode,
                          retarget_stall_bars=1)
        assert out["tp_geometry"]["read_state"] == "measured"


@pytest.mark.parametrize("cid", LEVER_CASES)
def test_revision_without_a_target_is_refused_not_run_inert(cid, tmp_path):
    with pytest.raises(ValueError, match="need a target to revise"):
        _geom(cid, tmp_path, extend_r=1.0, thesis="always")


def test_revision_on_a_capped_legacy_target_is_allowed(tmp_path):
    out, _ = _run_case("trend_capped_tp", tmp_path,
                       extra=dict(tp_geometry=Spec(extend_r=1.0, thesis="always",
                                                   approach_frac=0.5)))
    assert out["tp_geometry"]["read_state"] == "measured"


def test_spec_validation():
    for bad in (dict(thesis="maybe"), dict(retarget_mode="nope"), dict(target_r=0.0),
                dict(approach_frac=0.0), dict(retarget_stall_bars=0)):
        with pytest.raises(ValueError):
            Spec(**bad)


@pytest.mark.parametrize("script", ["backtest_trend.py", "backtest_pullback.py",
                                    "backtest_ict_scalp.py"])
def test_help_lists_the_flags(script):
    import subprocess
    out = subprocess.run([sys.executable, str(_REPO / "scripts" / script), "--help"],
                         capture_output=True, text=True, check=True).stdout
    for flag in ("--tp-target-r", "--tp-extend-r", "--tp-approach-frac",
                 "--tp-max-extends", "--tp-thesis", "--tp-retarget-mode",
                 "--tp-retarget-stall-bars"):
        assert flag in out, (script, flag)


@pytest.mark.parametrize("script", ["backtest_trend.py", "backtest_pullback.py"])
def test_cli_refuses_revision_without_target(script):
    import subprocess
    r = subprocess.run([sys.executable, str(_REPO / "scripts" / script),
                        "--data", str(_REPO / "data" / "backtest_candles.csv"),
                        "--resample", "5min", "--tp-extend-r", "1.0"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "need a target to revise" in r.stderr


@pytest.mark.parametrize("cid", ["trend_capped_tp", "pullback_capped_tp", "scalp_default"])
def test_report_only_changes_no_exit(cid, tmp_path):
    """`--tp-report` is for reading a base beside a lever cell: every exit, R and
    count must equal the default run; only the geometry fields are added."""
    base, base_rows = _run_case(cid, tmp_path)
    rep, rep_rows = _run_case(cid, tmp_path, extra=dict(tp_geometry=Spec(report=True)))
    g = rep.pop("tp_geometry")
    assert g["total_extends"] == 0 and g["total_retargets"] == 0
    rep.pop("params", None), base.pop("params", None)
    assert rep == base
    drop = {"final_target_r", "n_extends", "n_retargets", "exit_r", "tp_exit_r"}

    def _strip(r):
        out = {k: v for k, v in r.items() if k not in drop}
        if isinstance(out.get("meta"), dict):        # the scalp carries them in meta too
            out["meta"] = {k: v for k, v in out["meta"].items() if k not in drop}
        return out
    assert [_strip(r) for r in rep_rows] == base_rows


if __name__ == "__main__" and "--capture" in sys.argv:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        g = _capture(Path(d))
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(g, sort_keys=True, indent=1))
    print({k: len(v["rows"]) for k, v in g.items()})
