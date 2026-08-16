"""`matrix-corpus-agreement` must report what it could NOT check.

The guard looks up each cell's corpus evidence and, finding none, moves on.
That branch is correct for "rows exist and none contradicts" and WRONG for
"this lever has no corpus rows at all" — and the summary counted both as
`checked`. Measured 2026-08-14 on the committed pair: 3 of 8 lever columns
(`exit_ladder`, `exit_head_ml`, `regime_flip_exit`) have ZERO corpus rows, so
`376 live cell(s) checked` actually covered 235, and 141 (37.5%) were
unreachable by construction — 115 of them carrying an explicit
`honest_negative`, a stated negative resting on evidence the guard implied it
had verified.

These tests pin the three things that were wrong, or would have been wrong in
an obvious "fix":

  1. The two counts stay SEPARATE. Summing them back into one total is exactly
     the number that read as coverage.
  2. An UNDECLARED corpus-less column FAILS. Without this the fix degenerates
     into a hardcoded skip, and the next lever to ship without an evidence
     store inherits the same silence these three had.
  3. A DECLARED exemption that has since gained corpus rows FAILS. An exemption
     that outlived its reason keeps excusing a column from a check it can now
     pass — stale prose beside a changed field.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ci" / "check_matrix_corpus_agreement.py"
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD), *args],
                          capture_output=True, text=True)


def test_guard_self_test_passes_and_covers_the_unreachable_case() -> None:
    p = _run("--self-test")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "unreachable rather than checked" in p.stdout, (
        "the self-test no longer covers the corpus-less lever case"
    )


def test_summary_never_folds_unreachable_cells_into_the_checked_count() -> None:
    """The headline must not re-acquire the number that misled.

    Exit 1 is a legitimate outcome (real findings); what must never happen is a
    summary that reports every live cell as checked.
    """
    p = _run()
    assert p.returncode in (0, 1), f"guard crashed (rc={p.returncode}):\n{p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    if p.returncode != 0:
        return  # findings path prints no coverage summary; nothing to pin here

    import re
    checked = re.search(r"OK — (\d+) live cell\(s\) checked", p.stdout)
    assert checked, f"summary no longer states its denominator:\n{p.stdout}"

    matrix = json.loads(MATRIX.read_text())
    all_live = sum(
        1 for r in matrix.get("rows", []) if r.get("execution") == "live"
        for lv in matrix.get("lever_columns", []) if (r.get(lv) or {}).get("status")
    )
    assert int(checked.group(1)) < all_live, (
        f"the guard reports all {all_live} live cells as checked, but lever "
        f"columns with zero corpus rows cannot be checked — the two counts have "
        f"been folded back together"
    )
    assert "NOT CHECKED" in p.stdout, (
        "unreachable cells must be stated, not merely excluded from the total"
    )


def test_every_declared_exemption_names_a_real_lever_and_states_a_reason() -> None:
    src = GUARD.read_text()
    assert "CORPUS_EXEMPT_LEVERS" in src

    import importlib.util
    spec = importlib.util.spec_from_file_location("_mca", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    levers = set(json.loads(MATRIX.read_text()).get("lever_columns", []))
    for lever, reason in mod.CORPUS_EXEMPT_LEVERS.items():
        assert lever in levers, f"exemption names a non-existent column: {lever}"
        # A bare marker is the failure mode `new-table-wiring-guard` taught this
        # repo: the cheapest way to satisfy a presence-only check is to lie to
        # it. The reason has to say where the measurement actually lives.
        assert len(reason) > 80, f"{lever}: exemption reason is too thin to audit"


def test_an_undeclared_corpus_less_column_fails() -> None:
    """Otherwise the fix is just a hardcoded skip wearing a table."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_mca2", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cov = mod.lever_coverage(
        {"lever_columns": ["_never_declared"],
         "rows": [{"strategy": "_x", "execution": "live",
                   "_never_declared": {"status": "honest_negative"}}]},
        [],
    )
    assert cov["_never_declared"] == {
        "corpus_rows": 0, "live_cells": 1, "declared_exempt": False
    }, cov
