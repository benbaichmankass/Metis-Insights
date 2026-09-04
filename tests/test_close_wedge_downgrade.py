"""The unclearable-close-wedge downgrade: what buys silence, and what cannot.

CONTEXT (MI-34, operator decision 2026-09-02). ``alpaca_paper`` GLD has failed
to close since 2026-08-27 because OCO parent
``2e843e04-5487-470c-a702-70e796fbd05e`` sits at ``pending_cancel`` with
``canceled_at`` null. No bot-side lever clears it — both close paths and
Alpaca's own ``cancel_orders=true`` liquidation fail with "insufficient qty
available". Asked what the alarm should do meanwhile, the operator chose:
downgrade it out of the paging channel and carry it in the rolled-up digest.

THESE TESTS EXIST TO PIN THE NARROWNESS OF THAT DOWNGRADE. The dangerous
outcome is not that it fails to fire — it is that it fires for something it was
never meant to cover, or that a downgraded item goes quiet in both channels. So
the assertions are mostly NEGATIVE: what must STILL page.

Every state assertion carries a control that distinguishes it from its
neighbour, because the failure being guarded against is *"we did not look"*
reading as *"we looked and it cannot be fixed"* — and a happy-path test cannot
see that.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from src.runtime import close_wedge_standing as cw
from src.runtime.close_wedge_standing import Observation
from src.units.accounts.alpaca_client import (
    SHARE_HOLD_NOT_CLASSIFIED,
    SHARE_HOLD_STATES,
    classify_share_hold,
    format_share_hold_marker,
    parse_share_hold,
)

T0 = dt.datetime(2026, 9, 2, 6, 0, tzinfo=dt.timezone.utc)

#: The live wedge, verbatim — the shape `_open_orders_for_symbol` returns.
GLD_RESIDUAL = [{
    "id": "2e843e04-5487-470c-a702-70e796fbd05e", "status": "pending_cancel",
}]


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    return tmp_path / "close_wedge_standing.json"


def _wedge_obs(detail: str = "") -> Observation:
    state, det = classify_share_hold(GLD_RESIDUAL)
    return Observation("alpaca_paper", "GLD", "sell", state, detail or det)


# --------------------------------------------------------------------------
# The marker contract: producer and consumer share one owner.
# --------------------------------------------------------------------------

def test_marker_roundtrips_every_declared_state():
    for state in SHARE_HOLD_STATES:
        msg = f"insufficient qty available {format_share_hold_marker(state, 'detail here')}"
        assert parse_share_hold(msg) == state


def test_no_marker_reads_as_not_classified_never_as_a_state():
    """The control on the one above: absence must not resolve to any of the four."""
    for text in ("insufficient qty available for order", "", None, "share_hold"):
        got = parse_share_hold(text)
        assert got == SHARE_HOLD_NOT_CLASSIFIED
        assert got not in SHARE_HOLD_STATES


def test_unknown_marker_token_falls_back_to_the_ALARMING_reading():
    """A state this build has never heard of is 'we did not look', not one of the four.

    The failure direction matters: resolving an unknown token to
    `broker_cancel_wedged` would let a future writer silence the pager by
    accident.
    """
    assert parse_share_hold("[share_hold=some_future_state: x]") == SHARE_HOLD_NOT_CLASSIFIED


# --------------------------------------------------------------------------
# What must STILL page. These are the point of the change.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("share_hold", [
    "residual_unreadable",     # we could not look at the broker
    "no_residual_orders",      # held, cause unmodelled
    "orders_still_resting",    # a retry may well work
    SHARE_HOLD_NOT_CLASSIFIED,  # nobody classified it at all
    "",                        # no reading whatsoever
])
def test_every_reading_except_the_evidenced_one_still_pages(share_hold, ledger):
    d = cw.observe(Observation("a", "GLD", "sell", share_hold, "d"), now=T0, path=ledger)
    assert d.should_page is True
    assert d.transition == cw.NOT_A_WEDGE
    # And it writes NOTHING: a non-wedge must not enter the standing ledger.
    assert not ledger.exists()


def test_repetition_alone_never_buys_silence(ledger):
    """The load-bearing negative. A close failing forever for an UNKNOWN reason
    is exactly what must keep paging — the downgrade keys on evidence, and there
    is no retry count anywhere in the module."""
    obs = Observation("a", "GLD", "sell", "orders_still_resting", "x is new")
    for i in range(200):
        d = cw.observe(obs, now=T0 + dt.timedelta(minutes=i), path=ledger)
        assert d.should_page is True, f"went quiet on repetition at attempt {i}"


def test_module_contains_no_retry_count_policy():
    """A structural control on the test above: the policy must not be expressible
    as 'it failed N times'. If a failure counter ever appears in this module's
    CODE, this fails and the reviewer has to justify it.

    Docstrings and comments are stripped first, deliberately — the module talks
    about streaks and consecutive failures at length in order to explain why it
    does not use them, and a raw text search would fire on its own reasoning.
    """
    import ast

    tree = ast.parse(Path(cw.__file__).read_text(encoding="utf-8"))
    names = {
        n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name)
    } | {
        n.attr.lower() for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    } | {
        n.name.lower() for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.ClassDef))
    }
    forbidden = {n for n in names
                 if "streak" in n or "consecutive" in n or "retry_count" in n}
    assert not forbidden, f"a retry-count policy leaked into the module: {forbidden}"


# --------------------------------------------------------------------------
# The downgrade itself, and its floor.
# --------------------------------------------------------------------------

def test_first_wedge_pages_then_stands_down(ledger):
    d1 = cw.observe(_wedge_obs(), now=T0, path=ledger)
    assert (d1.transition, d1.should_page) == ("newly_wedged", True)
    d2 = cw.observe(_wedge_obs(), now=T0 + dt.timedelta(minutes=1), path=ledger)
    assert (d2.transition, d2.should_page) == ("still_standing", False)


def test_a_standing_wedge_is_floored_not_silenced(ledger, monkeypatch):
    """Silence must never be reachable while the wedge stands — the whole risk
    the operator named when choosing the digest, a channel with no track record."""
    cw.observe(_wedge_obs(), now=T0, path=ledger)
    # Quiet across a full day of ticks...
    for h in (1, 6, 12, 23):
        assert cw.observe(_wedge_obs(), now=T0 + dt.timedelta(hours=h),
                          path=ledger).should_page is False
    # ...then it pages again, on its own, with no state change at all.
    assert cw.observe(_wedge_obs(), now=T0 + dt.timedelta(hours=24, minutes=1),
                      path=ledger).should_page is True


def test_the_floor_is_tunable_and_an_unparseable_knob_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("CLOSE_WEDGE_REPAGE_HOURS", "3")
    assert cw.repage_hours() == 3.0
    monkeypatch.setenv("CLOSE_WEDGE_REPAGE_HOURS", "not-a-number")
    assert cw.repage_hours() == cw._DEFAULT_REPAGE_HOURS  # never 0 → never "page always"


def test_suppression_requires_the_carry_to_actually_work(ledger, monkeypatch):
    """If the ledger cannot be written the item is carried NOWHERE, so it pages.

    This is the difference between a downgrade and a deletion.
    """
    cw.observe(_wedge_obs(), now=T0, path=ledger)
    monkeypatch.setattr(cw, "_save", lambda *a, **k: False)
    d = cw.observe(_wedge_obs(), now=T0 + dt.timedelta(minutes=1), path=ledger)
    assert d.should_page is True
    assert "could not be written" in d.reason


def test_an_unreadable_ledger_pages(ledger):
    """We cannot tell a new wedge from a carried one, so we cannot claim the
    downgrade's precondition."""
    ledger.write_text("{ not json", encoding="utf-8")
    d = cw.observe(_wedge_obs(), now=T0, path=ledger)
    assert d.should_page is True
    assert "UNREADABLE" in d.reason


