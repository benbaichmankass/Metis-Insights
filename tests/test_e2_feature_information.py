"""E2's 31 self-tests must run in CI, not only when someone types `--selftest`.

`scripts/research/e2_feature_information.py` carries its own control suite —
including the two that decide whether any E2 verdict is admissible at all:

  * the **positive control** must fire (a feature built from the label must be
    detected), and
  * the **planted-failure** test must show that a DEAD positive control makes
    the run return `harness_invalid` rather than a result.

A control suite nobody runs is decoration. The E0 census has
`test_census_summary_columns.py` for the same reason; this is the E2 analogue,
and it exists because `--selftest` is not on any CI path — CI runs pytest.

Kept thin on purpose: the assertions live next to the code they check, and
duplicating them here would create a second copy free to drift from the first.
What this file owns is the *invocation*, plus the handful of properties that
would silently gut the tool if they regressed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.research import e2_feature_information as E2  # noqa: E402


def test_selftest_suite_passes():
    """All of E2's self-tests, run as one CI-visible unit."""
    assert E2._selftest() == 0, "e2_feature_information self-tests failed"


def test_the_leaky_univariate_is_never_called():
    """`analyze_exit_head._univariate_fdr` is POOLED and UN-PURGED.

    It is called on the whole row set with no folds, purge, embargo or
    grouping, so its analytic q-values assume an independence that overlapping
    triple-barrier labels violate. E2 imports the SPLITTER from that module and
    must never reuse the univariate. Needle assembled at runtime so this check
    does not match its own source line.
    """
    src = Path(E2.__file__).read_text(encoding="utf-8")
    assert ("_univariate" + "_fdr(") not in src


def test_splitter_is_imported_not_reimplemented():
    """A second copy of the splitter is free to drift from the one the exit
    head is validated under; then the two disagree for reasons nobody can find."""
    src = Path(E2.__file__).read_text(encoding="utf-8")
    assert ("def " + "_grouped_purged_folds") not in src
    assert "_grouped_purged_folds" in src


def test_underpowered_is_unmeasured_not_negative():
    """An underpowered null is not a negative result.

    Reporting one as "no feature carries information" would be a confident
    negative computed from almost no data.
    """
    rows, man = E2._synth_panel(5, 4, seed=19)
    rep = E2.score_panel(rows, man, n_folds=3, n_shuffles=20)
    assert rep["verdict"] == "unmeasured"
    assert rep["unmeasured_reason"]


def test_constant_feature_scores_none_not_zero():
    """A zero-variance feature has an UNDEFINED association, not a zero one.

    Contributing a fabricated 0.0 would drag an aggregate toward a value that
    was never observed.
    """
    rows = [{"trade_id": 0, E2.TARGET_COL: float(i), "feat_flat": 1.0} for i in range(40)]
    assert E2._prepare_fold_feature(rows, list(range(40)), "feat_flat", 20) is None
    assert E2.spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None
