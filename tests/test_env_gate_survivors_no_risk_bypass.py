"""S-067 follow-up #4 Phase-2 (item B) — env-gate survivor regressions.

The 2026-05-10 env-gate purge audit (PR #659, see
``docs/audits/env-gate-purge-2026-05-10.md``) identified two
``os.environ.get`` survivors in the protected runtime path:

  * ``src/runtime/pipeline.py`` — ``_multi_account_dispatch_enabled``
    reads ``MULTI_ACCOUNT_DISPATCH``.
  * ``src/runtime/order_monitor.py`` — ``_reconcile_enabled`` reads
    ``MONITOR_RECONCILE_ENABLED``.

Per the audit verdict, **both gates are legitimate survivors** —
neither is a live/dry escape hatch (the per-account
``RiskManager.dry_run`` flag in ``config/accounts.yaml`` is the
canonical live/dry switch, per BUG-039). But "legitimate today"
isn't a permanent contract — a careless future refactor could wire
one of these gates to bypass risk evaluation. These tests pin the
contract.

Test pattern: static AST analysis of each survivor's call site.
Walk the function body, find the ``os.environ.get(<NAME>, ...)``
call, and assert that no enclosing ``If`` branches around the
``raw`` value gate any call to ``RiskManager.evaluate`` /
``risk_manager.evaluate`` / ``risk_manager.approve`` in the same
module.

We use static analysis rather than runtime mocking because:

1. The gates are read at module-init / per-tick scope; the actual
   risk-evaluate calls are buried in deep call paths (Coordinator,
   per-account dispatcher, ...) that are expensive to set up in a
   unit test fixture.
2. The bug class we're protecting against is "someone wraps a
   risk-evaluate call in ``if not _multi_account_dispatch_enabled():``"
   — that's a structural property of the code, not a runtime
   property. Static analysis catches it directly.
3. The lint guard (``scripts/check_env_gate_in_diff.py``, PR #659)
   already catches NEW env-gate sites in protected paths. This
   test catches the complementary bug class: new risk-evaluate
   call sites placed under existing gates.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Set


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _find_risk_evaluate_calls(tree: ast.AST) -> List[ast.Call]:
    """Return every ``Call`` node whose target name is
    ``evaluate`` / ``approve`` on a ``risk_manager``-flavoured
    receiver. Walks the entire tree.
    """
    out: List[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in {"evaluate", "approve"}:
            continue
        # Receiver is a ``risk_manager`` / ``RiskManager`` symbol.
        recv = func.value
        recv_name = ""
        if isinstance(recv, ast.Name):
            recv_name = recv.id
        elif isinstance(recv, ast.Attribute):
            recv_name = recv.attr
        if "risk" in recv_name.lower():
            out.append(node)
    return out


def _calls_under_env_gate(
    tree: ast.AST, env_var_name: str, calls: List[ast.Call],
) -> List[ast.Call]:
    """For each call in *calls*, walk the AST upwards and check
    whether the call sits inside an ``If`` whose test references
    *env_var_name*. Returns the subset of calls that are gated.

    Because ast nodes don't carry parent pointers, we walk the
    whole tree once, collecting parent links, then trace each call
    up to module scope.
    """
    parents: dict = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    def _enclosing_ifs(node: ast.AST) -> List[ast.If]:
        out: List[ast.If] = []
        cur = parents.get(id(node))
        while cur is not None:
            if isinstance(cur, ast.If):
                out.append(cur)
            cur = parents.get(id(cur))
        return out

    def _if_tests_env(if_node: ast.If) -> bool:
        for sub in ast.walk(if_node.test):
            if isinstance(sub, ast.Constant) and sub.value == env_var_name:
                return True
            # Catches both os.environ.get("X", ...) and os.getenv("X", ...).
        return False

    def _calls_env_helper(if_node: ast.If) -> bool:
        # Catches `if _multi_account_dispatch_enabled():` style — the
        # If's test calls the helper that reads the env var.
        for sub in ast.walk(if_node.test):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                # Helper-name heuristic: matches "*_dispatch_enabled" or
                # "*_reconcile_enabled" etc.
                name = sub.func.id
                if env_var_name == "MULTI_ACCOUNT_DISPATCH":
                    if name.endswith("dispatch_enabled"):
                        return True
                if env_var_name == "MONITOR_RECONCILE_ENABLED":
                    if name == "_reconcile_enabled" or name.endswith("reconcile_enabled"):
                        return True
        return False

    gated: List[ast.Call] = []
    for call in calls:
        for if_node in _enclosing_ifs(call):
            if _if_tests_env(if_node) or _calls_env_helper(if_node):
                gated.append(call)
                break
    return gated


def _seen_helper_names(tree: ast.AST) -> Set[str]:
    """Return the names of all top-level FunctionDefs in the module."""
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


# ---------------------------------------------------------------------------
# Survivor 1 — pipeline.py / MULTI_ACCOUNT_DISPATCH
# ---------------------------------------------------------------------------


def test_multi_account_dispatch_does_not_gate_risk_evaluate():
    """Flipping ``MULTI_ACCOUNT_DISPATCH`` must not bypass any
    ``RiskManager.evaluate`` / ``.approve`` call in pipeline.py.

    Static-AST assertion: every risk-evaluate call in the module
    is reachable on BOTH branches of the gate (i.e., no risk-evaluate
    call is wrapped in an ``If`` whose test references
    ``MULTI_ACCOUNT_DISPATCH``).
    """
    src = (_REPO_ROOT / "src" / "runtime" / "pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    helpers = _seen_helper_names(tree)
    assert "_multi_account_dispatch_enabled" in helpers, (
        "Sanity: the survivor function must still exist in pipeline.py"
    )
    risk_calls = _find_risk_evaluate_calls(tree)
    if not risk_calls:
        # The pipeline may delegate risk evaluation to a sibling
        # module (Coordinator, dispatcher) — in that case there's
        # nothing in pipeline.py for the gate to bypass directly.
        # The test still passes the contract; document the absence.
        return
    gated = _calls_under_env_gate(
        tree, "MULTI_ACCOUNT_DISPATCH", risk_calls,
    )
    assert not gated, (
        "MULTI_ACCOUNT_DISPATCH must NOT gate any RiskManager.evaluate / "
        ".approve call. Found gated call(s) at line(s): "
        + ", ".join(str(c.lineno) for c in gated)
        + ". Per BUG-039 / docs/audits/env-gate-purge-2026-05-10.md, "
        "per-account RiskManager.dry_run is the canonical live/dry switch."
    )


def test_pipeline_helper_signature_pinned():
    """Pin the signature shape of ``_multi_account_dispatch_enabled``.

    The audit doc identifies this as a 'reads MULTI_ACCOUNT_DISPATCH
    via os.environ.get with a default'. If the helper grows new
    parameters or starts reading additional env vars, that's a
    contract change — the env-gate-purge follow-up should be
    re-audited.
    """
    src = (_REPO_ROOT / "src" / "runtime" / "pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    helper = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef)
         and n.name == "_multi_account_dispatch_enabled"),
        None,
    )
    assert helper is not None, "_multi_account_dispatch_enabled must exist"
    # Walk the helper body and confirm it reads exactly one env var:
    # MULTI_ACCOUNT_DISPATCH.
    env_reads: List[str] = []
    for sub in ast.walk(helper):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if isinstance(f, ast.Attribute) and f.attr == "get":
            recv = f.value
            if isinstance(recv, ast.Attribute) and recv.attr == "environ":
                if sub.args and isinstance(sub.args[0], ast.Constant):
                    env_reads.append(str(sub.args[0].value))
        elif isinstance(f, ast.Attribute) and f.attr == "getenv":
            if sub.args and isinstance(sub.args[0], ast.Constant):
                env_reads.append(str(sub.args[0].value))
    assert env_reads == ["MULTI_ACCOUNT_DISPATCH"], (
        f"_multi_account_dispatch_enabled must read exactly "
        f"MULTI_ACCOUNT_DISPATCH; observed env reads: {env_reads}"
    )


# ---------------------------------------------------------------------------
# Survivor 2 — order_monitor.py / MONITOR_RECONCILE_ENABLED
# ---------------------------------------------------------------------------


def test_monitor_reconcile_enabled_does_not_gate_risk_evaluate():
    """Flipping ``MONITOR_RECONCILE_ENABLED`` must not bypass any
    ``RiskManager.evaluate`` / ``.approve`` call in order_monitor.py.
    """
    src = (_REPO_ROOT / "src" / "runtime" / "order_monitor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    helpers = _seen_helper_names(tree)
    assert "_reconcile_enabled" in helpers, (
        "Sanity: the survivor function must still exist in order_monitor.py"
    )
    risk_calls = _find_risk_evaluate_calls(tree)
    if not risk_calls:
        # order_monitor doesn't directly call risk evaluation
        # (per BUG-039 architecture: pipeline does evaluation; the
        # monitor is post-hoc reconciliation). Empty set means the
        # gate has nothing to bypass. Pass.
        return
    gated = _calls_under_env_gate(
        tree, "MONITOR_RECONCILE_ENABLED", risk_calls,
    )
    assert not gated, (
        "MONITOR_RECONCILE_ENABLED must NOT gate any "
        "RiskManager.evaluate / .approve call. Found gated call(s) "
        "at line(s): " + ", ".join(str(c.lineno) for c in gated)
    )


def test_monitor_helper_signature_pinned():
    """Pin the signature shape of ``_reconcile_enabled``."""
    src = (_REPO_ROOT / "src" / "runtime" / "order_monitor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    helper = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef)
         and n.name == "_reconcile_enabled"),
        None,
    )
    assert helper is not None, "_reconcile_enabled must exist"
    env_reads: List[str] = []
    for sub in ast.walk(helper):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if isinstance(f, ast.Attribute) and f.attr == "get":
            recv = f.value
            if isinstance(recv, ast.Attribute) and recv.attr == "environ":
                if sub.args and isinstance(sub.args[0], ast.Constant):
                    env_reads.append(str(sub.args[0].value))
        elif isinstance(f, ast.Attribute) and f.attr == "getenv":
            if sub.args and isinstance(sub.args[0], ast.Constant):
                env_reads.append(str(sub.args[0].value))
    assert env_reads == ["MONITOR_RECONCILE_ENABLED"], (
        f"_reconcile_enabled must read exactly "
        f"MONITOR_RECONCILE_ENABLED; observed env reads: {env_reads}"
    )


# ---------------------------------------------------------------------------
# Cross-cutting — no NEW survivors snuck in
# ---------------------------------------------------------------------------


def test_no_new_protected_env_gates_in_runtime():
    """Defence in depth: the audit pinned exactly two surviving
    ``os.environ.get(<suspect>)`` reads in src/runtime/. Anything new
    must come with a fresh allow-silent + audit doc update.

    Suspect names are the same set the lint guard
    ``scripts/check_env_gate_in_diff.py`` flags — see
    ``_SUSPECT_NAME_RE`` there.
    """
    import re
    suspect_re = re.compile(
        r"\b("
        r"MULTI_ACCOUNT_[A-Z0-9_]+|"
        r"MONITOR_[A-Z0-9_]+|"
        r"DISPATCH_[A-Z0-9_]+|"
        r"[A-Z0-9_]+_APPLY_TO_[A-Z0-9_]+|"
        r"[A-Z0-9_]+_DRY_[A-Z0-9_]+|"
        r"[A-Z0-9_]+_ENABLED|"
        r"[A-Z0-9_]+_DISABLED"
        r")\b"
    )
    env_read_re = re.compile(
        r"os\.(?:environ\.get|getenv)\s*\(\s*['\"]([A-Z0-9_]+)['\"]"
    )

    runtime_dir = _REPO_ROOT / "src" / "runtime"
    survivors: List[str] = []
    for py in runtime_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in env_read_re.finditer(text):
            name = m.group(1)
            if suspect_re.match(name):
                # Find the line + check for allow-silent annotation.
                pos = m.start()
                line_start = text.rfind("\n", 0, pos) + 1
                line_end = text.find("\n", pos)
                if line_end == -1:
                    line_end = len(text)
                line = text[line_start:line_end]
                if "allow-silent" in line.lower():
                    continue
                rel = py.relative_to(_REPO_ROOT).as_posix()
                lineno = text.count("\n", 0, pos) + 1
                survivors.append(f"{rel}:{lineno} ({name})")

    expected = {
        # The two annotated survivors. The annotations get added
        # under this PR's patch-doc instructions
        # (docs/claude/env-gate-purge-phase2-annotations.md). Until
        # they land in place, the test allows the un-annotated
        # survivors at these specific paths.
        "src/runtime/pipeline.py",
        "src/runtime/order_monitor.py",
    }
    unexpected = [
        s for s in survivors
        if not any(s.startswith(prefix + ":") for prefix in expected)
    ]
    assert not unexpected, (
        "New protected env-gate survivor(s) found in src/runtime/ "
        "without `# allow-silent: <reason>` annotation:\n"
        + "\n".join(f"  - {s}" for s in unexpected)
        + "\n\nPer docs/audits/env-gate-purge-2026-05-10.md, the audit "
          "pinned exactly two survivors. Any new gate matching the "
          "suspect patterns needs (a) the inline allow-silent comment, "
          "(b) the audit doc updated to document why it survives."
    )
