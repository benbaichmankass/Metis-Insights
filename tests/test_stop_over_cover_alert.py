"""The disjoint-OCA stop-over-cover must reach an OPERATOR surface.

MEASURED GAP (2026-08-25). The DETECTION has been correct since
BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS -- the sweep counts
`over_covered` and emits a `logger.error`. But `logger.error` writes to the
systemd journal and nothing else: it never reaches `outcomes.jsonl`, which is
what feeds Telegram, the `/api/bot/notifications` banner, and
`/api/bot/logs?level=error`.

Measured live, both halves in one session:
  - `/api/bot/logs?level=error&limit=1000` -> 388 rows spanning
    2026-08-20T07:01Z-2026-08-25T09:28Z. Rows mentioning over-cover: **0**.
  - `/api/diag/ib_open_orders?account_id=ib_paper` -> MHG 29-lot position with
    TWO disjoint OCA groups (`oca-protect-416`, `oca-protect-432`), each
    holding a 29-lot STP and a 29-lot LMT. 58 of stop against 29 of position
    = **200%**.

So the condition was live, correctly detected, and invisible on every surface
a human reads. OCA cancels only WITHIN a group, so one stop firing flattens the
position and leaves the other group resting to sell 29 more into a naked SHORT.
"""
from __future__ import annotations

import time

import pytest

import src.runtime.order_monitor as om


@pytest.fixture()
def latched(tmp_path, monkeypatch):
    monkeypatch.setattr(om, "_alert_state_path",
                        lambda kind: tmp_path / f"{kind}_alert_state.json")
    return tmp_path / "stop_over_cover_alert_state.json"


@pytest.fixture()
def pages(monkeypatch):
    """Capture what reaches `outcomes.report`, the operator-facing path."""
    sent = []
    import src.runtime.outcomes as outcomes
    monkeypatch.setattr(outcomes, "report",
                        lambda *a, **k: sent.append((a, k)))
    return sent


def _emit(symbol="MHG"):
    return om._emit_stop_over_cover_alert(
        account_id="ib_paper", symbol=symbol, size=29.0, stop_qty=58.0,
        oca_groups={"oca-protect-432": 29.0, "oca-protect-416": 29.0},
    )


def test_it_reaches_the_operator_path_at_critical(latched, pages):
    """A journal line is not an operator surface. CRITICAL is what Telegrams."""
    assert _emit() is True
    assert len(pages) == 1, "the page must go through outcomes.report"
    args, kwargs = pages[0]
    from src.runtime.outcomes import Level
    assert kwargs["level"] is Level.CRITICAL
    assert args[0] == "ib_stop_over_cover"


def test_the_page_states_the_measurement_and_the_consequence(latched, pages):
    """A page naming neither the numbers nor the failure mode cannot be acted on."""
    _emit()
    reason = pages[0][1]["reason"]
    assert "29.0" in reason and "58.0" in reason and "200%" in reason
    assert "naked SHORT" in reason, "the consequence is the reason it is CRITICAL"
    # Both group names, sorted, so the operator can go cancel one by id.
    assert "oca-protect-416" in reason and "oca-protect-432" in reason
    kw = pages[0][1]
    assert kw["over_cover_pct"] == pytest.approx(200.0)
    assert kw["oca_groups"] == ["oca-protect-416", "oca-protect-432"]


def test_cooldown_is_durable_across_a_simulated_restart(latched, pages):
    """The target-naked latch failed EXACTLY here (per-process monotonic), and a
    copy-pasted latch would have failed the same way. Same gate, same proof."""
    assert _emit() is True
    assert _emit() is False, "inside 6h -> suppressed"
    for name in dir(om):
        obj = getattr(om, name)
        if isinstance(obj, dict) and "OVER_COVER" in name:
            obj.clear()
    assert _emit() is False, "a restart must NOT re-arm the page"
    assert len(pages) == 1


def test_a_different_symbol_is_not_suppressed(latched, pages):
    """Keyed per (account, symbol): MHG paging must not mute MES."""
    assert _emit("MHG") is True
    assert _emit("MES") is True
    assert len(pages) == 2


def test_unreadable_latch_alerts_rather_than_suppressing(latched, pages):
    assert _emit() is True
    latched.write_text("{ not json", encoding="utf-8")
    assert _emit() is True, (
        "'we could not look' must never be read as 'already paged' on a "
        "money-at-risk page"
    )


def test_a_failing_report_never_aborts_the_sweep(latched, monkeypatch):
    """This runs inside the broker sweep; an alert failure must not propagate."""
    import src.runtime.outcomes as outcomes

    def boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(outcomes, "report", boom)
    assert _emit() is True, "the sweep continues and the cooldown still commits"


def test_the_state_file_is_readable_on_the_diag_surface():
    """A latch that suppresses a CRITICAL and cannot be inspected is worse than
    none. #8778 shipped a writer with no allowlist entry; not again."""
    from src.web.api.routers.diag import _LOG_FILES
    assert "stop_over_cover_alert_state" in _LOG_FILES
    assert (_LOG_FILES["stop_over_cover_alert_state"].name
            == om._alert_state_path("stop_over_cover").name)


