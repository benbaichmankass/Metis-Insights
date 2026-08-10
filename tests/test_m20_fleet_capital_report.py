"""Tests for the fleet sweep's capital-efficiency REPORTING (Path B input).

`scripts/research/m20_fleet_exit_sweep.py::capital_delta` is the half that was
missing when every pullback `stale_stop` cell in the coverage matrix was graded
`honest_negative` off the 2026-07-12 fleet sweep: `scripts/capital_efficiency.py`
did not exist until 2026-08-10 (`3240557`), so Path A's net_R-AND-maxDD gate was
the only axis those verdicts could see, and a lever that frees capital at a small
net_R cost fails `beats()` before a walk-forward ever runs.

These pin the two properties that make the reported number safe to set a
threshold from:

  1. **Unmeasurable is `None`, never `0.0`.** A fabricated zero would rank an
     un-measured cell alongside a genuinely flat one, which is the collapsed-state
     failure `capital_efficiency.days_from_bars` already refuses upstream.
  2. **It reports, it does not grade.** Path B's thresholds are deliberately
     unset; nothing in this module may encode one.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "m20_fleet_exit_sweep", REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["m20_fleet_exit_sweep"] = _mod
_spec.loader.exec_module(_mod)

capital_delta = _mod.capital_delta
beats = _mod.beats


def _summary(*, net_r, capital_day=None, position_day=None, bars=None,
             cap_days=None, mdd=1.0):
    return {
        "net_total_r": net_r,
        "max_drawdown_r": mdd,
        "net_r_per_capital_day": capital_day,
        "net_r_per_position_day": position_day,
        "mean_bars_held": bars,
        "capital_days": cap_days,
    }


def test_unmeasurable_rate_reports_none_not_zero():
    """The whole point of the module upstream: 'we could not measure the hold'
    and 'the hold was zero' are opposite statements."""
    cell = _summary(net_r=10.0, capital_day=None)
    base = _summary(net_r=12.0, capital_day=None)
    out = capital_delta(cell, base)
    assert out["cell_net_r_per_capital_day"] is None
    assert out["base_net_r_per_capital_day"] is None
    assert out["d_net_r_per_capital_day"] is None
    # and specifically NOT 0.0 — a consumer sorting on this must be able to
    # separate unmeasured from flat.
    assert out["d_net_r_per_capital_day"] is not 0.0  # noqa: F632 - identity is the point


def test_half_measured_pair_still_refuses_to_invent_a_delta():
    """One side measured and the other not is the case a naive `(c or 0)-(b or 0)`
    would turn into a confident, wrong number."""
    out = capital_delta(_summary(net_r=10.0, capital_day=0.5),
                        _summary(net_r=10.0, capital_day=None))
    assert out["cell_net_r_per_capital_day"] == 0.5
    assert out["base_net_r_per_capital_day"] is None
    assert out["d_net_r_per_capital_day"] is None


def test_the_path_b_population_is_measured_even_though_path_a_rejects_it():
    """A capital-freeing lever: net_R falls slightly, capital/day rises. Path A
    says no; the capital axis must still carry a real number, because this cell
    is exactly what Path B exists to evaluate."""
    base = _summary(net_r=20.0, capital_day=0.10, bars=149.0, mdd=5.0)
    cell = _summary(net_r=18.0, capital_day=0.40, bars=30.0, mdd=5.0)

    assert beats(cell, base) is False              # Path A rejects it
    out = capital_delta(cell, base)
    assert out["d_net_r_per_capital_day"] == 0.30  # 4x the capital rate
    assert out["d_net_total_r"] == -2.0
    assert out["net_r_retained_frac"] == 0.9       # lost 10% of net_R
    assert out["d_mean_bars_held"] == -119.0


def test_net_r_retained_frac_is_none_when_base_net_r_is_not_positive():
    """'net_R fell no more than X%' is meaningless against a zero or negative
    base — None, not a large-looking ratio."""
    for bad_base in (0.0, -5.0):
        out = capital_delta(_summary(net_r=-1.0, capital_day=0.2),
                            _summary(net_r=bad_base, capital_day=0.1))
        assert out["net_r_retained_frac"] is None
    ok = capital_delta(_summary(net_r=5.0, capital_day=0.2),
                       _summary(net_r=10.0, capital_day=0.1))
    assert ok["net_r_retained_frac"] == 0.5


def test_non_numeric_values_degrade_to_none_rather_than_raising():
    """The sweep must never die on one odd harness payload mid-fleet."""
    out = capital_delta({"net_total_r": "n/a", "net_r_per_capital_day": "x"},
                        {"net_total_r": None})
    assert out["d_net_r_per_capital_day"] is None
    assert out["d_net_total_r"] is None
    assert out["net_r_retained_frac"] is None


def test_module_encodes_no_path_b_threshold():
    """Path B's two thresholds are the operator's to set from the measured
    distribution. A session inventing one here is the failure this guards.

    Scans the CODE, not the prose: the docstring necessarily *mentions*
    thresholds in order to say it does not apply any, and a raw substring
    search over the whole function flags that explanation as a violation. The
    docstring is stripped before scanning so the test cannot be satisfied by
    deleting the comment that explains the rule — the same reasoning that makes
    `provenance-consumer-guard`'s override verified rather than presence-only.
    """
    import ast
    src = (REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "capital_delta")
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                      # drop the docstring
    assert body, "capital_delta has no executable body to check"
    code = "\n".join(ast.unparse(n) for n in body)

    # No pass/fail vocabulary in the returned keys or logic...
    for banned in ("PASS", "path_b", "threshold", "verdict"):
        assert banned.lower() not in code.lower(), \
            f"capital_delta must report, not grade: found {banned!r}"
    # ...and no comparison of a metric against a numeric literal, which is what
    # a hard-coded threshold would look like. The `bn > 0` guard is a
    # well-definedness check on the DENOMINATOR, not a gate on the metric, so
    # it is allowed by name.
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Compare):
            continue
        rendered = ast.unparse(node)
        if rendered == "bn > 0":
            continue
        assert not any(isinstance(c, ast.Constant) and isinstance(c.value, (int, float))
                       and not isinstance(c.value, bool)
                       for c in node.comparators), \
            f"capital_delta compares a metric to a literal (a threshold?): {rendered}"


# --------------------------------- the percentile arm must not fake abstention
def test_winner_mfe_p80_distinguishes_shape_mismatch_from_thin_sample():
    """`winner_mfe_p80` returns None for two very different reasons, and its
    docstring contract declares only one of them ("< 30 winners").

    Measured 2026-08-10: it read `row["mfe_r"]` top-level, so for every
    `ict_scalp` leg -- whose harness nests mfe_r under `meta` -- it collected
    zero MFEs and returned the not-enough-winners answer for a leg with 1,102
    trades. An inert arm that reports a legitimate-looking abstention is worse
    than one that errors, because the caller records the abstention as data.

    Structural, because the function shells out to a real harness: it must read
    through the ONE accessor, and it must branch on winners-seen so the two
    causes stay distinguishable.
    """
    import ast
    src = (REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "winner_mfe_p80")
    code = ast.unparse(fn)
    assert "mfe_r_of" in code, \
        "winner_mfe_p80 re-derives the MFE read instead of using the one accessor"
    assert "t['mfe_r']" not in code and 't["mfe_r"]' not in code, \
        "winner_mfe_p80 still does a raw top-level mfe_r read"
    assert "winners_seen" in code, \
        "winner_mfe_p80 cannot tell 'no winners' from 'no readable MFE'"
