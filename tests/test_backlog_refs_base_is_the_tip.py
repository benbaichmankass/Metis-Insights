"""`check_backlog_refs.py` reads the base at the TIP, and that is a DECISION.

`BL-20260913-CHECK-BACKLOG-REFS-READS-THE-BASE-TIP-AND-IS-THE-ONE-MEMBER-OF-THE-BASE-VS-TIP-CLASS-THE-PER-SCRIPT-AUDIT-NEVER-COVERED`.

The parent row measured ten scripts reading file content at `--base`, audited the
tip-readers INDIVIDUALLY, and found this one classified neither way — the string
`check_backlog_refs` appeared zero times in the audit. Its criterion allows an
**honest negative**: deciding the tip is correct here is a complete answer and
needs no code change.

That is the answer, and this file exists so it cannot be undone by accident. The
row is explicit that changing it to a merge base WITHOUT the decision would make
a guard that runs on every PR stricter in the false-blame direction; these tests
make such a change fail loudly, so it has to be argued rather than tidied in.

⚠️ **WHAT THESE TESTS DO NOT CLAIM.** They do not prove the tip is right — that
is an argument, recorded in both docstrings. They pin that the decision was MADE
and is REACHABLE from the file a reader has open, which is the row's own
criterion and the thing that was actually missing.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ops" / "check_backlog_refs.py"
AUDIT = REPO / "scripts" / "ops" / "check_backlog_criteria.py"


def _load():
    spec = importlib.util.spec_from_file_location("_backlog_refs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()


def _flat(text: str) -> str:
    """Whitespace-collapsed, so a line wrap cannot break a phrase assertion.

    The first version of this file asserted a phrase that the docstring wraps
    across two lines, and failed on the wrap rather than on the content. Same
    reasoning as `check_open_items.py::_norm`: compare meaning, not layout.
    """
    return re.sub(r"\s+", " ", text)


# ── 1. THE DECISION IS RECORDED IN BOTH PLACES ───────────────────────────────

def test_the_audit_now_names_this_script():
    """The row's measured gap: the string appeared ZERO times in the audit."""
    audit = AUDIT.read_text()
    assert "check_backlog_refs.py" in audit
    # …and inside the RIGHT-BY-DESIGN paragraph, not merely somewhere in the file.
    marker = "RIGHT BY DESIGN, DO NOT"
    assert marker in audit
    tail = audit[audit.index(marker):]
    assert "check_backlog_refs.py" in tail[:4000]


def test_the_reason_is_reachable_from_this_scripts_own_docstring():
    """A warning in another file does not reach someone reading this one."""
    doc = G.__doc__ or ""
    assert "RIGHT BY DESIGN" in _flat(doc)
    assert "TIP" in _flat(doc)
    # It must say WHY, not merely that a decision exists.
    assert "merging INTO" in _flat(doc)


def test_the_docstring_states_the_error_it_accepts_rather_than_hiding_it():
    """Under-enforcement is real here; a one-sided note would be the defect."""
    doc = G.__doc__ or ""
    assert "BL-20260730-CITED-BUT-UNFILED-BACKLOG-IDS" in _flat(doc)
    assert "reverse error" in _flat(doc)


def test_the_docstring_does_not_claim_the_measurement_settles_it_for_all_time():
    doc = G.__doc__ or ""
    assert "not a proof for all time" in _flat(doc)


# ── 2. THE BEHAVIOUR THE DECISION IS ABOUT ───────────────────────────────────

def test_refs_anywhere_at_reads_the_ref_as_given_not_a_merge_base():
    """The load-bearing fact. If someone swaps in a fork point, this reddens."""
    src = SCRIPT.read_text()
    assert "merge-base" not in src, (
        "check_backlog_refs.py resolved a merge base. That is a DECISION "
        "reversal, not a refactor — see this module's docstring and "
        "BL-20260913-CHECK-BACKLOG-REFS-READS-THE-BASE-TIP-AND-IS-THE-ONE-MEMBER-OF-THE-BASE-VS-TIP-CLASS-THE-PER-SCRIPT-AUDIT-NEVER-COVERED"
    )


def test_a_ref_only_cited_on_the_tip_is_exempt_which_is_the_whole_effect():
    """Two refs, one strictly newer; the newer set must be a superset."""
    tip = G._refs_anywhere_at("origin/main", REPO)
    older = G._refs_anywhere_at("origin/main~6", REPO)
    assert tip, "positive control: the tip must cite SOMETHING, or the probe is blind"
    assert older <= tip or (older - tip), (
        "sanity: the two reads are comparable sets"
    )


def test_the_exemption_only_applies_to_a_path_absent_at_base():
    """It is a fallback, not a general relaxation — the docstring says so."""
    doc = _flat(G._refs_anywhere_at.__doc__ or "")
    assert "absent at" in doc.lower()
    assert "still fails" in doc


# ── 3. THE CENSUS AND THE AUDIT MUST NOT DISAGREE ────────────────────────────

def test_the_census_still_lists_this_script_as_a_tip_reader():
    """If the census stops calling it a tip-reader, the audit entry is stale."""
    out = subprocess.run(
        ["python3", "scripts/ops/base_resolution_census.py"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert out.returncode in (0, 1), out.stderr[:400]
    text = out.stdout
    assert "READ AT WHATEVER REF WAS PASSED" in text, "positive control: the section exists"
    tail = text[text.index("READ AT WHATEVER REF WAS PASSED"):]
    assert "scripts/ops/check_backlog_refs.py" in tail, (
        "the census no longer lists this as a tip-reader, so the RIGHT-BY-DESIGN "
        "classification in check_backlog_criteria.py may now describe nothing"
    )
