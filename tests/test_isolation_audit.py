"""Controls for the test-isolation audit.

⚠️ **THE END-TO-END CONTROLS RUN PYTEST IN A SUBPROCESS ON A TEMP FILE.**
A control that leaked inside THIS suite would be reported by the very audit it
is testing, and would then need baselining — a test that must be excused by the
thing it verifies proves nothing. The subprocess keeps the leak inside a run we
own, and lets us assert on the audit's actual stdout and exit code rather than
on our belief about them.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests import isolation_audit as iso
from tests.isolation_baseline import BASELINE, is_baselined



def _synthetic_module(name: str):
    """Build a module the way tests under `tests/` actually build one.

    ⚠️ **THE ``__spec__`` IS LOAD-BEARING IN THIS HELPER.** A bare
    ``type(sys)(name)`` leaves ``__spec__`` as ``None``, and
    ``importlib.util.find_spec`` RAISES ``ValueError`` on that rather than
    returning a spec — which the audit's own ``except Exception`` swallows into
    "not real". So a control built that way passes against BOTH the correct
    discriminator and the broken one, and a mutation test on the fix comes back
    green. It did: reverting to ``find_spec`` passed all 23 controls until this
    helper was introduced. Real tests build these with
    ``spec_from_file_location``, which sets a spec, and that is what makes the
    broken discriminator answer "real".
    """
    mod = type(sys)(name)
    mod.__spec__ = importlib.util.spec_from_loader(name, loader=None)
    return mod


# --------------------------------------------------------------------------
# Pure classification
# --------------------------------------------------------------------------

def test_a_removed_module_is_graded_removed():
    before = {"json": id(sys.modules["json"])}
    after = {k: v for k, v in sys.modules.items() if k != "json"}
    assert iso.classify_module_change("json", before["json"], after) == iso.MODULE_REMOVED


def test_an_untouched_module_is_clean():
    assert iso.classify_module_change("json", id(sys.modules["json"]), sys.modules) == iso.CLEAN


def test_replacing_a_REAL_module_is_the_harmful_state():
    fake = _synthetic_module("json")
    after = dict(sys.modules, json=fake)
    assert iso.classify_module_change("json", id(sys.modules["json"]), after) == iso.MODULE_REPLACED_REAL


def test_replacing_a_SYNTHETIC_loader_name_is_NOT_reported():
    """The 127-of-161 case. `_fc_probe` is a name a test invents to load a
    script; re-creating it per test is correct behaviour, and reporting it
    would make ~80% of the audit's output noise.

    ⚠️ **THE NAME IS PUT INTO REAL ``sys.modules`` FIRST, AND THAT IS THE POINT.**
    The first version of this control built an ``after`` dict without touching
    ``sys.modules`` — so it never reproduced the condition the audit actually
    runs in, passed, and let a broken discriminator through to a full-suite run
    that reported 116 false findings. ``importlib.util.find_spec`` returns a
    spec for ANY name present in ``sys.modules``, so a synthetic name looks real
    at exactly the moment the audit asks.
    """
    name = "_fc_probe_definitely_not_installed"
    a, b = _synthetic_module(name), _synthetic_module(name)
    saved = sys.modules.get(name)
    sys.modules[name] = b
    try:
        assert iso.classify_module_change(name, id(a), sys.modules) == iso.MODULE_REPLACED_SYNTHETIC
    finally:
        if saved is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved
    assert iso.MODULE_REPLACED_SYNTHETIC not in iso.HARMFUL


def test_the_discriminator_does_not_consult_sys_modules():
    """Pins the specific mechanism, so a revert to ``find_spec`` fails here
    with a name rather than as 116 mystery findings in a 14-minute suite."""
    name = "_iso_audit_never_installed_xyz"
    saved = sys.modules.get(name)
    sys.modules[name] = _synthetic_module(name)
    try:
        assert not iso.is_real_module_name(name), (
            "a name present only because something put it in sys.modules "
            "must not be graded as a real importable module"
        )
    finally:
        if saved is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved


def test_a_builtin_is_real_even_though_it_has_no_file_on_sys_path():
    """PathFinder cannot see builtins; without the explicit check a replaced
    ``sys`` would be dismissed as a synthetic name."""
    assert iso.is_real_module_name("sys")


def test_the_synthetic_names_measured_in_the_real_suite_are_all_classified_synthetic():
    """Guards the discriminator against the REAL measured population.

    ⚠️ Each name is inserted into ``sys.modules`` first, because that is the
    state the audit sees at teardown — and it is the difference between this
    control passing in isolation and catching the ``find_spec`` defect. It DID
    catch it, in the full suite, where these names were already present.

    ⚠️ `recombination_sweep` and `backtest_trend` carry no underscore prefix, so
    a name-pattern rule would have mis-graded them as real modules. This is why
    the discriminator asks the interpreter instead of matching text.
    """
    names = ("_fc_probe", "_m20_consol", "m20_fleet_exit_sweep",
             "recombination_sweep", "backtest_trend", "_lw_sweep")
    saved = {n: sys.modules.get(n) for n in names}
    for n in names:
        sys.modules[n] = _synthetic_module(n)
    try:
        for n in names:
            assert not iso.is_real_module_name(n), n
    finally:
        for n, old_mod in saved.items():
            if old_mod is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old_mod


def test_real_first_party_names_measured_in_the_real_suite_are_classified_real():
    for name in ("src.runtime.notify", "src.web.api.routers.dashboard"):
        assert iso.is_real_module_name(name), name


def test_is_real_module_name_never_raises_on_a_poisoned_parent():
    """`find_spec` RAISES rather than returning None when a parent is a mock —
    which is exactly the polluted state this audit runs in."""
    from unittest.mock import MagicMock
    saved = sys.modules.get("telegram")
    sys.modules["telegram"] = MagicMock()
    try:
        assert iso.is_real_module_name("telegram.nonexistent_submodule") is False
    finally:
        if saved is None:
            sys.modules.pop("telegram", None)
        else:
            sys.modules["telegram"] = saved


# --------------------------------------------------------------------------
# Mode resolution
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (None, iso.MODE_ANNOTATE),
    ("annotate", iso.MODE_ANNOTATE),
    ("enforce", iso.MODE_ENFORCE),
    ("off", iso.MODE_OFF),
    ("  ENFORCE  ", iso.MODE_ENFORCE),
])
def test_mode_resolution(raw, expected):
    assert iso.resolve_mode(raw) == expected


def test_a_typo_falls_back_to_annotate_never_to_off_or_enforce():
    """A typo must not silently switch the only thing watching this class off,
    and must certainly not switch a suite-failing check on."""
    for bad in ("enfroce", "ON", "1", "", "true"):
        assert iso.resolve_mode(bad) == iso.MODE_ANNOTATE


# --------------------------------------------------------------------------
# runtime_logs tree snapshot
# --------------------------------------------------------------------------

def test_a_missing_dir_is_EMPTY_but_an_unreadable_one_is_NOT_MEASURED(tmp_path):
    assert iso.snapshot_tree(tmp_path / "nope") == frozenset()
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert f.as_posix() in iso.snapshot_tree(tmp_path)


# --------------------------------------------------------------------------
# Baseline integrity
# --------------------------------------------------------------------------

def test_every_baseline_state_is_a_real_harmful_state():
    """A baselined state that the audit can never emit would silently excuse
    nothing while looking like it excused something."""
    for nodeid, states in BASELINE.items():
        assert states, nodeid
        for s in states:
            assert s in iso.HARMFUL, f"{nodeid} declares non-harmful state {s}"


def test_baseline_is_keyed_on_state_not_just_nodeid():
    node = "tests/test_insights_router.py::test_router_module_does_not_import_anthropic"
    assert is_baselined(node, iso.MODULE_REPLACED_REAL)
    assert not is_baselined(node, iso.MODULE_REMOVED)


def test_every_baselined_nodeid_names_a_test_file_that_exists():
    """A stale entry silently excuses nothing and hides that it is stale."""
    for nodeid in BASELINE:
        path = Path(nodeid.split("::", 1)[0])
        assert path.is_file(), f"baseline names a missing file: {path}"


# --------------------------------------------------------------------------
# End-to-end, in a subprocess (see module docstring)
# --------------------------------------------------------------------------

_LEAKY = textwrap.dedent(
    """
    import sys
    def test_leaks_by_removing_a_real_module():
        sys.modules.pop("json", None)
    def test_clean_control():
        assert 1 + 1 == 2
    """
)


def _run(tmp_path: Path, mode: str) -> subprocess.CompletedProcess:
    """Run the probe UNDER ``tests/`` so the REAL ``tests/conftest.py`` loads.

    ⚠️ A ``/tmp`` tmp_path does NOT pick up ``tests/conftest.py`` — conftest
    applies to its own directory tree only. The first version of this helper
    used ``tmp_path`` and the audit silently never fired; the controls caught
    it, which is the whole reason they run the real thing end to end instead of
    asserting against a copy of the hook logic (a copy is how the two drift).

    The directory is created AFTER collection of the outer run has finished, so
    the outer suite cannot pick it up, and it is removed unconditionally.
    """
    import os
    import shutil
    import uuid

    probe_dir = Path("tests") / f"_iso_probe_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    probe_dir.mkdir(parents=True, exist_ok=True)
    try:
        t = probe_dir / "test_leaky_probe.py"
        t.write_text(_LEAKY)
        return subprocess.run(
            [sys.executable, "-m", "pytest", str(t), "-q", "-p", "no:cacheprovider"],
            capture_output=True, text=True, cwd=Path.cwd(),
            env={**os.environ, "TEST_ISOLATION_AUDIT": mode},
        )
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def test_annotate_REPORTS_the_leak_and_does_not_fail_the_run(tmp_path):
    r = _run(tmp_path, "annotate")
    assert "test-isolation audit" in r.stdout, r.stdout[-3000:]
    assert iso.MODULE_REMOVED in r.stdout, r.stdout[-3000:]
    assert "UNDECLARED" in r.stdout, r.stdout[-3000:]
    assert r.returncode == 0, f"annotate must not fail the run: {r.returncode}"


def test_enforce_FAILS_the_run_on_the_same_leak(tmp_path):
    r = _run(tmp_path, "enforce")
    assert iso.MODULE_REMOVED in r.stdout, r.stdout[-3000:]
    assert r.returncode != 0, "enforce must fail on an undeclared harmful finding"


def test_off_reports_nothing(tmp_path):
    r = _run(tmp_path, "off")
    assert "test-isolation audit" not in r.stdout, r.stdout[-3000:]
    assert r.returncode == 0


def test_the_clean_test_in_the_same_file_is_NOT_reported(tmp_path):
    """The negative control. Without it, an audit that flagged everything would
    pass every positive control above and still be useless."""
    r = _run(tmp_path, "annotate")
    assert "test_clean_control" not in r.stdout, r.stdout[-3000:]
