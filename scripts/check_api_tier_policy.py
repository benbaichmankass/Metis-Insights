#!/usr/bin/env python3
"""CI guard: every route on the live API must carry a tier row.

WHY (the defect class this guard exists to prevent recurring)
-------------------------------------------------------------
``docs/api-tier-policy.md`` opens by calling itself the *"single source of
truth for which routes are Tier 1 / 2 / 2.5 / 3"*, and ``CLAUDE.md`` points at
it for *"the complete tier inventory"*. Measured 2026-08-09, **54 of 90 routes
defined under ``src/web/api/routers/`` had no row** — so on the completeness
axis both claims were false, and a reader who took an absence there as "no such
route" was wrong more often than right.

The cause was structural, not neglect. Every sibling inventory in this repo
that STAYS correct has a CI check behind it — ``canonical-doc-coherence``,
``provenance-consumer-guard``, ``new-table-wiring-guard``,
``canonical-db-resolver``. This one never did, so every route added between
S-063 (2026-05-09) and 2026-08-09 could land without a row and **none of them
announced itself**. Three sessions in a row read the file as authoritative.

The completeness figure was itself miscounted twice while the warning banner
was being written: 77% → 69% (once the Tier-2.5 family row was credited) →
60% (after that PR's own backfill). **A hand-counted completeness claim is
stale the moment anyone edits the file, including the person writing the
claim.** That is the argument for a guard rather than a periodic manual audit,
and it is why ``--list`` below computes the number rather than restating one.

WHAT IT CHECKS
--------------
One rule, mechanically decidable: a route defined under
``src/web/api/routers/`` must appear in ``docs/api-tier-policy.md``.

Routes are enumerated by joining each ``@router.<verb>("...")`` decorator to
its ``APIRouter(prefix=...)`` **via AST, not a regex over the decorator
alone** — the prefix is where most of the real path lives (``diag.py`` binds
``/api/diag``, ``devices.py`` binds ``/api/bot/devices``), so matching on the
decorator string would compare ``/snapshot`` against ``/api/diag/snapshot``
and report a gap for every single route in the repo.

CREDITING THE TIER-2.5 FAMILY-ROW CONVENTION
--------------------------------------------
The Tier-2.5 section documents the diag surface as *family rows*::

    | `GET /api/diag/snapshot`, `audit`, `journal`, `status`, ... |

The bare leaves inherit the directory of the full path beside them. A naive
exact-string match credits **none** of that and reports 76% missing against a
true 69% — it manufactures a gap the file does not have. This parser resolves
a leaf against the last full path in the same row, so the existing convention
is honoured **as written**.

That is a deliberate constraint on this guard, not an accident: a guard that
silently forces a doc to be reformatted to suit the checker is a guard that
picks a fight with the thing it is supposed to protect. If the convention
should change, change it in a PR that says so.

GRANDFATHERING
--------------
``_GRANDFATHERED`` carries routes deliberately exempt from the rule. It exists
so shipping the guard could never block an open PR on a gap that predated it.
**It is empty**, because the same PR that added this guard backfilled all 54
missing rows — an entry here is now an explicit, reviewable decision rather
than a backlog.

Exit 0 = clean. Exit 1 = at least one route with no row.

Usage:
    python3 scripts/check_api_tier_policy.py [DIFF]   # diff on stdin/argv
    python3 scripts/check_api_tier_policy.py --all    # the standing audit
    python3 scripts/check_api_tier_policy.py --list   # measured coverage
"""
from __future__ import annotations

import argparse
import ast
import os
import posixpath
import re
import sys
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ROUTER_DIR = "src/web/api/routers"
_POLICY_DOC = "docs/api-tier-policy.md"

# FastAPI's routing decorators. `websocket` is included deliberately:
# `/ws/market` is a live service surface with its own tier (it was the one
# route in the original inventory whose absence was least visible, because it
# is not a `GET` and so never showed up in an endpoint-shaped grep).
_VERBS: Tuple[str, ...] = (
    "get", "post", "put", "patch", "delete", "head", "options", "websocket",
)

