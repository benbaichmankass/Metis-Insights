"""Test-isolation audit — detect a test that leaves process-global state changed.

WHY THIS EXISTS, and why it is a DETECTOR rather than more stubbing.

Three defects inside two days (2026-08-27) all had one shape: a test mutated
process-global state and did not put it back, and the damage landed on an
unrelated test far away in the run.

  * ``BL-20260827-NOTIFY-SESSION-TEST-POPS-SYS-MODULES-AND-NEVER-RESTORES``
  * ``BL-20260827-TEST-MODULES-STUB-SKLEARN-INTO-SYS-MODULES-AT-IMPORT-TIME``
  * ``BL-20260822-TEST-SUITE-CROSS-FILE-SYS-MODULES-POLLUTION``

Each was fixed as an INSTANCE. The first one's own write-up names the remedy for
the CLASS, and this module is it::

    "A conftest-level audit — no test may leave sys.modules or runtime_logs/
     different from how it found them — would catch the class rather than the
     instances."

⚠️ **THE REMEDY IS "NEVER STUB", NOT "STUB MORE".** An earlier attempt at the
sklearn instance added packages to a stub list, put a ``MagicMock`` in
``sys.modules["numpy"]``, and broke ``pytest.approx`` outright in the numpy-less
``guards`` job. Nothing here stubs anything: it only observes.

⚠️ **ALL THREE WERE INVISIBLE IN CI** — CI installs the full requirements, which
masks them; only a leaner box could see them. So this audit's own baseline was
measured on ONE sandbox and must not be assumed to be CI's. That is the whole
reason the default mode is ``annotate`` rather than ``enforce``.

## The cheap-detect / expensive-classify split

Detection runs on EVERY test (13,334 of them), so it must be cheap: one
``{name: id(obj)}`` snapshot, measured at ~15 us per 2000 modules — about **7 s
added to a 785 s suite (~1%)**.

Classification is the expensive part (``importlib.util.find_spec``) and runs
ONLY on the handful of names that actually changed. Doing it the other way round
would put a spec lookup on every module on every test.

## Why "replaced" alone is not the finding

Measured over the full suite: **127 of 161 findings were a module being
replaced, and nearly all of them are correct behaviour.** Tests load a script
under test via ``spec_from_file_location("_fc_probe", ...)`` and re-create that
synthetic name per test — ``_m20_consol``, ``_corpus_extract_probe``,
``m20_fleet_exit_sweep`` and 19 others. Reporting those would make ~80% of the
output noise, and an alarm that fires constantly on correct behaviour is the
desensitized-alarm P1 this repo treats as its own bug class.

The discriminator is whether the name is a REAL importable module. A synthetic
loader name is not importable; ``src.runtime.notify`` is.
"""
from __future__ import annotations

from importlib.machinery import PathFinder
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# The import path AS IT WAS BEFORE ANY TEST RAN.
#
# ⚠️ **THIS IS PINNED AT IMPORT TIME AND NEVER REFRESHED, WHICH IS THE POINT.**
# Tests insert their own directories into ``sys.path`` — measured: at least five
# add ``scripts/research`` — which makes names like ``m20_fleet_exit_sweep`` and
# ``m20_dispersion_rate`` genuinely importable partway through the run. Asking
# the LIVE ``sys.path`` therefore grades a test's own script-under-test as a
# real shared module and reports it, which is how a full-suite run still
# produced **36 undeclared findings that were all correct behaviour** after the
# ``find_spec`` defect was already fixed.
#
# Pinning the base path asks the question that actually matters: *could this
# name have been imported before any test meddled with the interpreter?*
# ``src`` resolves on the base path; ``m20_fleet_exit_sweep`` does not.
# ---------------------------------------------------------------------------
_BASE_SYS_PATH: list[str] = list(sys.path)


# ---------------------------------------------------------------------------
# States. Never collapsed — "we did not look" is not "we looked and found
# nothing", and "a synthetic name was re-created" is not "a real module was
# shadowed".
# ---------------------------------------------------------------------------
CLEAN = "clean"
MODULE_REMOVED = "module_removed"                    # harmful
MODULE_REPLACED_REAL = "module_replaced_real"        # harmful
MODULE_REPLACED_SYNTHETIC = "module_replaced_synthetic"  # benign by design
RUNTIME_LOGS_WRITTEN = "runtime_logs_written"        # reported, not enforced
NOT_MEASURED = "not_measured"                        # we could not look

HARMFUL = (MODULE_REMOVED, MODULE_REPLACED_REAL)

MODE_OFF = "off"
MODE_ANNOTATE = "annotate"
MODE_ENFORCE = "enforce"
_VALID_MODES = (MODE_OFF, MODE_ANNOTATE, MODE_ENFORCE)


