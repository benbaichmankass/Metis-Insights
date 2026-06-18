"""Unit tests for the research-sweep orchestrator's pure logic (2026-06-18).

Covers run enumeration (base + variants + ablations), delta application
(drop/set), args→CLI-flag conversion, and the ablation attribution (Δ vs base).
The harness/fold-report subprocess calls are NOT exercised here (those run on the
trainer); these pin the glue that decides WHAT gets run and how results compose.
"""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "research_sweep", Path(__file__).resolve().parents[2] / "scripts" / "ops" / "research_sweep.py")
rs = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rs        # register so @dataclass resolution works under importlib
_spec.loader.exec_module(rs)


_STUDY = {
    "name": "t", "harness": "backtest_pullback.py",
    "base": {"data": "data/ETHUSDT_15m.csv", "resample": "2h", "symbol": "ETHUSDT",
             "args": {"trend-lookback": 40, "trail-mult": 5.0, "adx-min": 25, "min-confidence": 0.0}},
    "variants": [{"name": "adx_none", "drop": ["adx-min"]},
                 {"name": "adx20", "set": {"adx-min": 20}}],
    "ablations": [{"name": "regime_gate", "drop": ["adx-min", "adx-max"]}],
}


def test_build_runs_enumerates_base_variants_ablations():
    runs = rs.build_runs(_STUDY)
    assert [r.name for r in runs] == ["base", "adx_none", "adx20", "regime_gate"]
    assert [r.kind for r in runs] == ["base", "variant", "variant", "ablation"]
    # every run inherits the base common fields
    assert all(r.symbol == "ETHUSDT" and r.resample == "2h" for r in runs)


def test_apply_delta_drop_and_set():
    base = {"adx-min": 25, "trail-mult": 5.0}
    assert rs._apply_delta(base, {"drop": ["adx-min"]}) == {"trail-mult": 5.0}
    assert rs._apply_delta(base, {"set": {"adx-min": 20}}) == {"adx-min": 20, "trail-mult": 5.0}
    # base dict is not mutated
    assert base == {"adx-min": 25, "trail-mult": 5.0}


def test_base_run_is_unmodified_base_args():
    runs = rs.build_runs(_STUDY)
    base = next(r for r in runs if r.name == "base")
    assert base.args == _STUDY["base"]["args"]
    # the ablation drops adx-min; the variant adx20 overrides it
    abl = next(r for r in runs if r.name == "regime_gate")
    assert "adx-min" not in abl.args
    assert next(r for r in runs if r.name == "adx20").args["adx-min"] == 20


def test_args_to_flags_formats_numbers_and_skips_none():
    flags = rs._args_to_flags({"adx-min": 25, "trail-mult": 5.0, "min-confidence": 0.0})
    assert "--adx-min" in flags
    # int-valued floats render compactly ("5" not "5.0"); argparse float() parses it fine
    assert flags[flags.index("--adx-min") + 1] == "25"
    assert flags[flags.index("--trail-mult") + 1] == "5"
    assert flags[flags.index("--min-confidence") + 1] == "0"
    # a non-integer float keeps its decimal
    assert rs._args_to_flags({"pullback-frac": 0.5})[1] == "0.5"
    # None is skipped
    assert rs._args_to_flags({"adx-max": None}) == []


def test_attribution_delta_vs_base():
    rows = [
        {"name": "base", "kind": "base", "net_r_base": 63.0, "tier": "live_ready"},
        {"name": "regime_gate", "kind": "ablation", "net_r_base": 59.0, "tier": "paper_ready"},
        {"name": "adx20", "kind": "variant", "net_r_base": 50.0, "tier": "paper_ready"},
    ]
    attr = rs._attribution(rows)
    assert len(attr) == 1                         # only the ablation row
    a = attr[0]
    assert a["component"] == "regime_gate"
    assert a["delta_net_r"] == 4.0               # 63 - 59: ADX gate adds +4R
    assert a["base_tier"] == "live_ready" and a["ablated_tier"] == "paper_ready"


def test_attribution_empty_without_numeric_base():
    rows = [{"name": "base", "kind": "base", "net_r_base": None, "tier": None},
            {"name": "regime_gate", "kind": "ablation", "net_r_base": 59.0, "tier": "paper_ready"}]
    assert rs._attribution(rows) == []
