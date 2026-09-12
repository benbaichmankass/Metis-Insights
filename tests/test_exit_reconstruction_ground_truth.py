"""The exit-reconstruction validators must not carry their OWN ground-truth set.

WHY THIS EXISTS (`BL-20260807-EXIT-ANCHOR-VALIDATED-ON-DISJOINT-POPULATION`).
Both validators graded the bracket-resolution estimator against a LOCAL copy of
"which `exit_price_source` values are broker truth", while
``src/runtime/provenance.py`` is the declared single owner of exactly that
question — ``CLAUDE.md``: *"One module owns this ... Import it; do not re-derive
the vocabulary."*

The copy had drifted in BOTH directions. MEASURED 2026-09-12 over the newest
1000 ``trades`` rows read via ``/api/diag/journal``::

    the validators' old set matched        50 rows
    provenance.MEASURED_SOURCES matches   131 rows
    in both                                33

    ONLY the old set (17)      recorded_exit_price
    ONLY the canonical (98)    exchange_fill 93 + ib_execution 5

⚠️ **THE 17 IS THE ONE THAT MATTERS.** ``recorded_exit_price`` was DEMOTED from
MEASURED on 2026-08-24 by an operator-approved change
(``BL-20260824-RECORDED-EXIT-PRICE-OUTNUMBERS-ALL-BROKER-TRUTH-COMBINED``) and
buckets ``estimated`` — **the same bucket as ``candle_at_close``, the estimator
under test**. A third of the old "ground truth" was the estimator's own class, so
the validator was partly measuring it against itself.

And the 98 it discarded is 75% of the genuine broker truth available to it,
including ``exchange_fill``, the largest real source in the journal.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
VALIDATORS = (
    REPO / "scripts/research/exit_reconstruction_validator.py",
    REPO / "scripts/research/exit_reconstruction_validator_v2.py",
)


def _canonical():
    import sys
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from src.runtime.provenance import MEASURED_SOURCES
    return MEASURED_SOURCES


@pytest.mark.parametrize("path", VALIDATORS, ids=lambda p: p.name)
def test_the_validator_imports_the_canonical_set_and_defines_no_second_one(path):
    """Grade the CODE, not the prose: a docstring may legitimately name the set."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports_it = any(
        isinstance(n, ast.ImportFrom)
        and (n.module or "").endswith("runtime.provenance")
        and any(a.name == "MEASURED_SOURCES" for a in n.names)
        for n in ast.walk(tree)
    )
    assert imports_it, (
        f"{path.name} does not import MEASURED_SOURCES from src.runtime.provenance. "
        f"That module is the declared single owner of which exit_price_source "
        f"values are broker truth; a local copy drifted in BOTH directions and "
        f"partly graded the estimator against its own bucket."
    )
    # No literal set/frozenset of source-looking names assigned to a MEASURED name.
    for n in ast.walk(tree):
        if not isinstance(n, ast.Assign):
            continue
        names = {t.id for t in n.targets if isinstance(t, ast.Name)}
        if not names & {"MEASURED", "MEASURED_SOURCES", "GROUND_TRUTH"}:
            continue
        assert not isinstance(n.value, (ast.Set, ast.Call)) or (
            isinstance(n.value, ast.Call)
            and getattr(n.value.func, "id", "") not in ("set", "frozenset")
        ), (
            f"{path.name} assigns a literal ground-truth set again. There is one "
            f"owner; a second copy is how this drifted."
        )


def test_the_canonical_set_is_the_one_the_drift_was_about():
    """Pins the two directions of the drift, so a silent re-demotion is caught."""
    m = _canonical()
    # the 98 the validators used to discard
    assert "exchange_fill" in m
    assert "ib_execution" in m
    # the 17 they used to count as truth
    assert "recorded_exit_price" not in m, (
        "recorded_exit_price is back in MEASURED_SOURCES. It was demoted "
        "2026-08-24 because it is not a fill; if that is being reversed it is a "
        "Tier-2 decision, not a test update."
    )
    # and the estimator under test must never be its own ground truth
    assert "candle_at_close" not in m


def test_the_estimator_and_the_demoted_source_share_a_bucket():
    """The reason the 17 were disqualifying, asserted rather than asserted-about."""
    import sys
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from src.runtime.provenance import classify
    assert classify("recorded_exit_price") == classify("candle_at_close") == "estimated"
