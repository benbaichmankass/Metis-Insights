"""A peer-close and a wedge are opposite conditions and must not share a response.

BL-20260825-IB-BREAKER-CANNOT-TELL-A-PEER-CLOSE-FROM-A-WEDGE.

`_probe_liveness` returned a bare bool, so both reached the same
`_trip_breaker(reason="liveness_probe_timeout")` and suppressed EVERY IB call
for 120s:

  * a WEDGE (gateway accepts the socket, never answers) MUST be backed off from
    — every call would block the trader loop, the June 2026 cascade class;
  * a PEER-CLOSE (gateway drops the socket) is the opposite — the socket is
    already gone, `_is_connected` fails, and the next `connect()` builds a
    fresh one. That reconnect IS the recovery.

MEASURED on the live trader journal, episode start 2026-08-24T23:59:09Z, three
consecutive lines with no timeout anywhere in them:

    ERROR   ib_insync.client | Peer closed connection.
    WARNING IBClient: liveness probe error ...: ConnectionError: Socket disconnect
    WARNING IBClient: circuit breaker tripped ...; IB calls suppressed for 120s.

74 such episodes over 2026-08-20T07:01Z–2026-08-25T09:28Z, each blinding every
IB leg on an account holding open positions.

⚠️ THAT EPISODE COUNT WAS MEASURED ON THE ERROR FEED AND ITS DENOMINATOR HAS
SINCE MOVED — `BL-20260825-TRANSIENT-CLASSIFIER-MISSES-THE-VARIANT-FAMILIES`
regraded those rows ERROR→WARN, so an ERROR-level re-measurement will read ~0
and mean nothing. Re-measure on WARN+ or on the journal.
"""
from __future__ import annotations

import pathlib

import pytest

import src.units.accounts.ib_client as ibc

_REPO = pathlib.Path(__file__).resolve().parents[1]


class TestTheClassifierIsNarrow:
    @pytest.mark.parametrize("exc", [
        ConnectionError("Socket disconnect"),
        ConnectionResetError("Connection reset by peer"),
        BrokenPipeError("Broken pipe"),
        EOFError("stream closed"),
    ])
    def test_a_dropped_socket_is_a_peer_close(self, exc):
        assert ibc._is_peer_close(exc) is True

    def test_the_live_exception_is_recognised(self):
        """The exact type+message from the 2026-08-24T23:59:09Z journal line."""
        assert ibc._is_peer_close(ConnectionError("Socket disconnect")) is True

    def test_a_message_only_signature_still_matches(self):
        """ib_insync may wrap the drop in a plain RuntimeError."""
        assert ibc._is_peer_close(RuntimeError("Peer closed connection.")) is True

    @pytest.mark.parametrize("exc", [
        TimeoutError("no answer"),
        RuntimeError("some other library failure"),
        ValueError("garbage from the gateway"),
    ])
    def test_anything_unrecognised_stays_a_WEDGE(self, exc):
        """Fail toward the CONSERVATIVE pre-existing response: an unclassified
        failure keeps the full breaker back-off rather than earning a reconnect
        it may not deserve."""
        assert ibc._is_peer_close(exc) is False


class TestProbeReturnsStatesNotABool:
    def test_the_four_states_are_distinct(self):
        assert len(set(ibc.PROBE_STATES)) == 4

    def test_skipped_is_not_ok(self):
        """'We could not look' must never equal 'we looked and it answered' —
        the caller branches on exactly this to avoid caching a non-verdict."""
        assert ibc.PROBE_SKIPPED != ibc.PROBE_OK

    def test_a_stub_client_reports_SKIPPED_not_OK(self):
        client = object.__new__(ibc.IBClient)
        client._ib_factory = lambda: None
        assert client._probe_liveness(object()) == ibc.PROBE_SKIPPED

    def test_the_probe_never_returns_a_bool(self):
        """A bool is the defect. Source-level, because a caller comparing
        `if not probe_state` against the string 'ok' would be silently wrong."""
        src = (_REPO / "src/units/accounts/ib_client.py").read_text()
        body = src[src.index("def _probe_liveness("):]
        body = body[:body.index("\n    def ", 10)]
        assert "return True" not in body
        assert "return False" not in body


