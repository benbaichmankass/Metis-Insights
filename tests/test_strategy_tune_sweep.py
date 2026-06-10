"""Tests for the M8 canonical parameter-sweep harness
(``scripts/ml/strategy_tune_sweep.py``).

The orchestrator is exercised end-to-end against a *fake harness* runner so the
suite needs no candle data and no real backtester — it verifies the parts M8
owns: search-space grammar, target/recipe parsing, registry dispatch, metric
normalization across heterogeneous harness output, best-pick + advisory
recommendation, and the JSON/Markdown emission.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "strategy_tune_sweep",
    Path(__file__).resolve().parents[1] / "scripts" / "ml" / "strategy_tune_sweep.py",
)
sweep = importlib.util.module_from_spec(_SPEC)
# Register before exec so @dataclass can resolve cls.__module__ during decoration.
sys.modules["strategy_tune_sweep"] = sweep
_SPEC.loader.exec_module(sweep)  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# parse_target
# --------------------------------------------------------------------------- #
def test_parse_target_splits_file_strategy_param():
    f, s, p = sweep.parse_target("config/strategies.yaml::vwap.threshold")
    assert (f, s, p) == ("config/strategies.yaml", "vwap", "threshold")


def test_parse_target_dotted_param_keeps_tail():
    # A nested param keeps everything after the first dot.
    _, s, p = sweep.parse_target("config/strategies.yaml::fade_breakout.exit.tp_r")
    assert s == "fade_breakout" and p == "exit.tp_r"


@pytest.mark.parametrize("bad", ["no_colon.field", "config.yaml::nodot", "f::.param", "f::strat."])
def test_parse_target_rejects_malformed(bad):
    with pytest.raises(ValueError):
        sweep.parse_target(bad)


# --------------------------------------------------------------------------- #
# parse_search_space grammar
# --------------------------------------------------------------------------- #
def test_search_space_explicit_grid():
    g = sweep.parse_search_space("[0.8, 1.0, 1.2]", current_value=None)
    assert g == [0.8, 1.0, 1.2]


def test_search_space_grid_kind_prefix():
    assert sweep.parse_search_space("grid [1, 2, 3]", None) == [1.0, 2.0, 3.0]


def test_search_space_uniform_count():
    g = sweep.parse_search_space("uniform [0.0, 1.0]", None, samples=5)
    assert g == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_search_space_log_uniform_is_geometric():
    g = sweep.parse_search_space("log-uniform [0.001, 0.1]", None, samples=3)
    # geometric midpoint of 0.001..0.1 is 0.01
    assert g[0] == pytest.approx(0.001)
    assert g[1] == pytest.approx(0.01)
    assert g[2] == pytest.approx(0.1)


def test_search_space_colon_range():
    g = sweep.parse_search_space("0:0.6:0.2", None)
    assert g == pytest.approx([0.0, 0.2, 0.4, 0.6])


def test_search_space_folds_in_current_value_and_dedups():
    g = sweep.parse_search_space("[0.8, 1.2]", current_value=1.0)
    assert g == [0.8, 1.0, 1.2]
    # current value already present → no duplicate row
    g2 = sweep.parse_search_space("[0.8, 1.0, 1.2]", current_value=1.0)
    assert g2 == [0.8, 1.0, 1.2]


def test_search_space_log_uniform_rejects_nonpositive_lo():
    with pytest.raises(ValueError):
        sweep.parse_search_space("log-uniform [0.0, 0.1]", None)


@pytest.mark.parametrize("bad", ["", "   ", "garbage", "uniform [1]", "uniform [2, 1]", "0:1"])
def test_search_space_rejects_bad(bad):
    with pytest.raises(ValueError):
        sweep.parse_search_space(bad, None)


# --------------------------------------------------------------------------- #
# registry dispatch
# --------------------------------------------------------------------------- #
def test_resolve_spec_research_harness_per_value():
    r = sweep.TuneRecipe("config/strategies.yaml::fade_breakout.min_confidence",
                         0.0, "[0,0.2]", "scripts/backtest_fade.py")
    spec = sweep.resolve_spec(r)
    assert spec.flag == "--min-confidence" and not spec.native_sweep_flag


def test_resolve_spec_vwap_alias_native_sweep():
    r = sweep.TuneRecipe("config/strategies.yaml::vwap.threshold",
                         1.0, "[0.8,1.0]", "scripts/backtest_vwap.py")  # aliased name
    spec = sweep.resolve_spec(r)
    assert spec.native_sweep_flag == "--threshold-sweep"
    assert spec.native_rows_key == "threshold_comparison"


def test_resolve_spec_unknown_pair_raises_with_pointer():
    r = sweep.TuneRecipe("config/strategies.yaml::vwap.nonexistent",
                         1.0, "[1]", "scripts/backtest_vwap.py")
    with pytest.raises(KeyError) as ei:
        sweep.resolve_spec(r)
    assert "docs/strategy-tuning.md" in str(ei.value)


# --------------------------------------------------------------------------- #
# normalization across heterogeneous output
# --------------------------------------------------------------------------- #
def test_normalize_reads_research_r_keys():
    row = sweep.normalize_row(0.2, {
        "total_trades": 40, "win_rate_pct": 55.0,
        "net_total_r": 3.2, "net_expectancy_r": 0.08, "max_drawdown_r": -2.1,
    })
    assert row == {"value": 0.2, "trades": 40.0, "win_rate_pct": 55.0,
                   "net_total": 3.2, "net_expectancy": 0.08, "max_drawdown": -2.1}


def test_normalize_reads_core_pnl_keys_and_missing_is_none():
    row = sweep.normalize_row(1.0, {"total_pnl": 120.0, "win_rate": 60.0})
    assert row["net_total"] == 120.0 and row["win_rate_pct"] == 60.0
    assert row["net_expectancy"] is None and row["trades"] is None


# --------------------------------------------------------------------------- #
# end-to-end run against a fake harness
# --------------------------------------------------------------------------- #
def _fake_runner_factory(by_value):
    """Return a runner that maps each per-value invocation to a canned summary,
    keyed off the value passed via the harness flag in argv."""

    def runner(argv):
        # per-value harness: value is the token after the flag
        for flag in ("--min-confidence", "--threshold-sweep"):
            if flag in argv:
                idx = argv.index(flag)
                if flag == "--threshold-sweep":
                    return by_value  # native: return the whole grid dict
                val = float(argv[idx + 1])
                return by_value[val]
        raise AssertionError(f"no known flag in {argv}")

    return runner


def test_run_sweep_per_value_picks_best_and_recommends(tmp_path):
    recipe = sweep.TuneRecipe(
        "config/strategies.yaml::fade_breakout.min_confidence",
        current_value=0.0, search_space="[0.0, 0.2, 0.4]",
        harness="scripts/backtest_fade.py",
    )
    canned = {
        0.0: {"total_trades": 50, "win_rate_pct": 48, "net_total_r": -1.0, "net_expectancy_r": -0.02},
        0.2: {"total_trades": 30, "win_rate_pct": 56, "net_total_r": 4.5, "net_expectancy_r": 0.15},
        0.4: {"total_trades": 10, "win_rate_pct": 70, "net_total_r": 6.0, "net_expectancy_r": 0.60},
    }
    result = sweep.run_sweep(recipe, data=None, fee_bps=7.5, samples=9,
                             runner=_fake_runner_factory(canned))

    assert len(result["grid"]) == 3
    # best by total = 0.4 (highest net_total, no min-trade floor)
    assert result["best_by_net_total"]["value"] == 0.4
    # best by expectancy with >=20 trades floor excludes 0.4 (only 10 trades) -> 0.2
    assert result["best_by_net_expectancy_minN"]["value"] == 0.2
    rec = result["recommendation"]
    assert rec["tier"] == 3 and rec["action"] == "propose_value"
    assert rec["proposed_value"] == 0.2          # prefers the expectancy optimum
    assert rec["beats_baseline"] is True
    assert rec["yaml_line"] == "config/strategies.yaml::fade_breakout.min_confidence : 0.2"
    # baseline row captured at current_value
    assert result["baseline_row"]["value"] == 0.0


def test_run_sweep_native_sweep_rekeys_rows():
    recipe = sweep.TuneRecipe(
        "config/strategies.yaml::vwap.threshold",
        current_value=1.0, search_space="[0.8, 1.0, 1.2]",
        harness="scripts/backtest_vwap.py",
    )
    native = {"threshold_comparison": [
        {"entry_std_threshold": 0.8, "total_trades": 40, "net_total_r": 1.0, "net_expectancy_r": 0.02},
        {"entry_std_threshold": 1.0, "total_trades": 35, "net_total_r": 2.5, "net_expectancy_r": 0.07},
        {"entry_std_threshold": 1.2, "total_trades": 25, "net_total_r": 0.5, "net_expectancy_r": 0.02},
    ]}
    result = sweep.run_sweep(recipe, data=None, fee_bps=7.5, samples=9,
                             runner=_fake_runner_factory(native))
    assert [r["value"] for r in result["grid"]] == [0.8, 1.0, 1.2]
    assert result["best_by_net_total"]["value"] == 1.0


def test_run_sweep_insufficient_evidence_when_grid_empty_metrics():
    recipe = sweep.TuneRecipe(
        "config/strategies.yaml::fade_breakout.min_confidence",
        current_value=None, search_space="[0.1, 0.2]",
        harness="scripts/backtest_fade.py",
    )
    canned = {0.1: {"total_trades": 5}, 0.2: {"total_trades": 5}}  # no net metrics
    result = sweep.run_sweep(recipe, data=None, fee_bps=7.5, samples=9,
                             runner=_fake_runner_factory(canned))
    assert result["recommendation"]["action"] == "insufficient_evidence"


# --------------------------------------------------------------------------- #
# recipe ingestion + output emission
# --------------------------------------------------------------------------- #
def test_load_recipe_from_review_packet(tmp_path):
    packet = {
        "strategy": "vwap", "proposed_action": "tune",
        "tune_recipe": {
            "target": "config/strategies.yaml::vwap.threshold",
            "current_value": 1.0, "search_space": "log-uniform [0.5, 2.0]",
            "harness": "scripts/backtest_vwap.py", "evidence_window_days": 90,
            "note": "ties to the long-side overtrade pattern from S-STRAT-IMPROVE-S2.",
        },
    }
    p = tmp_path / "vwap.json"
    p.write_text(json.dumps(packet))
    r = sweep.load_recipe(p)
    assert r.strategy == "vwap" and r.param == "threshold"
    assert r.current_value == 1.0 and r.evidence_window_days == 90


def test_load_recipe_bare_object(tmp_path):
    p = tmp_path / "recipe.json"
    p.write_text(json.dumps({"target": "config/strategies.yaml::fade_breakout.min_confidence",
                             "current_value": 0.0, "search_space": "[0,0.2]",
                             "harness": "scripts/backtest_fade.py"}))
    r = sweep.load_recipe(p)
    assert r.strategy == "fade_breakout"


def test_load_recipe_without_tune_recipe_raises(tmp_path):
    p = tmp_path / "hold.json"
    p.write_text(json.dumps({"strategy": "vwap", "proposed_action": "hold"}))
    with pytest.raises(ValueError):
        sweep.load_recipe(p)


def test_write_outputs_emits_json_and_md(tmp_path):
    recipe = sweep.TuneRecipe(
        "config/strategies.yaml::fade_breakout.min_confidence",
        0.0, "[0.0, 0.2]", "scripts/backtest_fade.py",
    )
    canned = {
        0.0: {"total_trades": 50, "net_total_r": -1.0, "net_expectancy_r": -0.02},
        0.2: {"total_trades": 30, "net_total_r": 4.5, "net_expectancy_r": 0.15},
    }
    result = sweep.run_sweep(recipe, data=None, fee_bps=7.5, samples=9,
                             runner=_fake_runner_factory(canned))
    json_path, md_path = sweep.write_outputs(result, tmp_path)
    assert json_path.exists() and md_path.exists()
    assert json_path.name == "fade_breakout__min_confidence.json"
    md = md_path.read_text()
    assert "Tune sweep" in md and "Tier-3" in md
    # round-trips
    assert json.loads(json_path.read_text())["param"] == "min_confidence"


def test_build_invocation_forwards_fixed_args_per_value():
    spec = sweep._REGISTRY[("backtest_trend.py", "min_confidence")]
    argv = sweep.build_invocation(
        spec, value=0.3, data="data/btc_1h_multiyear.csv", fee_bps=7.5,
        window_days=None, fixed_args=["--timeframe", "1h", "--donchian", "20", "--trail-mult", "5.0"],
    )
    # pinned live params present, and the swept value still injected
    assert "--timeframe" in argv and argv[argv.index("--timeframe") + 1] == "1h"
    assert "--trail-mult" in argv and "--donchian" in argv
    assert argv[argv.index("--min-confidence") + 1] == "0.3"


def test_fixed_args_coercion_from_string_and_list():
    assert sweep._coerce_fixed_args("--timeframe 1h --donchian 20") == [
        "--timeframe", "1h", "--donchian", "20"]
    assert sweep._coerce_fixed_args(["--trail-mult", 5.0]) == ["--trail-mult", "5.0"]
    assert sweep._coerce_fixed_args(None) == []


def test_load_recipe_reads_fixed_args(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({
        "target": "config/strategies.yaml::trend_donchian.min_confidence",
        "current_value": 0.3, "search_space": "[0.0, 0.3, 0.5]",
        "harness": "scripts/backtest_trend.py",
        "fixed_args": ["--timeframe", "1h", "--donchian", "20"],
    }))
    r = sweep.load_recipe(p)
    assert r.fixed_args == ["--timeframe", "1h", "--donchian", "20"]


def test_extract_json_tolerates_leading_table_text():
    out = "some table text\nmore lines\n{\"total_pnl\": 12.0}\n"
    assert sweep._extract_json(out) == {"total_pnl": 12.0}


def test_extract_json_skips_python_dict_repr_in_table():
    # The trend/fade harnesses print a table containing a Python-dict repr
    # (single quotes — not JSON) before the real json.dumps payload.
    out = (
        "trend_donchian backtest\n"
        "config: {'donchian': 20, 'trail_mult': 5.0}\n"
        "{\n  \"total_trades\": 559,\n  \"net_total_r\": 56.2,\n  \"net_expectancy_r\": 0.101\n}\n"
    )
    got = sweep._extract_json(out)
    assert got["total_trades"] == 559 and got["net_total_r"] == 56.2


def test_extract_json_picks_last_toplevel_object():
    out = "{\"a\": 1}\nnoise\n{\"b\": 2}\n"
    assert sweep._extract_json(out) == {"b": 2}


def test_extract_json_returns_outer_not_nested_object():
    # Regression: the real trend payload nests by_year/by_outcome; a "last object
    # found" scan grabbed the inner {"trades":0,...} (which has trades but no net
    # metrics — the exact first-real-run symptom). Must return the OUTER object.
    out = (
        "trend table\n"
        "{\n"
        '  "total_trades": 53,\n'
        '  "net_total_r": 56.2,\n'
        '  "net_expectancy_r": 0.101,\n'
        '  "by_year": {"2023": {"trades": 10, "net_r": 1.2}},\n'
        '  "by_outcome": {"win": {"trades": 30}}\n'
        "}\n"
    )
    got = sweep._extract_json(out)
    assert got["total_trades"] == 53 and got["net_total_r"] == 56.2
    assert "by_year" in got


# --------------------------------------------------------------------------- #
# S2 — walk-forward / OOS split
# --------------------------------------------------------------------------- #
def test_build_invocation_injects_window_flags():
    spec = sweep._REGISTRY[("backtest_trend.py", "min_confidence")]
    argv = sweep.build_invocation(
        spec, value=0.3, data="d.csv", fee_bps=7.5, window_days=None,
        window=("2020-01-01", "2025-01-01"),
    )
    assert argv[argv.index("--start") + 1] == "2020-01-01"
    assert argv[argv.index("--end") + 1] == "2025-01-01"


def test_build_invocation_window_open_ends_omit_flag():
    spec = sweep._REGISTRY[("backtest_trend.py", "min_confidence")]
    argv = sweep.build_invocation(
        spec, value=0.3, data="d.csv", fee_bps=7.5, window_days=None,
        window=(None, "2025-01-01"),
    )
    assert "--start" not in argv and argv[argv.index("--end") + 1] == "2025-01-01"


def _wf_runner_factory(by_window_value):
    """Runner keyed on (window-label, value). Window label derived from --end:
    train ends at the split date, oos has no --end here (open) so label by --start."""

    def runner(argv):
        val = float(argv[argv.index("--min-confidence") + 1])
        # train window passes --end SPLIT; oos passes --start SPLIT
        label = "train" if "--end" in argv else "oos"
        return by_window_value[(label, val)]

    return runner


def test_run_sweep_walk_forward_gates_on_oos():
    recipe = sweep.TuneRecipe(
        "config/strategies.yaml::trend_donchian.min_confidence",
        current_value=0.3, search_space="[0.3, 0.6]",
        harness="scripts/backtest_trend.py",
    )
    canned = {
        # value 0.3: great in-sample, poor OOS
        ("train", 0.3): {"total_trades": 300, "net_total_r": 80, "net_expectancy_r": 0.26},
        ("oos", 0.3): {"total_trades": 100, "net_total_r": 5, "net_expectancy_r": 0.05},
        # value 0.6: modest in-sample, best OOS
        ("train", 0.6): {"total_trades": 250, "net_total_r": 60, "net_expectancy_r": 0.24},
        ("oos", 0.6): {"total_trades": 90, "net_total_r": 30, "net_expectancy_r": 0.33},
    }
    wf = sweep.WalkForward(oos_start="2025-01-01")
    result = sweep.run_sweep(recipe, data=None, fee_bps=7.5, samples=9,
                             runner=_wf_runner_factory(canned), wf=wf)
    assert result["metric_basis"] == "oos"
    assert result["walk_forward"]["oos_start"] == "2025-01-01"
    # top-level row metrics are OOS; in-sample nested under train
    row06 = next(r for r in result["grid"] if r["value"] == 0.6)
    assert row06["net_total"] == 30 and row06["train"]["net_total"] == 60
    # OOS-best is 0.6 (30 > 5), NOT the in-sample-best 0.3
    assert result["best_by_net_total"]["value"] == 0.6
    rec = result["recommendation"]
    assert rec["proposed_value"] == 0.6 and rec["metric_basis"] == "oos"
    assert rec["train_oos_consistent"] is True  # 0.6 positive in both


def test_run_sweep_full_sample_flags_not_oos_validated():
    recipe = sweep.TuneRecipe(
        "config/strategies.yaml::fade_breakout.min_confidence",
        current_value=0.0, search_space="[0.0, 0.2]",
        harness="scripts/backtest_fade.py",
    )

    def runner(argv):
        v = float(argv[argv.index("--min-confidence") + 1])
        return {"total_trades": 50, "net_total_r": v * 10, "net_expectancy_r": v}

    result = sweep.run_sweep(recipe, data=None, fee_bps=7.5, samples=9, runner=runner, wf=None)
    assert result["metric_basis"] == "full_sample"
    assert result["walk_forward"] is None
    assert "IN-SAMPLE" in result["recommendation"]["detail"] or \
           "walk-forward split" in result["recommendation"]["detail"]


# --------------------------------------------------------------------------- #
# S3 — k-fold anchored walk-forward
# --------------------------------------------------------------------------- #
def test_fold_windows_kfold_anchored_count_and_anchor():
    kf = sweep.KFold(wf_start="2021-01-01", wf_end="2026-01-01", folds=4, train_frac=0.4)
    fw = sweep.fold_windows(None, kf)
    assert len(fw) == 4
    # every fold trains from the span start (anchored/expanding)
    assert all(tw[0] == "2021-01-01" for tw, ow in fw)
    # OOS segments are contiguous and ordered
    oos_starts = [ow[0] for tw, ow in fw]
    assert oos_starts == sorted(oos_starts)
    # first OOS starts after the initial train fraction (~2 of 5 years in)
    assert fw[0][1][0] > "2022-01-01"


def test_fold_windows_single_split_one_fold():
    wf = sweep.WalkForward(oos_start="2025-01-01", oos_end="2026-01-01")
    fw = sweep.fold_windows(wf, None)
    assert fw == [((None, "2025-01-01"), ("2025-01-01", "2026-01-01"))]


def test_fold_windows_none_when_no_wf():
    assert sweep.fold_windows(None, None) is None


@pytest.mark.parametrize("kf", [
    sweep.KFold("2026-01-01", "2021-01-01", 4, 0.4),   # end before start
    sweep.KFold("2021-01-01", "2026-01-01", 0, 0.4),   # zero folds
    sweep.KFold("2021-01-01", "2026-01-01", 4, 1.5),   # bad train_frac
])
def test_fold_windows_kfold_rejects_bad(kf):
    with pytest.raises(ValueError):
        sweep.fold_windows(None, kf)


def _kfold_runner_factory(by_fold_value):
    """Runner keyed on (fold_index, window-label, value). Fold index derived from
    the OOS-start date in argv; window label from presence of matching dates."""

    def runner(argv):
        val = float(argv[argv.index("--min-confidence") + 1])
        # train windows carry --end == a fold boundary; oos carry --start == boundary
        # We encode folds by the boundary date present.
        start = argv[argv.index("--start") + 1] if "--start" in argv else None
        end = argv[argv.index("--end") + 1] if "--end" in argv else None
        label = "train" if end and start == "2021-01-01" and end != "2026-01-01" else "oos"
        # fold key = the oos boundary date
        key_date = end if label == "train" else start
        return by_fold_value[(key_date, label, val)]

    return runner


def test_run_sweep_kfold_aggregates_and_gates_on_robustness():
    recipe = sweep.TuneRecipe(
        "config/strategies.yaml::trend_donchian.min_confidence",
        current_value=0.3, search_space="[0.3, 0.6]",
        harness="scripts/backtest_trend.py",
    )
    kf = sweep.KFold(wf_start="2021-01-01", wf_end="2026-01-01", folds=2, train_frac=0.4)
    fw = sweep.fold_windows(None, kf)
    # fold k keys on its OOS-start date (== that fold's train-end and oos-start)
    b0 = fw[0][1][0]  # fold0 boundary
    b1 = fw[1][1][0]  # fold1 boundary

    def summ(net):
        return {"total_trades": 60, "net_total_r": net, "net_expectancy_r": net / 60}

    canned = {
        # value 0.3: fold0 oos positive, fold1 oos NEGATIVE → not robust
        (b0, "train", 0.3): summ(40), (b0, "oos", 0.3): summ(10),
        (b1, "train", 0.3): summ(35), (b1, "oos", 0.3): summ(-8),
        # value 0.6: positive in BOTH oos folds → robust
        (b0, "train", 0.6): summ(30), (b0, "oos", 0.6): summ(6),
        (b1, "train", 0.6): summ(28), (b1, "oos", 0.6): summ(9),
    }
    result = sweep.run_sweep(recipe, data=None, fee_bps=7.5, samples=9,
                             runner=_kfold_runner_factory(canned), kfold=kf)
    assert result["metric_basis"] == "oos"
    assert result["walk_forward"]["scheme"] == "kfold_anchored"
    row06 = next(r for r in result["grid"] if r["value"] == 0.6)
    row03 = next(r for r in result["grid"] if r["value"] == 0.3)
    # aggregate OOS net = sum across folds
    assert row06["net_total"] == 15 and row06["folds_positive"] == 2 and row06["n_folds"] == 2
    assert row03["net_total"] == 2 and row03["folds_positive"] == 1  # one fold negative
    rec = result["recommendation"]
    # best net_total = 0.6 (15 > 2); robust because positive in all folds
    assert rec["proposed_value"] == 0.6
    assert rec["robust"] is True and rec["folds_positive"] == 2
