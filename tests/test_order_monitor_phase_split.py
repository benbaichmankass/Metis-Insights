"""The monitor phase split must stay COMPLETE as phases are added.

`/api/diag/tick_cost` measured `order_monitor` at 48.7s mean / 52.8s max --
46.8% of a 104s tick, against the operator's 60s exit-evaluation ask. The split
added 2026-08-10 says WHERE that goes, and its whole value is that the parts sum
to the whole: a split covering 13 of 15 phases would let a reader conclude the
covered ones ARE the cost when they are a lower bound on it. That is the
`rCoverage` / `attributed_pct` discipline one level down, and the failure mode is
silent -- the next phase added is individually cheap, exactly like every
component in both June 2026 wedges.

So this is a STRUCTURAL test over the source: every `try:`-guarded phase call in
`run_monitor_tick`, plus the per-strategy loop, must be inside a `_phase(...)`.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "runtime" / "order_monitor.py"


# THE TICK WAS SPLIT 2026-08-12 (Tier-2 decouple): the phases now live in TWO
# functions -- `run_exit_evaluation_tick` (the exit half, 1 phase) and
# `run_reconciliation_tick` (the hygiene half, 13) -- with `run_monitor_tick`
# reduced to a wrapper that calls both. Scoping this scan to `run_monitor_tick`
# alone would have made it pass over a body containing ZERO phases, i.e. a
# vacuous green: the completeness property these tests exist to protect is about
# the phases WHEREVER they live, so the scan follows them.
_TICK_FUNCS = ("def run_exit_evaluation_tick", "def run_reconciliation_tick")


def _func_body(prefix: str) -> list[str]:
    lines = SRC.read_text().splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith(prefix))
    j = next((k for k in range(i + 1, len(lines)) if lines[k].startswith("def ")),
             len(lines))
    return lines[i:j]


def _run_monitor_tick_body() -> list[str]:
    """Both halves concatenated — the region the old single function covered."""
    body: list[str] = []
    for prefix in _TICK_FUNCS:
        body += _func_body(prefix)
    return body


def test_the_split_covers_both_halves_not_just_one():
    """Guards the scan itself. If a half is renamed, `_run_monitor_tick_body`
    must fail loudly rather than silently scan a smaller region and pass."""
    for prefix in _TICK_FUNCS:
        assert _func_body(prefix), f"{prefix} not found — the scan lost a half"
    assert 'with _phase("strategy_monitor_loop")' in "\n".join(
        _func_body("def run_exit_evaluation_tick")), "exit half lost its phase"


def test_every_guarded_monitor_phase_is_inside_the_split():
    """A phase added without a `_phase(...)` wrap makes the split under-report.

    The call shape this matches is the one every phase uses: a `try:` whose first
    statement invokes a module-level `_helper(db)`.
    """
    body = _run_monitor_tick_body()
    call = re.compile(r"^\s*(?:\w+ = )?(_\w+)\(db\)$")
    unwrapped = []
    for idx, line in enumerate(body):
        m = call.match(line)
        if not m or m.group(1) == "_phase":
            continue
        # The wrap is the immediately-preceding line.
        if "_phase(" not in (body[idx - 1] if idx else ""):
            unwrapped.append(m.group(1))
    assert not unwrapped, (
        "monitor phase(s) not inside the tick-cost split, so the per-phase "
        f"numbers would under-report the 48.7s they explain: {unwrapped}")


def test_the_per_strategy_loop_is_measured_too():
    """The loop is the biggest single candidate; unmeasured it is a blind spot.

    Without it the 13 post-loop phases could sum to a fraction of `order_monitor`
    and a reader would have no idea whether the remainder is the loop or an
    unwrapped phase -- two findings with completely different fixes.
    """
    body = _run_monitor_tick_body()
    loop = next(i for i, ln in enumerate(body)
                if ln.strip().startswith("for strategy_name in _load_strategies"))
    assert '_phase("strategy_monitor_loop")' in body[loop - 1], (
        "the per-strategy monitor loop is not inside the split")


def test_phase_names_are_prefixed_and_distinct():
    """`monitor.` prevents collision with src/main.py's top-level hook names.

    A collision would silently MERGE two different costs under one name -- a
    number that is real, labelled, and means something else.
    """
    body = "\n".join(_run_monitor_tick_body())
    names = re.findall(r'_phase\("([^"]+)"\)', body)
    assert names, "no phases found -- the split is gone"
    assert len(names) == len(set(names)), f"duplicate phase names: {names}"
    src = SRC.read_text()
    assert 'return hook(f"monitor.{name}")' in src, (
        "phase names are no longer namespaced under `monitor.`")


def test_the_wrapper_never_swallows_and_never_blocks_a_tick():
    """Two properties the live monitor depends on, pinned against a rewrite.

    It must fall back to a no-op if the measurement module cannot be imported
    (instrumentation must never stop a tick), and it must NOT catch the wrapped
    body's exceptions -- each phase keeps its own handler, and a phase that burns
    time and then throws has to appear in the split rather than vanish from it.
    """
    src = SRC.read_text()
    i = src.index("def _phase(")
    j = src.index("\ndef ", i + 1)
    fn = src[i:j]
    assert "contextlib.nullcontext()" in fn, "no no-op fallback on import failure"
    # The only `except` in the wrapper guards the IMPORT, not the body: the body
    # is never executed inside this function at all (it returns a context
    # manager), which is what makes the non-swallowing property structural.
    assert "yield" not in fn, (
        "_phase became a generator-based CM; the body now runs inside it and "
        "could be swallowed -- it must stay a function that RETURNS a manager")
