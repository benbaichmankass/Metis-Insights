"""No test module may put a MagicMock in `sys.modules["numpy"]`.

`BL-20260902-FIVE-TEST-MODULES-STUB-NUMPY-AND-BREAK-PYTEST-APPROX-WHERE-NUMPY-IS-ABSENT`
is a RECURRENCE: the fix was applied to `tests/conftest.py` and the copy-pasted
stubs it names were left in place, so the class was closed in one file and open
in six.

⚠️ **THE CONFTEST REMEDY ONLY WORKS WHERE NUMPY IS INSTALLED.** It PRE-IMPORTS
the real numpy so it wins the `sys.modules` slot before any test module runs its
own `setdefault` — and where numpy is ABSENT that pre-import is a documented
no-op, which is exactly the lean `guards` CI job (ruff / import-linter / pyyaml /
pytest, no numpy). `pytest-run` installs the full requirements, so the stub is
inert there BY CONSTRUCTION and a green `pytest-run` is not evidence.

⚠️ **IT IS A GUARD, NOT A CLEANUP.** Fixing the six as INSTANCES is what left
numpy standing the first time; this is the check that makes a seventh fail.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

TESTS = Path(__file__).resolve().parent


def _magicmock_stub_tuples(path: Path) -> list[list[str]]:
    """Module names in every `for X in (...): sys.modules.setdefault(X, MagicMock())`.

    ⚠️ PARSED, NEVER GREPPED — the backlog row asks for exactly this, and the
    reason is concrete: a bare `numpy` grep hits docstrings, comments and the
    word inside an unrelated import, while the tuple is the only thing that
    actually reaches `sys.modules`.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    out: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, (ast.Tuple, ast.List)):
            continue
        dumped = ast.dump(node)
        if "setdefault" not in dumped or "MagicMock" not in dumped:
            continue
        out.append([e.value for e in node.iter.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)])
    return out


def test_no_test_module_stubs_numpy_into_sys_modules():
    offenders = {
        p.name: names
        for p in sorted(TESTS.glob("*.py"))
        for names in _magicmock_stub_tuples(p)
        if "numpy" in names
    }
    assert not offenders, (
        "these test modules put a MagicMock in sys.modules['numpy'], which "
        "breaks pytest.approx for the whole session on any box without numpy: "
        f"{offenders}. The remedy is NEVER STUB, not stub more "
        "(tests/isolation_audit.py) — drop numpy from the tuple."
    )


def test_the_probe_can_find_a_positive():
    """A search returning nothing is not proof of absence.

    The assertion above is worthless unless the parser can see a stub when one
    is there — so plant one and require it to be found.
    """
    planted = Path(__file__).with_name("_planted_stub_fixture.py")
    planted.write_text(
        "import sys\n"
        "from unittest.mock import MagicMock\n"
        'for _mod in ("pandas", "numpy"):\n'
        "    sys.modules.setdefault(_mod, MagicMock())\n",
        encoding="utf-8")
    try:
        found = _magicmock_stub_tuples(planted)
        assert found == [["pandas", "numpy"]], found
    finally:
        planted.unlink()


def test_the_parser_ignores_the_word_where_it_cannot_reach_sys_modules():
    """…and the other half of a probe: it must stay quiet on a false positive.

    A docstring, a comment and a real import all contain "numpy" and none of
    them installs a MagicMock. A grep would report all three.
    """
    quiet = Path(__file__).with_name("_quiet_stub_fixture.py")
    quiet.write_text(
        '"""A module that mentions numpy in prose."""\n'
        "# numpy is heavy, so we do not import it\n"
        "import sys\n"
        "from unittest.mock import MagicMock\n"
        'for _mod in ("pandas",):\n'
        "    sys.modules.setdefault(_mod, MagicMock())\n",
        encoding="utf-8")
    try:
        found = _magicmock_stub_tuples(quiet)
        assert found == [["pandas"]], found
        assert "numpy" in quiet.read_text(encoding="utf-8"), \
            "positive control: the word IS in the file, so a grep would fire"
    finally:
        quiet.unlink()


def test_a_magicmock_numpy_really_does_break_pytest_approx():
    """THE MECHANISM, reproduced rather than quoted.

    Without this, the guard above is a rule whose reason nobody can check — and
    the failure it prevents does NOT present as an environment error: it
    surfaces as an ordinary assertion failure on a value that is in fact
    correct, four frames above the real TypeError.

    ⚠️ RESTORED IN `finally`, and the real numpy is put back exactly as found —
    a test that poisons `sys.modules` to prove poisoning is bad would be the
    defect wearing a lab coat.
    """
    had = "numpy" in sys.modules
    real = sys.modules.get("numpy")
    try:
        sys.modules["numpy"] = MagicMock()
        with pytest.raises(TypeError):
            # `is_bool` does `isinstance(val, np.bool_)`; a MagicMock attribute
            # is not a type, so isinstance raises rather than returning False.
            assert 0.018 == pytest.approx(0.018)
    finally:
        if had:
            sys.modules["numpy"] = real
        else:
            sys.modules.pop("numpy", None)
    # …and the restore worked, or every later test in this process is poisoned
    # by the control itself.
    assert 0.018 == pytest.approx(0.018)
