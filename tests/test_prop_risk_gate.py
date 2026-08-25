"""The prop ticket risk gate — the four states, and the incident it replays.

The motivating measurement (``/system-review`` 2026-08-25, report
``RPT-20260825-092500-since-last``): ``breakout_1`` sat $64.00 above its $4,700
static-drawdown floor and the bot emitted a ticket suggesting $75.00 of risk.
``test_the_2026_08_25_incident_*`` replay that exact arithmetic, so a
regression is a failing test rather than a rediscovery six weeks later.
"""
from __future__ import annotations

from src.prop import prop_risk_gate as G


# ── The four states, never collapsed ────────────────────────────────────────

def test_within_cushion_when_the_risk_fits():
    v = G.grade_ticket_risk(
        risk_usd=10.0, distance_to_dd_floor_usd=500.0,
        distance_to_daily_loss_usd=142.92, status_freshness="ok")
    assert v["state"] == G.WITHIN
    assert v["binding_limit"] == "daily_loss"   # the SMALLER of the two
    assert v["cushion_usd"] == 142.92
    assert v["overshoot_usd"] is None
    assert G.caveat_lines(v) == [], "a fitting ticket must not warn — every-ticket warnings are the desensitised-alarm P1"


def test_exceeds_cushion_names_the_binding_limit_and_the_overshoot():
    v = G.grade_ticket_risk(
        risk_usd=75.0, distance_to_dd_floor_usd=64.0,
        distance_to_daily_loss_usd=500.0, status_freshness="ok")
    assert v["state"] == G.EXCEEDS
    assert v["binding_limit"] == "dd_floor"
    assert v["cushion_usd"] == 64.0
    assert v["overshoot_usd"] == 11.0
    assert any("DO NOT PLACE" in ln for ln in G.caveat_lines(v))


def test_no_risk_declared_is_its_own_state():
    """A suppressed / shadow ticket carries no size. Nothing to grade — and
    emphatically not a pass."""
    v = G.grade_ticket_risk(risk_usd=None, distance_to_dd_floor_usd=64.0,
                            status_freshness="ok")
    assert v["state"] == G.NO_RISK
    assert G.caveat_lines(v) == []


# ── "We could not look" is never "it fits" ──────────────────────────────────

def test_a_stale_snapshot_is_cushion_unknown_not_within():
    """THE CASE THAT ACTUALLY FIRED. The distance is present and comfortable-
    looking; the snapshot behind it is 40.7 h old. Grading that `within` would
    assert a fact nobody measured."""
    v = G.grade_ticket_risk(
        risk_usd=10.0, distance_to_dd_floor_usd=64.0,
        distance_to_daily_loss_usd=None, status_freshness="stale")
    assert v["state"] == G.UNKNOWN
    assert v["cushion_usd"] is None
    assert v["limits_known"] == []
    assert any("CUSHION UNKNOWN" in ln for ln in G.caveat_lines(v))


def test_unchecked_freshness_is_unknown_because_nobody_is_checking():
    v = G.grade_ticket_risk(risk_usd=10.0, distance_to_dd_floor_usd=64.0,
                            status_freshness="unchecked")
    assert v["state"] == G.UNKNOWN


def test_absent_snapshot_is_unknown():
    v = G.grade_ticket_risk(risk_usd=10.0, status_freshness="absent")
    assert v["state"] == G.UNKNOWN


def test_fresh_but_both_distances_none_is_unknown_not_within():
    """Fresh snapshot, neither distance derivable. Still `we could not look`."""
    v = G.grade_ticket_risk(
        risk_usd=10.0, distance_to_dd_floor_usd=None,
        distance_to_daily_loss_usd=None, status_freshness="ok")
    assert v["state"] == G.UNKNOWN
    assert "neither" in v["reason"]


def test_a_partial_read_grades_on_what_is_known_and_says_which():
    """The measured account: the DD distance was known, the daily one was None
    (`day_pnl_state: realized_unreported`). Grade on the DD floor, and RECORD
    that the daily limit was never read — an unread limit is a different fact
    from a comfortable one."""
    v = G.grade_ticket_risk(
        risk_usd=75.0, distance_to_dd_floor_usd=64.0,
        distance_to_daily_loss_usd=None, status_freshness="ok")
    assert v["state"] == G.EXCEEDS
    assert v["limits_known"] == ["dd_floor"]
    assert "daily_loss" not in v["limits_known"]


