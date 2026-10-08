#!/usr/bin/env python3
"""checklist-readers-guard -- nothing reads the checklist monolith path directly.

PI-20261004-APBY4NTV-0003, step (c'). After the per-row cutover the committed
MANAGER-CHECKLIST.json is gone and the rows are the truth, so a script that does
`json.loads(Path("docs/claude/work/MANAGER-CHECKLIST.json").read_text())` would
report the checklist ABSENT on a perfectly healthy tree -- the register-reads-as-
empty failure this repo polices everywhere. Every reader goes through
`src/runtime/checklist_store.py` (`load_path`, `load_at`, `exists`).

The guard walks every non-test .py file with an AST and finds string constants that
NAME the monolith path. A file that does must either (a) use the loader (import
`checklist_store`), or (b) be on PATH_LIST_ONLY, a short, reasoned allowlist of
files that only use the path as a git pathspec, a display string or a test fixture
name. A NEW file naming the path fails until it does one of the two, so a new
direct reader cannot appear unreviewed.

Exit: 0 clean / 1 refused.
"""
from __future__ import annotations

import ast
import sys
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NEEDLE = "MANAGER-CHECKLIST.json"
ROOTS = ("scripts", "src")

#: Files that name the path WITHOUT reading it as a file. Keep each line's reason
#: true: if a file here starts reading the path, move it to the loader instead.
PATH_LIST_ONLY: dict[str, str] = {
    "src/runtime/checklist_store.py": "the loader itself",
    "scripts/ops/checklist.py": "the loader's CLI",
    "scripts/ops/checklist_fill_missing_state.py": "one-off monolith editor; refuses when the store is seeded",
    "scripts/ops/merge_json_register.py": "git merge-driver for the monolith; moot after cutover",
    "scripts/ops/uncarried_specs.py": "path list for a text-mention corpus (store dir listed beside it)",
    "scripts/ops/manager_preflight.py": "git pathspec list (store dir listed beside it)",
    "scripts/ops/handoff_check.py": "git pathspec list (store dir listed beside it)",
    "scripts/ops/render_due_list.py": "a link/display string",
    "scripts/ops/work_digest.py": "register identity passed to the path-aware _items_at",
    "scripts/ops/checklist_routing_age.py": "history pathspec + load_path via checklist_store",
    "scripts/ci/run_guards.py": "guard trigger globs (store glob listed beside it)",
    "scripts/ci/check_scope_overlap.py": "a fixture describing a historic PR body",
    "scripts/ci/check_canonical_doc_coherence.py": "a regex for doc mentions",
    "scripts/ci/check_collapsed_states.py": "prose in a finding message",
    "scripts/ci/check_spec_carrier.py": "text corpus list (store glob listed beside it)",
    "scripts/ci/check_manager_scope.py": "history pathspecs + load_at via _json_at",
    "scripts/ci/check_operator_owed.py": "prose",
    "scripts/ci/check_edge_kind_vocabulary.py": "prose",
    "scripts/ci/check_stated_population.py": "prose",
    "scripts/ci/check_research_workflow_landing.py": "prose",
    "scripts/ci/check_corpus_row_floor.py": "prose in a message",
    "scripts/ci/check_wip_ceiling.py": "prose",
    "scripts/ci/check_soak_registered.py": "prose",
    "scripts/ci/check_stale_in_flight.py": "prose",
    "scripts/ops/backtest_data_source.py": "a refusal message",
    "scripts/ops/manager_view.py": "a display string; reads through session_registry.read_json (path-aware)",
    "src/web/api/routers/work.py": "reads through manager_status.read_json_file (path-aware); the rest is prose",
}


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and n.body and isinstance(n.body[0], ast.Expr) \
                and isinstance(n.body[0].value, ast.Constant):
            ids.add(id(n.body[0].value))
    return ids


def _names_path(tree: ast.AST) -> list[int]:
    skip = _docstrings(tree)
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and NEEDLE in n.value and id(n) not in skip]


def findings(repo: Path = REPO) -> list[str]:
    out: list[str] = []
    for root in ROOTS:
        for p in sorted((repo / root).rglob("*.py")):
            rel = str(p.relative_to(repo))
            if "/tests/" in rel or rel in PATH_LIST_ONLY:
                continue
            try:
                src = p.read_text(encoding="utf-8")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    tree = ast.parse(src)
            except (OSError, SyntaxError, ValueError):
                continue
            lines = _names_path(tree)
            if lines and "checklist_store" not in src and "import checklist" not in src:
                out.append(f"{rel}:{lines[0]} names {NEEDLE} but does not use "
                           f"src/runtime/checklist_store.py -- read it with load_path()/load_at()/exists(), "
                           f"or add the file to PATH_LIST_ONLY with the reason it never reads the path")
    return out


def _self_test() -> int:
    import tempfile

    ok = True
    with tempfile.TemporaryDirectory() as td:
        r = Path(td)
        (r / "scripts").mkdir()
        (r / "src").mkdir()
        (r / "scripts" / "bad.py").write_text('P = "docs/claude/work/MANAGER-CHECKLIST.json"\nopen(P).read()\n')
        (r / "scripts" / "good.py").write_text('from src.runtime import checklist_store\nP = "x/MANAGER-CHECKLIST.json"\n')
        (r / "scripts" / "prose.py").write_text('"""mentions MANAGER-CHECKLIST.json in a docstring only"""\n')
        got = findings(r)
        for label, cond in (("a direct reader is refused", any("bad.py" in g for g in got)),
                            ("a loader user passes", not any("good.py" in g for g in got)),
                            ("a docstring-only mention passes", not any("prose.py" in g for g in got))):
            print(f"  {'PASS' if cond else 'FAIL'}  {label}")
            ok &= cond
    print("checklist-readers-guard self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "--self-test" in argv:
        return _self_test()
    f = findings()
    if f:
        print("::error::a script reads the checklist monolith path directly:")
        for x in f:
            print(f"  ✗ {x}")
        return 1
    print("checklist-readers-guard: every file naming the checklist path uses the loader or is a reasoned path-list.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
