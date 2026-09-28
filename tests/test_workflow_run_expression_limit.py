"""A workflow step whose ``run:`` script contains ANY ``${{ }}`` expression
is evaluated as one expression template, and GitHub caps a template at 21,000
characters. Over the cap, the WHOLE workflow file is invalid: every trigger
fails at parse time and no job runs (2026-09-28: two expressions added to
system-actions.yml's ~32k-char "Execute action wrapper on VM" script took
every system action down). Long scripts must take such values through the
step's ``env:``."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.y*ml"))
LIMIT = 21_000


def _steps(doc):
    for jn, job in ((doc or {}).get("jobs") or {}).items():
        for i, st in enumerate((job or {}).get("steps") or []):
            if isinstance(st, dict) and isinstance(st.get("run"), str):
                yield jn, i, st


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_no_long_run_script_carries_an_expression(wf):
    doc = yaml.safe_load(wf.read_text())
    bad = [f"{jn}[{i}] {st.get('name')!r}: {len(st['run'])} chars with ${{{{ }}}}"
           for jn, i, st in _steps(doc) if "${{" in st["run"] and len(st["run"]) > LIMIT]
    assert not bad, "move these expressions into the step's env: " + "; ".join(bad)