# ── The incident, replayed on its real numbers ──────────────────────────────

def test_the_2026_08_25_incident_suggested_risk_exceeded_the_whole_cushion():
    """Ticket prop-manual-1a29db54154e: risk_usd 75.00 vs a $64.00 cushion."""
    v = G.grade_ticket_risk(
        risk_usd=75.00, distance_to_dd_floor_usd=64.0,
        distance_to_daily_loss_usd=None, status_freshness="ok")
    assert v["state"] == G.EXCEEDS, "the ticket that could have killed the account must not grade WITHIN"
    assert v["overshoot_usd"] == 11.0


def test_the_2026_08_25_incident_the_recompute_instruction_ALSO_overshoots():
    """The ticket told the executor to recompute at 1.5% of the live balance.
    1.5% x $4,764 = $71.46 — still more than the $64.00 cushion. Fixing the
    SUGGESTED size alone would not have saved the account."""
    recomputed = round(0.015 * 4764.0, 2)
    assert recomputed == 71.46
    v = G.grade_ticket_risk(
        risk_usd=recomputed, distance_to_dd_floor_usd=64.0,
        status_freshness="ok")
    assert v["state"] == G.EXCEEDS


def test_risking_exactly_the_cushion_is_a_breach_not_a_fit():
    """`>=`, not `>`: a full loss of exactly the cushion breaches the limit."""
    v = G.grade_ticket_risk(risk_usd=64.0, distance_to_dd_floor_usd=64.0,
                            status_freshness="ok")
    assert v["state"] == G.EXCEEDS


# ── Mode ────────────────────────────────────────────────────────────────────

def test_mode_defaults_to_annotate(monkeypatch):
    monkeypatch.delenv("PROP_TICKET_RISK_GATE_MODE", raising=False)
    assert G.mode() == "annotate"


def test_an_unparseable_mode_falls_back_to_the_default_not_to_off(monkeypatch):
    """A typo must not silently disarm the only thing comparing a ticket to the
    line that kills the account."""
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "anotate")
    assert G.mode() == "annotate"


def test_off_is_reachable_as_the_rollback(monkeypatch):
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "off")
    assert G.mode() == "off"


# ── The impure wrapper fails to UNKNOWN, never to WITHIN ────────────────────

def test_a_read_failure_grades_unknown_not_within(monkeypatch):
    import src.prop.prop_reconcile as pr

    def boom(*a, **k):
        raise RuntimeError("journal unreadable")

    monkeypatch.setattr(pr, "compute_rule_distance", boom)
    v = G.grade_account_ticket_risk("breakout_1", risk_usd=75.0)
    assert v["state"] == G.UNKNOWN, "a broken reader must never read as a comfortable account"


# ── The ticket itself ───────────────────────────────────────────────────────

def _ticket():
    from datetime import datetime, timezone
    from src.prop.breakout_ticket import BreakoutSignal, TicketConfig
    sig = BreakoutSignal(
        strategy="trend_donchian_sol_prop", symbol="SOLUSDT", direction="long",
        entry=100.39, sl=96.05607143, tp=110.32861, timeframe="4h",
        signal_time=datetime(2026, 8, 25, 0, 15, tzinfo=timezone.utc),
    )
    return sig, TicketConfig()


def test_off_mode_short_circuits_the_gate_entirely(monkeypatch):
    """`off` is the rollback path, so prove it BYPASSES rather than merely
    producing no caveat: plant a sentinel the gate would emit and require it
    absent. Asserting only that the real caveat strings are missing would pass
    even if the gate ran and happened to grade WITHIN."""
    from src.prop import prop_risk_gate
    from src.prop.breakout_ticket import build_ticket, render_ticket
    sig, cfg = _ticket()
    t = build_ticket(sig, cfg)
    monkeypatch.setattr(prop_risk_gate, "caveat_lines",
                        lambda v: ["  SENTINEL-GATE-RAN"])
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "off")
    assert "SENTINEL-GATE-RAN" not in render_ticket(t, account_id="breakout_1")
    # ...and the same plant IS visible at the default, so the probe can fail.
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "annotate")
    assert "SENTINEL-GATE-RAN" in render_ticket(t, account_id="breakout_1")


