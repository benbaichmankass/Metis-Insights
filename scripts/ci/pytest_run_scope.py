#!/usr/bin/env python3
"""Decide HOW MUCH of the suite `pytest-run` must execute for a diff (CI-DIET).

`pytest-run` is a REQUIRED check, so it can never carry a `paths:` filter.  It
already short-circuits when a diff touches nothing the suite reads (the
`grep -Eq` relevance filter in `.github/workflows/pytest-run.yml`, pinned by
`tests/test_pytest_run_filter.py`).  That filter is deliberately wide: it
includes `docs/claude/work/checklist/`, `PIPELINE.jsonl`, `research/` and
`comms/` because real tests read those trees.  The consequence, MEASURED over
the 48h ending 2026-10-13: 179 of 233 commits that landed on main (77%) touched
nothing but those data trees (research results, checklist rows, pr-landing /
merge-slot / automerge request files) and every one of them paid the full
~36-minute suite on the PR and again on push-to-main.

A data-only diff cannot change what the other ~1,300 test files observe: they
do not read it.  What it CAN change is the tests that read it.  So for such a
diff this script selects **exactly the test files that mention the data trees**
and the workflow runs those, instead of the whole suite.  Nothing is skipped
that could observe the change; the `guards` job still runs unconditionally.

Output (one `key=value` per line, for `$GITHUB_OUTPUT`):

    mode=full | data
    reason=<one line, shown in the job summary>

`full`: some changed file is relevant to the suite (per the workflow's own
relevance regex) AND is not in the explicit DATA allowlist below.
`data`: every relevant changed file is in the allowlist.

The allowlist is EXPLICIT and pinned by `tests/test_pytest_run_scope.py`; a
path not named here gets the full suite, so the failure direction is "runs
more", never "runs less".

AFFECTED MODE (GH-CAPACITY-PR-CI-FANOUT, operator 2026-10-15: "Yes, affected
tests on PRs").  With ``--event pull_request`` a CODE diff is no longer an
automatic full run.  The selector builds the repo's import graph and runs the
test files that can observe the change:

  * every test whose transitive static imports reach a changed module
    (importing ``a.b.c`` also executes the ``a`` and ``a.b`` packages);
  * every file that names a changed module as a dotted STRING (importlib /
    dispatch tables) -- treated as an import edge;
  * every module whose ``importlib.import_module(f"<prefix>...")`` prefix covers
    a changed module (e.g. ``src.units.strategies.*`` loaded by the coordinator);
  * every test that mentions a changed file by repo path or basename (tests that
    read source as text, load it by path, or run it as a subprocess);
  * every test that walks a tree (``rglob`` / ``os.walk`` / ``glob``) -- they scan
    code no import names;
  * the data-reader subset when data-tree files are also in the diff.

It falls back to the FULL suite (``mode=full``) when the diff touches a
conftest, requirements / pyproject / pytest.ini / setup.cfg, anything under
``.github/`` or ``scripts/ci/``, a script the guards job runs, a module named
in a config tree (loaded by name from YAML), any suite-relevant non-Python file
outside the data allowlist, a module with high fan-in (it reaches more than
FANIN_FULL_FRACTION of the test files), or when the selection itself is that
large.  Push to main, merge_group and workflow_dispatch are never affected-
mode: they keep the full suite.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "pytest-run.yml"

# The explicit data-only allowlist.  Each entry is a diff path the suite reads
# only through a SMALL, nameable set of test files (see TEST_TOKENS).
#
# Deliberately NOT here (these run the full suite):
#   comms/schema/**            -- the suite validates against these templates
#   docs/ARCHITECTURE-CANONICAL.md, docs/CLAUDE-RULES-CANONICAL.md and the other
#   enumerated docs/ readers   -- read as-committed by tests that do not mention
#                                 any token below (see test_pytest_run_filter.COVERED)
#   anything under src/ scripts/ tests/ config/ .github/workflows/ ml/ .claude/
DATA_ALLOWLIST: tuple[str, ...] = (
    r"^docs/claude/work/checklist/",
    r"^docs/claude/work/pipeline/",
    r"^docs/claude/work/PIPELINE\.jsonl$",
    r"^research/queue/[^/]+\.ya?ml$",
    r"^research/queue/blocked/[^/]+\.ya?ml$",
    r"^research/results/",
    r"^comms/(?!schema/)",
    r"^\.github/pr-landing/",
    r"^\.github/pr-automerge-requests/",
    r"^\.github/merge-slots/",
)
DATA_RE = re.compile("|".join(DATA_ALLOWLIST))

# A test file is a "data reader" if its text names one of the data trees: by path
# literal, by the module that loads it, or by the quoted path SEGMENT a
# `Path(...) / "comms" / ...` join spells.  Segment spellings are included on
# purpose: the repo has already been bitten by a reader (`os.path.join`) that
# the literal-path scan could not see (tests/test_pytest_run_filter.py).  The
# bare words "checklist" / "pipeline" / "comms" are NOT tokens -- they appear in
# ~190 files that only talk about those things (the ML/trade "pipeline"), and
# selecting on them ran 25% of the suite, measured 2026-10-13 (5,650 tests,
# 21 min) for no added coverage of the data trees.  Over-selecting costs minutes;
# under-selecting is the 'green tick that ran nothing' failure.
TEST_TOKENS = re.compile(
    r"work/checklist|checklist_store|checklist\.py|MANAGER-CHECKLIST|[\"']checklist[\"']|\bchecklist/"
    r"|PIPELINE\.jsonl|work/pipeline|ops/pipeline|ops import pipeline|ops\.pipeline|import pipeline"
    r"|[\"']pipeline[\"']|pipeline\.py"
    r"|research/queue|research_queue|research/results|[\"']queue[\"']|[\"']results[\"']"
    r"|queue_loader|dispatch_queue"
    r"|comms/|[\"']comms[\"']"
    r"|pr-landing|pr-automerge|merge-slots|merge_slot|pr_landing|pr_automerge"
    r"|check_pr_landing|claim_merge",
    re.IGNORECASE,
)
ALWAYS = (
    "tests/test_pytest_run_filter.py",
    "tests/test_pytest_run_scope.py",
)
IGNORED = ("tests/test_main_loop.py",)  # imports the live entrypoint; mirrors pytest-run


def relevance_regex(workflow: Path = WORKFLOW) -> str:
    """The workflow's own `grep -Eq '<re>'` -- the single source of truth."""
    m = re.search(r"grep -Eq '([^']+)'", workflow.read_text())
    if not m:
        raise SystemExit("could not find the relevance grep in pytest-run.yml")
    return m.group(1)


