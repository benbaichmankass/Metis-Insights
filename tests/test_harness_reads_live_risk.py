"""The harness can finally SEE live risk — fix 2.3 (B3 + B5).

The operator named this class directly:

    "the back test risk and the live config don't match ... either the way the
    backtesting is done needs to be independent of what the risk is set to, or
    it needs to be made up to date. It needs to, IN ANY CASE, check various
    different risk percentages."

MEASURED 2026-08-20 over all 25 harness files (`scripts/backtest_*.py`,
`scripts/walkforward_*.py`, `src/backtest/*.py`): **0** read `config/accounts.yaml`
or reached live risk in any form. And `src/research/risk_basis.py` — the module
shipped the previous day precisely to close this — had **no consumer**, which is
the build-and-abandon class this audit exists to catch. These tests cover the
consumer.

Live today resolves to **1.5%** (`accounts.yaml::bybit_2.risk.risk_pct = 0.015`,
a FRACTION) against the harness default **0.3** (a PERCENT) — ratio **0.2**, i.e.
every default backtest sizes at one fifth of live risk. The default is
deliberately NOT changed here: moving it would silently re-base every historical
comparison. What changes is that the gap is now *stated on every run*.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import backtest_system as bs  # noqa: E402
from src.research import risk_basis  # noqa: E402


# ---------------------------------------------------------------------------
# the CLI surface
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("0.3", 0.3), ("1.5", 1.5), ("live", "live"), ("LIVE", "live"), (" live ", "live"),
])
def test_risk_pct_accepts_numbers_and_the_live_sentinel(raw, expected):
    assert bs._risk_pct_arg(raw) == expected


def test_a_nonsense_risk_pct_is_REFUSED_not_coerced():
    """Negative control. Silently coercing would reintroduce the whole defect:
    a run sized at something nobody asked for."""
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        bs._risk_pct_arg("nonsense")


def test_the_numeric_default_is_unchanged():
    """0.3 stays. Moving it would silently re-base every stored comparison —
    the restraint is the point, not an oversight."""
    src = (REPO / "scripts" / "backtest_system.py").read_text(encoding="utf-8")
    assert 'p.add_argument("--risk-pct", type=_risk_pct_arg, default=0.3,' in src


# ---------------------------------------------------------------------------
# the stamp — and that it is computed where EVERY caller gets it
# ---------------------------------------------------------------------------
def test_the_engine_stamps_the_risk_basis_not_just_the_cli():
    """An in-process caller (the trainer's signal-cache driver, the sweeps)
    never goes through main(), so a stamp applied there would leave exactly the
    callers that cannot be inspected from a run log ungraded."""
    src = (REPO / "scripts" / "backtest_system.py").read_text(encoding="utf-8")
    engine = src[src.index("summary = _summarize("):src.index("return summary")
                 if "return summary" in src else len(src)]
    assert '"risk_basis": risk_basis.compare_to_live(risk_pct)' in engine, (
        "the stamp is not inside the engine's params block — it would be "
        "CLI-only, and params.get('risk_basis') would be None for every "
        "in-process run"
    )


def test_the_metadata_field_is_actually_populated_not_a_dead_key():
    """`"risk_basis": params.get("risk_basis")` is only worth anything if
    something puts it in `params`. A key that is always None is the
    written-and-never-populated defect in miniature."""
    src = (REPO / "scripts" / "backtest_system.py").read_text(encoding="utf-8")
    assert '"risk_basis": params.get("risk_basis")' in src
    assert '"risk_basis": risk_basis.compare_to_live(risk_pct)' in src, (
        "the metadata reads a key nothing writes"
    )


# ---------------------------------------------------------------------------
# the comparison itself, against the REAL live config
# ---------------------------------------------------------------------------
def test_live_resolves_from_the_real_accounts_yaml():
    live = risk_basis.live_risk()
    assert live.ok, f"live risk did not resolve: {live.describe()}"
    assert live.state == risk_basis.STATE_RESOLVED
    assert live.percent is not None and live.percent > 0


def test_the_default_is_graded_as_DIFFERING_from_live():
    """The operator's example, as an assertion. If this ever starts passing as
    `matches_live`, either the default moved or live did — and both are things
    a reader must be told about rather than discover."""
    r = risk_basis.compare_to_live(0.3)
    assert r["verdict"] == "differs_from_live"
    assert r["ratio"] is not None and r["ratio"] < 0.5, (
        f"expected the default to size well under live; got ratio {r['ratio']}"
    )


def test_matching_live_grades_as_matching():
    """Negative control for the test above — proves the verdict discriminates
    on the VALUE, not merely reporting `differs` for everything."""
    live = risk_basis.live_risk()
    assert risk_basis.compare_to_live(live.percent)["verdict"] == "matches_live"


def test_an_unreadable_config_grades_live_unknown_NOT_matching(tmp_path):
    """The third verdict is the one that matters: `live_unknown` must never
    collapse into `matches_live`, or an unreadable config reads as agreement."""
    missing = tmp_path / "nope.yaml"
    r = risk_basis.compare_to_live(0.3, accounts_path=missing)
    assert r["verdict"] == "live_unknown"
    assert r["verdict"] != "matches_live"


def test_the_grid_answers_the_operators_actual_ask():
    """"It needs to, in any case, check various different risk percentages."

    The grid is built AROUND live, so a sweep brackets the real setting rather
    than an arbitrary CLI default.
    """
    grid, live = risk_basis.risk_grid_percent()
    assert grid is not None, f"no grid: {live.describe()}"
    assert len(grid) >= 3
    assert min(grid) < live.percent < max(grid), (
        f"grid {grid} does not bracket live {live.percent}"
    )


def test_the_grid_REFUSES_rather_than_sweeping_around_a_guess(tmp_path):
    """`None`, not a default grid. Sweeping around a basis that was never read
    would produce confident numbers about nothing."""
    grid, live = risk_basis.risk_grid_percent(accounts_path=tmp_path / "nope.yaml")
    assert grid is None
    assert not live.ok
