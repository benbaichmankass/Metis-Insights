"""CI guard: the closed→flat invariant's lookback must be CADENCE-DERIVED.

AUDIT F-12 (2026-09-09, operator-approved Tier-2). ``DEFAULT_WINDOW_SECONDS``
was 60 and **never overridden** — 1 of 1 production call sites — against a
measured invocation period of **≥ 101.9 s** (two independent measurements:
``/api/diag/tick_cost`` implying a 124.3 s mean, and a durable 888-row WARN
feed over 14.7 days whose once-per-tick inter-arrival gaps are n=140 with a
minimum of 101.9 s and **0 of 140 at or under 60 s**). So on every pass there
was a stretch of the timeline in which a trade could close and be examined by
nobody — **by arithmetic, not by failure**, and therefore not something a
runtime test or a soak could ever surface.

⚠️ **A CONSTANT PASSED EXPLICITLY IS NOT A FIX**, which is why this guard
rejects a literal rather than merely requiring the keyword: re-introducing
``window_seconds=60`` at the call site would satisfy a presence-only check
while reproducing the defect exactly.

Scope: production code under ``src/`` only. Tests legitimately pin a window to
make a fixture deterministic.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

#: Entry points whose window must be derived from the caller's own cadence.
_ENTRYPOINTS = {"check", "check_detailed"}

#: The module that owns the vocabulary — its own definitions are not calls.
_PRODUCER = "src/runtime/closed_flat_invariant.py"


def _invocations():
    """Yield ``(relpath, lineno, call_node)`` for every invariant entry-point
    call in ``src/``."""
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel == _PRODUCER:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "closed_flat_invariant" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover - a syntax error is another test's finding
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in _ENTRYPOINTS:
                owner = fn.value
                if isinstance(owner, ast.Name) and owner.id == "closed_flat_invariant":
                    yield rel, node.lineno, node


def test_the_guard_can_actually_find_the_call_site():
    """Positive control — a probe that finds nothing proves nothing.

    Without this, deleting or renaming the production call site would make the
    guard below vacuously green: an empty denominator reporting a clean pass is
    the unprovenanced-diagnostic sub-class C this repo names in CLAUDE.md.
    """
    found = list(_invocations())
    assert found, (
        "no closed_flat_invariant.check/check_detailed call found anywhere in "
        "src/ — either the invariant lost its wiring (a far bigger finding "
        "than this guard's) or this guard has stopped being able to see it."
    )


def test_every_production_invocation_passes_a_derived_window():
    for rel, lineno, node in _invocations():
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        assert "window_seconds" in kwargs, (
            f"{rel}:{lineno} calls the closed→flat invariant with no "
            f"window_seconds, so it inherits DEFAULT_WINDOW_SECONDS (60s) — "
            f"shorter than the measured invocation period (>= 101.9s), which "
            f"leaves >= 41% of every period examined by nobody. Derive it from "
            f"the caller's own MEASURED cadence (see "
            f"_closed_flat_wiring.derive_window_seconds)."
        )
        value = kwargs["window_seconds"]
        assert not isinstance(value, ast.Constant), (
            f"{rel}:{lineno} passes a LITERAL window_seconds="
            f"{value.value!r}. Passing the constant explicitly satisfies the "
            f"keyword and reproduces F-12 exactly: the window must be derived "
            f"from the caller's measured invocation interval, not declared."
        )


def test_cadence_basis_is_passed_too():
    """A window with no stated basis cannot be graded when it is read back."""
    for rel, lineno, node in _invocations():
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        if "check_detailed" not in ast.dump(node.func):
            continue
        assert "cadence_basis" in kwargs, (
            f"{rel}:{lineno} derives a window but does not say HOW. "
            f"`measured` and `bootstrap` are different amounts of trust and "
            f"the coverage soak must carry which one applied."
        )