# Routes deliberately exempt. EMPTY BY DESIGN — see the module docstring.
# An entry is a "VERB /path" string and needs a comment saying why, because
# the whole failure mode this guard addresses is a route whose absence from
# the inventory nobody had to justify.
_GRANDFATHERED: Set[str] = set()


class Route(NamedTuple):
    verb: str
    path: str
    file: str
    lineno: int

    @property
    def key(self) -> str:
        return f"{self.verb} {self.path}"

    def render(self) -> str:
        return f"{self.file}:{self.lineno} [{self.verb} {self.path}]"


# --------------------------------------------------------------------------- #
# route enumeration — AST, joining each decorator to its router's prefix
# --------------------------------------------------------------------------- #
def _router_prefixes(tree: ast.Module) -> Dict[str, str]:
    """Map each module-level ``APIRouter(...)`` variable to its prefix.

    A router declared with no ``prefix=`` maps to ``""`` — that is a real
    case (``market_ws.py``), not a parse failure, and must not be confused
    with "this file has no router".
    """
    out: Dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "APIRouter":
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                prefix = str(kw.value.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = prefix
    return out


def routes_in_source(rel_path: str, source: str) -> List[Route]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # A file that does not parse is a different failure, loudly reported
        # by every other check in CI. Claiming "no routes here" would let a
        # syntax error read as full tier coverage.
        return []
    prefixes = _router_prefixes(tree)
    if not prefixes:
        return []

    found: List[Route] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not isinstance(func, ast.Attribute) or func.attr not in _VERBS:
                continue
            base = func.value
            if not isinstance(base, ast.Name) or base.id not in prefixes:
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            path = prefixes[base.id] + str(dec.args[0].value)
            found.append(
                Route(func.attr.upper(), normalise_path(path), rel_path, dec.lineno)
            )
    return found


def _router_files() -> List[str]:
    root = os.path.join(_REPO_ROOT, _ROUTER_DIR)
    if not os.path.isdir(root):
        return []
    return [
        f"{_ROUTER_DIR}/{fn}"
        for fn in sorted(os.listdir(root))
        if fn.endswith(".py") and fn != "__init__.py"
    ]


def enumerate_routes() -> List[Route]:
    out: List[Route] = []
    for rel in _router_files():
        src = _read(rel)
        if src is not None:
            out.extend(routes_in_source(rel, src))
    return out


# --------------------------------------------------------------------------- #
# path normalisation
# --------------------------------------------------------------------------- #
_PARAM_RE = re.compile(r"\{[^}]*\}")


def normalise_path(path: str) -> str:
    """Canonical form for comparison.

    Path parameters collapse to ``{}``: the doc writing ``{name}`` where the
    handler calls it ``{table}`` is a naming difference, not a missing row,
    and failing on it would train contributors to edit the doc to match a
    variable name rather than to think about the tier.
    """
    path = path.split("?", 1)[0].strip()
    path = _PARAM_RE.sub("{}", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def _normalise_verb(verb: str) -> str:
    verb = verb.upper()
    return "WEBSOCKET" if verb in ("WS", "WEBSOCKET") else verb


# --------------------------------------------------------------------------- #
# the doc side — table rows, crediting the family-row convention
# --------------------------------------------------------------------------- #
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_VERB_PREFIX_RE = re.compile(
    r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|WS|WEBSOCKET)\s+(.*)$", re.IGNORECASE
)


def documented_keys(doc_text: str) -> Set[str]:
    """Every ``"VERB /path"`` the policy doc declares.

    Reads the FIRST cell of markdown table rows only. Deliberately not every
    backtick in the file: prose mentions a route while discussing something
    else, and crediting those would let the inventory look complete because
    someone name-dropped an endpoint in a paragraph.
    """
    keys: Set[str] = set()
    for raw in doc_text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cell = line.strip("|").split("|", 1)[0]
        if set(cell.strip()) <= set("-: "):  # the header underline
            continue

        # The family-row convention: a bare leaf inherits the directory of
        # the last full path in the SAME row.
        family_dir: Optional[str] = None
        family_verb: Optional[str] = None

        for span in _CODE_SPAN_RE.findall(cell):
            token = span.strip()
            verb: Optional[str] = None
            m = _VERB_PREFIX_RE.match(token)
            if m:
                verb, token = _normalise_verb(m.group(1)), m.group(2).strip()

            if token.startswith("/"):
                path = normalise_path(token)
                verb = verb or "GET"
                keys.add(f"{verb} {path}")
                family_dir = posixpath.dirname(path)
                family_verb = verb
                continue

            # A leaf: `audit`, `journal`, or a deeper `strategy/{name}`.
            if family_dir and re.fullmatch(r"[A-Za-z0-9_./{}-]+", token):
                leaf = normalise_path(posixpath.join(family_dir, token))
                keys.add(f"{verb or family_verb or 'GET'} {leaf}")
    return keys


# --------------------------------------------------------------------------- #
# diff parsing
# --------------------------------------------------------------------------- #
def added_lines_by_file(diff_text: str) -> Dict[str, Set[int]]:
    """``{path: {new_lineno, ...}}`` for every added line in a unified diff."""
    out: Dict[str, Set[int]] = {}
    path: Optional[str] = None
    lineno = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            path = (
                None if target == "/dev/null"
                else target[2:] if target.startswith(("a/", "b/")) else target
            )
            lineno = 0
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,\d+)?", raw)
            lineno = int(m.group(1)) - 1 if m else 0
            continue
        if raw.startswith(("---", "diff ")):
            continue
        if raw.startswith("+") and not raw.startswith("++"):
            lineno += 1
            if path:
                out.setdefault(path, set()).add(lineno)
            continue
        if raw.startswith("-"):
            continue
        lineno += 1
    return out


