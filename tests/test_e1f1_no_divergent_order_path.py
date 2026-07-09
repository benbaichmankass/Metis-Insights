"""E1-F1 (full-system audit 2026-07-09) — no divergent live-order path.

The trading pipeline (`src/runtime/pipeline.py`) must route every order
through the ONE sanctioned path: `Coordinator.multi_account_execute` /
`multi_account_execute_typed`, where the per-account `RiskManager.position_size`
sizes it and the strategy-supplied SL/TP rides along.

The bug this locks closed: the legacy single-client `else` branch used to
size a hardcoded placeholder qty (1.0) and call `safe_place_order`, which —
with the real Bybit adapter `src/main.py` injects — would send a NAKED,
un-sized, SL/TP-less market order straight to the exchange. It was latent
(builders populate SL/TP, `MULTI_ACCOUNT_DISPATCH` defaults on) but nothing
*guaranteed* it, so the class stayed reachable. The fix removed the divergent
placement outright; the branch now refuses.

These are static (AST) checks — no pandas/runtime deps — so they run in any
environment. They fail if a future refactor re-introduces a direct exchange
placement or the `safe_place_order` seam into the pipeline module.
"""
from __future__ import annotations

import ast
from pathlib import Path

_PIPELINE = Path(__file__).resolve().parents[1] / "src" / "runtime" / "pipeline.py"


def _tree() -> ast.AST:
    return ast.parse(_PIPELINE.read_text())


def test_pipeline_does_not_import_or_reference_safe_place_order():
    """`safe_place_order` (the legacy placement seam) must not appear in the
    pipeline as code — no import, no call, no name reference. Comments/docstrings
    describing the removed path are fine (they are not AST nodes)."""
    tree = _tree()
    refs: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(alias.name == "safe_place_order" for alias in node.names):
                refs.append(getattr(node, "lineno", -1))
        elif isinstance(node, ast.Name) and node.id == "safe_place_order":
            refs.append(getattr(node, "lineno", -1))
        elif isinstance(node, ast.Attribute) and node.attr == "safe_place_order":
            refs.append(getattr(node, "lineno", -1))
    assert not refs, (
        "E1-F1: src/runtime/pipeline.py must not reference safe_place_order "
        f"(the removed divergent live-order path). Found at lines {sorted(set(refs))}."
    )


def test_pipeline_places_no_order_directly_on_a_client():
    """No `<something>.place_order(...)` call anywhere in the pipeline module.

    The only sanctioned order dispatch is `Coordinator.multi_account_execute` /
    `multi_account_execute_typed`; a bare `client.place_order(...)` in the
    pipeline is the naked-order bypass this audit item removed."""
    tree = _tree()
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "place_order":
                hits.append(getattr(node, "lineno", -1))
    assert not hits, (
        "E1-F1: src/runtime/pipeline.py must not call .place_order() directly — "
        "orders route only through Coordinator.multi_account_execute. "
        f"Found at lines {sorted(set(hits))}."
    )