# --------------------------------------------------------------------------
# State CHANGES must page again.
# --------------------------------------------------------------------------

def test_a_different_wedged_order_pages_and_does_not_inherit_the_suppression(ledger):
    cw.observe(_wedge_obs(), now=T0, path=ledger)
    cw.observe(_wedge_obs(), now=T0 + dt.timedelta(minutes=1), path=ledger)  # suppressed
    other = classify_share_hold([{"id": "ffffffff-0000-0000-0000-000000000000",
                                  "status": "pending_replace"}])
    d = cw.observe(Observation("alpaca_paper", "GLD", "sell", other[0], other[1]),
                   now=T0 + dt.timedelta(minutes=2), path=ledger)
    assert (d.transition, d.should_page) == ("evidence_changed", True)
    # A second, unexamined fault must start from a clean budget.
    assert d.entry["pages_suppressed"] == 0
    assert d.entry["first_seen"] == (T0 + dt.timedelta(minutes=2)).isoformat()


def test_confirmed_close_resolves_WITH_attribution_and_pages(ledger):
    cw.observe(_wedge_obs(), now=T0, path=ledger)
    d = cw.resolve_confirmed("alpaca_paper", "GLD", "sell",
                             attribution="monitor saw a confirmed close",
                             now=T0 + dt.timedelta(hours=1), path=ledger)
    assert d is not None
    assert (d.transition, d.should_page) == ("cleared_confirmed", True)
    assert d.entry["attribution"] == "monitor saw a confirmed close"
    assert cw.load_standing(ledger)["count"] == 0


