"""`pytest-run` AFFECTED mode (GH-CAPACITY-PR-CI-FANOUT, operator 2026-10-15:
"Yes, affected tests on PRs").

On a pull_request, `scripts/ci/pytest_run_scope.py` may run only the test files
that can observe a code change, chosen from the import graph.  The danger is a
required check that goes green having not run the test that would have failed.
So every test here plants a case and pins the direction: a change the selector
cannot place, or that most of the suite can see, runs the FULL suite.
"""
from __future__ import annotations

import importlib.util
import io
import textwrap
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "pytest-run.yml"

_spec = importlib.util.spec_from_file_location(
    "pytest_run_scope_affected", REPO / "scripts" / "ci" / "pytest_run_scope.py"
)
scope = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scope)

RELEVANT = scope.relevance_regex()


def _write(root: Path, rel: str, body: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature repo with a known graph.

    pkg.hub      <- imported by 6 of 10 test files (high fan-in)
    pkg.leaf     <- pkg.mid <- tests/test_mid.py ; tests/test_leaf.py imports leaf
    pkg.eager    <- imported at import time by tests/conftest.py (every test)
    pkg.fixdep   <- lazily imported by a non-autouse plugin fixture `fdb`
    pkg.strategies.alpha <- loaded by pkg.coord via import_module(f"pkg.strategies.{x}")
    scripts/tool.py      <- read by tests/test_reads_tool.py as text, by basename
    """
    _write(tmp_path, "pkg/__init__.py")
    _write(tmp_path, "pkg/hub.py", "X = 1\n")
    _write(tmp_path, "pkg/leaf.py", "def f():\n    return 1\n")
    _write(tmp_path, "pkg/mid.py", "from pkg.leaf import f\n")
    _write(tmp_path, "pkg/eager.py", "Y = 2\n")
    _write(tmp_path, "pkg/fixdep.py", "Z = 3\n")
    _write(tmp_path, "pkg/configured.py", "W = 4\n")
    _write(tmp_path, "pkg/strategies/__init__.py")
    _write(tmp_path, "pkg/strategies/alpha.py", "A = 1\n")
    _write(tmp_path, "pkg/coord.py", """\
        import importlib
        def load(x):
            return importlib.import_module(f"pkg.strategies.{x}")
    """)
    _write(tmp_path, "scripts/tool.py", "print('hi')\n")
    _write(tmp_path, "scripts/ops/guard_x.py", "pass\n")
    _write(tmp_path, "scripts/ci/run_guards.py", 'GUARDS = ["scripts/ops/guard_x.py"]\n')
    _write(tmp_path, "config/app.yaml", "loader: pkg.configured\n")
    _write(tmp_path, "tests/__init__.py")
    _write(tmp_path, "tests/conftest.py", """\
        import pytest
        pytest_plugins = ("tests.fixtures.plug",)
        import pkg.eager
    """)
    _write(tmp_path, "tests/fixtures/__init__.py")
    _write(tmp_path, "tests/fixtures/plug.py", """\
        import pytest

        @pytest.fixture
        def fdb():
            from pkg import fixdep
            return fixdep.Z
    """)
    for i in range(6):
        _write(tmp_path, f"tests/test_hub{i}.py", "from pkg.hub import X\n")
    _write(tmp_path, "tests/test_mid.py", "import pkg.mid\n")
    _write(tmp_path, "tests/test_leaf.py", "from pkg import leaf\n")
    _write(tmp_path, "tests/test_uses_fixture.py", "def test_a(fdb):\n    assert fdb\n")
    _write(tmp_path, "tests/test_reads_tool.py", """\
        from pathlib import Path
        def test_t():
            assert (Path(__file__).parents[1] / "scripts" / "tool.py").exists()
    """)
    _write(tmp_path, "tests/test_coord.py", "from pkg.coord import load\n")
    return tmp_path


def _aff(repo: Path, *files: str):
    graph = scope.ImportGraph(repo)
    return scope.classify_affected(list(files), RELEVANT, graph)


# --- planted: a leaf change selects its importers, and only them ----------------

def test_leaf_change_selects_direct_and_transitive_importers(repo):
    mode, reason, tests = _aff(repo, "pkg/leaf.py")
    assert mode == "affected", reason
    assert "tests/test_leaf.py" in tests  # direct importer
    assert "tests/test_mid.py" in tests  # transitive: leaf <- mid <- test_mid
    assert not any(t.startswith("tests/test_hub") for t in tests)
    assert "tests/test_uses_fixture.py" not in tests


def test_a_changed_test_file_runs_itself(repo):
    mode, _, tests = _aff(repo, "tests/test_leaf.py")
    assert mode == "affected" and "tests/test_leaf.py" in tests


def test_deleted_module_still_selects_its_importers(repo):
    (repo / "pkg" / "leaf.py").unlink()
    mode, _, tests = _aff(repo, "pkg/leaf.py")
    assert mode == "affected"
    assert {"tests/test_leaf.py", "tests/test_mid.py"} <= set(tests)


def test_test_that_reads_a_script_by_path_is_selected(repo):
    mode, _, tests = _aff(repo, "scripts/tool.py")
    assert mode == "affected" and "tests/test_reads_tool.py" in tests


def test_dynamic_import_prefix_selects_the_loaders_importers(repo):
    mode, _, tests = _aff(repo, "pkg/strategies/alpha.py")
    assert mode == "affected" and "tests/test_coord.py" in tests


def test_plugin_fixture_dependency_selects_tests_that_name_the_fixture(repo):
    mode, _, tests = _aff(repo, "pkg/fixdep.py")
    assert mode == "affected" and "tests/test_uses_fixture.py" in tests


def test_package_init_change_reaches_every_submodule_importer(repo):
    mode, reason, _ = _aff(repo, "pkg/__init__.py")
    # pkg/__init__ executes before pkg.hub, so the hub's 6 test files see it -> full
    assert mode == "full", reason


# --- planted: everything that must fall back to the FULL suite -------------------

def test_high_fan_in_module_selects_the_full_suite(repo):
    mode, reason, tests = _aff(repo, "pkg/hub.py")
    assert mode == "full" and tests == []
    assert "high fan-in" in reason


def test_module_conftest_imports_eagerly_selects_the_full_suite(repo):
    mode, reason, _ = _aff(repo, "pkg/eager.py")
    assert mode == "full" and "every test" in reason


def test_module_named_in_config_selects_the_full_suite(repo):
    mode, reason, _ = _aff(repo, "pkg/configured.py")
    assert mode == "full" and "config" in reason


def test_guard_script_selects_the_full_suite(repo):
    mode, reason, _ = _aff(repo, "scripts/ops/guard_x.py")
    assert mode == "full" and "guards" in reason


@pytest.mark.parametrize(
    "path",
    [
        "conftest.py", "tests/conftest.py", "tests/sub/conftest.py",
        "requirements.txt", "requirements-test.txt", "pyproject.toml", "pytest.ini", "setup.cfg",
        ".github/workflows/pytest-run.yml", ".github/actions/x/action.yml",
        "scripts/ci/pytest_run_scope.py", "scripts/ci/run_guards.py",
        # a suite-read non-Python file outside the data allowlist
        "config/accounts.yaml", "deploy/x.service", ".claude/skills/manager/SKILL.md",
        "docs/ARCHITECTURE-CANONICAL.md",
    ],
)
def test_fallback_paths_select_the_full_suite(repo, path):
    mode, _, tests = _aff(repo, "pkg/leaf.py", path)
    assert mode == "full" and tests == []


def test_data_files_alongside_code_add_the_data_reader_subset(repo):
    _write(repo, "tests/test_reads_queue.py", 'Q = "research/queue"\n')
    mode, _, tests = _aff(repo, "pkg/leaf.py", "research/queue/RQ-1.yaml")
    assert mode == "affected"
    assert {"tests/test_leaf.py", "tests/test_reads_queue.py"} <= set(tests)


def test_tree_scanning_tests_are_always_selected(repo):
    _write(repo, "tests/test_scan.py", "from pathlib import Path\nP = list(Path('.').rglob('*.py'))\n")
    mode, _, tests = _aff(repo, "pkg/leaf.py")
    assert mode == "affected" and "tests/test_scan.py" in tests


# --- the CLI: only pull_request may select fewer than all ------------------------

def _cli(monkeypatch, tmp_path, stdin: str, *args: str) -> dict[str, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    buf = io.StringIO()
    with redirect_stdout(buf):
        scope.main([*args, "--tests-out", str(tmp_path / "out.txt")])
    return dict(line.split("=", 1) for line in buf.getvalue().splitlines())


@pytest.mark.parametrize("event", ["push", "merge_group", "workflow_dispatch", ""])
def test_non_pr_events_never_get_affected_mode(monkeypatch, tmp_path, event):
    out = _cli(monkeypatch, tmp_path, "scripts/ops/ml_plan_status.py\n", "--event", event)
    assert out["mode"] == "full"


def test_pr_event_on_a_real_leaf_writes_the_selection(monkeypatch, tmp_path):
    out = _cli(monkeypatch, tmp_path, "scripts/ops/ml_plan_status.py\n", "--event", "pull_request")
    assert out["mode"] == "affected", out["reason"]
    selected = (tmp_path / "out.txt").read_text().split()
    assert "tests/test_ml_plan_status.py" in selected
    assert "tests/test_pytest_run_scope.py" in selected  # ALWAYS


def test_real_repo_high_fan_in_module_runs_the_full_suite():
    graph = scope.ImportGraph()
    for path in ("src/utils/paths.py", "src/runtime/order_monitor.py"):
        mode, reason, _ = scope.classify_affected([path], RELEVANT, graph)
        assert mode == "full", (path, reason)


def test_data_only_diff_is_unchanged_by_the_event(monkeypatch, tmp_path):
    out = _cli(monkeypatch, tmp_path, "research/results/x.json\n", "--event", "pull_request")
    assert out["mode"] == "data"
