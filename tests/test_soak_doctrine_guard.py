"""Positive controls for `scripts/check_soak_doctrine.py` CHECK D.

WHY THIS FILE EXISTS. Check D was written because checks A-C reached exactly
three files -- CLAUDE-RULES-CANONICAL.md and two SKILL.md files -- and reached
NONE of the code that decides anything. The guard was green for the entire
eight days (2026-09-01..09-08) that the M7 strategy-review gate contradicted
the canonical rule it exists to enforce, grading 52 of 52 legs `hold` off a
count of live in-window closes.

So a GREEN RUN PROVES NOTHING HERE, and that is the whole point of this file:
green is precisely the state the guard held throughout the contradiction. The
evidence that check D reaches the surface is it FAILING on a planted violation
and passing once removed. That observation was made by hand on 2026-09-09
(MI-215); these tests are what keep it true, because a hand observation dies
with the session that made it.

⚠️ Nothing here mutates a real repository file. The planted violations are
synthetic sources passed to the detector, plus one ledger round-trip in tmp.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_soak_doctrine", ROOT / "scripts" / "check_soak_doctrine.py"
)
guard = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(guard)


# The shape of the real gate: a Decision object whose `.action` is assigned.
_CLEAN = '''
def decide(headline, cells, diag):
    decision = Decision(action="hold")
    win = headline.win_rate or 0.0
    if win <= 0.25 and headline.expectancy < 0:
        decision.action = "kill"
    return decision
'''


def test_clean_gate_has_no_findings():
    """A verdict gated on EDGE statistics alone is compliant.

    The negative control. Without it, a detector that flagged everything would
    look identical to one that works.
    """
    assert guard.find_accrual_gated_verdicts(_CLEAN, ("decide",)) == []


def test_planted_close_count_gate_is_caught():
    """The canonical violation: a verdict withheld for want of live closes."""
    planted = _CLEAN.replace(
        "    if win <= 0.25",
        "    if headline.n_closed < 20:\n"
        "        decision.action = \"hold\"\n"
        "    if win <= 0.25",
    )
    hits = guard.find_accrual_gated_verdicts(planted, ("decide",))
    assert len(hits) == 1
    assert "n_closed" in hits[0][2]


def test_planted_calendar_gate_is_caught():
    """`N days at stage` -- the construct clause 3 names verbatim."""
    planted = _CLEAN.replace(
        "    return decision",
        '    if shadow_soak_days < 14:\n'
        '        decision.action = "hold"\n'
        "    return decision",
    )
    hits = guard.find_accrual_gated_verdicts(planted, ("decide",))
    assert len(hits) == 1
    assert "shadow_soak_days" in hits[0][2]


def test_renaming_the_variable_does_not_evade_the_check():
    """THE LOAD-BEARING CASE: detection is by alias closure, not by keyword.

    A guard that matched the literal name `n_closed` would be defeated by one
    rename -- and a rename is exactly what a later refactor does without any
    intent to evade. The real gate already does this (`n = headline.n_closed`),
    so a keyword matcher would have missed six of its seven branches.
    """
    planted = _CLEAN.replace(
        "    win = headline.win_rate or 0.0",
        "    win = headline.win_rate or 0.0\n"
        "    trades_banked = headline.n_closed\n"
        "    if trades_banked < 40:\n"
        '        decision.action = "hold"',
    )
    hits = guard.find_accrual_gated_verdicts(planted, ("decide",))
    assert len(hits) == 1, "alias of n_closed must still be caught"
    assert "trades_banked" in hits[0][2]


def test_transitive_alias_is_caught():
    """Two hops, because one hop is a refactor away from two."""
    planted = _CLEAN.replace(
        "    win = headline.win_rate or 0.0",
        "    win = headline.win_rate or 0.0\n"
        "    a = headline.n_closed\n"
        "    b = a + 1\n"
        "    if b < 40:\n"
        '        decision.action = "hold"',
    )
    hits = guard.find_accrual_gated_verdicts(planted, ("decide",))
    assert len(hits) == 1
    assert "b" in hits[0][2]


def test_accrual_read_without_a_verdict_is_not_a_finding():
    """Reporting a close count is fine; DECIDING on it is not.

    The rule bans accrual as the BASIS OF A VERDICT, never as a published
    diagnostic -- the packet is required to report `n_closed`. A detector that
    flagged the report too would be unsatisfiable, and an unsatisfiable guard
    gets deleted.
    """
    planted = _CLEAN.replace(
        "    return decision",
        "    if headline.n_closed < 20:\n"
        '        decision.reasons.append("below floor")\n'
        "    return decision",
    )
    assert guard.find_accrual_gated_verdicts(planted, ("decide",)) == []


def test_unregistered_function_is_not_scanned():
    """Scope is explicit: only functions named in GATE_SURFACES are gates."""
    planted = _CLEAN.replace("def decide(", "def summarise(")
    assert guard.find_accrual_gated_verdicts(planted, ("decide",)) == []


# ---------------------------------------------------------------------------
# The ledger is a RATCHET, and it is VERIFIED.
# ---------------------------------------------------------------------------

def test_registered_surfaces_exist_and_are_scanned():
    """A surface that vanished must be de-registered, never skipped silently."""
    assert guard.GATE_SURFACES, "check D with no surfaces reaches nothing"
    for rel in guard.GATE_SURFACES:
        assert (ROOT / rel).exists(), f"{rel} registered but missing"


def test_live_surface_count_matches_the_ledger():
    """The tree and the ledger agree today -- so a drift in EITHER fails CI.

    This is what makes the count a ratchet rather than a comment: adding a
    branch fails as a new violation, and REMOVING one fails as a stale ledger,
    so the number can only be walked down deliberately.
    """
    ledger = json.loads(
        (ROOT / "docs" / "claude" / "soak-doctrine-exceptions.json").read_text()
    )["surfaces"]
    for rel, funcs in guard.GATE_SURFACES.items():
        found = guard.find_accrual_gated_verdicts(
            (ROOT / rel).read_text(encoding="utf-8"), funcs
        )
        declared = ledger.get(rel, {}).get("known_accrual_gated_branches", 0)
        assert len(found) == declared, (
            f"{rel}: {len(found)} accrual-gated verdict branches on disk vs "
            f"{declared} declared. If you REPAIRED one, lower the ledger. If "
            f"you ADDED one, it contradicts binding canon -- see "
            f"docs/CLAUDE-RULES-CANONICAL.md § 'Promotion evidence'."
        )


def test_ledger_backlog_row_is_verified_not_merely_present():
    """An entry naming a row that does not exist must FAIL.

    Presence-only overrides are how a real finding gets silenced: the cheapest
    way past the guard must not be to invent a row id.
    """
    ledger = json.loads(
        (ROOT / "docs" / "claude" / "soak-doctrine-exceptions.json").read_text()
    )["surfaces"]
    known_ids = guard._load_backlog_ids()
    assert known_ids, "no backlog rows loaded -- the verification is inert"
    for rel, entry in ledger.items():
        if entry.get("known_accrual_gated_branches"):
            row = entry.get("backlog_row")
            assert row in known_ids, (
                f"{rel} declares an exception against {row!r}, which is not a "
                "row in any review backlog."
            )


def test_guard_refuses_on_an_unreadable_ledger(tmp_path, monkeypatch):
    """`we could not look` is never `nothing is declared`."""
    bad = tmp_path / "soak-doctrine-exceptions.json"
    bad.write_text("{ not json")
    monkeypatch.setattr(guard, "EXCEPTIONS", bad)
    errors, _ = guard.check_gate_surfaces()
    assert errors and "unreadable" in errors[0]


def test_guard_passes_on_the_current_tree():
    """The whole guard, end to end.

    ⚠️ ON ITS OWN THIS ASSERTS NOTHING about check D's reach -- green is the
    state that held through the eight-day contradiction. It is meaningful only
    beside the planted-violation tests above.
    """
    assert guard.main() == 0