# ---------------------------------------------------------------------------
# A WORSENING condition must break the cooldown; an unchanged or improving one
# must not. BL-20260825-OVER-COVER-LATCH-CANNOT-SEE-A-WORSENING-CONDITION.
#
# The live miss: the page fired for ib_paper/MHG at 2026-08-25T12:27:44Z at
# 2 disjoint OCA groups / 200% and stayed silent while the SAME symbol reached
# 3 groups / 300% two hours later, inside the 6h window.
# ---------------------------------------------------------------------------

def test_a_third_oca_group_pages_inside_the_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(om, "runtime_logs_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", lambda: tmp_path)
    seen = []
    monkeypatch.setattr("src.runtime.outcomes.report",
                        lambda *a, **k: seen.append(k.get("reason", "")))

    def _fire(groups):
        return om._emit_stop_over_cover_alert(
            account_id="ib_paper", symbol="MHG", size=29.0,
            stop_qty=29.0 * len(groups),
            oca_groups=[f"oca-protect-{400 + i}" for i in range(len(groups))])

    assert _fire(["a", "b"]) is True, "first page must fire"
    assert _fire(["a", "b"]) is False, "unchanged condition must stay silent"
    assert _fire(["a", "b", "c"]) is True, "a THIRD group is a new fact"
    assert _fire(["a", "b", "c"]) is False, "still unchanged at 3"


def test_an_improving_condition_does_not_page(tmp_path, monkeypatch):
    """Going from 3 groups back to 2 is a repair, not a new alarm. Paging
    CRITICAL on an improvement is the desensitized-alarm P1 itself."""
    monkeypatch.setattr(om, "runtime_logs_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", lambda: tmp_path)
    monkeypatch.setattr("src.runtime.outcomes.report", lambda *a, **k: None)

    def _fire(n):
        return om._emit_stop_over_cover_alert(
            account_id="ib_paper", symbol="MHG", size=29.0, stop_qty=29.0 * n,
            oca_groups=[f"oca-protect-{400 + i}" for i in range(n)])

    assert _fire(3) is True
    assert _fire(2) is False, "an improvement must not page"
    assert _fire(3) is False, "back to the already-latched worst: still silent"
    assert _fire(4) is True, "worse than anything latched: pages"


def test_severity_is_per_symbol_not_global(tmp_path, monkeypatch):
    """A 3-group latch on MHG must not suppress a 2-group condition on MES."""
    monkeypatch.setattr(om, "runtime_logs_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", lambda: tmp_path)
    monkeypatch.setattr("src.runtime.outcomes.report", lambda *a, **k: None)

    assert om._emit_stop_over_cover_alert(
        account_id="ib_paper", symbol="MHG", size=29.0, stop_qty=87.0,
        oca_groups=["a", "b", "c"]) is True
    assert om._emit_stop_over_cover_alert(
        account_id="ib_paper", symbol="MES", size=15.0, stop_qty=30.0,
        oca_groups=["x", "y"]) is True


def test_a_pre_severity_latch_entry_still_suppresses(tmp_path, monkeypatch):
    """Upgrade path. A latch written by the previous build has no severity, so
    it says the condition alerted recently and NOTHING about how bad it was.
    'We do not know it got worse' must not become 'it got worse'."""
    monkeypatch.setattr(om, "runtime_logs_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", lambda: tmp_path)
    monkeypatch.setattr("src.runtime.outcomes.report", lambda *a, **k: None)
    # exactly the live file shape: {"ib_paper|MHG": <epoch>}
    om._save_alert_state("stop_over_cover", {"ib_paper|MHG": time.time()})

    assert om._emit_stop_over_cover_alert(
        account_id="ib_paper", symbol="MHG", size=29.0, stop_qty=87.0,
        oca_groups=["a", "b", "c"]) is False


# ---------------------------------------------------------------------------
# The page must say WHO submitted each group, because its own remediation line
# ("cancel the leg that does NOT match trades.stop_loss") is un-executable for
# a group whose submitting session has rotated its clientId — IB binds cancel
# rights to the submitter and refuses a foreign cancel with Error 10147.
# BL-20260825-OVER-COVER-PAGE-CANNOT-SAY-WHY-THE-GROUPS-ARE-DISJOINT
#
# Measured live on ib_paper MHG 2026-08-25 (`/api/diag/ib_open_orders`):
#   oca-protect-465  STP 6.312   LMT 7.1415  clientId 497   <- matches
#                                                              trades.stop_loss
#                                                              6.31207143
#   oca-protect-446  STP 6.2625  LMT 7.1415  clientId 597   <- the PREVIOUS
#                                                              trail level
# 29-lot long, 58 of resting stop = 200%. The second group is the trailing
# amend's own cancel-and-re-place with the cancel half refused, so the count
# grows by one per (clientId rotation, trailing amend) pair.
# ---------------------------------------------------------------------------

