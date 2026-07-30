"""Regression tests for the diagnostic-provenance guard.

The guard exists because seven instances of one defect class landed on
2026-07-30, every one caught by luck rather than by any check. These tests pin
the three checks AND the two properties that decide whether a guard is worth
having at all:

* it must not fire on the compliant shape (a guard that cries wolf gets waved
  through wholesale — the alarm-fatigue failure this repo already classes as
  its own P1 bug), and
* its override must be VERIFIED, not presence-only (``new-table-wiring-guard``
  accepted any ``# data-wiring:`` line, so the cheapest way to silence it was
  to name a table that does not exist).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_diagnostic_provenance",
        _ROOT / "scripts" / "check_diagnostic_provenance.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load()


def _checks(findings):
    return sorted(f.check.split("/")[0] for f in findings)


def _scan(src: str, path: str = "scripts/ml/_probe.py"):
    return guard.check_file(path, src.splitlines())


# --------------------------------------------------------------------------- #
# A — semantic substitution
# --------------------------------------------------------------------------- #
def test_A_flags_score_read_in_a_file_claiming_a_probability():
    """The exact shape of the parity-probe and m20-exit-probe defects.

    `score` is `ShadowPredictor.predict` -> `wrapped.predict(row)`, which is
    `max(proba.values())` for a multiclass regime head: >= 0.5 by construction
    and HIGH for a confidently-CALM bar. Printing it as P(volatile) inverts
    the meaning.
    """
    src = '\n'.join([
        'def go(rows):',
        '    for r in rows:',
        '        s = r.get("score")',
        '        print(f"P(volatile) = {s}")',
    ])
    assert "A" in _checks(_scan(src))


def test_A_clean_when_the_file_calls_the_unambiguous_accessor():
    """Calling predict_proba is the fix, and must clear the check."""
    src = '\n'.join([
        'def go(model, rows):',
        '    for r in rows:',
        '        p = model.predict_proba(r)["volatile"]',
        '        print(f"P(volatile) = {p}")',
    ])
    assert "A" not in _checks(_scan(src))


def test_A_ignores_a_raw_estimator_predict():
    """`booster.predict(X)` is a different API with unambiguous semantics.

    Flagging it would be a false positive, and a noisy guard gets silenced.
    """
    src = '\n'.join([
        'import numpy as np',
        'def go(booster, X):',
        '    p = booster.predict(np.array(X))',
        '    print(f"probability = {p}")',
    ])
    assert "A" not in _checks(_scan(src))


def test_A_does_not_fire_on_a_file_making_no_probability_claim():
    src = '\n'.join([
        'def go(rows):',
        '    return [r.get("score") for r in rows]',
    ])
    assert _scan(src) == []


# --------------------------------------------------------------------------- #
# B — implicit input selection
# --------------------------------------------------------------------------- #
def test_B_flags_alphabetically_last_glob_pick():
    """`sorted(glob(...))[-1]` labelled "TRAINING dataset" compared a head
    against data it never trained on (BL-20260730-PARITY-PROBE-...)."""
    src = '\n'.join([
        'import glob',
        'def training_rows(sym):',
        '    cands = sorted(glob.glob(f"datasets-out/{sym}/*/data.jsonl"))',
        '    return load(cands[-1])',
    ])
    assert "B" in _checks(_scan(src))


def test_B_clean_when_the_pinned_version_is_resolved():
    src = '\n'.join([
        'import glob',
        'def training_rows(sym, model_id):',
        '    version = manifest_dataset_version(model_id)',
        '    cands = sorted(glob.glob(f"datasets-out/{sym}/*/data.jsonl"))',
        '    return load(cands[-1])',
    ])
    assert "B" not in _checks(_scan(src))


def test_B_clean_when_the_chosen_path_is_printed():
    """Printing the choice is the minimum bar: a fallback is never silent."""
    src = '\n'.join([
        'import glob',
        'def training_rows(sym):',
        '    cands = sorted(glob.glob(f"datasets-out/{sym}/*/data.jsonl"))',
        '    print(f"using dataset {cands[-1]}")',
        '    return load(cands[-1])',
    ])
    assert "B" not in _checks(_scan(src))


# --------------------------------------------------------------------------- #
# C — unquantified universal claim
# --------------------------------------------------------------------------- #
def test_C_flags_bare_all_clear():
    """The bybit-bracket roll-up shape: an all-clear over a real anomaly."""
    src = '\n'.join([
        'def rollup(bad):',
        '    if not bad:',
        '        print("  every audited symbol is fully SL-covered at the broker.")',
    ])
    assert "C" in _checks(_scan(src, "scripts/ops/audit.py"))


def test_C_clean_when_the_denominator_is_in_the_claim():
    src = '\n'.join([
        'def rollup(bad, n):',
        '    if not bad:',
        '        print(f"  all {n} audited symbols covered; 0 naked.")',
    ])
    assert "C" not in _checks(_scan(src, "scripts/ops/audit.py"))


def test_C_denominator_must_be_in_the_claim_not_merely_nearby():
    """A formatted detail line above does not license a bare summary line.

    The bybit roll-up printed its all-clear three lines below formatted per-
    symbol detail; a proximity window would have cleared exactly the line that
    misled a reader who stopped at the summary.
    """
    src = '\n'.join([
        'def rollup(rows, bad):',
        '    for r in rows:',
        '        print("    %-10s coverage=%.1f%%" % (r.sym, r.pct))',
        '    if not bad:',
        '        print("  every audited symbol is fully SL-covered.")',
    ])
    assert "C" in _checks(_scan(src, "scripts/ops/audit.py"))


# --------------------------------------------------------------------------- #
# the override must be VERIFIED, not presence-only
# --------------------------------------------------------------------------- #
def test_annotation_naming_a_real_accessor_clears_the_finding():
    src = '\n'.join([
        'def go(rows, predict_proba):',
        '    for r in rows:',
        '        # provenance: predict_proba — this is P(volatile), the gate cut',
        '        s = r.get("score")',
        '        print(f"P(volatile) = {s}")',
    ])
    assert _scan(src) == []


def test_annotation_naming_nothing_real_does_not_clear_the_finding():
    """The anti-"lie to the guard" rule.

    ``new-table-wiring-guard`` accepted any marker, so the path of least
    resistance to silencing it was to name something that does not exist. An
    annotation whose named identifiers appear nowhere in the file is rejected,
    and the message says why.
    """
    src = '\n'.join([
        'def go(rows):',
        '    for r in rows:',
        '        # provenance: this is definitely fine, trust me',
        '        s = r.get("score")',
        '        print(f"P(volatile) = {s}")',
    ])
    findings = _scan(src)
    assert "A" in _checks(findings)
    assert "names no identifier" in findings[0].detail


# --------------------------------------------------------------------------- #
# scoping + diff plumbing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path,expected", [
    ("scripts/ml/probe.py", True),
    ("scripts/research/study.py", True),
    ("scripts/ops/audit.py", True),
    ("scripts/check_new_table_wiring.py", True),   # guards are diagnostics too
    ("src/runtime/pipeline.py", False),            # runtime, not human-facing
    ("tests/test_probe.py", False),
    ("scripts/ml/notes.md", False),
])
def test_surface_scoping(path, expected):
    assert guard.in_surface(path) is expected


def test_scan_diff_only_reports_added_lines(tmp_path, monkeypatch):
    """CI runs diff-scoped so pre-existing sites are grandfathered (same
    contract as silent-empty-guard); `--all` is the audit mode."""
    target = tmp_path / "scripts" / "ml" / "probe.py"
    target.parent.mkdir(parents=True)
    target.write_text('\n'.join([
        'def old(rows):',
        '    s = rows[0].get("score")',        # line 2 — pre-existing
        '    print(f"P(volatile) {s}")',
        'def new(rows):',
        '    t = rows[1].get("score")',        # line 5 — added by the PR
        '    print(f"probability {t}")',
    ]), encoding="utf-8")
    monkeypatch.setattr(guard, "_REPO_ROOT", str(tmp_path))

    diff = ("+++ b/scripts/ml/probe.py\n"
            "@@ -4,0 +5,1 @@\n"
            '+    t = rows[1].get("score")\n')
    findings = guard.scan_diff(diff)
    assert [f.lineno for f in findings] == [5]


def test_the_repo_is_clean_on_an_empty_diff():
    assert guard.scan_diff("") == []


def test_the_fixed_parity_probe_passes():
    """PR #8091's fix must satisfy the guard that generalises it — otherwise
    the guard does not actually encode the lesson it was written from."""
    lines = (_ROOT / "scripts" / "ml" / "_feature_parity_probe.py").read_text(
        encoding="utf-8").splitlines()
    assert guard.check_file("scripts/ml/_feature_parity_probe.py", lines) == []