def test_the_caveat_appears_ABOVE_the_size_it_contradicts(monkeypatch):
    """A "do not place" warning below the size is a warning the executor
    scrolls past on a phone."""
    from src.prop import prop_risk_gate
    from src.prop.breakout_ticket import build_ticket, render_ticket
    sig, cfg = _ticket()
    t = build_ticket(sig, cfg)
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "annotate")
    monkeypatch.setattr(
        prop_risk_gate, "grade_account_ticket_risk",
        lambda *a, **k: prop_risk_gate.grade_ticket_risk(
            risk_usd=75.0, distance_to_dd_floor_usd=64.0, status_freshness="ok"),
    )
    body = render_ticket(t, account_id="breakout_1")
    assert "DO NOT PLACE AT THE SUGGESTED SIZE" in body
    assert body.index("DO NOT PLACE") < body.index("Size     :"), \
        "the caveat must precede the size"


def test_a_gate_failure_still_emits_the_ticket(monkeypatch):
    """Never lose the ticket over its own caveat."""
    from src.prop import prop_risk_gate
    from src.prop.breakout_ticket import build_ticket, render_ticket
    sig, cfg = _ticket()
    t = build_ticket(sig, cfg)
    monkeypatch.setenv("PROP_TICKET_RISK_GATE_MODE", "annotate")

    def boom(*a, **k):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(prop_risk_gate, "grade_account_ticket_risk", boom)
    body = render_ticket(t, account_id="breakout_1")
    assert "BREAKOUT TRADE SETUP" in body
    assert "Stop     :" in body


# ── The last-known cushion, carried through a STALE verdict ─────────────────
#
# Added after the live payload was measured while building this: at the moment
# of the real incident the snapshot was ALREADY 32 h stale, so a gate that
# stops at a bare "unknown" would have said nothing about $75 vs $64 on the
# night it mattered.

def test_a_stale_verdict_carries_the_last_known_cushion():
    v = G.grade_ticket_risk(
        risk_usd=75.0, distance_to_dd_floor_usd=64.0,
        distance_to_daily_loss_usd=None, status_freshness="stale")
    assert v["state"] == G.UNKNOWN
    assert v["cushion_usd"] is None, "a stale figure must NEVER occupy cushion_usd"
    assert v["last_known_cushion_usd"] == 64.0
    assert v["last_known_limit"] == "dd_floor"
    assert v["last_known_exceeded"] is True


def test_the_stale_caveat_quotes_the_last_known_comparison():
    v = G.grade_ticket_risk(
        risk_usd=75.0, distance_to_dd_floor_usd=64.0, status_freshness="stale")
    body = "\n".join(G.caveat_lines(v))
    assert "CUSHION UNKNOWN" in body
    assert "AS OF THE LAST REPORT this risk did NOT fit" in body
    assert "$75.00" in body and "$64.00" in body
    assert "STALE, not" in body, "it must not read as a live comparison"


def test_a_stale_verdict_that_FITS_the_last_known_cushion_does_not_cry_wolf():
    v = G.grade_ticket_risk(
        risk_usd=5.0, distance_to_dd_floor_usd=64.0, status_freshness="stale")
    assert v["state"] == G.UNKNOWN
    assert v["last_known_exceeded"] is False
    body = "\n".join(G.caveat_lines(v))
    assert "CUSHION UNKNOWN" in body
    assert "did NOT fit" not in body


def test_absent_snapshot_has_no_last_known_figure_to_quote():
    """`absent` is not `stale` — there has never been a report, so there is no
    last-known cushion and inventing one would be a fabrication."""
    v = G.grade_ticket_risk(risk_usd=75.0, status_freshness="absent")
    assert v["state"] == G.UNKNOWN
    assert v["last_known_cushion_usd"] is None
    assert v["last_known_exceeded"] is None
