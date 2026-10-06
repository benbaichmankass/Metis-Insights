"""The prop executor tick must EMIT the trail step's own action rows.

ACTIVE-GEOMETRY lane, 2026-10-06. ``prop_trail.run_trail_step`` records every
per-position decision through ``CycleResult.log`` (``trail_amend`` /
``trail_skip`` -> ``res.actions``). The tick emitted ``res.actions`` once,
BEFORE the trail step ran, and after the step only ``res.reports`` and
``res.alerts`` -- so an SL amend on a prop account never reached the journal
and could not be counted from any session. This is a source-shape test: the
tick is a Playwright entry point and is not importable in CI.
"""
from __future__ import annotations

import re
from pathlib import Path

TICK = Path(__file__).resolve().parents[1] / "scripts" / "prop" / "prop_executor_tick.py"


def _src() -> str:
    return TICK.read_text(encoding="utf-8")


def test_trail_step_actions_are_emitted_after_the_step() -> None:
    src = _src()
    step = src.index("prop_trail.run_trail_step(")
    after = src[step:]
    # Marker captured BEFORE the step, emitted AFTER it -- same shape as the
    # reports / alerts markers that already existed.
    before = src[:step]
    assert re.search(r"n_act\s*,\s*n_rep\s*,\s*n_al\s*=\s*len\(res\.actions\)", before), (
        "the tick must snapshot len(res.actions) before run_trail_step")
    m = re.search(r"for a in res\.actions\[n_act:\]:\s*\n\s*emit\(\{\"action\": a\}", after)
    assert m, "res.actions appended by the trail step must be emitted after it"
    # And the emission sits before the function returns its exit code.
    ret = after.index("return EXIT_UNPARSED if res.halted else EXIT_OK")
    assert m.start() < ret


def test_prop_trail_records_decisions_through_res_log() -> None:
    """Positive control for the test above: the trail really does write its
    evidence to ``res.actions`` (via ``res.log``), so emitting that slice is
    what makes ``trail_amend`` / ``trail_skip`` observable."""
    trail = (TICK.parents[2] / "src" / "prop" / "prop_trail.py").read_text(encoding="utf-8")
    assert 'res.log("trail_amend"' in trail
    assert 'res.log("trail_skip"' in trail
    executor = (TICK.parents[2] / "src" / "prop" / "prop_executor.py").read_text(encoding="utf-8")
    assert re.search(r"def log\(self, what: str, \*\*kw: Any\) -> None:\s*\n\s*self\.actions\.append", executor)
