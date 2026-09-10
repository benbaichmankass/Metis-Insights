"""Recurrence detector for audit **F-28** — the strategy→builder roster has ONE home.

WHY THIS FILE EXISTS
--------------------
``pipeline._STRATEGY_BUILDERS`` used to be a SECOND strategy→builder registry
beside ``intent_multiplexer._default_intent_builders()``, and nothing kept them
in step. **Measured on 2026-09-09 against ``config/strategies.yaml`` (population:
all 55 declared strategies, of which 45 are ``execution: live``): 55 names in the
intent roster, 16 in the pipeline one, 39 missing — and 35 of the 45 live legs
were among the missing (78%).**

That mattered because ``MULTI_STRATEGY_INTENT_LAYER=false`` is documented in
``pipeline.run_pipeline`` as the sanctioned no-redeploy rollback, and it routes
to the 16-name path. So *exercising the rollback* would have stopped 35 of 45
live legs — one ``logger.warning`` and a ``continue`` each. **The failure mode is
the worst available: it looks like it worked.**

The operator was offered a parity test over the two registries and chose the
structural fix instead (``DEC-20260909-ROLLBACK-BUILDER-REGISTRY``, ``chosen:
collapse_to_one_registry``, 2026-09-09): **one registry cannot drift from
itself.** These tests are what fails loudly if a future change re-splits it.

WHAT EACH TEST CAN AND CANNOT SEE
---------------------------------
- ``test_pipeline_declares_no_second_builder_registry`` and
  ``test_no_module_under_src_declares_a_second_builder_registry`` are an **AST
  scan**, so they see a dict LITERAL of ``name -> *_signal_builder``. A registry
  assembled some other way (a loop, a decorator, a comprehension) is **not**
  caught — stated rather than implied. They carry a positive control so a scan
  that has silently stopped matching anything cannot pass as a clean result.
- ``test_the_rollback_path_resolves_through_the_single_roster`` is
  **behavioural** and is the one that would have caught F-28 directly: it swaps
  the single home's roster and asserts the ROLLBACK path sees the swap.
- ``test_every_live_leg_is_buildable_on_both_paths`` is the F-28 measurement
  itself, re-run as an assertion.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

pytest.importorskip("pandas")
pytest.importorskip("yaml")

import yaml  # noqa: E402

from src.runtime import pipeline as pl  # noqa: E402
from src.runtime import intent_multiplexer as im  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The ONE home. Everything else is a re-split.
SINGLE_HOME = ("src/runtime/intent_multiplexer.py", "_default_intent_builders")

#: A dict literal is called a builder registry when it maps at least this many
#: string keys to callables whose names look like signal builders. Three, not
#: one, so an incidental two-entry alias map in a test helper is not a finding —
#: F-28's registry had sixteen.
_MIN_ENTRIES = 3


def _looks_like_a_builder(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id.endswith("_signal_builder")
    if isinstance(node, ast.Attribute):
        return node.attr.endswith("_signal_builder")
    return False


def _builder_registry_dicts(tree: ast.AST):
    """Every dict literal in *tree* that maps ≥3 str keys to ``*_signal_builder``.

    Returns ``[(lineno, n_entries)]``. Deliberately scans the WHOLE module, not
    just module scope: F-28's dict was module-level, but the one home is a dict
    returned from inside a function, so a re-split hidden in a function body is
    exactly as dangerous and exactly as findable.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs = [
            (k, v) for k, v in zip(node.keys, node.values)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
            and _looks_like_a_builder(v)
        ]
        if len(pairs) >= _MIN_ENTRIES:
            found.append((node.lineno, len(pairs)))
    return found


def _scan(rel_path: str):
    return _builder_registry_dicts(
        ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
    )


# ---------------------------------------------------------------------------
# The detector's own positive control
# ---------------------------------------------------------------------------

def test_the_scan_finds_the_one_registry_that_should_exist():
    """POSITIVE CONTROL — a scan that matches nothing anywhere is not evidence.

    If this fails, the two "no second registry" tests below are passing
    vacuously and prove nothing, which is the shape this repo calls an
    unasserted denominator.
    """
    home_path, home_func = SINGLE_HOME
    hits = _scan(home_path)
    assert hits, (
        f"The AST scan can no longer find the roster in {home_path}::{home_func} "
        "— it has stopped matching anything, so the 'no second registry' tests "
        "below are vacuous. Fix the scan, do not trust their green."
    )
    assert max(n for _, n in hits) >= 16, (
        f"{home_path} roster shrank below F-28's measured 16 as seen by the "
        "scan — either the roster moved or the matcher is only partly working."
    )


# ---------------------------------------------------------------------------
# The re-split detector
# ---------------------------------------------------------------------------

def test_pipeline_declares_no_second_builder_registry():
    """``pipeline`` must not carry a roster of its own. This IS F-28."""
    assert not hasattr(pl, "_STRATEGY_BUILDERS"), (
        "pipeline._STRATEGY_BUILDERS is back. It was DELETED on 2026-09-09 "
        "(audit F-28, operator decision DEC-20260909-ROLLBACK-BUILDER-REGISTRY "
        "'collapse_to_one_registry'): as a second roster it drifted to 16 names "
        "against the intent layer's 55, which made the documented rollback "
        "MULTI_STRATEGY_INTENT_LAYER=false a 78% capability outage. Register the "
        f"strategy in {SINGLE_HOME[0]}::{SINGLE_HOME[1]} instead — that is the "
        "one home, and pipeline.strategy_builders() reads it."
    )
    hits = _scan("src/runtime/pipeline.py")
    assert not hits, (
        "src/runtime/pipeline.py declares a strategy->builder dict literal at "
        f"line(s) {[ln for ln, _ in hits]}. That is the F-28 re-split, whatever "
        "it is named."
    )


