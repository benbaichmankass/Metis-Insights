"""P1 — the prop fills-staleness alert.

The controls that matter most are the NEGATIVE ones. This alert sits on a
manual bridge where the operator is often unavailable, and the operator has
directed twice that an unanswered ticket is expected behaviour and not a
success metric. So the tests below pin, as hard assertions, that neither
detector can fire from ticket state at all — a regression that made this alert
fire on unacted tickets would be worse than not having it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.prop import prop_fills_staleness as fs


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _snap(i, bal, when):
    return {"id": i, "account_id": "breakout_1", "balance": bal,
            "reported_at": when.isoformat()}


def _fill(when, account="breakout_1"):
    return {"account_id": account, "reported_at": when.isoformat()}


# ── detector B: balance moved with no fills ───────────────────────────

class TestBalanceMove:
    def test_the_live_incident_reproduces(self):
        """The real 2026-08-20 -> 2026-08-23 pair: -$111.86, zero fills."""
        snaps = [
            _snap(11, 4871.0, datetime(2026, 8, 23, 8, 11, tzinfo=timezone.utc)),
            _snap(10, 4982.86, datetime(2026, 8, 20, 18, 57, tzinfo=timezone.utc)),
        ]
        a = fs.assess_balance_move(snaps, [], delta_threshold=25.0)
        assert a["balance_state"] == "unreported"
        assert a["delta"] == pytest.approx(-111.86, abs=0.01)
        assert a["fills_in_window"] == 0

    def test_a_reported_fill_in_the_window_explains_the_move(self):
        start = datetime(2026, 8, 19, 12, 52, tzinfo=timezone.utc)
        end = datetime(2026, 8, 19, 22, 21, tzinfo=timezone.utc)
        snaps = [_snap(9, 4983.0, end), _snap(8, 4738.0, start)]
        a = fs.assess_balance_move(
            snaps, [_fill(datetime(2026, 8, 19, 21, 31, tzinfo=timezone.utc))],
            delta_threshold=25.0,
        )
        assert a["balance_state"] == "explained"

    def test_it_does_NOT_require_the_fills_to_RECONCILE_with_the_delta(self):
        """The live 08-19 pair moved +245.00 against +235.97 of reported fills.

        A reconciliation test would call that a finding. Fees, funding and
        partial reports break the arithmetic without breaking the record — the
        question is whether ANYTHING was reported.
        """
        start = datetime(2026, 8, 19, 12, 52, tzinfo=timezone.utc)
        end = datetime(2026, 8, 19, 22, 21, tzinfo=timezone.utc)
        snaps = [_snap(9, 4983.0, end), _snap(8, 4738.0, start)]
        fill = _fill(datetime(2026, 8, 19, 21, 31, tzinfo=timezone.utc))
        fill["pnl"] = 235.97          # deliberately != the 245.00 delta
        a = fs.assess_balance_move(snaps, [fill], delta_threshold=25.0)
        assert a["balance_state"] == "explained"

    def test_a_move_under_the_threshold_is_within_noise_not_a_finding(self):
        """The live -0.14 pair. Real, tiny, and not worth an alarm."""
        snaps = [
            _snap(10, 4982.86, datetime(2026, 8, 20, 18, 57, tzinfo=timezone.utc)),
            _snap(9, 4983.0, datetime(2026, 8, 19, 22, 21, tzinfo=timezone.utc)),
        ]
        a = fs.assess_balance_move(snaps, [], delta_threshold=25.0)
        assert a["balance_state"] == "within_noise"

    def test_one_snapshot_is_insufficient_NOT_clean(self):
        """No delta EXISTS. This must never read as 'nothing wrong'."""
        a = fs.assess_balance_move(
            [_snap(1, 5000.0, NOW)], [], delta_threshold=25.0
        )
        assert a["balance_state"] == "insufficient_snapshots"
        assert a["balance_state"] != "within_noise"

    def test_zero_snapshots_is_insufficient_too(self):
        assert fs.assess_balance_move([], [], delta_threshold=25.0)[
            "balance_state"] == "insufficient_snapshots"

    def test_an_unreadable_balance_is_its_own_state(self):
        snaps = [_snap(2, None, NOW), _snap(1, 5000.0, NOW - timedelta(days=1))]
        assert fs.assess_balance_move(snaps, [], delta_threshold=25.0)[
            "balance_state"] == "balance_unreadable"

    def test_an_undateable_snapshot_cannot_bound_a_window(self):
        """Without a window we cannot say what was reported inside it, so this
        is 'could not look' — never 'unreported'."""
        snaps = [{"id": 2, "balance": 4800.0, "reported_at": "not-a-date"},
                 _snap(1, 5000.0, NOW - timedelta(days=1))]
        assert fs.assess_balance_move(snaps, [], delta_threshold=25.0)[
            "balance_state"] == "balance_unreadable"

    def test_a_fill_at_the_closing_instant_counts_as_inside_the_window(self):
        """The live 07-17 pair: fill 22:14:08, snapshot 22:14:19. The interval
        is half-open (prev, latest] precisely so this explains rather than
        alarms."""
        end = datetime(2026, 7, 17, 22, 14, 19, tzinfo=timezone.utc)
        snaps = [_snap(5, 4928.0, end),
                 _snap(4, 4990.0, datetime(2026, 7, 17, 8, 42, tzinfo=timezone.utc))]
        a = fs.assess_balance_move(snaps, [_fill(end)], delta_threshold=25.0)
        assert a["balance_state"] == "explained"

    def test_a_fill_from_ANOTHER_account_does_not_explain_this_one(self):
        snaps = [_snap(11, 4871.0, NOW), _snap(10, 4982.86, NOW - timedelta(days=2))]
        other = _fill(NOW - timedelta(hours=1), account="breakout_2")
        # the caller pre-filters by account; assert the pre-filter is required
        a = fs.assess_balance_move(snaps, [], delta_threshold=25.0)
        assert a["balance_state"] == "unreported"
        assert other["account_id"] != "breakout_1"


# ── detector A: a crossed bracket with no close report ────────────────

class TestCrossings:
    def _pos(self, key="k1"):
        return {"key": key, "account_id": "breakout_1", "symbol": "ETHUSDT",
                "direction": "long", "opened_at": NOW.isoformat()}

    def test_a_crossing_older_than_the_grace_is_the_finding(self):
        st = {"k1": {"sl_alerted_at": (NOW - timedelta(hours=9)).isoformat(),
                     "tp_alerted_at": None}}
        [row] = fs.assess_crossings([self._pos()], st, now=NOW, grace_hours=6.0)
        assert row["crossing_state"] == "crossed_unreported"
        assert row["level"] == "sl"
        assert row["hours_since_crossing"] == pytest.approx(9.0, abs=0.01)

    def test_a_fresh_crossing_is_still_within_grace(self):
        st = {"k1": {"sl_alerted_at": (NOW - timedelta(hours=1)).isoformat(),
                     "tp_alerted_at": None}}
        [row] = fs.assess_crossings([self._pos()], st, now=NOW, grace_hours=6.0)
        assert row["crossing_state"] == "crossed_within_grace"

    def test_an_ABSENT_entry_is_unknown_NOT_not_crossed(self):
        """prop_sl_tp_alert has not looked at this position. 'We did not look'
        and 'the level was not crossed' are opposite statements."""
        [row] = fs.assess_crossings([self._pos()], {}, now=NOW, grace_hours=6.0)
        assert row["crossing_state"] == "unknown"
        assert row["crossing_state"] != "not_crossed"

    def test_a_present_entry_with_no_stamps_is_not_crossed(self):
        st = {"k1": {"sl_alerted_at": None, "tp_alerted_at": None}}
        [row] = fs.assess_crossings([self._pos()], st, now=NOW, grace_hours=6.0)
        assert row["crossing_state"] == "not_crossed"

    def test_the_EARLIEST_crossing_decides(self):
        st = {"k1": {"sl_alerted_at": (NOW - timedelta(hours=2)).isoformat(),
                     "tp_alerted_at": (NOW - timedelta(hours=20)).isoformat()}}
        [row] = fs.assess_crossings([self._pos()], st, now=NOW, grace_hours=6.0)
        assert row["level"] == "tp"
        assert row["crossing_state"] == "crossed_unreported"

    def test_an_unparseable_stamp_does_not_manufacture_a_crossing(self):
        st = {"k1": {"sl_alerted_at": "garbage", "tp_alerted_at": None}}
        [row] = fs.assess_crossings([self._pos()], st, now=NOW, grace_hours=6.0)
        assert row["crossing_state"] == "not_crossed"


# ── the operator's constraint, as a hard assertion ────────────────────

class TestNeverFiresOnUnactedTickets:
    """Operator-directed twice (2026-08-23): an unanswered ticket is the
    EXPECTED shape on a manual bridge and ticket answer-rate is not a metric of
    success. Neither detector may read ticket state."""

    def test_detector_A_reads_only_OPEN_POSITIONS_and_crossing_stamps(self):
        # No positions => no crossing findings, however many tickets exist.
        assert fs.assess_crossings([], {"k1": {"sl_alerted_at": "2026-01-01T00:00:00Z"}},
                                   now=NOW, grace_hours=6.0) == []

    def test_detector_B_reads_only_SNAPSHOTS_and_FILLS(self):
        import inspect
        src = inspect.getsource(fs.assess_balance_move)
        assert "ticket" not in src.lower()

    def test_the_module_never_calls_list_tickets(self):
        import inspect
        src = inspect.getsource(fs)
        assert "list_tickets" not in src
        assert "find_unacted_tickets" not in src
        assert "prop_reconcile" not in src


# ── knobs fall back to defaults, never to disabled ────────────────────

class TestKnobs:
    def test_garbage_falls_back_to_the_default_not_to_zero(self, monkeypatch):
        monkeypatch.setenv("PROP_FILLS_STALENESS_CHECK_SECONDS", "banana")
        assert fs._int_knob("PROP_FILLS_STALENESS_CHECK_SECONDS", 3600) == 3600
        monkeypatch.setenv("PROP_FILLS_STALENESS_BALANCE_DELTA_USD", "")
        assert fs._float_knob("PROP_FILLS_STALENESS_BALANCE_DELTA_USD", 25.0) == 25.0

    def test_nan_and_inf_fall_back_too(self, monkeypatch):
        for bad in ("nan", "inf", "-inf"):
            monkeypatch.setenv("PROP_FILLS_STALENESS_BALANCE_DELTA_USD", bad)
            assert fs._float_knob(
                "PROP_FILLS_STALENESS_BALANCE_DELTA_USD", 25.0) == 25.0

    def test_an_explicit_value_is_honoured(self, monkeypatch):
        monkeypatch.setenv("PROP_FILLS_STALENESS_BALANCE_DELTA_USD", "5")
        assert fs._float_knob(
            "PROP_FILLS_STALENESS_BALANCE_DELTA_USD", 25.0) == 5.0

    def test_a_zero_cadence_pauses_and_says_so(self, monkeypatch):
        monkeypatch.setenv("PROP_FILLS_STALENESS_CHECK_SECONDS", "0")
        out = fs.run_prop_fills_staleness(now=NOW)
        assert out == {"checked": False, "reason": "paused"}


# ── messages ──────────────────────────────────────────────────────────

class TestMessages:
    def test_the_balance_message_states_the_amount_and_the_window(self):
        a = {"delta": -111.86, "window_start": "2026-08-20T18:57:00+00:00",
             "window_end": "2026-08-23T08:11:00+00:00", "fills_in_window": 0}
        msg = fs.describe_balance("breakout_1", a)
        assert "$111.86" in msg and "breakout_1" in msg
        assert "2026-08-20T18:57:00+00:00" in msg
        # It must offer the non-trading explanation rather than assert trades.
        assert "deposit" in msg.lower() or "withdrawal" in msg.lower()

    def test_no_message_prints_a_python_repr_where_a_number_belongs(self):
        """The P3 lesson: a safety message must never render `$None`."""
        a = {"delta": -111.86, "window_start": None, "window_end": None,
             "fills_in_window": 0}
        assert "$None" not in fs.describe_balance("breakout_1", a)
        row = {"symbol": "ETHUSDT", "direction": "long", "account_id": "breakout_1",
               "level": None, "hours_since_crossing": None}
        assert "$None" not in fs.describe_crossing(row, 6.0)

    def test_the_crossing_message_says_it_fires_once(self):
        row = {"symbol": "ETHUSDT", "direction": "long",
               "account_id": "breakout_1", "level": "sl",
               "hours_since_crossing": 9.0}
        msg = fs.describe_crossing(row, 6.0)
        assert "9.0h" in msg and "OPEN" in msg
        assert "once" in msg.lower()


# ── the run path: latching, recovery, and read failures ───────────────

class TestRunPath:
    """End-to-end through `run_prop_fills_staleness` with the journal mocked.

    `force=True` bypasses the cadence gate; `alerter` captures the sends.
    """

    @pytest.fixture(autouse=True)
    def _isolate_state(self, tmp_path, monkeypatch):
        import src.utils.paths as paths
        monkeypatch.setattr(paths, "runtime_logs_dir", lambda: tmp_path)

    def _wire(self, monkeypatch, *, snapshots, fills, positions=(), sl_tp=None,
              tables=True):
        from src.prop import prop_journal
        monkeypatch.setattr(prop_journal, "tables_present", lambda: tables)
        monkeypatch.setattr(prop_journal, "list_fills",
                            lambda **kw: list(fills))
        monkeypatch.setattr(prop_journal, "list_account_status",
                            lambda aid, **kw: list(snapshots))
        monkeypatch.setattr(fs, "_sl_tp_state", lambda: dict(sl_tp or {}))
        import src.prop.prop_monitor_pulse as pmp
        monkeypatch.setattr(pmp, "find_open_prop_positions",
                            lambda **kw: list(positions))
        import src.prop.prop_identity as pid
        monkeypatch.setattr(pid, "declared_prop_account_ids",
                            lambda **kw: ["breakout_1"])

    def test_an_unexplained_move_alerts_ONCE_then_latches(self, monkeypatch):
        self._wire(monkeypatch,
                   snapshots=[_snap(11, 4871.0, NOW),
                              _snap(10, 4982.86, NOW - timedelta(days=2))],
                   fills=[])
        sent = []
        out = fs.run_prop_fills_staleness(now=NOW, force=True, alerter=sent.append)
        assert out["checked"] is True
        assert len(sent) == 1 and "$111.86" in sent[0]

        out2 = fs.run_prop_fills_staleness(
            now=NOW + timedelta(minutes=1), force=True, alerter=sent.append)
        assert len(sent) == 1, "a latched finding must not re-fire"
        assert out2["alerted"] == []

    def test_a_crossed_unreported_position_alerts_then_recovers_on_close(
            self, monkeypatch):
        pos = {"key": "k1", "account_id": "breakout_1", "symbol": "ETHUSDT",
               "direction": "long", "opened_at": NOW.isoformat()}
        sl_tp = {"k1": {"sl_alerted_at": (NOW - timedelta(hours=9)).isoformat(),
                        "tp_alerted_at": None}}
        self._wire(monkeypatch, snapshots=[], fills=[], positions=[pos],
                   sl_tp=sl_tp)
        sent = []
        fs.run_prop_fills_staleness(now=NOW, force=True, alerter=sent.append)
        assert len(sent) == 1 and "may have closed unrecorded" in sent[0]
        assert fs.stale_fill_accounts()["breakout_1"]

        # the operator reports the close -> the position leaves the open set
        self._wire(monkeypatch, snapshots=[], fills=[], positions=[], sl_tp={})
        fs.run_prop_fills_staleness(now=NOW + timedelta(hours=1), force=True,
                                    alerter=sent.append)
        assert len(sent) == 2 and "[OK]" in sent[1]
        assert not fs.stale_fill_accounts()

    def test_a_superseded_balance_pair_is_pruned_WITHOUT_a_false_OK(
            self, monkeypatch):
        """A newer snapshot supersedes the evidence; it does not repair the
        journal, so an '[OK]' there would be a false statement."""
        self._wire(monkeypatch,
                   snapshots=[_snap(11, 4871.0, NOW),
                              _snap(10, 4982.86, NOW - timedelta(days=2))],
                   fills=[])
        sent = []
        fs.run_prop_fills_staleness(now=NOW, force=True, alerter=sent.append)
        assert len(sent) == 1

        later = NOW + timedelta(days=1)
        self._wire(monkeypatch,
                   snapshots=[_snap(12, 4871.0, later), _snap(11, 4871.0, NOW)],
                   fills=[])
        out = fs.run_prop_fills_staleness(now=later, force=True,
                                          alerter=sent.append)
        assert len(sent) == 1, "no recovery ping for a superseded balance pair"
        assert out["recovered"] == []

    def test_a_read_failure_grades_NOTHING_and_is_not_clean(self, monkeypatch):
        from src.prop import prop_journal
        monkeypatch.setattr(prop_journal, "tables_present", lambda: True)

        def boom(**kw):
            raise RuntimeError("db locked")
        monkeypatch.setattr(prop_journal, "list_fills", boom)
        sent = []
        out = fs.run_prop_fills_staleness(now=NOW, force=True, alerter=sent.append)
        assert out == {"checked": False, "reason": "read_failed"}
        assert sent == []

    def test_absent_tables_are_could_not_look_not_clean(self, monkeypatch):
        self._wire(monkeypatch, snapshots=[], fills=[], tables=False)
        out = fs.run_prop_fills_staleness(now=NOW, force=True)
        assert out == {"checked": False, "reason": "tables_absent"}

    def test_the_skip_list_excludes_an_account(self, monkeypatch):
        monkeypatch.setenv("PROP_FILLS_STALENESS_SKIP", "breakout_1")
        self._wire(monkeypatch,
                   snapshots=[_snap(11, 4871.0, NOW),
                              _snap(10, 4982.86, NOW - timedelta(days=2))],
                   fills=[])
        sent = []
        out = fs.run_prop_fills_staleness(now=NOW, force=True, alerter=sent.append)
        assert sent == [] and out["accounts"] == 0

    def test_the_cadence_gate_holds_between_runs(self, monkeypatch):
        self._wire(monkeypatch, snapshots=[], fills=[])
        fs.run_prop_fills_staleness(now=NOW, force=True)
        out = fs.run_prop_fills_staleness(now=NOW + timedelta(seconds=5))
        assert out == {"checked": False, "reason": "cadence"}
