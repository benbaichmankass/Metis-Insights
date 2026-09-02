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
        '        print(f"P(volatile) = {predict_proba} {s}")',
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


# --------------------------------------------------------------------------- #
# D and E — added 2026-07-31 because the guard MISSED the worst instance of its
# own class, committed by the session that wrote it. The acceptance test for
# "is the class actually fixed" is whether the guard catches what I shipped.
# --------------------------------------------------------------------------- #
def test_D_flags_a_parameter_the_body_never_reads():
    """The ROOT of the trend_threshold mislabel.

    `_label_regime` accepts `trend_threshold` and ignores it, so every doc
    describing its effect asserted something that does not exist. Catching the
    dead parameter catches the mislabel before anyone can write it.
    """
    src = '\n'.join([
        'def label(forward_vol, *, vol_threshold, trend_threshold):',
        '    if forward_vol > vol_threshold:',
        '        return "volatile"',
        '    return "range"',
    ])
    findings = _scan(src, "ml/datasets/families/x.py")
    inert = [f for f in findings if f.check.startswith("D")]
    assert [f for f in inert if "trend_threshold" in f.detail]
    assert not [f for f in inert if "vol_threshold" in f.detail and
                "trend" not in f.detail], "a USED parameter must not be flagged"


def test_D_ignores_abstract_and_stub_bodies():
    """An ABC declaring an interface asserts nothing about what the args do.

    Flagging those is the alarm-fatigue failure mode — ml/evaluators/base.py
    alone would contribute a wall of noise and get the guard waved through.
    """
    src = '\n'.join([
        'class E:',
        '    def score(self, model_state, rows, config):',
        '        ...',
        '    def other(self, a, b):',
        '        raise NotImplementedError',
    ])
    assert [f for f in _scan(src, "ml/evaluators/base.py")
            if f.check.startswith("D")] == []


def test_D_respects_a_deliberate_inert_marker_that_NAMES_the_parameter():
    src = '\n'.join([
        'def label(forward_vol, *, vol_threshold,',
        '          trend_threshold):  # inert: trend_threshold — back-compat, 2-class collapse',
        '    return "volatile" if forward_vol > vol_threshold else "range"',
    ])
    assert not [f for f in _scan(src, "ml/datasets/families/x.py")
                if f.check.startswith("D") and "trend_threshold" in f.detail]


def test_D_inert_override_is_VERIFIED_not_presence_only():
    """A bare ``# inert:`` naming nothing must NOT silence the finding.

    This module's own docstring has always said the override must be verified
    rather than presence-only, citing ``new-table-wiring-guard``, whose
    presence-only marker made the cheapest way to silence a real finding a
    comment naming a table that does not exist. The ``# inert:`` marker was
    nonetheless matched by a bare ``re.compile(r'#\\s*inert:')`` until
    2026-09-02, so the docstring and the code disagreed and the code won.

    Measured when the check was tightened: 11 markers across 4 real files
    (dukascopy_span_probe, e2_feature_information, probe_actions_log,
    render_due_list) named no parameter at all, and the tree still reported
    OK. Every one carried a genuine reason — the point is not that they were
    dishonest, it is that nothing could tell them from a marker that was.
    """
    bare = '\n'.join([
        'def label(forward_vol, *, vol_threshold,',
        '          trend_threshold):  # inert: kept for back-compat',
        '    return "volatile" if forward_vol > vol_threshold else "range"',
    ])
    flagged = [f for f in _scan(bare, "ml/datasets/families/x.py")
               if f.check.startswith("D") and "trend_threshold" in f.detail]
    assert flagged, "a `# inert:` naming no parameter must not silence the finding"
    assert "does not name" in flagged[0].detail

    # And a marker naming the WRONG parameter must not launder the right one:
    # otherwise one annotation could be copy-pasted across a whole signature.
    wrong = '\n'.join([
        'def label(forward_vol, *, vol_threshold,',
        '          trend_threshold):  # inert: vol_threshold — wrong parameter',
        '    return "volatile" if forward_vol > vol_threshold else "range"',
    ])
    assert [f for f in _scan(wrong, "ml/datasets/families/x.py")
            if f.check.startswith("D") and "trend_threshold" in f.detail]


def test_E_flags_an_interpretation_printed_unconditionally():
    """The m20 probe shape: conclusion emitted with every bucket at n=0.

    Sub-class C did not fire — the sentence has no universal quantifier. What
    makes it wrong is that it is UNCONDITIONAL, so an ABSENT measurement reads
    exactly like a measured one.
    """
    src = '\n'.join([
        'def main():',
        '    buckets = compute()',
        '    for b in ("lo", "mid", "hi"):',
        '        print(f"{b} n={len(buckets[b])}")',
        '    print("Interpretation: a more negative mean in hi means the head "',
        '          "carries exit information")',
        '    return 0',
    ])
    assert [f for f in _scan(src, "scripts/research/probe.py")
            if f.check.startswith("E")]


def test_E_accepts_a_guard_clause_as_gating():
    """The idiomatic fix must not itself be flagged, or the check punishes
    exactly the change it asks for."""
    src = '\n'.join([
        'def main():',
        '    buckets = compute()',
        '    n = sum(len(v) for v in buckets.values())',
        '    if n == 0:',
        '        print("NO CONCLUSION AVAILABLE: nothing measured")',
        '        return 0',
        '    print("Interpretation: a more negative mean in hi means the head "',
        '          "carries exit information")',
        '    return 0',
    ])
    assert not [f for f in _scan(src, "scripts/research/probe.py")
                if f.check.startswith("E")]


def test_the_ml_dataset_surface_is_in_scope():
    """It was not, which is why the guard missed its own worst instance."""
    assert guard.in_surface("ml/datasets/families/market_features.py")
    assert guard.in_surface("ml/labeling/trend_regime.py")
    assert not guard.in_surface("src/runtime/pipeline.py")