def test_no_module_under_src_declares_a_second_builder_registry():
    """Repo-wide, so the re-split cannot simply move to a new file.

    Population: every ``*.py`` under ``src/``, scanned — not sampled. The one
    home is the only permitted hit.
    """
    offenders = {}
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == SINGLE_HOME[0]:
            continue
        hits = _builder_registry_dicts(ast.parse(path.read_text(encoding="utf-8")))
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "A second strategy->builder registry has appeared: "
        f"{offenders}. The roster has ONE home ({SINGLE_HOME[0]}::"
        f"{SINGLE_HOME[1]}); a second copy is audit F-28 recurring, and F-28 "
        "was a 78% outage on the documented rollback."
    )


# ---------------------------------------------------------------------------
# The behavioural half — the rollback path reads the SAME roster
# ---------------------------------------------------------------------------

def test_the_rollback_path_resolves_through_the_single_roster(monkeypatch):
    """Swap the single home's roster; the ROLLBACK path must see the swap.

    This is the assertion that would have caught F-28 on the day it drifted:
    before the collapse, ``multiplexed_signal_builder`` read a module-local dict
    and this swap would have been invisible to it.

    ⚠️ **The obvious form of this test does not work, and it is worth saying
    why.** Swapping the roster for one that omits every real name and asserting
    ``side == "none"`` PASSES against a planted module-local dict — measured
    while writing this: plant ``{"turtle_soup": turtle_soup_signal_builder}``
    into ``multiplexed_signal_builder`` and the assertion still holds, because
    the real builder also returns ``none`` on a bare settings dict. That is an
    unasserted denominator: a green for the wrong reason. So the sentinel
    builder is registered under a REAL strategy name and must be OBSERVED
    FIRING — a path reading any other roster calls the real builder instead and
    ``fired`` stays empty.
    """
    fired = []

    def _sentinel_builder(_settings):
        fired.append(1)
        return {"symbol": "BTCUSDT", "side": "buy",
                "meta": {"strategy_name": "turtle_soup", "sentinel": True}}

    monkeypatch.setattr(im, "_resolve_builders",
                        lambda: {"turtle_soup": _sentinel_builder})
    monkeypatch.setattr(pl, "STRATEGIES", ["turtle_soup"])

    assert pl.strategy_builders() == {"turtle_soup": _sentinel_builder}

    out = pl.multiplexed_signal_builder({"SYMBOL": "BTCUSDT"})
    assert fired == [1], (
        "multiplexed_signal_builder did not consult the single home — it "
        "resolved 'turtle_soup' from somewhere else. That is F-28: the "
        "rollback path reading a roster of its own."
    )
    assert out["side"] == "buy"
    assert out["meta"].get("sentinel") is True


def test_rollback_and_intent_paths_agree_on_the_roster():
    """The two entry points must resolve the SAME names — no diff, in either
    direction. A superset is not good enough: an intent-only name is what F-28
    was (35 live legs), and a rollback-only name is the mirror defect."""
    rollback = set(pl.strategy_builders())
    intent = set(im._resolve_builders())
    assert rollback == intent, {
        "in_rollback_only": sorted(rollback - intent),
        "in_intent_only": sorted(intent - rollback),
    }


def test_builders_override_is_honoured_and_does_not_reach_the_roster():
    """The injection point that replaced the module global tests used to patch."""
    calls = []

    def _fires(_settings):
        calls.append(1)
        return {"symbol": "BTCUSDT", "side": "buy",
                "meta": {"strategy_name": "_injected"}}

    import unittest.mock as _mock
    with _mock.patch.object(pl, "STRATEGIES", ["_injected"]):
        out = pl.multiplexed_signal_builder({"SYMBOL": "BTCUSDT"},
                                            builders={"_injected": _fires})
    assert out["side"] == "buy"
    assert out["meta"]["strategy_name"] == "_injected"
    assert calls == [1]
    # The override is per-call and leaves the real roster untouched.
    assert "_injected" not in pl.strategy_builders()


# ---------------------------------------------------------------------------
# The F-28 measurement, re-run as an assertion
# ---------------------------------------------------------------------------

def _declared_strategies():
    cfg = yaml.safe_load((REPO_ROOT / "config" / "strategies.yaml").read_text(encoding="utf-8"))
    return cfg["strategies"] if isinstance(cfg, dict) and "strategies" in cfg else cfg


def test_every_live_leg_is_buildable_on_both_paths():
    """The F-28 gap itself: 35 of 45 live legs used to be unbuildable on the
    rollback path. Both paths now read one roster, so the number is 0 of 45 by
    construction — this asserts it stays that way, and would also catch a live
    leg registered in no roster at all."""
    declared = _declared_strategies()
    live = [
        name for name, cfg in declared.items()
        if str((cfg or {}).get("execution", "live")).lower() == "live"
    ]
    assert live, "no execution:live strategy found — the population is empty, " \
                 "so a green here would mean nothing"

    roster = pl.strategy_builders()
    missing = sorted(n for n in live if n not in roster)
    assert not missing, (
        f"{len(missing)} of {len(live)} execution:live strategies have no "
        f"builder in the single roster: {missing}. On the rollback path each is "
        "one warning and a skipped leg — the F-28 shape."
    )
