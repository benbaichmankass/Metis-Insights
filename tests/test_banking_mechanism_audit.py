"""Tests for scripts/research/banking_mechanism_audit.py (MI-163).

The instrument's load-bearing claims are arithmetic, so they are asserted here
rather than argued in prose:

* ``R_TO_BREAKEVEN = trail_mult / atr_stop_mult`` exactly (the ATR cancels).
* the population is the 44 enabled+live legs, reproducing MI-146/148/155.
* ``exit_plan.py`` has no live reader — asserted over the tree, so the finding
  becomes a standing detector instead of a one-time observation.
* the telemetry hook set is re-derived from source, never trusted.
* the counterfactual refuses to describe itself as net.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD_PATH = os.path.join(_REPO, "scripts", "research", "banking_mechanism_audit.py")


def _load():
    spec = importlib.util.spec_from_file_location("banking_mechanism_audit", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


def test_population_is_enabled_and_live(mod):
    legs = mod.load_population()
    assert len(legs) == 44, f"population drifted from MI-155's denominator: {len(legs)}"
    for name, cfg in legs.items():
        assert cfg.get("enabled") is True, name
        assert str(cfg.get("execution", "live")).lower() == "live", name


def test_r_to_breakeven_is_the_exact_ratio(mod):
    """The ATR cancels: R_TO_BE is trail_mult / atr_stop_mult and nothing else."""
    row = mod.grade_leg("x", {"timeframe": "1h", "atr_stop_mult": 2.0,
                              "trail_mult": 5.0})
    assert row["mechanism"] in ("chandelier_ratchet", "unknown_unit")
    if row["mechanism"] == "chandelier_ratchet":
        assert row["r_to_breakeven"] == pytest.approx(2.5)
        assert row["r_to_bank_1r"] == pytest.approx(3.5)


def test_a_real_ratchet_leg_grades(mod):
    legs = mod.load_population()
    row = mod.grade_leg("trend_donchian", legs["trend_donchian"])
    assert row["mechanism"] == "chandelier_ratchet"
    assert row["monotone"] is True
    # trail_mult 5.0 / atr_stop_mult 2 = 2.5R just to reach break-even.
    assert row["r_to_breakeven"] == pytest.approx(2.5)


def test_one_shot_legs_cannot_accrue_r(mod):
    """`_base.monitor_breakeven_sl` guards on `sl < entry` — it fires once, ever."""
    legs = mod.load_population()
    oneshot = [n for n, c in legs.items()
               if mod.grade_leg(n, c)["mechanism"] == "one_shot_breakeven"]
    assert oneshot, "expected the ict_scalp family to grade one_shot_breakeven"
    for name in oneshot:
        row = mod.grade_leg(name, legs[name])
        assert row["accrues_r"] is False
        assert row["r_banked_ceiling"] == 0.0


def test_be_at_r_is_unreachable_over_the_population():
    """Stronger than "0 of 44 declare it": no leg in the 44 can even consume it.

    All 8 one-shot legs are `ict_scalp`, which never reads `be_at_r` — so the
    threshold is `_base.monitor_breakeven_sl`'s signature default of 1.0. The two
    units that DO read it are out of the population. If `ict_scalp` ever starts
    reading it, this fails and §2.5 of the memo must be re-established.
    """
    scalp = os.path.join(_REPO, "src", "units", "strategies", "ict_scalp.py")
    with open(scalp, encoding="utf-8") as fh:
        src = fh.read()
    assert "be_at_r" not in src, "ict_scalp now reads be_at_r — re-measure §2.5"
    assert "one_r_threshold" not in src, "ict_scalp now passes one_r_threshold"

    base = os.path.join(_REPO, "src", "units", "strategies", "_base.py")
    with open(base, encoding="utf-8") as fh:
        assert "one_r_threshold: float = 1.0" in fh.read(), (
            "the _base signature default moved off 1.0 — the effective live "
            "break-even threshold on all 8 one-shot legs changed with it")


def test_the_units_that_read_be_at_r_are_outside_the_population(mod):
    """turtle_soup is `execution: shadow`; vwap is `enabled: false`."""
    legs = mod.load_population()
    assert "turtle_soup" not in legs
    assert "vwap" not in legs
    for name, cfg in legs.items():
        assert cfg.get("be_at_r") is None, f"{name} declares an inert be_at_r"


def test_exit_plan_has_no_live_reader():
    """A standing detector, not a one-time observation.

    `exit_plan` / `exit_plan_state` are written by the coordinator and the soak.
    If any module outside that write path starts reading them back, this fails
    and the shadow/live verdict in docs/research/banking-half-2026-09-07.md must
    be re-established before it is quoted again.
    """
    allowed = {
        os.path.join("src", "runtime", "exit_plan.py"),
        os.path.join("src", "runtime", "exit_plan_realism.py"),
        os.path.join("src", "runtime", "exit_plan_materializer.py"),
        os.path.join("src", "runtime", "exit_ladder_soak.py"),
        os.path.join("src", "core", "coordinator.py"),          # the writer
        os.path.join("src", "units", "db", "database.py"),      # serialise-on-write
        os.path.join("src", "units", "strategies", "turtle_soup.py"),  # uncalled hook
    }
    offenders = []
    for root, _dirs, files in os.walk(os.path.join(_REPO, "src")):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, _REPO)
            if rel in allowed:
                continue
            with open(full, encoding="utf-8", errors="replace") as fh:
                if "exit_plan" in fh.read():
                    offenders.append(rel)
    assert not offenders, (
        "exit_plan gained a reference outside its known write path — re-establish "
        f"whether it is still shadow before quoting the finding: {offenders}")


def test_telemetry_hook_set_is_derived_not_trusted(mod):
    legs = {g["leg"]: g for g in
            (mod.grade_leg(n, c) for n, c in mod.load_population().items())}
    gap = mod.telemetry_hook_gap(legs)
    assert gap["hooked_units"] == sorted(mod.TELEMETRY_HOOKED_UNITS), (
        "the set of units carrying record_position_telemetry has changed; the "
        "'9 structurally invisible legs' finding must be re-measured")
    assert gap["drifted"] is False
    # The 8 ict_scalp legs + squeeze_breakout_4h.
    assert gap["n_invisible"] == 9, gap["legs_structurally_invisible"]


def test_counterfactual_never_claims_to_be_net(mod):
    rows = [{"lifecycle": "closed", "peak_gradeable": True, "peak_r": 2.0,
             "open_r": -1.0, "strategy": "trend_donchian"}]
    legs = mod.load_population()
    out = mod.counterfactual(rows, legs, [1.0])
    assert out["is_net"] is False
    assert "UNMEASURED" in out["cost_side"]
    # peak 2.0 >= 1.0 and terminal -1.0 < 1.0 ⇒ rescued, improvement >= 1-(-1) = 2.
    assert out["arms"][0]["rescued"] == 1
    assert out["arms"][0]["gross_delta_r_lower_bound"] == pytest.approx(2.0)


def test_pooled_marks_every_peak_as_a_lower_bound(mod):
    rows = [{"peak_gradeable": True, "peak_r": 1.0, "peak_r_is_lower_bound": True,
             "strategy": "trend_donchian", "lifecycle": "closed"}]
    out = mod.pooled_analysis(rows, mod.load_population(), [1.0])
    assert out["all_peak_r_lower_bound"] is True
    assert out["pooled_n_vs_floor"]["floor"] == mod.MIN_LIVE_N == 30
