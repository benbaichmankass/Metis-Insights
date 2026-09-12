"""The two exit audits must not be free to disagree about the same capability.

WHAT THIS PINS
──────────────
`exit_mechanism_coverage` and `exit_path_coverage` both answer "does this unit
run lever X?". They used to answer it from SEPARATE tables, and they diverged.

STATE THE POPULATION. Measured 2026-09-12 on main, before the fix: over every
strategy unit module, `emc.module_implements(unit, "exit_head")` returned True
for `trend_donchian` and `ict_scalp` while `epc.module_reads(unit, exit_head
keys)` returned False for both. `trend_donchian` is the REAL-MONEY donchian
family and `ict_scalp` carries 8 legs; both delegate `exit_head_verdict` to
`src/runtime/exit_head_apply.py` and run the lever perfectly.

The cause was structural: the sibling generalised its entry to carry the owning
MODULE per mechanism on 2026-09-06, while this file kept a bare symbol->reason
map plus ONE hardcoded module path. A lever whose body lives anywhere else was
invisible to it. That is
BL-20260818-CAPABILITY-AUDITS-GREP-ONE-FILE-AND-MISS-SHARED-LEVERS, whose
resolution criterion asks for the general relationship rather than each audit
enumerating the modules it happens to know about.

⚠️ THE INVARIANT IS "CANNOT DISAGREE", NOT "AGREE TODAY". Adding a third entry
to the second table would have made them agree today and re-broken on the next
extraction. The test below therefore also asserts the single-owner property, not
just the current answers.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"


def _load(name: str):
    """Load an ops script AND register it in ``sys.modules``.

    ⚠️ REGISTERING IS LOAD-BEARING, not tidiness. `_shared_lever_map()` does
    `import exit_mechanism_coverage` at call time, so without this the audit
    resolves a DIFFERENT module object than the test holds, and a monkeypatch
    of the owner's table silently reaches nothing — the brand-new-module test
    below fails for a reason that has nothing to do with the property it is
    testing. Measured while writing it.
    """
    import sys
    if str(OPS) not in sys.path:
        sys.path.insert(0, str(OPS))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, OPS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def audits():
    return _load("exit_path_coverage"), _load("exit_mechanism_coverage")


def _disagreements(epc, emc) -> list[tuple[str, str]]:
    """Units × levers where the two audits answer differently."""
    out = []
    for unit, src in epc.load_units().items():
        for mech, (_sym, _rel) in emc._SHARED_VERDICT_SYMBOLS.items():
            keys = emc.MECHANISMS[mech]
            if emc.module_implements(src, mech) != epc.module_reads(src, keys):
                out.append((unit, mech))
    return out


def test_the_two_audits_agree_on_every_unit_and_lever(audits):
    epc, emc = audits
    bad = _disagreements(epc, emc)
    assert not bad, (
        f"{len(bad)} unit/lever pair(s) where the two exit audits disagree about "
        f"the same capability: {bad[:6]}. Before 2026-09-12 this was "
        f"[('trend_donchian','exit_head'), ('ict_scalp','exit_head')] — two "
        f"live families under-reported as not implementing a lever they run."
    )


def test_the_population_is_not_vacuously_empty(audits):
    """A positive denominator. If no unit delegates any lever, the agreement
    test above passes while checking nothing."""
    epc, emc = audits
    units = epc.load_units()
    assert len(units) > 5, f"only {len(units)} unit modules loaded"
    delegating = [u for u, s in units.items()
                  if any(sym in s for sym, _ in emc._SHARED_VERDICT_SYMBOLS.values())]
    assert delegating, "no unit delegates any shared lever — the probe is vacuous"


def test_exit_path_coverage_keeps_no_second_copy_of_the_lever_vocabulary(audits):
    """The single-owner property — the thing that makes disagreement impossible
    rather than merely absent today."""
    epc, _emc = audits
    src = (OPS / "exit_path_coverage.py").read_text()
    assert not [l for l in src.splitlines()
                if l.startswith("_SHARED_VERDICTS")], (
        "exit_path_coverage defines its own lever table again; it must read the "
        "one in exit_mechanism_coverage or the two are free to diverge"
    )
    assert hasattr(epc, "_shared_lever_map")


def test_a_lever_extracted_to_a_BRAND_NEW_module_is_seen_without_editing_this_file(
        audits, tmp_path, monkeypatch):
    """The general property the backlog row actually asks for.

    A third-party lever is added to the ONE owner's table, its body placed in a
    module neither audit has ever heard of, and `exit_path_coverage` must see it
    with no edit of its own.
    """
    epc, emc = audits
    (tmp_path / "src" / "runtime").mkdir(parents=True)
    (tmp_path / "src" / "runtime" / "brand_new_lever.py").write_text(
        'def novel_verdict(cfg):\n    return cfg["novel_key"]\n')
    monkeypatch.setattr(epc, "REPO", tmp_path)
    monkeypatch.setitem(emc._SHARED_VERDICT_SYMBOLS, "novel",
                        ("novel_verdict", "src/runtime/brand_new_lever.py"))

    unit = "from src.runtime.brand_new_lever import novel_verdict\n"
    assert epc.module_reads(unit, ("novel_key",)) is True, (
        "a lever whose body lives in a module this file has never named was not "
        "seen — the enumeration is back"
    )


def test_planted_defect_the_pre_fix_shape_REDS_the_agreement_test(
        audits, monkeypatch):
    """Re-install the pre-fix shape: only the two `exit_levers` symbols, one
    hardcoded module. The agreement test must fail."""
    epc, emc = audits
    assert not _disagreements(epc, emc), "PRE-RED: already disagreeing"

    monkeypatch.setattr(epc, "_shared_lever_map", lambda: {
        "stale_stop_verdict": ("stale_stop", "src/runtime/exit_levers.py"),
        "giveback_verdict": ("giveback_stop", "src/runtime/exit_levers.py"),
    })
    bad = _disagreements(epc, emc)
    assert bad, (
        "THE PLANT DID NOT LAND: dropping exit_head from the map produced no "
        "disagreement, so this probe is inert."
    )
    assert any(m == "exit_head" for _u, m in bad)
    with pytest.raises(AssertionError, match="disagree"):
        test_the_two_audits_agree_on_every_unit_and_lever((epc, emc))


def test_negative_control_reordering_the_map_changes_nothing(audits, monkeypatch):
    """An edit that touches the same accessor without losing an entry must stay
    green, so the probe reds on lost coverage rather than on any change."""
    epc, emc = audits
    real = epc._shared_lever_map()
    monkeypatch.setattr(epc, "_shared_lever_map",
                        lambda: dict(reversed(list(real.items()))))
    assert not _disagreements(epc, emc)
