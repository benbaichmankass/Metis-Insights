"""The Bybit over-cover condition must reach the OPERATOR, not just the journal.

Sibling of `tests/test_stop_over_cover_alert.py`. The Bybit sweep has counted
`over_covered` since 2026-07-30 and reported it with a `logger.error`, which
reaches the systemd journal and nothing else — the same defect fixed for IB one
day earlier and explicitly left unchecked on Bybit.

The load-bearing test here is `test_it_is_not_the_ib_page_reworded`: the IB
page's hazard argument (OCA groups, a naked SHORT, `cancel-ib-order`, Error
10147) is FALSE on Bybit, where every resting SL leg is reduceOnly. Reusing it
would be a semantic substitution — a confident label over a quantity the code
did not compute.
"""
import pytest

import src.runtime.order_monitor as om


@pytest.fixture()
def latched(tmp_path, monkeypatch):
    monkeypatch.setattr(om, "_alert_state_path",
                        lambda kind: tmp_path / f"{kind}_alert_state.json")
    return tmp_path / "bybit_over_cover_alert_state.json"


@pytest.fixture()
def pages(monkeypatch):
    """Capture what reaches `outcomes.report`, the operator-facing path."""
    sent = []
    import src.runtime.outcomes as outcomes
    monkeypatch.setattr(outcomes, "report", lambda *a, **k: sent.append((a, k)))
    return sent


def _emit(symbol="ETHUSDT", leg_count=7, covered=9.32, size=5.59):
    return om._emit_bybit_over_cover_alert(
        account_id="bybit_1", symbol=symbol, size=size,
        covered=covered, leg_count=leg_count)


def test_it_reaches_the_operator_path(latched, pages):
    """A `logger.error` is not an operator surface."""
    assert _emit() is True
    assert len(pages) == 1, "the page must go through outcomes.report"
    args, kwargs = pages[0]
    from src.runtime.outcomes import Level
    # ERROR, not CRITICAL — both Telegram, and CRITICAL is reserved for a
    # position that is UNPROTECTED or REVERSED. This one is over-protected and
    # reduce-only; spending CRITICAL on it trains the channel away.
    assert kwargs["level"] is Level.ERROR
    assert args[0] == "bybit_over_cover"


def test_it_reaches_telegram_at_this_level():
    """ERROR is not a quieter delivery than CRITICAL — it is a quieter LABEL."""
    from src.runtime.outcomes import Level, _TELEGRAM_LEVELS
    assert Level.ERROR in _TELEGRAM_LEVELS


def test_it_is_not_the_ib_page_reworded(latched, pages):
    """The IB hazard argument is FALSE here and must not be ASSERTED here.

    ⚠️ This deliberately does NOT ban the token "OCA". A first cut did, and it
    failed on the page's own sentence *"this is NOT a naked-reverse hazard (that
    is the IB/OCA shape)"* — a contrast that is the single most useful line for
    an operator who has just read three `ib_stop_over_cover` pages this week.
    A token ban would have forced deleting it. What must not appear is the IB
    hazard stated as APPLYING, and the IB REMEDY, which is simply wrong here.
    """
    assert _emit() is True
    reason = pages[0][1]["reason"]
    low = reason.lower()
    # The IB remedy is wrong on this venue and must never be routed to.
    for ib_only in ("cancel-ib-order", "10147", "clientid"):
        assert ib_only not in low, f"IB-only remedy {ib_only!r} leaked into the Bybit page"
    # The naked-reverse hazard may only appear NEGATED.
    assert "naked" not in low or "not a naked-reverse hazard" in low
    # And it states the hazard it DOES have, measured rather than assumed.
    assert "reduceonly" in low.replace("-", "")
    assert "110061" in reason          # the cap refusal code
    assert "silently" in low           # what the cap costs
    assert "closed trade" in low       # the second, non-cap hazard


def test_the_page_states_the_measurement_and_the_cap_headroom(latched, pages):
    assert _emit(leg_count=7, covered=9.32, size=5.59) is True
    args, kwargs = pages[0]
    assert kwargs["size"] == 5.59 and kwargs["covered_qty"] == 9.32
    assert kwargs["sl_leg_count"] == 7
    assert kwargs["leg_cap"] == 20
    assert kwargs["leg_cap_headroom"] == 13      # the actionable urgency
    assert round(kwargs["over_cover_pct"]) == 167
    assert "167%" in kwargs["reason"]


def test_the_remedy_carries_its_own_caveat(latched, pages):
    """Advising a tool that was catastrophic until 2026-08-26 without saying so
    is how the operator repeats the failure this page exists beside."""
    assert _emit() is True
    reason = pages[0][1]["reason"]
    assert "cancel-stale-tpsl-legs" in reason
    assert "DRY-RUN" in reason
    assert "journal" in reason.lower()


def test_cooldown_is_durable_and_shared_not_copied(latched, pages):
    """It must use the SHARED `_cooldown_admits`; a copied latch is how the
    per-PROCESS defect that put 202 CRITICALs on the channel would return."""
    calls = []
    real = om._cooldown_admits

    def _spy(kind, key, cooldown_s, **kw):
        calls.append((kind, key, kw.get("severity")))
        return real(kind, key, cooldown_s, **kw)

    om._cooldown_admits, saved = _spy, om._cooldown_admits
    try:
        assert _emit() is True
        assert _emit() is False            # suppressed inside the window
    finally:
        om._cooldown_admits = saved
    assert calls[0][0] == "bybit_over_cover"
    assert calls[0][1] == "bybit_1|ETHUSDT"
    assert calls[0][2] == 7                # severity IS the leg count
    assert len(pages) == 1


def test_a_worsening_leg_count_pages_inside_the_cooldown(latched, pages):
    assert _emit(leg_count=7) is True
    assert _emit(leg_count=7) is False     # unchanged -> still suppressed
    assert _emit(leg_count=9) is True      # WORSENING breaks the window
    assert len(pages) == 2


def test_an_improving_count_does_not_page(latched, pages):
    assert _emit(leg_count=9) is True
    assert _emit(leg_count=7) is False
    assert len(pages) == 1


def test_a_different_symbol_is_not_suppressed(latched, pages):
    assert _emit(symbol="ETHUSDT") is True
    assert _emit(symbol="XRPUSDT") is True
    assert len(pages) == 2


def test_a_failing_report_never_aborts_the_sweep(latched, monkeypatch):
    import src.runtime.outcomes as outcomes

    def boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(outcomes, "report", boom)
    assert _emit() is True                 # it alerted (cooldown consumed), no raise