def test_resolving_a_key_that_was_never_wedged_is_not_an_event(ledger):
    """The control on the above: every healthy close in the fleet runs through
    this path, and it must not manufacture a page."""
    assert cw.resolve_confirmed("a", "SPY", "buy", now=T0, path=ledger) is None


def test_a_position_that_just_disappears_is_recorded_as_UNATTRIBUTED(ledger):
    """Never as a clean clear. CLAUDE.md's PROTECTION_REASSERT_MODE row is the
    precedent: crediting an unattributed resolution banks a repair nobody made."""
    cw.observe(_wedge_obs(), now=T0, path=ledger)
    cw.sweep_vanished(now=T0, path=ledger)                       # arms the sweep clock
    cw.sweep_vanished(now=T0 + dt.timedelta(hours=40), path=ledger)  # keeps it armed
    out = cw.sweep_vanished(now=T0 + dt.timedelta(hours=80), path=ledger)
    assert [d.transition for d in out] == ["vanished_unattributed"]
    assert out[0].should_page is True
    assert "NOT ESTABLISHED" in out[0].entry["attribution"]
    assert out[0].entry["resolution"] != "cleared_confirmed"


def test_a_blind_gap_in_OUR_observation_is_not_a_disappearance(ledger):
    """If the trader was down longer than the window, everything looks vanished
    at once. A restart must not page a flood of false disappearances — 'we were
    not looking' is not 'it went away'."""
    cw.observe(_wedge_obs(), now=T0, path=ledger)
    cw.sweep_vanished(now=T0, path=ledger)
    # 200h later: the wedge is stale AND so is our last sweep. Retire nothing.
    assert cw.sweep_vanished(now=T0 + dt.timedelta(hours=200), path=ledger) == []
    assert cw.load_standing(ledger)["count"] == 1


def test_a_weekend_of_venue_closure_does_not_trip_the_sweep(ledger):
    """US equities close Friday and reopen Monday — ~65h. GLD is a US equity ETF,
    and a market-session DEFER clears the close-failure streak, so observations
    genuinely stop. The window must clear that."""
    assert cw.vanish_after_hours() > 65.0


# --------------------------------------------------------------------------
# Only ONE transition is quiet, and that must stay structurally true.
# --------------------------------------------------------------------------

def test_exactly_one_transition_is_quiet():
    assert set(cw.TRANSITIONS) - cw.LOUD_TRANSITIONS == {"still_standing"}


def test_a_transition_added_later_is_loud_by_default(monkeypatch):
    """LOUD_TRANSITIONS is the COMPLEMENT of the quiet state, not a hand-listed
    set, so a new state cannot inherit silence by omission."""
    loud = frozenset((*cw.TRANSITIONS, "some_new_state")) - {"still_standing"}
    assert "some_new_state" in loud