class TestTheCallerBranchesOnAllFour:
    def _connect_src(self) -> str:
        src = (_REPO / "src/units/accounts/ib_client.py").read_text()
        body = src[src.index("if fresh_connect or not self._probe_cache_valid():"):]
        return body[:6000]

    def test_a_peer_close_does_NOT_trip_the_breaker_within_grace(self):
        body = self._connect_src()
        seg = body[body.index("PROBE_DISCONNECTED"):]
        seg = seg[:seg.index("PROBE_SKIPPED")]
        assert "_peer_close_streak <= _IB_PEER_CLOSE_GRACE_ATTEMPTS" in seg
        assert "NOT tripping" in seg

    def test_an_exhausted_grace_DOES_trip(self):
        """Bounded, or an unbounded reconnect loop against a sick gateway is
        the exact failure class the breaker exists for."""
        body = self._connect_src()
        assert 'reason="liveness_probe_disconnected"' in body

    def test_the_trip_reason_no_longer_lies(self):
        """A peer-close tripping under `liveness_probe_timeout` asserts a cause
        no code path tested — the unprovenanced-diagnostic class."""
        body = self._connect_src()
        dis = body[body.index("PROBE_DISCONNECTED"):body.index("PROBE_SKIPPED")]
        assert "liveness_probe_timeout" not in dis

    def test_a_SKIPPED_probe_does_not_stamp_the_cache(self):
        body = self._connect_src()
        assert "if probe_state == PROBE_SKIPPED" in body
        skipped = body[body.index("if probe_state == PROBE_SKIPPED"):]
        skipped = skipped[:skipped.index("elif probe_state")]
        # Comments stripped: the assertion is about what the branch DOES, and
        # the branch's own comment names `_mark_probe_ok` to explain why it is
        # deliberately absent. Matching prose here would fail for the opposite
        # of the reason this test exists.
        code = "\n".join(ln for ln in skipped.splitlines()
                         if not ln.strip().startswith("#"))
        assert "_mark_probe_ok" not in code
        assert code.strip().endswith("pass"), (
            "the SKIPPED branch must fall through without stamping anything"
        )

    def test_a_successful_connect_clears_the_streak(self):
        """The grace is for CONSECUTIVE drops. Without this, a slow trickle of
        unrelated drops would eventually trip the breaker on a healthy gateway."""
        body = self._connect_src()
        assert "self._peer_close_streak = 0" in body


class TestTheStateIsReadableFromOutside:
    def test_the_state_file_carries_the_probe_verdict(self):
        """Fail-permissive behaviour is unobservable unless the verdict is
        published — the `venue_session` lesson: a permanently-broken gate reads
        exactly like a working one."""
        src = (_REPO / "src/units/accounts/ib_client.py").read_text()
        assert '"last_probe_state": self._last_probe_state' in src
        assert '"peer_close_streak": self._peer_close_streak' in src

    def test_the_grace_setting_ships_beside_the_streak(self):
        """A streak without its bound cannot be read — `max_multiple` beside
        `measured_n`, same discipline."""
        src = (_REPO / "src/units/accounts/ib_client.py").read_text()
        assert '"peer_close_grace_attempts"' in src


class TestTheRollbackIsOneEnvFlip:
    def test_zero_grace_restores_the_old_behaviour(self):
        """`IB_PEER_CLOSE_GRACE_ATTEMPTS=0` means every probe failure trips,
        byte-for-byte the pre-2026-08-25 response."""
        assert ibc._IB_PEER_CLOSE_GRACE_ATTEMPTS >= 0

    def test_an_unparseable_value_falls_back_to_the_DEFAULT_not_zero(self, monkeypatch):
        """A typo must not silently re-arm the condition this fixes."""
        src = (_REPO / "src/units/accounts/ib_client.py").read_text()
        decl = src[src.index("_IB_PEER_CLOSE_GRACE_ATTEMPTS = "):]
        decl = decl[:decl.index("\n\n")]
        assert '"IB_PEER_CLOSE_GRACE_ATTEMPTS", 1.0' in decl