def classify(files: list[str], relevant_re: str) -> tuple[str, str]:
    rel = re.compile(relevant_re)
    files = [f for f in (x.strip() for x in files) if f]
    relevant = [f for f in files if rel.search(f)]
    blocking = [f for f in relevant if not DATA_RE.search(f)]
    if blocking:
        shown = ", ".join(blocking[:3]) + (" ..." if len(blocking) > 3 else "")
        return "full", f"{len(blocking)} changed file(s) outside the data allowlist ({shown}) -> full suite"
    reason = (
        f"all {len(relevant)} suite-relevant changed file(s) are data-only "
        "(checklist/pipeline/research queue+results/comms/pr-landing/merge-slot/automerge) "
        "-> ran every test file that reads those trees; guards run separately"
    )
    return "data", reason


def select_tests(tests_dir: Path | None = None) -> list[str]:
    tests_dir = tests_dir or REPO / "tests"
    root = tests_dir.parent
    out = set(ALWAYS)
    for p in tests_dir.rglob("test_*.py"):
        rel = p.relative_to(root).as_posix()
        if rel in IGNORED:
            continue
        try:
            if TEST_TOKENS.search(p.read_text(errors="replace")):
                out.add(rel)
        except OSError:
            continue
    return sorted(x for x in out if (root / x).exists())


# ---------------------------------------------------------------------------
# AFFECTED MODE (pull_request only) -- see the module docstring.
# ---------------------------------------------------------------------------

# Any of these in the diff -> the full suite, whatever the graph says.
FULL_FALLBACK = re.compile(
    r"(^|/)conftest\.py$"
    r"|(^|/)requirements[^/]*\.txt$|(^|/)pyproject\.toml$|(^|/)pytest\.ini$|(^|/)setup\.cfg$"
    r"|^\.github/(?!pr-landing/|pr-automerge-requests/|merge-slots/)"
    r"|^scripts/ci/"
)
# A changed module reaching more than this fraction of the test files is
# "high fan-in": its importers are most of the suite, so run all of it.  The
# same bound applies to the union of the selection.  MEASURED 2026-10-15 over
# the repo graph: the FastAPI app and the telegram/runtime core each reach
# ~45% of test files (646-686 of 1,456), most leaf modules reach under 10.
# Above half, a selection saves too little to be worth being the one run that
# differs from main's.
FANIN_FULL_FRACTION = 0.5
# Trees whose .py files are graph nodes.  `docs/archive/` is frozen history.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
# Text trees that name modules by dotted string for runtime loading.
CONFIG_TREES = ("config", "ml/configs")
TREE_SCAN = re.compile(r"\brglob\(|\bos\.walk\(|\bglob\.glob\(|\.glob\(|\bglob\(")
DOTTED_STR = re.compile(r"[\"']([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)+)[\"']")  # fallback for unparseable files
DYN_PREFIX = re.compile(r"import_module\(\s*f?[\"']([A-Za-z_][\w.]*\.)\{")


