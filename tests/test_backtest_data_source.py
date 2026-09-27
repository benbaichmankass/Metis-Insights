"""`--symbol` must constrain the DATA, not just the header line.

WHY THIS EXISTS (`BL-20260813-HARNESS-SYMBOL-IS-A-LABEL-DATA-DEFAULTS-TO-BTC`).
Two harnesses declared a `--symbol` beside a `--data` carrying a hardcoded
default, so the symbol was a DISPLAY LABEL over whatever the default pointed at.

REPRODUCED 2026-09-12 on `main` before the fix, which is why these tests exist
rather than a docstring::

    $ python3 scripts/backtest_trend.py --symbol SOLUSDT --timeframe 1h
    trend_donchian - SOLUSDT 1h {...}
      data 2022-07-23 21:24:00+00:00 -> 2022-07-27 08:43:00+00:00  trades=144
      win_rate=31.25%  net_r=-82.6483 ...
    RC=0

144 trades, a win rate and a net-R, headed SOLUSDT and computed from the BTC
fixture, exit 0. The span is verbatim the one the backlog row names.

⚠️ THE BACK-COMPAT CASES ARE THE POINT OF HALF THIS FILE. A refusal is easy; a
refusal that does not break the 251 existing `--data` call sites or the
fleet-wide `BACKTEST_DATA_PATH` knob is the actual requirement. Each of those
is asserted here in its own test, so a future tightening that breaks one
fails loudly instead of quietly stranding a harness.

⚠️ ONE CASE THAT WAS DELIBERATELY BACK-COMPAT UNTIL 2026-09-25 IS NOT ANY
MORE: an invocation naming no symbol AND no data used to keep reaching the
fixture unchanged ("nothing to contradict"). `docs/claude/work/
MANAGER-CHECKLIST.json` row E4 named that exact path as the live defect --
"anything falling back to it produces a confident answer about nothing" --
and required that reaching the fixture BY DEFAULT become impossible while the
fixture itself stays available as an explicit choice. See
`test_bare_invocation_now_refuses_instead_of_reaching_the_fixture` and
`test_allow_implicit_default_is_the_documented_opt_in_escape_hatch` below.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "_bds_under_test", _REPO / "scripts" / "ops" / "backtest_data_source.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


BDS = _mod()


@pytest.fixture()
def data_dir(tmp_path):
    (tmp_path / "SOLUSDT_1h.csv").write_text("ts,open,high,low,close,volume\n")
    return tmp_path


def test_an_explicit_data_path_is_used_as_given_and_never_second_guessed(data_dir):
    """251 call sites pass `--data`. A caller naming a file is naming it."""
    r = BDS.resolve_or_refuse("SOLUSDT", "1h", "anywhere/else.csv",
                              legacy_default="data/btc.csv", data_dir=data_dir, env={})
    assert r.state == BDS.EXPLICIT
    assert r.path == "anywhere/else.csv"
    assert r.ok


def test_a_contradicting_explicit_path_still_runs(data_dir):
    """An explicit path that does NOT look like the symbol is still honoured.

    Proxies, resamples and ad-hoc files are legitimate. Refusing here would make
    the fix a bigger outage than the defect.
    """
    r = BDS.resolve_or_refuse("SOLUSDT", "1h", "data/backtest_candles.csv",
                              legacy_default="data/btc.csv", data_dir=data_dir, env={})
    assert r.state == BDS.EXPLICIT and r.ok


def test_backtest_data_path_env_is_honoured(data_dir):
    """A FLEET-WIDE knob read by 23 files — CLAUDE.md corrects the claim that it
    was M5-only. Breaking it would strand the research fleet."""
    r = BDS.resolve_or_refuse("SOLUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=data_dir, env={BDS.DEFAULT_ENV_VAR: "env/path.csv"})
    assert r.state == BDS.ENV and r.path == "env/path.csv"


def test_a_blank_env_var_is_not_set_and_falls_through(data_dir):
    """`BACKTEST_DATA_PATH=''` is not a path. Treating it as one would resolve
    every run to the empty string."""
    r = BDS.resolve_or_refuse("SOLUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=data_dir, env={BDS.DEFAULT_ENV_VAR: "   "})
    assert r.state == BDS.RESOLVED


def test_a_symbol_with_a_matching_file_resolves_to_it(data_dir):
    r = BDS.resolve_or_refuse("SOLUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=data_dir, env={})
    assert r.state == BDS.RESOLVED
    assert r.path.endswith("SOLUSDT_1h.csv")


def test_bare_invocation_now_refuses_instead_of_reaching_the_fixture(data_dir):
    """E4 (docs/claude/work/MANAGER-CHECKLIST.json): no --symbol and no --data
    used to silently return LEGACY_DEFAULT ("nothing to contradict"), which is
    precisely how a bare invocation reached the 5,001-row/3.5-day-of-2022 BTC
    fixture BY DEFAULT. That is now REFUSED; the fixture stays reachable only
    as an explicit choice."""
    r = BDS.resolve_or_refuse(None, "1h", None, legacy_default="data/btc.csv",
                              data_dir=data_dir, env={})
    assert r.state == BDS.REFUSED
    assert r.path is None
    assert not r.ok
    msg = BDS.refusal_message(r, harness="x.py", legacy_default="data/btc.csv")
    assert "--data data/btc.csv" in msg


def test_allow_implicit_default_is_the_documented_opt_in_escape_hatch(data_dir):
    """A caller that wants the pre-E4 contract on purpose can still get it."""
    r = BDS.resolve_or_refuse(None, "1h", None, legacy_default="data/btc.csv",
                              data_dir=data_dir, env={}, allow_implicit_default=True)
    assert r.state == BDS.LEGACY_DEFAULT and r.path == "data/btc.csv"


def test_a_named_symbol_with_no_data_is_REFUSED_not_defaulted(data_dir):
    """The finding. `path` is None — never a substituted default, which IS the
    defect this module exists for."""
    r = BDS.resolve_or_refuse("ETHUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=data_dir, env={})
    assert r.state == BDS.REFUSED
    assert r.path is None
    assert not r.ok


def test_the_default_looking_symbol_gets_no_special_case(data_dir):
    """`--symbol BTCUSDT` with no data is refused too.

    Nothing establishes that `data/backtest_candles.csv` holds BTC — not its
    name, not a manifest. A special case here would assert a provenance nobody
    has checked, which is the neighbouring defect rather than a convenience.
    """
    r = BDS.resolve_or_refuse("BTCUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=data_dir, env={})
    assert r.state == BDS.REFUSED


def test_the_refusal_names_the_symbol_the_declined_fallback_and_the_remedy(data_dir):
    """A refusal a reader cannot act on is a different failure, not a fix."""
    r = BDS.resolve_or_refuse("ETHUSDT", "1h", None, legacy_default="data/btc.csv",
                              data_dir=data_dir, env={})
    msg = BDS.refusal_message(r, harness="x.py", legacy_default="data/btc.csv")
    assert "ETHUSDT" in msg
    assert "data/btc.csv" in msg
    assert "--data" in msg
    assert BDS.DEFAULT_ENV_VAR in msg
    assert "BL-20260813-HARNESS-SYMBOL-IS-A-LABEL-DATA-DEFAULTS-TO-BTC" in msg


def test_a_resolved_run_still_names_its_file(data_dir):
    """`resolved` without a provenance line is still an IMPLICIT input selection
    — sub-class B with a better outcome. The line is the other half of the fix."""
    r = BDS.resolve_or_refuse("SOLUSDT", "1h", None, legacy_default=None,
                              data_dir=data_dir, env={})
    line = r.provenance_line()
    assert "[resolved]" in line and "SOLUSDT_1h.csv" in line


def test_every_state_is_reachable_and_they_are_five_distinct_values():
    """A negative control on the vocabulary itself: five names, five values."""
    states = {BDS.EXPLICIT, BDS.ENV, BDS.RESOLVED, BDS.LEGACY_DEFAULT, BDS.REFUSED}
    assert len(states) == 5


def test_the_module_does_not_carry_a_second_symbol_to_file_mapping():
    """`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`, made mechanical.

    The (symbol, timeframe) -> file mapping has ONE owner. This asserts the
    policy module still DELEGATES rather than having grown its own convention —
    the failure mode being two functions that answer "which file is SOLUSDT?"
    and drift apart after the next proxy change.
    """
    import ast

    path = _REPO / "scripts" / "ops" / "backtest_data_source.py"
    src = path.read_text()
    assert "m20_fleet_exit_sweep" in src, "the canonical resolver is no longer referenced"

    # ⚠️ GRADE THE CODE, NOT THE PROSE. An earlier version of this test grepped
    # the raw file and failed on its own docstring, which legitimately NAMES the
    # owner's `data/{SYMBOL}_{grain}.csv` convention while explaining why it is
    # not re-implemented here. A test that cannot tell a description from an
    # implementation is the same class of error as the defect under test.
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
    # `ast.unparse` also drops comments, which are prose by another name.
    code = ast.unparse(tree)

    for forbidden in ("_{grain}", "_%s.csv", ".glob(", "PROXY_DATA"):
        assert forbidden not in code, (
            f"backtest_data_source appears to carry its own symbol->file mapping "
            f"({forbidden!r} in executable code). That mapping has ONE owner; a "
            f"second copy is RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED."
        )


def test_self_test_passes():
    """The module's own planted controls, run from the suite so they cannot rot."""
    assert BDS._self_test() == 0
