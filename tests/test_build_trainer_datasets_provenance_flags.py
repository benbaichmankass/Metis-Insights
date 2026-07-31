"""The provenance-filter flag must actually be PASSED by the production
build script — closing gap G7 of the 2026-07-31 full-system audit.

The 2026-07-31 half-registration incident (#8152): the fabricated-label
filter was built into a family and CI passed while the production caller
never handed the flag to the family the model actually trains on. A filter
that exists but is not wired where it matters is indistinguishable from no
filter — so this test pins the wiring, not just the implementation.

Deliberately a TEXT assertion against ``scripts/ops/build_trainer_datasets.sh``
(the same shape as ``tests/test_run_training_cycle_sh.py``'s bash checks):
it cannot prove the shell runs, only that the flag reaches each filtered
family's ``build_family`` invocation and that the kill-switch env resolves
the value. Honest scope: a syntax-level guard against silent de-wiring.
"""
from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "build_trainer_datasets.sh"

# Every family whose labels/targets derive from journal pnl must receive the
# filter flag from the production builder. Extend this list when a new
# pnl-consuming family gains the filter — a family filtered in code but
# absent here is exactly the half-registration this test exists to catch.
FILTERED_FAMILIES = ("trade_outcomes", "setup_labels", "conviction_meta")


def _build_family_blocks(text: str) -> dict[str, str]:
    """Map family name -> the text of its ``build_family`` invocation
    (from ``build_family <name>`` to the first non-continuation line)."""
    blocks: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"\s*build_family\s+(\w+)", lines[i])
        if m:
            start = i
            while i < len(lines) and lines[i].rstrip().endswith("\\"):
                i += 1
            blocks.setdefault(m.group(1), "\n".join(lines[start:i + 1]))
        i += 1
    return blocks


def test_script_resolves_kill_switch_env():
    text = SCRIPT.read_text()
    assert "DATASET_FABRICATED_LABEL_FILTER_DISABLED" in text
    assert 'EXCLUDE_FABRICATED="true"' in text  # default ON


def test_every_filtered_family_receives_the_flag():
    text = SCRIPT.read_text()
    blocks = _build_family_blocks(text)
    for family in FILTERED_FAMILIES:
        assert family in blocks, f"build_family {family} invocation not found"
        assert "exclude_fabricated_pnl=${EXCLUDE_FABRICATED}" in blocks[family], (
            f"build_family {family} does not pass "
            f"exclude_fabricated_pnl=${{EXCLUDE_FABRICATED}} — the filter "
            f"exists in code but is not wired in the production builder "
            f"(the #8152 half-registration class)."
        )