# --------------------------------------------------------------------------
# Routing, end to end through the alert path.
# --------------------------------------------------------------------------

def test_route_defaults_to_page_when_anything_is_uncertain(monkeypatch):
    from src.runtime import execution_diagnostics as ed

    def boom(*a, **k):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(ed, "route_close_failure", ed.route_close_failure)
    monkeypatch.setattr(
        "src.units.accounts.alpaca_client.parse_share_hold", boom, raising=True)
    route, transition, reason, _ = ed.route_close_failure(
        account="a", symbol="GLD", side="sell", error="anything")
    assert route == "page"
    assert transition == "routing_failed"


def test_every_share_hold_state_gets_its_own_operator_guidance():
    """A page that says the same sentence for four different causes is the
    collapse in miniature — a field written and never read."""
    from src.runtime.execution_diagnostics import _share_hold_guidance

    texts = {s: _share_hold_guidance(s)
             for s in (*SHARE_HOLD_STATES, SHARE_HOLD_NOT_CLASSIFIED)}
    assert len(set(texts.values())) == len(texts), "two states share guidance"
    # The two 'we could not establish anything' readings must SAY so, and must
    # not borrow the reassuring wording of a state we did not observe.
    assert "could NOT read" in texts["residual_unreadable"]
    assert "DID NOT LOOK" in texts[SHARE_HOLD_NOT_CLASSIFIED]
    assert "may well clear" not in texts["residual_unreadable"]


def test_the_ring_row_is_written_on_BOTH_routes(tmp_path, monkeypatch):
    """`operator_alerts.jsonl` is the only surface a page RATE is recoverable
    from. Dropping the row along with the page would make 'downgraded' and
    'never fired' identical there."""
    from src.runtime import execution_diagnostics as ed

    ring = tmp_path / "operator_alerts.jsonl"
    monkeypatch.setattr(ed, "OPERATOR_ALERTS_LOG", ring)
    monkeypatch.setattr(ed, "PENDING_PINGS_DIR", tmp_path / "pings")
    monkeypatch.setattr(ed, "route_close_failure",
                        lambda **k: ("digest", "still_standing", "carried", "broker_cancel_wedged"))
    ed.enqueue_close_failure(account="a", symbol="GLD", side="sell", qty=39,
                             consecutive=99, error="insufficient qty")
    rows = [json.loads(ln) for ln in ring.read_text().splitlines() if ln.strip()]
    assert len(rows) == 1
    assert rows[0]["kind"] == "close_failure"
    assert rows[0]["route"] == "digest"
    assert rows[0]["share_hold"] == "broker_cancel_wedged"
    # ...and NO telegram ping was queued. That is the downgrade.
    assert not (tmp_path / "pings").exists() or not list((tmp_path / "pings").glob("*.json"))


def test_the_page_route_still_queues_a_ping(tmp_path, monkeypatch):
    """The control on the test above."""
    from src.runtime import execution_diagnostics as ed

    monkeypatch.setattr(ed, "OPERATOR_ALERTS_LOG", tmp_path / "operator_alerts.jsonl")
    monkeypatch.setattr(ed, "PENDING_PINGS_DIR", tmp_path / "pings")
    monkeypatch.setattr(ed, "route_close_failure",
                        lambda **k: ("page", cw.NOT_A_WEDGE, "unknown cause", "not_classified"))
    path = ed.enqueue_close_failure(account="a", symbol="GLD", side="sell", qty=39,
                                    consecutive=3, error="insufficient qty")
    assert path is not None and path.is_file()


def test_extra_fields_can_never_overwrite_a_core_ring_field(tmp_path, monkeypatch):
    from src.runtime import execution_diagnostics as ed

    ring = tmp_path / "operator_alerts.jsonl"
    monkeypatch.setattr(ed, "OPERATOR_ALERTS_LOG", ring)
    ed._append_operator_alert("close_failure", "high", "body",
                              extra={"kind": "spoofed", "ts": "spoofed", "route": "digest"})
    row = json.loads(ring.read_text().splitlines()[0])
    assert row["kind"] == "close_failure" and row["ts"] != "spoofed"
    assert row["route"] == "digest"