def _module_name(rel: str) -> str | None:
    if not rel.endswith(".py"):
        return None
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def guard_scripts(repo: Path = REPO) -> set[str]:
    """Every script path the `guards` job runs (named in run_guards.py)."""
    try:
        text = (repo / "scripts" / "ci" / "run_guards.py").read_text()
    except OSError:
        return set()
    return set(re.findall(r"scripts/[\w./-]+\.(?:py|sh)", text))


class ImportGraph:
    """Static import graph over every .py file in the repo, keyed by module name.

    Edges are over-approximate on purpose (lazy imports inside functions count,
    a dotted string naming a module counts, `import x` after a sys.path hack
    matches every module whose stem is `x`): extra edges run extra tests, a
    missing edge is a test that could have failed and did not run.

    The pytest-specific channel is modelled explicitly: a conftest's
    module-level code and AUTOUSE fixtures run for every test (`every_test`), and
    its other fixtures run for the tests that NAME them (`fixture_users`).
    """

    def __init__(self, repo: Path = REPO):
        self.repo = repo
        self.files: dict[str, str] = {}  # module -> repo-relative path
        self.text: dict[str, str] = {}  # repo-relative path -> source
        for root, dirs, names in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for n in names:
                if not n.endswith(".py"):
                    continue
                rel = (Path(root) / n).relative_to(repo).as_posix()
                if rel.startswith("docs/archive/"):
                    continue
                try:
                    self.text[rel] = (Path(root) / n).read_text(errors="replace")
                except OSError:
                    continue
                mod = _module_name(rel)
                if mod:
                    self.files[mod] = rel
        self.stems: dict[str, set[str]] = {}
        for mod in self.files:
            self.stems.setdefault(mod.rsplit(".", 1)[-1], set()).add(mod)
        self.tops = {m.split(".", 1)[0] for m in self.files}
        self.stdlib = set(getattr(sys, "stdlib_module_names", ()))
        self.fwd: dict[str, set[str]] = {}  # file -> modules it may execute
        self.eager: dict[str, set[str]] = {}  # file -> modules its import-time code / autouse fixtures execute
        self.fixtures: dict[str, set[str]] = {}  # file -> non-autouse fixture names it defines
        self.dyn_prefixes: list[tuple[str, str]] = []  # (prefix, loader path)
        for rel, src in self.text.items():
            self._scan(rel, src)
        # rev[m] = files (repo-relative) that import / load m
        self.rev: dict[str, set[str]] = {}
        for rel, mods in self.fwd.items():
            for m in mods:
                self.rev.setdefault(m, set()).add(rel)
            mod = _module_name(rel)
            if mod:  # executing a.b.c executes the a and a.b packages first
                parts = mod.split(".")
                for i in range(1, len(parts)):
                    self.rev.setdefault(".".join(parts[:i]), set()).add(rel)
        # pytest plugin roots: every conftest.py and every module a conftest names
        # in `pytest_plugins` (a dotted string -> already a fwd edge).
        self.conftests = {r for r in self.text if Path(r).name == "conftest.py"}
        self.plugins = {
            self.files[m]
            for c in self.conftests
            for m in re.findall(r"[\"']([\w.]+)[\"']", _pytest_plugins_src(self.text[c]))
            if m in self.files
        }
        self.every_test = self._every_test_closure()

    def _resolve(self, name: str) -> set[str]:
        """Module names an import of `name` may execute (over-approximate)."""
        if not name:
            return set()
        top = name.split(".", 1)[0]
        if top in self.tops:
            return {name}  # kept even if absent: a deleted module keeps its importers
        if top in self.stdlib:
            return set()
        # `sys.path.insert(..., "scripts/ops"); import pipeline` -- match by stem.
        return set(self.stems.get(top, ()))

    def _scan(self, rel: str, src: str) -> None:
        self.fwd[rel] = set()
        self.eager[rel] = set()
        self.fixtures[rel] = set()
        for p in DYN_PREFIX.findall(src):
            self.dyn_prefixes.append((p, rel))
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(src)
        except (SyntaxError, ValueError):
            # Unparseable: fall back to a text scan for dotted module names.
            self.fwd[rel] = {m for m in DOTTED_STR.findall(src) if m in self.files}
            self.eager[rel] = set(self.fwd[rel])
            return
        mod = _module_name(rel) or ""
        pkg = mod if rel.endswith("__init__.py") else mod.rpartition(".")[0]
        docstrings = {
            id(n.value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
        }

        def visit(node: ast.AST, eager: bool) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                autouse = False
                is_fixture = False
                for d in node.decorator_list:
                    txt = ast.unparse(d)
                    if "fixture" in txt:
                        is_fixture = True
                        autouse = autouse or "autouse=True" in txt.replace(" ", "")
                if is_fixture and not autouse:
                    self.fixtures[rel].add(node.name)
                # A function body runs later; an autouse fixture runs for every test.
                eager = eager and autouse
            found: set[str] = set()
            if isinstance(node, ast.Import):
                for a in node.names:
                    found |= self._resolve(a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg.split(".") if pkg else []
                    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                    name = ".".join(base + ([node.module] if node.module else []))
                    targets = {name} if name else set()
                else:
                    targets = self._resolve(node.module or "")
                for t in targets:
                    found.add(t)
                    for a in node.names:  # `from pkg import submodule` (kept if deleted)
                        if f"{t}.{a.name}" in self.files or t.split(".", 1)[0] in self.tops:
                            found.add(f"{t}.{a.name}")
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and node.value in self.files
            ):
                found.add(node.value)  # importlib / dispatch-table / sys.modules key
            self.fwd[rel] |= found
            if eager:
                self.eager[rel] |= found
            for child in ast.iter_child_nodes(node):
                visit(child, eager)

        visit(tree, True)

    def _every_test_closure(self) -> set[str]:
        """Files whose code runs for EVERY test: conftests + plugins' import-time
        code and autouse fixtures, then everything that code imports."""
        seen: set[str] = set()
        stack: list[str] = []
        for root in self.conftests | self.plugins:
            seen.add(root)
            stack.extend(self.eager.get(root, ()))
        while stack:
            m = stack.pop()
            for f in self._files_for(m):
                if f in seen:
                    continue
                seen.add(f)
                stack.extend(self.fwd.get(f, ()))
        return seen

    def _files_for(self, mod: str) -> list[str]:
        """The file for `mod` and its parent packages' __init__ files."""
        parts = mod.split(".")
        return [
            self.files[".".join(parts[:i])]
            for i in range(1, len(parts) + 1)
            if ".".join(parts[:i]) in self.files
        ]

    def is_test(self, rel: str) -> bool:
        return rel.startswith("tests/") and Path(rel).name.startswith("test_")

    def test_files(self) -> list[str]:
        return sorted(r for r in self.text if self.is_test(r) and r not in IGNORED)

    def fixture_users(self, rel: str) -> set[str]:
        names = self.fixtures.get(rel, set())
        if not names:
            return set()
        pat = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(names))) + r")\b")
        return {t for t in self.test_files() if pat.search(self.text[t])}

    def dependents(self, rel: str) -> set[str]:
        """Every file that can execute or read `rel`, transitively.

        A conftest / plugin reached on the way contributes the tests that use
        its fixtures (by name); whether it runs for EVERY test is decided
        separately, by `every_test`.
        """
        mod = _module_name(rel)
        seen: set[str] = set()
        frontier: list[str] = []

        def reach(f: str) -> None:
            if f in seen:
                return
            seen.add(f)
            if f in self.conftests or f in self.plugins:
                seen.update(self.fixture_users(f))
                return
            fm = _module_name(f)
            if fm:
                frontier.append(fm)
                for prefix, loader in self.dyn_prefixes:
                    if fm.startswith(prefix):
                        reach(loader)

        if mod:
            frontier.append(mod)
            for prefix, loader in self.dyn_prefixes:
                if mod.startswith(prefix):
                    reach(loader)
        # first hop: files that name the changed file by path or basename
        base = Path(rel).name
        if base != "__init__.py":
            for other, src in self.text.items():
                if other != rel and (rel in src or base in src):
                    reach(other)
        done: set[str] = set()
        while frontier:
            m = frontier.pop()
            if m in done:
                continue
            done.add(m)
            for f in self.rev.get(m, ()):
                reach(f)
        return seen