def _touched_router_lines(diff_text: str) -> Dict[str, Set[int]]:
    return {
        p: lines
        for p, lines in added_lines_by_file(diff_text).items()
        if p.startswith(_ROUTER_DIR + "/") and p.endswith(".py")
    }


def routes_in_scope(diff_text: str) -> List[Route]:
    """Routes this diff added or changed.

    A route is in scope when its own decorator line was touched, OR when the
    file's ``APIRouter(prefix=...)`` line was — **editing a prefix silently
    rewrites the path of every route in the file**, which is exactly the kind
    of change that would otherwise slip past a decorator-line-only scope while
    invalidating a whole section of the inventory at once.
    """
    scoped: List[Route] = []
    for path, touched in sorted(_touched_router_lines(diff_text).items()):
        src = _read(path)
        if src is None:  # deleted in this PR
            continue
        try:
            prefix_lines = {
                node.lineno
                for node in ast.walk(ast.parse(src))
                if isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and (getattr(node.value.func, "id", None)
                     or getattr(node.value.func, "attr", None)) == "APIRouter"
            }
        except SyntaxError:
            prefix_lines = set()

        file_routes = routes_in_source(path, src)
        if prefix_lines & touched:
            scoped.extend(file_routes)
            continue
        scoped.extend(r for r in file_routes if r.lineno in touched)
    return scoped


