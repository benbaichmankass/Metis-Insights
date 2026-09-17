"""The backlog `status` vocabulary has ONE home and every surface agrees with it.

BL-20260912-THE-CANONICAL-DOCS-FIVE-TERMINAL-BACKLOG-DISPOSITIONS-ARE-NONE-OF-THE-SIX-THE-GUARD-ACCEPTS:
docs/CLAUDE-RULES-CANONICAL.md § "Backlog governance" rule 3 named five words as
the terminal dispositions and the intersection with the enum `claim-basis-guard`
enforces was EMPTY, so the document ranked FIRST in the instruction hierarchy
told a session to write a `status` CI refuses.

⚠️ THE CONTROLS GRADE THE AGREEMENT AND THE COVERAGE, not the words. A check
that cannot go red is the state this row describes one level up.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DOC = REPO / "docs" / "CLAUDE-RULES-CANONICAL.md"


def _coherence():
    spec = importlib.util.spec_from_file_location(
        "_cdc", REPO / "scripts" / "ci" / "check_canonical_doc_coherence.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_doc_mirror_equals_the_enforced_enum():
    """The agreement itself, over the real files."""
    from scripts.check_claim_basis import STATUS_ENUM

    c = _coherence()
    assert c._mirrored_status_enum() == set(STATUS_ENUM)
    assert c.check_status_enum_mirror() == []


def test_a_mirror_that_drifts_either_way_is_a_finding(tmp_path, monkeypatch):
    """BOTH directions, because they are different harms: a doc that lists a
    value CI rejects sends a session into a refusal; a doc that omits one
    understates the vocabulary."""
    c = _coherence()
    enforced = c._enforced_status_enum()
    assert enforced, "positive control: the enum must be readable at all"

    monkeypatch.setattr(c, "_mirrored_status_enum",
                        lambda: enforced | {"fixed"})
    extra = c.check_status_enum_mirror()
    assert extra and "does NOT accept" in extra[0], extra

    monkeypatch.setattr(c, "_mirrored_status_enum",
                        lambda: enforced - {"invalid"})
    missing = c.check_status_enum_mirror()
    assert missing and "omits" in missing[0], missing


def test_an_absent_marker_or_unreadable_enum_FAILS_rather_than_passing(
        tmp_path, monkeypatch):
    """`we could not look` must not render as agreement — that is how a guard
    stops looking while still printing PASS."""
    c = _coherence()
    monkeypatch.setattr(c, "_mirrored_status_enum", lambda: None)
    assert "mirror block is missing" in c.check_status_enum_mirror()[0]

    monkeypatch.setattr(c, "_enforced_status_enum", lambda: None)
    out = c.check_status_enum_mirror()
    assert out and "silently disabled" in out[0], out


def test_the_doc_no_longer_prescribes_a_status_the_guard_refuses():
    """The row's own failure, as a control: the five words must not appear as a
    prescribed `status` in rule 3's mirror."""
    from scripts.check_claim_basis import STATUS_ENUM

    mirrored = _coherence()._mirrored_status_enum()
    for word in ("fixed", "closed_answered", "closed_unfixable",
                 "promoted_to_roadmap", "snoozed"):
        assert word not in mirrored, f"{word} is not in STATUS_ENUM"
        assert word not in STATUS_ENUM  # positive control on the other side


def test_every_backlog_the_digest_reads_is_enum_guarded():
    """COVERAGE, which is the half a passing guard cannot show you.

    The guard scanned THREE backlogs while `work_digest` read FOUR, so the
    fourth was the one place a free-text status could land unremarked.
    """
    from scripts.check_claim_basis import BACKLOGS
    from scripts.ops.work_digest import SOURCES

    digest_backlogs = {s.path for s in SOURCES if "backlog" in s.path}
    assert digest_backlogs, "positive control: the digest must read some backlog"
    assert digest_backlogs <= set(BACKLOGS), (
        "a backlog the digest reads is NOT enum-guarded: "
        f"{sorted(digest_backlogs - set(BACKLOGS))}")


def test_terminal_is_derived_from_the_enum_not_listed_beside_it():
    """A hand-written second list goes stale silently; a derived one cannot."""
    from scripts.check_claim_basis import STATUS_ENUM
    from scripts.ops.work_digest import BACKLOG_NON_TERMINAL, BACKLOG_TERMINAL

    assert BACKLOG_TERMINAL == set(STATUS_ENUM) - BACKLOG_NON_TERMINAL
    assert BACKLOG_TERMINAL <= set(STATUS_ENUM), (
        "a terminal value the field cannot hold is an unreachable predicate")
    assert BACKLOG_NON_TERMINAL < set(STATUS_ENUM)
    assert BACKLOG_TERMINAL, "positive control: the terminal set is not empty"


def test_no_row_in_any_guarded_backlog_carries_an_off_enum_status():
    """The denominator the row asked for, asserted rather than remembered."""
    from scripts.check_claim_basis import BACKLOGS, STATUS_ENUM

    total, bad = 0, []
    for rel in BACKLOGS:
        doc = json.loads((REPO / rel).read_text(encoding="utf-8"))
        rows = doc if isinstance(doc, list) else (doc.get("items") or [])
        assert rows, f"positive control: {rel} must have rows to grade"
        total += len(rows)
        bad += [(rel, r.get("id"), r.get("status")) for r in rows
                if r.get("status") not in STATUS_ENUM]
    assert total > 1000, f"positive control: only {total} rows read"
    assert not bad, bad