class TestGroupOwnershipIsClassifiedNotGuessed:
    def test_matching_ids_are_this_session(self):
        assert om._classify_group_owner(497, 497) == "this_session"

    def test_a_different_id_is_other_session_not_retired(self):
        """`other_session` deliberately does NOT claim the session retired: a
        live sibling in the 496/497/498 exec cluster is indistinguishable from
        a dead one here, and either way this client cannot cancel it."""
        assert om._classify_group_owner(597, 497) == "other_session"

    def test_a_missing_group_id_is_unknown_NOT_this_session(self):
        """The collapse that would matter: reading 'we could not look' as
        'ours' makes the page promise a cancel that then fails."""
        assert om._classify_group_owner(None, 497) == "unknown"

    def test_a_missing_reader_id_is_also_unknown(self):
        assert om._classify_group_owner(597, None) == "unknown"


class TestThePageNamesTheOwnerAndTheMechanism:
    def _live_case(self):
        return om._emit_stop_over_cover_alert(
            account_id="ib_paper", symbol="MHG", size=29.0, stop_qty=58.0,
            oca_groups={"oca-protect-465": 29.0, "oca-protect-446": 29.0},
            group_client_ids={"oca-protect-465": 497, "oca-protect-446": 597},
            reader_client_id=497,
        )

    def test_the_foreign_group_is_named_with_its_clientId(self, latched, pages):
        assert self._live_case() is True
        reason = pages[0][1]["reason"]
        assert "clientId=597" in reason and "clientId=497" in reason
        assert "oca-protect-446" in reason

    def test_it_says_the_foreign_group_cannot_be_cancelled_from_here(
        self, latched, pages,
    ):
        """Without this the operator is told to cancel a leg the API refuses."""
        self._live_case()
        reason = pages[0][1]["reason"]
        assert "10147" in reason

    def test_it_names_the_REPO_PATH_not_a_manual_one(self, latched, pages):
        """An earlier draft said the group 'must be cancelled in TWS'. That was
        wrong and in the expensive direction: `cancel-ib-order` reads the owning
        clientId account-wide and CONNECTS AS IT, so the repo has an audited,
        allowlisted path. A CRITICAL page that sends the operator to a manual
        tool when a workflow exists is pressure toward the riskier option."""
        self._live_case()
        reason = pages[0][1]["reason"]
        assert "cancel-ib-order" in reason
        assert "force_client_id" in reason
        assert "TWS" not in reason, (
            "do not send the operator to a manual tool when the action exists"
        )

    def test_it_states_the_cost_of_the_override(self, latched, pages):
        """`force_client_id` on a trader-band id (below 9000) EVICTS the
        trader's live IB session. A page naming the override without its cost
        invites waiving a guard blind."""
        self._live_case()
        reason = pages[0][1]["reason"]
        assert "9000" in reason and "EVICT" in reason.upper()

    def test_it_names_the_cause_not_only_the_symptom(self, latched, pages):
        """'2 disjoint groups' is a symptom. The cause is the refused cancel
        half of a trailing amend, and it is one field away from the page."""
        self._live_case()
        assert "trailing amend" in pages[0][1]["reason"]

    def test_the_owners_ride_structured_beside_the_prose(self, latched, pages):
        """A consumer must be able to branch without parsing the reason string."""
        self._live_case()
        kw = pages[0][1]
        assert kw["group_owners"] == {
            "oca-protect-446": "other_session",
            "oca-protect-465": "this_session",
        }
        assert kw["reader_client_id"] == 497
        assert kw["oca_group_client_ids"]["oca-protect-446"] == 597

    def test_an_all_ours_case_does_not_claim_an_uncancellable_group(
        self, latched, pages,
    ):
        """A false 'you cannot cancel this' would send the operator to TWS for
        a leg they could have cancelled from here."""
        om._emit_stop_over_cover_alert(
            account_id="ib_paper", symbol="MHG", size=29.0, stop_qty=58.0,
            oca_groups={"a": 29.0, "b": 29.0},
            group_client_ids={"a": 497, "b": 497}, reader_client_id=497,
        )
        reason = pages[0][1]["reason"]
        assert "10147" not in reason
        assert "naked SHORT" in reason, "the hazard is unchanged — still page it"

    def test_an_unreadable_owner_is_flagged_as_unreadable(self, latched, pages):
        om._emit_stop_over_cover_alert(
            account_id="ib_paper", symbol="MHG", size=29.0, stop_qty=58.0,
            oca_groups={"a": 29.0, "b": 29.0},
            group_client_ids={"a": 497}, reader_client_id=497,
        )
        reason = pages[0][1]["reason"]
        assert "unreadable, not this session" in reason
        assert pages[0][1]["group_owners"]["b"] == "unknown"

    def test_omitting_the_new_fields_still_pages(self, latched, pages):
        """Back-compat: a coverage dict from a client predating these keys must
        degrade to 'unknown' owners, never raise inside a safety page."""
        assert _emit() is True
        assert set(pages[0][1]["group_owners"].values()) == {"unknown"}
        assert "naked SHORT" in pages[0][1]["reason"]
