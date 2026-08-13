"""Every harness's `--emit-trades` row must carry what the E0 builder requires.

WHY THIS EXISTS. `build_exit_head_dataset.py` refuses a row missing any of
`entry_time` / `exit_time` / `entry` / `sl`, and cannot resolve candles without
`symbol`. Three of the five harnesses — trend, squeeze, fvg_range — emitted only
`entry_time`, so **100% of their rows were dropped**, and had been for as long as
the harness existed. No trend/squeeze/fvg leg had ever produced a single E0
dataset row; it read as "the family has no data".

It went unnoticed because the drop is silent by construction: the load-stage
counters that name the missing key were surfaced only in the total-failure
branch, so a round where one family loaded fine and another dropped entirely
reported nothing at all. Measured 2026-08-13 on the 1d round: 371 trend rows, 0
usable, next to 578 pullback rows, 578 usable — and `build_report.json` showed
only candle-stage counters.

It also survived a first fix. #8889 added `symbol` alone, diagnosed from the
*name* of the counter the survivors happened to land in rather than measured, and
the drop stayed at 100%. What actually found it was diffing the key set against
the family that WORKS — which is what this test does, permanently.

THE REQUIREMENT IS IMPORTED, NOT RESTATED. A hand-copied list here would be a
second definition free to drift from the builder's, and then this test would pass
while the builder rejected the rows — the exact failure it exists to prevent.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
BUILDER = REPO / "scripts" / "ml" / "build_exit_head_dataset.py"

HARNESSES = [
    "backtest_trend.py",
    "backtest_pullback.py",
    "backtest_squeeze.py",
    "backtest_fvg_range.py",
    "backtest_ict_scalp.py",
]


def required_keys() -> set[str]:
    """The keys the builder refuses a row without — read from the builder.

    Parsed out of the `if None in (t0, t1, entry, sl):` guard's own reporting
    loop, which enumerates `("entry_time", t0), ("exit_time", t1), ...`. That
    tuple IS the contract; reading it means this test cannot drift from it.
    """
    tree = ast.parse(BUILDER.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        # for key, val in (("entry_time", t0), ("exit_time", t1), ...)
        if not isinstance(node.iter, ast.Tuple):
            continue
        keys = set()
        for elt in node.iter.elts:
            if (isinstance(elt, ast.Tuple) and elt.elts
                    and isinstance(elt.elts[0], ast.Constant)
                    and isinstance(elt.elts[0].value, str)):
                keys.add(elt.elts[0].value)
        if {"entry_time", "exit_time"} <= keys:
            return keys
    raise AssertionError(
        "could not locate the builder's required-key guard — if it moved, fix "
        "this parser rather than hardcoding the list, or the test silently "
        "stops checking the real contract")


def emitted_keys(harness: str) -> set[str]:
    """String keys of the dict literal written under `if emit_path:`.

    Scoped to the EMIT block deliberately. The `--json` summary dict is a
    separate site that carries `symbol` and `strategy` of its own, and
    conflating the two is not hypothetical: a watcher grepping the whole file
    for the emit key matched the summary site and fired against unfixed code.
    """
    tree = ast.parse((REPO / "scripts" / harness).read_text())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                and node.test.id == "emit_path"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "entry_time"
                    for k in sub.keys if k is not None):
                return {k.value for k in sub.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    raise AssertionError(f"{harness}: no `if emit_path:` dict literal found")


def test_required_keys_parsed_from_the_builder():
    """The parser must find a positive before any absence is trusted."""
    req = required_keys()
    assert req == {"entry_time", "exit_time", "entry", "sl"}, req


@pytest.mark.parametrize("harness", HARNESSES)
def test_emit_row_satisfies_the_builder(harness):
    missing = required_keys() - emitted_keys(harness)
    assert not missing, (
        f"{harness} emits rows missing {sorted(missing)} — "
        f"build_exit_head_dataset.py drops 100% of them, silently. "
        f"The Trade dataclass carries every one of these fields; the emit "
        f"dict simply omitted them."
    )


@pytest.mark.parametrize("harness", HARNESSES)
def test_emit_row_carries_symbol_and_strategy(harness):
    """`symbol` resolves candles; `strategy` is the coverage-matrix row key.

    Neither is in the builder's hard-refuse set — a row missing `symbol` dies
    one stage later at `no_candles`, and a row missing `strategy` survives with
    the WRONG attribution, which is worse than dying.
    """
    keys = emitted_keys(harness)
    assert {"symbol", "strategy"} <= keys, sorted({"symbol", "strategy"} - keys)


def test_the_check_can_fail():
    """A guard that cannot fail proves nothing about the code it guards."""
    assert required_keys() - {"entry_time"}, (
        "removing a key from the emitted set must leave a non-empty missing "
        "set, or the assertion above is vacuous")