def _pytest_plugins_src(src: str) -> str:
    m = re.search(r"^pytest_plugins\s*=\s*([\s\S]*?\))", src, re.MULTILINE)
    return m.group(1) if m else ""


def _named_in_config(mod: str, repo: Path = REPO) -> bool:
    pat = re.compile(r"(?<![\w.])" + re.escape(mod) + r"(?![\w])")
    for tree in CONFIG_TREES:
        for p in (repo / tree).rglob("*"):
            if p.is_file() and p.suffix in (".yaml", ".yml", ".json", ".toml"):
                try:
                    if pat.search(p.read_text(errors="replace")):
                        return True
                except OSError:
                    continue
    return False


def classify_affected(
    files: list[str], relevant_re: str, graph: ImportGraph | None = None
) -> tuple[str, str, list[str]]:
    """`("affected", reason, tests)` or `("full", reason, [])` for a PR diff."""
    rel_re = re.compile(relevant_re)
    files = [f for f in (x.strip() for x in files) if f]
    relevant = [f for f in files if rel_re.search(f)]
    if not relevant:
        return "full", "affected: no suite-relevant file to select from -> full suite", []
    guards = guard_scripts(graph.repo if graph else REPO)
    data_files = [f for f in relevant if DATA_RE.search(f)]
    code_files = [f for f in relevant if not DATA_RE.search(f)]
    for f in code_files:
        if FULL_FALLBACK.search(f):
            return "full", f"affected: {f} is conftest/requirements/.github/scripts/ci -> full suite", []
        if f in guards:
            return "full", f"affected: {f} is run by the guards job -> full suite", []
        if not f.endswith(".py"):
            return "full", f"affected: {f} is a suite-read non-Python file -> full suite", []
    graph = graph or ImportGraph()
    tests_all = graph.test_files()
    limit = max(1, int(len(tests_all) * FANIN_FULL_FRACTION))
    selected: set[str] = set(ALWAYS)
    for f in code_files:
        mod = _module_name(f)
        if mod and _named_in_config(mod, graph.repo):
            return "full", f"affected: {mod} is named in a config tree (loaded by name) -> full suite", []
        if f in graph.every_test or f == "tests/__init__.py":
            return "full", (
                f"affected: {f} runs for every test (conftest / pytest plugin import-time "
                "code or autouse fixture) -> full suite"
            ), []
        deps = graph.dependents(f)
        reach = {d for d in deps if graph.is_test(d)}
        if graph.is_test(f):
            reach.add(f)
        reach.discard("tests/test_main_loop.py")
        if len(reach) > limit:
            return "full", (
                f"affected: {f} has high fan-in ({len(reach)} of {len(tests_all)} test files) -> full suite"
            ), []
        if not reach and not mod and not graph.is_test(f):
            return "full", f"affected: cannot place {f} in the import graph -> full suite", []
        selected |= reach
    selected |= {t for t in tests_all if TREE_SCAN.search(graph.text.get(t, ""))}
    if data_files:
        selected |= set(select_tests(graph.repo / "tests"))
    selected = {t for t in selected if (graph.repo / t).exists() and t not in IGNORED}
    if len(selected) > limit:
        return "full", f"affected: selection is {len(selected)} of {len(tests_all)} test files -> full suite", []
    reason = (
        f"affected: {len(code_files)} code file(s) + {len(data_files)} data file(s) -> "
        f"{len(selected)} of {len(tests_all)} test files (importers, path readers, tree "
        "scanners); push to main runs the full suite"
    )
    return "affected", reason, sorted(selected)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--select-tests", action="store_true", help="print the data-reader test files and exit")
    ap.add_argument("--workflow", type=Path, default=WORKFLOW)
    ap.add_argument(
        "--event", default="",
        help="github.event_name; only 'pull_request' may select affected tests",
    )
    ap.add_argument(
        "--tests-out", type=Path,
        help="where to write the affected test files (one per line) when mode=affected",
    )
    args = ap.parse_args(argv)
    if args.select_tests:
        print("\n".join(select_tests()))
        return 0
    files = sys.stdin.read().splitlines()
    relevant_re = relevance_regex(args.workflow)
    mode, reason = classify(files, relevant_re)
    if mode == "full" and args.event == "pull_request" and args.tests_out:
        try:
            mode, reason, tests = classify_affected(files, relevant_re)
        except Exception as exc:  # noqa: BLE001 -- undecidable -> the full suite, never fewer tests
            mode, reason, tests = "full", f"affected: selector error ({type(exc).__name__}) -> full suite", []
        if mode == "affected":
            args.tests_out.write_text("\n".join(tests) + "\n")
    print(f"mode={mode}")
    print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
