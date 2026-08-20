"""`--risk-pct grid` — the consumer `risk_grid_percent()` shipped without.

WHY THIS FILE EXISTS
--------------------
`src/research/risk_basis.py` shipped 2026-08-20 with a `risk_grid_percent()`
whose whole justification is the operator's ask — *"it needs to, in any case,
check various different risk percentages to see how they perform"* — and the
next measurement found it at **6 references, every one a test, zero in
production**. That is definition-of-done clause 2 (*a consumer exists*)
violated by the change that wrote the clause, and it is the same
write-and-never-read shape as `exit_price_source` (written in 12 files,
branched on in one).

So the load-bearing test here is :func:`test_risk_grid_percent_has_a_production_consumer`.
The rest guard the seam the sweep is rendered through.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "backtest_system.py"


def _load_harness():
    """Import backtest_system by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("_bt_system", HARNESS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_bt_system"] = mod
    spec.loader.exec_module(mod)
    return mod


bt = _load_harness()


# ---------------------------------------------------------------------------
# The anti-build-and-abandon assertion
# ---------------------------------------------------------------------------

def test_risk_grid_percent_has_a_production_consumer():
    """`risk_grid_percent` must be called from production, not only tests.

    Asserted against the harness SOURCE rather than by importing, because the
    failure being guarded is *nothing calls it* — an import proves the symbol
    resolves, which was already true while it had no consumer at all.
    """
    src = HARNESS.read_text()
    assert "risk_grid_percent" in src, (
        "risk_grid_percent() lost its only production consumer — it is back to "
        "being a capability with a runner and no caller (definition-of-done "
        "clause 2)."
    )


def test_every_grid_arm_is_reachable_from_the_declared_multipliers():
    """The grid the harness sweeps IS `DEFAULT_GRID_MULTIPLIERS` x live.

    A harness that swept its own hardcoded arms would re-create the two-owner
    problem `risk_basis` exists to retire.
    """
    from src.research import risk_basis

    grid, live = risk_basis.risk_grid_percent()
    assert live.ok, live.describe()
    expected = sorted({round(live.percent * m, 6)
                       for m in risk_basis.DEFAULT_GRID_MULTIPLIERS})
    assert list(grid) == expected


# ---------------------------------------------------------------------------
# The argument type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("grid", "grid"), ("GRID", "grid"), (" grid ", "grid"),
    ("live", "live"), ("LIVE", "live"),
])
def test_risk_pct_arg_passes_the_keywords_through(raw, expected):
    assert bt._risk_pct_arg(raw) == expected


def test_risk_pct_arg_still_takes_a_number():
    assert bt._risk_pct_arg("0.3") == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# The table reads the engine's ACTUAL keys
# ---------------------------------------------------------------------------

_REPORT = {"live_percent": 1.5, "account_id": "bybit_2"}

#: Exactly the key names `run_system_backtest` returns, read off a real run.
_REAL_ARM = {
    "risk_pct": 1.5, "multiple_of_live": 1.0,
    "result": {"total_trades": 7, "net_pnl": -183.14,
               "max_drawdown_usd": 194.27, "return_dd_ratio": -0.94},
}

#: The names the FIRST DRAFT guessed. Production returns none of them.
_GUESSED_ARM = {
    "risk_pct": 1.5, "multiple_of_live": 1.0,
    "result": {"trades": 7, "total_pnl": -183.14, "max_drawdown": 194.27},
}


def test_table_renders_the_engine_keys():
    out = bt._fmt_risk_grid([_REAL_ARM], _REPORT)
    for token in ("7", "-183.14", "194.27", "-0.94"):
        assert token in out, f"{token!r} missing from:\n{out}"


def test_table_shows_a_dash_for_the_guessed_keys():
    """NEGATIVE CONTROL — the discriminating half.

    Without this, `test_table_renders_the_engine_keys` passes for a formatter
    that renders anything it is handed. This pins that the table reads the
    engine's names specifically: fed the guessed ones it must render em-dashes,
    which is exactly the (silent, plausible) failure the first draft shipped.
    """
    out = bt._fmt_risk_grid([_GUESSED_ARM], _REPORT)
    assert "-183.14" not in out and "194.27" not in out
    assert out.count("—") >= 4


def test_table_never_renders_a_bool_as_a_number():
    arm = {"risk_pct": 1.5, "multiple_of_live": 1.0,
           "result": {"total_trades": True, "net_pnl": False,
                      "max_drawdown_usd": None, "return_dd_ratio": None}}
    out = bt._fmt_risk_grid([arm], _REPORT)
    # Assert on the DATA ROW, not the whole render — the header carries
    # "1 arm(s)", which a naive substring check matches (it did, first try).
    row = next(ln for ln in out.splitlines() if "1.00x" in ln)
    assert row.count("—") == 4, row
    assert "1" not in row.split("1.00x")[1], row


def test_table_states_the_quantization_caveat():
    """The harness sizes CONTINUOUSLY; production refuses sub-1-contract.

    A sweep that omits this errs FLATTERINGLY — a small-risk arm reads as safe
    when it may mean the leg never traded. The caveat travels with the table.
    """
    out = bt._fmt_risk_grid([_REAL_ARM], _REPORT)
    assert "quantization" in out.lower()
    assert "sub-1-contract" in out


# ---------------------------------------------------------------------------
# Refusal — never a silent fallback
# ---------------------------------------------------------------------------

def test_grid_refuses_when_live_cannot_be_resolved():
    """An unresolvable basis EXITS, it does not fall back to the 0.3 default.

    Silently sweeping around a basis nobody could read is the collapse
    `risk_basis` deliberately has no fallback constant for.
    """
    proc = subprocess.run(
        [sys.executable, str(HARNESS), "--risk-pct", "grid",
         "--risk-account", "no_such_account_xyz"],
        capture_output=True, text=True, timeout=300, cwd=str(REPO))
    assert proc.returncode == 1, proc.stdout[-2000:]
    assert "could not resolve" in proc.stderr
    assert "no_such_account_xyz" in proc.stderr
    # It must name what it DID find, so the reader can see it looked.
    assert "declared accounts" in proc.stderr