def resolve_mode(raw: str | None) -> str:
    """Resolve the audit mode.

    ⚠️ An unrecognised value falls back to ``annotate``, never to ``off`` and
    never to ``enforce``. A typo must not silently switch the only thing
    watching this class off, and must certainly not switch a suite-failing
    check on. Same discipline as ``CANDLE_CACHE_TTL_FRACTION`` and
    ``PROTECTION_STRAY_GROUP_MODE``.
    """
    if raw is None:
        return MODE_ANNOTATE
    cleaned = raw.strip().lower()
    return cleaned if cleaned in _VALID_MODES else MODE_ANNOTATE


def is_real_module_name(name: str) -> bool:
    """True when *name* names a module that genuinely exists on this box.

    ⚠️ **IT MUST NOT USE ``importlib.util.find_spec``, AND THE FIRST VERSION DID.**
    ``find_spec`` consults ``sys.modules`` FIRST and returns the existing
    module's ``__spec__`` without ever searching the path. This audit asks the
    question at teardown, when the synthetic name **is** in ``sys.modules`` —
    so every test-local loader name came back "real" and the audit reported
    **116 undeclared findings that were all correct behaviour**, which is the
    desensitized-alarm P1 it was written to avoid. Measured directly::

        name not in sys.modules -> find_spec(name) is None          # synthetic
        name IN     sys.modules -> find_spec(name) is a ModuleSpec  # "real"
        either way              -> PathFinder().find_spec(...) is None

    ``PathFinder`` searches ``sys.path`` and never consults ``sys.modules``, so
    it answers the question actually being asked: *could this name be imported
    if nothing had put it there?*

    ⚠️ **It resolves the ROOT package, not the full dotted name.** A replaced
    ``src.runtime.notify`` may have had its own parent replaced too, and asking
    about a submodule requires walking a possibly-poisoned parent's
    ``__path__``. The root is enough to separate an invented name
    (``_fc_probe``) from a real family (``src.*``, ``ml.*``, ``scripts.*``), and
    when it errs it errs toward REPORTING — the safe direction for a detector.

    ⚠️ **It searches the PINNED base ``sys.path``, not the live one.** Tests add
    their own directories mid-run, which makes a test's own script-under-test
    importable and therefore "real". See ``_BASE_SYS_PATH`` above.

    ⚠️ Builtins are checked separately because they have no file on ``sys.path``
    and ``PathFinder`` cannot see them; without this a replaced ``sys`` would be
    dismissed as a synthetic name.

    ⚠️ Any exception is treated as NOT real. The import machinery raises rather
    than returning ``None`` when a parent is itself a mock — precisely the
    polluted state this audit runs in — and a raising probe must not take the
    suite down.

    ⚠️ It is asked of the interpreter rather than matched against a name
    pattern. A rule like "starts with an underscore" is the match-incidental-
    text defect: ``recombination_sweep`` and ``backtest_trend`` are synthetic
    and have no underscore prefix at all.
    """
    root = name.split(".", 1)[0]
    if root in sys.builtin_module_names:
        return True
    try:
        return PathFinder().find_spec(root, _BASE_SYS_PATH) is not None
    except Exception:
        return False


def classify_module_change(name: str, before_id: int, modules) -> str:
    """Grade one module name's before/after transition. Pure."""
    if name not in modules:
        return MODULE_REMOVED
    if id(modules[name]) == before_id:
        return CLEAN
    return MODULE_REPLACED_REAL if is_real_module_name(name) else MODULE_REPLACED_SYNTHETIC


def snapshot_modules(modules=None) -> dict[str, int]:
    """Cheap identity snapshot. ~15 us per 2000 entries."""
    mods = sys.modules if modules is None else modules
    return {k: id(v) for k, v in list(mods.items())}


def diff_modules(before: dict[str, int], modules=None) -> dict[str, list[str]]:
    """Return ``{state: [names]}`` for every name whose state is not CLEAN."""
    mods = sys.modules if modules is None else modules
    out: dict[str, list[str]] = {}
    for name, before_id in before.items():
        state = classify_module_change(name, before_id, mods)
        if state != CLEAN:
            out.setdefault(state, []).append(name)
    return out


def snapshot_tree(root: Path) -> frozenset[str]:
    """Files under *root*. Missing dir is an EMPTY set; an unreadable one is None.

    ⚠️ Returning ``None`` for unreadable rather than an empty set keeps "we
    could not look" distinguishable from "the directory is empty" — collapsing
    them would make a permission error read as a clean tree.
    """
    try:
        if not root.is_dir():
            return frozenset()
        return frozenset(p.as_posix() for p in root.rglob("*") if p.is_file())
    except Exception:
        return None  # type: ignore[return-value]