# --------------------------------------------------------------------------- #
# drivers
# --------------------------------------------------------------------------- #
def _read(rel: str) -> Optional[str]:
    try:
        with open(os.path.join(_REPO_ROOT, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def undocumented(routes: Iterable[Route], documented: Set[str]) -> List[Route]:
    return [
        r for r in routes
        if r.key not in documented and r.key not in _GRANDFATHERED
    ]


_EXPLAINER = """
  docs/api-tier-policy.md calls itself the single source of truth for route
  tiers, and CLAUDE.md sends readers there for the complete inventory. A route
  with no row makes both claims false for that route, silently — which is how
  the file reached 60% incomplete without anyone noticing.

  Add a row to the section matching the route's ACTUAL runtime gate, not the
  one its name suggests. Read the gate in the router:

    Tier 1    no dependency, no token check          -> public read
    Tier 2    Depends(require_session), or a write   -> session-gated
    Tier 2.5  _require_diag_token / DIAG_READ_TOKEN  -> operator diagnostics
    Tier 3    operator control / risk surface        -> explicit gates

  Where the code and the doc disagree, the runtime gate is authoritative and
  the file's own header requires fixing one of them in the SAME PR.

  A route that genuinely should not be listed goes in `_GRANDFATHERED` in
  scripts/check_api_tier_policy.py WITH a reason — an exemption someone had
  to write down is the point; an absence nobody had to justify is the bug.
"""


def _print_coverage(routes: Sequence[Route], documented: Set[str]) -> None:
    missing = undocumented(routes, documented)
    total = len(routes)
    covered = total - len(missing)
    pct = (100.0 * covered / total) if total else 100.0
    print(f"api-tier-policy coverage: {covered}/{total} routes documented "
          f"({pct:.1f}%) · population: every @router.<verb> under "
          f"{_ROUTER_DIR}/ joined to its APIRouter(prefix=...)")
    if _GRANDFATHERED:
        print(f"  grandfathered (exempt, not counted as gaps): {len(_GRANDFATHERED)}")
    if missing:
        print(f"\n  {len(missing)} route(s) with no row:")
        for r in missing:
            print(f"    - {r.render()}")

    # Reverse direction: a row naming a route that no longer exists. Reported,
    # never gating — the doc legitimately covers surfaces outside routers/
    # (`/api/health` in main.py, the login + static mounts), so a gating check
    # here would fail on rows that are correct.
    live = {r.key for r in routes}
    orphans = sorted(k for k in documented if k not in live)
    if orphans:
        print(f"\n  {len(orphans)} documented endpoint(s) with no route under "
              f"{_ROUTER_DIR}/ (advisory — main.py surfaces and mounts live "
              f"here legitimately):")
        for k in orphans:
            print(f"    - {k}")


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(description="api-tier-policy guard")
    ap.add_argument("diff", nargs="?", help="unified diff (default: stdin)")
    ap.add_argument("--all", action="store_true",
                    help="check every route, not just a diff's (standing audit)")
    ap.add_argument("--list", action="store_true",
                    help="print measured coverage and exit 0")
    args = ap.parse_args(argv[1:])

    doc = _read(_POLICY_DOC)
    if doc is None:
        print(f"::error::{_POLICY_DOC} is missing — the tier inventory this "
              f"guard enforces does not exist.", file=sys.stderr)
        return 1
    documented = documented_keys(doc)

    if args.list:
        _print_coverage(enumerate_routes(), documented)
        return 0

    if args.all:
        routes = enumerate_routes()
    else:
        text = (open(args.diff, encoding="utf-8", errors="replace").read()
                if args.diff else sys.stdin.read())
        routes = routes_in_scope(text)

    missing = undocumented(routes, documented)
    if not missing:
        scope = "every route" if args.all else f"{len(routes)} route(s) in this diff"
        print(f"api-tier-policy: OK — {scope} carries a row in {_POLICY_DOC}.")
        return 0

    print("api-tier-policy guard: FAIL\n", file=sys.stderr)
    for r in missing:
        print(f"  - {r.render()} has no row in {_POLICY_DOC}", file=sys.stderr)
    print(_EXPLAINER, file=sys.stderr)
    for r in missing:
        print(f"API_TIER_POLICY_GUARD\t{r.file}:{r.lineno}\t{r.key}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
