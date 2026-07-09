#!/usr/bin/env python3
"""async-route blocking guard — keep synchronous work OFF uvicorn's event loop.

RISK-3 / BL-20260707-HEALTHAPI-ACCTBAL-BLOCKING-DB. A blocking sqlite / file /
subprocess / ``time.sleep`` / ``requests`` call inside an ``async def`` FastAPI
route runs on uvicorn's single event loop and starves EVERY concurrent request
for its duration — which is why the sync health endpoints (`/api/bot/health/*`)
intermittently failed in the same window as `/accounts/balances`: one blocking
DB read on the loop froze them all. The structural fix is that a handler must
either

  * be a **plain ``def``** (FastAPI runs sync routes in a threadpool, so they can
    never block the loop), or
  * be ``async def`` and offload every blocking call via ``asyncio.to_thread`` /
    ``loop.run_in_executor`` (and genuinely ``await`` something).

This guard makes that a merge gate — the same way the env-gate / qty-legalization
guards killed their bug classes. It fails the build when a FastAPI route:

  A. is ``async def`` but ``await``s nothing (an entirely synchronous handler
     masquerading as a coroutine — it should be a plain ``def``); or
  B. is ``async def`` and calls a known blocking primitive DIRECTLY in its body
     (not wrapped in ``asyncio.to_thread`` / ``run_in_executor``).

Detection is **AST-based**, so a comment / docstring / string that merely
mentions one of these names is not a violation — only a real call is. A
genuinely-reviewed exception carries an inline ``# async-route-allow: <reason>``
comment on the offending line (the ``def`` line for A, the call line for B),
mirroring the env-gate guard's ``# allow-silent``.

Usage::

    python scripts/ci/check_async_route_blocking.py              # scan src/web/api (CI default)
    python scripts/ci/check_async_route_blocking.py path ...     # scan explicit paths

Exit 0 = clean; exit 1 = at least one violation (offending ``file:line`` printed).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# FastAPI route decorators (``@router.get`` / ``@app.post`` / ...). ``websocket``
# handlers are inherently async (they await accept/receive) and are NOT scanned.
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})

# Calls that hand work to a worker thread — a blocking primitive lexically inside
# one of these is fine (that's the sanctioned fix), so Check B skips it.
_OFFLOAD_CALLS = frozenset({"to_thread", "run_in_executor", "run_account_read", "run_in_threadpool"})

# (module, {attr, ...}) → a blocking primitive that must never run on the loop.
_BLOCKING_ATTR_BY_MODULE = {
    "sqlite3": frozenset({"connect"}),
    "subprocess": frozenset({"run", "call", "check_output", "check_call", "Popen"}),
    "time": frozenset({"sleep"}),
    "requests": frozenset({"get", "post", "put", "delete", "patch", "head", "request"}),
}

_ALLOW_MARKER = "# async-route-allow"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_route(fn: ast.AsyncFunctionDef) -> bool:
    """True if *fn* is decorated as an HTTP route (``@x.get`` / ``@x.post(...)``)."""
    for dec in fn.decorator_list:
        node = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(node, ast.Attribute) and node.attr in _HTTP_METHODS:
            return True
    return False


def _blocking_call_name(node: ast.Call) -> str | None:
    """Return ``"module.attr"`` if *node* is a known blocking primitive, else None."""
    func = node.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return None
    mod, attr = func.value.id, func.attr
    if attr in _BLOCKING_ATTR_BY_MODULE.get(mod, frozenset()):
        return f"{mod}.{attr}"
    return None


def _offloaded_node_ids(fn: ast.AsyncFunctionDef) -> set[int]:
    """ids of every node lexically inside an offload call (``to_thread(...)`` etc.)."""
    ids: set[int] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
        if name in _OFFLOAD_CALLS:
            for inner in ast.walk(node):
                ids.add(id(inner))
    return ids


def _has_await(fn: ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
            return True
    return False


def find_violations_in_source(source: str, rel_path: str) -> List[Tuple[int, str]]:
    """Return ``[(lineno, message), ...]`` for route violations in *source*.

    A per-file helper (no filesystem) so the guard's self-test can feed it a
    planted-violation string directly.
    """
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return []
    lines = source.splitlines()

    def _allowed(lineno: int) -> bool:
        idx = lineno - 1
        return 0 <= idx < len(lines) and _ALLOW_MARKER in lines[idx]

    hits: List[Tuple[int, str]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef) or not _is_route(fn):
            continue
        # Check A: async route that awaits nothing → should be a plain ``def``.
        if not _has_await(fn) and not _allowed(fn.lineno):
            hits.append((
                fn.lineno,
                f"async route '{fn.name}' awaits nothing — make it a plain 'def' "
                f"(FastAPI runs sync routes in a threadpool; async-without-await "
                f"runs the whole handler on the event loop)",
            ))
        # Check B: direct blocking primitive on the event loop.
        offloaded = _offloaded_node_ids(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            name = _blocking_call_name(node)
            if name is None or id(node) in offloaded:
                continue
            if _allowed(node.lineno):
                continue
            hits.append((
                node.lineno,
                f"blocking call {name}() on the event loop in async route "
                f"'{fn.name}' — wrap it in asyncio.to_thread(...) or make the "
                f"route a plain 'def'",
            ))
    return hits


def scan_paths(paths: Iterable[Path], root: Path) -> List[str]:
    violations: List[str] = []
    files: List[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(p.rglob("*.py")))
        elif p.suffix == ".py":
            files.append(p)
    for f in files:
        rel = _rel(f, root)
        try:
            source = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, msg in find_violations_in_source(source, rel):
            violations.append(f"{rel}:{lineno}: {msg}")
    return violations


def main(argv: List[str]) -> int:
    root = _repo_root()
    targets = [Path(a) for a in argv[1:]] if len(argv) > 1 else [root / "src" / "web" / "api"]

    violations = scan_paths(targets, root)
    if not violations:
        print("async-route blocking guard: OK — no synchronous work runs on the event loop.")
        return 0

    print("async-route blocking guard: FAIL — a FastAPI route blocks the event loop.\n")
    print("A blocking call in an async route starves EVERY concurrent request while")
    print("it runs (RISK-3, BL-20260707-HEALTHAPI-ACCTBAL-BLOCKING-DB). Fix by making")
    print("the route a plain 'def' (FastAPI threadpools it) or offloading the blocking")
    print("call via asyncio.to_thread(...)/run_in_executor(...):\n")
    for v in violations:
        print(f"  {v}")
    print(
        f"\nIf a call is a genuinely-reviewed exception, annotate its line with "
        f"'{_ALLOW_MARKER}: <reason>'."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
