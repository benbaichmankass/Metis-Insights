"""The squeeze harness can express `vol_trail`, and the rule has ONE definition.

`BL-20260817-SQUEEZE-VOLTRAIL-HARNESS-GAP-DISPOSITION-RESTS-ON-A-FLOOR-VS-TARGET-CONFLATION`.

`scripts/backtest_squeeze.py` had `--trail-mult` but no `--trail-vol-*`, so the
`vol_trail` column was UNREACHABLE for the squeeze family and its matrix cell
read `blocked:no_harness_levers`. Two things are pinned here.

**1. Reachability, end to end.** A flag that parses is not a lever. The sweep
builds one argv per cell and hands it to whichever harness the family maps to,
so the test that matters is that `cells_for` emits vol_trail cells for
`squeeze`, that the flag SPELLINGS match what it emits, and that an armed run
actually changes the result. Measured on `data/backtest_candles.csv`: 100 trades
levers-off vs 107 / 117 / 120 for the three emitted cells.

**2. `trail_decay` must NOT come with it.** The two levers shared one
`fam in ("donchian", "pullback")` gate, so adding squeeze there would also have
emitted four `--trail-decay-*` cells the squeeze harness does not declare —
argparse rejects the argv, or worse a tolerant harness grades a lever it never
applied. Two levers, two reachability questions, two gates.

**3. One definition of the firing rule.** It had already been written twice
(`backtest_trend.py::_effective_trail_mult` plus an inline copy in
`backtest_pullback.py`); a third was about to be added here. It now lives in
`src/research/trail_levers.py`, and `backtest_trend.py` keeps the private name
as an alias because `m20_trail_attribution.py` resolves it dynamically by name.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

REPO = Path(__file__).resolve().parents[1]
CANDLES = REPO / "data" / "backtest_candles.csv"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 1. THE RULE HAS ONE HOME.

def test_trail_lever_rule_is_not_re_derived_in_the_squeeze_harness():
    """The squeeze harness must IMPORT the rule, not restate it.

    A lever whose firing condition is stated in two places is free to drift, and
    a drifted exit lever silently re-grades every cell measured against it.
    """
    src = (REPO / "scripts" / "backtest_squeeze.py").read_text()
    assert "from src.research.trail_levers import" in src
    # The tail test itself must NOT appear inline here — that is the shape being
    # prevented (backtest_pullback.py has exactly this inline).
    assert "trail_vol_above_pctl > 0.0 and float(" not in src, (
        "the squeeze harness re-derived the vol-tail test inline instead of "
        "importing it — a third copy of the rule")


def test_trend_keeps_the_private_alias_resolvable():
    """`m20_trail_attribution.py` loads the harness dynamically and refers to
    `_effective_trail_mult` BY NAME, so dropping the alias would break a
    consumer no import graph would reveal."""
    bt = _load("scripts/backtest_trend.py", "_bt_alias_probe")
    assert hasattr(bt, "_effective_trail_mult")
    # Compare against the harness's OWN imported symbol, not a separately
    # file-loaded copy: `spec_from_file_location` builds a NEW module object, so
    # its function would fail an identity check against the imported one even
    # though the source is identical. What matters is that the private alias is
    # the IMPORTED function rather than a redefinition living in the harness.
    assert bt._effective_trail_mult is bt.effective_trail_mult, (
        "the alias must BE the imported shared function, not a second copy")
    assert bt.effective_trail_mult.__module__.endswith("trail_levers"), (
        f"expected the rule to come from trail_levers, got "
        f"{bt.effective_trail_mult.__module__}")


def test_vol_trail_armed_requires_both_a_tail_and_a_tight_mult():
    """A half-configured lever is not a declaration.

    A tail with no tight mult, or a mult with no tail, would otherwise tighten
    against a bound nobody set.
    """
    tl = _load("src/research/trail_levers.py", "_tl_armed")
    assert tl.vol_trail_armed(1.5, 0.8, 0.0) is True
    assert tl.vol_trail_armed(1.5, 0.0, 0.1) is True
    assert tl.vol_trail_armed(0.0, 0.8, 0.0) is False, "tail with no tight mult"
    assert tl.vol_trail_armed(1.5, 0.0, 0.0) is False, "tight mult with no tail"
    assert tl.vol_trail_armed(0.0, 0.0, 0.0) is False


def test_the_two_tighteners_compose_by_minimum_not_by_sum():
    """"Tightest wins" is the documented contract, and it is load-bearing:
    composing by MAX makes the vol lever inert (measured — flipping min to max
    returned the levers-off trade count exactly)."""
    tl = _load("src/research/trail_levers.py", "_tl_compose")
    pctl = pd.Series([0.95] * 5)
    # decay would set 2.5, vol would set 1.5 -> 1.5 must win
    got = tl.effective_trail_mult(3.5, 3.0, 0, True, 2.0, 0, 2.5,
                                  True, pctl, 0, 0.8, 0.0, 1.5)
    assert got == 1.5
    # An UNDEFINED percentile (window unfilled) leaves the vol half inert
    # rather than ranking the bar as calm.
    got_nan = tl.effective_trail_mult(3.5, 0.0, 0, False, 0.0, 0, 0.0,
                                      True, pd.Series([float("nan")]), 0,
                                      0.8, 0.0, 1.5)
    assert got_nan == 3.5


# --------------------------------------------------------------------------
# 2. THE SWEEP EMITS IT FOR SQUEEZE — AND EMITS NO DECAY.

def test_cells_for_emits_vol_trail_for_squeeze_and_no_trail_decay():
    sw = _load("scripts/research/m20_fleet_exit_sweep.py", "_sw_cells")
    cfg = {"trail_mult": 3.5, "timeframe": "4h", "symbols": ["BTCUSDT"]}
    cells = sw.cells_for(cfg, "squeeze", skipped=[])
    by = {}
    for tag, lever, argv in cells:
        by.setdefault(lever, []).append((tag, argv))
    assert len(by.get("vol_trail", [])) == 3, by.keys()
    assert "trail_decay" not in by, (
        "squeeze got trail_decay cells, whose --trail-decay-* flags "
        "backtest_squeeze.py does not declare — the argv would be rejected, or "
        "a tolerant harness would grade a lever it never applied")


def test_the_emitted_flag_spellings_are_the_ones_the_harness_declares():
    """A divergent spelling would make the family silently unreachable again."""
    sw = _load("scripts/research/m20_fleet_exit_sweep.py", "_sw_spell")
    cfg = {"trail_mult": 3.5, "timeframe": "4h", "symbols": ["BTCUSDT"]}
    emitted = {a for _t, lever, argv in sw.cells_for(cfg, "squeeze", skipped=[])
               if lever == "vol_trail" for a in argv if a.startswith("--")}
    harness = (REPO / "scripts" / "backtest_squeeze.py").read_text()
    for flag in emitted:
        assert f'"{flag}"' in harness, (
            f"the sweep emits {flag} but backtest_squeeze.py does not declare "
            f"it — this is exactly the blocked:no_harness_levers state")


def test_donchian_and_pullback_are_unchanged_by_the_gate_split():
    """The split must not have moved anything for the families that already had
    both levers — that is the regression risk of separating the gates."""
    sw = _load("scripts/research/m20_fleet_exit_sweep.py", "_sw_regress")
    cfg = {"trail_mult": 3.5, "timeframe": "1h", "symbols": ["BTCUSDT"]}
    for fam in ("donchian", "pullback"):
        by = {}
        for tag, lever, argv in sw.cells_for(cfg, fam, skipped=[]):
            by.setdefault(lever, []).append(tag)
        assert len(by.get("vol_trail", [])) == 3, fam
        assert len(by.get("trail_decay", [])) == 4, fam


# --------------------------------------------------------------------------
# 3. END TO END: the lever is inert at defaults and BITES when armed.

@pytest.mark.skipif(not CANDLES.exists(), reason="sample candles absent")
def test_lever_is_inert_at_defaults_and_bites_when_armed():
    """Runs the real harness. `total_trades` is the coarse signal that the
    managed-bar arithmetic actually changed; a flag that parses and changes
    nothing is decorative, which is how a cell gets a verdict it never earned.
    """
    def run(extra):
        out = REPO / "runtime_logs" / "_test_sq.json"
        out.parent.mkdir(exist_ok=True)
        cmd = [sys.executable, str(REPO / "scripts" / "backtest_squeeze.py"),
               "--data", str(CANDLES), "--timeframe", "1m", "--symbol",
               "BTCUSDT", "--json", str(out)] + extra
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                           cwd=str(REPO))
        assert r.returncode == 0, r.stderr[-800:]
        import json
        return json.loads(out.read_text())["total_trades"]

    off = run([])
    armed = run(["--trail-vol-above-pctl", "0.8", "--trail-vol-tight-mult", "1.5"])
    half = run(["--trail-vol-above-pctl", "0.8"])
    assert armed != off, "the armed lever changed nothing — decorative flag"
    assert half == off, (
        "a tail with no tight mult changed the result; a half-configured lever "
        "must be inert")