# --------------------------------------------------------------------------
# MI-101: the ledger's ABSENCE must stop being readable as a clean fleet.
#
# Measured 2026-09-03: the trader had NEVER written this file, diag answered
# `present: false`, the fetch step synthesised an empty ledger from that, and
# twelve consecutive digests reported "none (ledger read, 0 entries) — a real
# observation, not an absence of one" over a real-money close path. Every one
# of those words was false and each layer was individually defensible, which is
# why the assertions below are spread across writer, unwrapper and reader.
# --------------------------------------------------------------------------


def test_sweep_creates_an_empty_but_present_ledger(ledger: Path) -> None:
    """The WRITER produces it. Nothing else may."""
    assert not ledger.exists()
    cw.sweep_vanished(now=T0, path=ledger)
    assert ledger.exists(), "a sweep with no wedges must still stamp the ledger"
    raw = json.loads(ledger.read_text())
    assert raw["wedges"] == {}
    assert raw["updated_at"] == T0.isoformat()


def test_the_ledger_declares_its_own_freshness_budget(ledger: Path) -> None:
    """A reader must not need a second copy of the cadence to grade staleness."""
    cw.sweep_vanished(now=T0, path=ledger)
    raw = json.loads(ledger.read_text())
    assert raw["heartbeat_interval_s"] == int(cw.heartbeat_minutes() * 60)
    assert raw["stale_after_intervals"] == cw.STALE_AFTER_INTERVALS


def test_heartbeat_is_floored_so_it_is_not_a_per_pass_write(ledger: Path) -> None:
    cw.sweep_vanished(now=T0, path=ledger)
    first = ledger.read_text()
    cw.sweep_vanished(now=T0 + dt.timedelta(minutes=1), path=ledger)
    assert ledger.read_text() == first, "a sweep inside the floor must not rewrite"
    cw.sweep_vanished(
        now=T0 + dt.timedelta(minutes=cw.heartbeat_minutes() + 1), path=ledger,
    )
    assert ledger.read_text() != first, "and past the floor it MUST refresh"


def test_heartbeat_never_advances_the_observation_clock(ledger: Path) -> None:
    """Liveness is not observation-continuity, and conflating them invents a
    disappearance: a sweep that believed it had watched a full window would
    retire the first wedge it ever sees as `vanished_unattributed`."""
    for i in range(6):
        cw.sweep_vanished(now=T0 + dt.timedelta(hours=i), path=ledger)
    assert json.loads(ledger.read_text())["last_sweep_at"] is None


def test_heartbeat_never_overwrites_a_store_it_could_not_parse(ledger: Path) -> None:
    """Tidying a file we failed to read would destroy standing wedges."""
    ledger.write_text("{ this is not json")
    cw.sweep_vanished(now=T0, path=ledger)
    assert ledger.read_text() == "{ this is not json"


def test_a_real_wedge_still_retires_normally_after_the_heartbeat(ledger: Path) -> None:
    """The POSITIVE CONTROL for the sweep: the heartbeat must not have broken
    the retirement path it now shares an entry point with."""
    cw.sweep_vanished(now=T0, path=ledger)              # ledger exists, empty
    d = cw.observe(_wedge_obs(), now=T0, path=ledger)   # a real wedge lands
    assert d.transition == "newly_wedged"
    assert json.loads(ledger.read_text())["wedges"]
    # Arm the clock, then let the wedge age past the window WITHOUT letting the
    # sweep's own observation lapse — a gap in OUR watching re-arms instead of
    # retiring, which is the guard this control must not trip over.
    window = cw.vanish_after_hours()
    cw.sweep_vanished(now=T0 + dt.timedelta(hours=1), path=ledger)
    swept = cw.sweep_vanished(
        now=T0 + dt.timedelta(hours=window + 0.5), path=ledger,
    )
    assert [s.transition for s in swept] == ["vanished_unattributed"]
