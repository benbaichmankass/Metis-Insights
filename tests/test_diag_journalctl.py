"""Pin the diag-relay journalctl helper's timestamp normalization +
rc=1 disambiguation. Closes #930.

Two bugs fixed:
  1. journalctl on Ubuntu 20.04 (v245) rejects `2026-05-11T15:40:00Z`
     and returns rc=1 with no matches. The helper now normalizes
     `T` → ` ` and `Z` → ` UTC` (journalctl-universal form) so any
     supported version accepts the timestamp.
  2. journalctl rc=1 with empty stderr means "valid query, zero
     matches" — NOT a real failure. The helper now reports
     `available: true` in that case so callers can distinguish a
     legitimate empty window from a real error.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.web.api.routers.diag import (
    _journalctl_tail,
    _normalize_journalctl_timestamp,
)


# -- timestamp normalization ----------------------------------------------


def test_normalize_iso_with_T_and_Z_to_space_and_UTC():
    assert (
        _normalize_journalctl_timestamp("2026-05-11T15:40:00Z")
        == "2026-05-11 15:40:00 UTC"
    )


def test_normalize_iso_with_T_and_explicit_zero_offset():
    assert (
        _normalize_journalctl_timestamp("2026-05-11T15:40:00+00:00")
        == "2026-05-11 15:40:00 UTC"
    )


def test_normalize_iso_with_T_and_explicit_zero_offset_no_colon():
    assert (
        _normalize_journalctl_timestamp("2026-05-11T15:40:00+0000")
        == "2026-05-11 15:40:00 UTC"
    )


def test_normalize_iso_with_T_naive_passes_through_minus_T():
    # No Z, no offset → keep naive (journalctl will treat as local)
    assert (
        _normalize_journalctl_timestamp("2026-05-11T15:40:00")
        == "2026-05-11 15:40:00"
    )


def test_normalize_space_separated_passes_through_unchanged():
    # Already journalctl-native; no-op
    assert (
        _normalize_journalctl_timestamp("2026-05-11 15:40:00")
        == "2026-05-11 15:40:00"
    )


def test_normalize_non_utc_offset_preserves_offset():
    # Non-UTC offset stays in ISO form (journalctl 252+ accepts it;
    # older journalctl would error and the rc=1+stderr path surfaces it)
    assert (
        _normalize_journalctl_timestamp("2026-05-11T15:40:00-05:00")
        == "2026-05-11 15:40:00-05:00"
    )


# -- rc=1 disambiguation --------------------------------------------------


def _mk_proc(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def test_rc0_with_lines_is_available_with_lines():
    with patch(
        "src.web.api.routers.diag.subprocess.run",
        return_value=_mk_proc(0, "line1\nline2\n"),
    ):
        out = _journalctl_tail("ict-web-api.service", lines=10)
    assert out["available"] is True
    assert out["returncode"] == 0
    assert out["lines"] == ["line1", "line2"]
    assert "stderr" not in out


def test_rc1_with_empty_stderr_is_available_empty_lines():
    """Legit zero-match window: journalctl exits 1, no stderr → still 'available'."""
    with patch(
        "src.web.api.routers.diag.subprocess.run",
        return_value=_mk_proc(1, "", ""),
    ):
        out = _journalctl_tail(
            "ict-web-api.service",
            lines=10,
            since="2026-05-11T15:40:00Z",
            until="2026-05-11T16:00:00Z",
        )
    # Before this fix, available was False; now it must be True.
    assert out["available"] is True
    assert out["returncode"] == 1
    assert out["lines"] == []
    assert "stderr" not in out


def test_rc1_with_stderr_is_real_failure():
    """Genuine journalctl error: stderr has content → 'available': False."""
    with patch(
        "src.web.api.routers.diag.subprocess.run",
        return_value=_mk_proc(1, "", "Failed to parse timestamp: foo\n"),
    ):
        out = _journalctl_tail(
            "ict-web-api.service",
            lines=10,
            since="garbage",
        )
    assert out["available"] is False
    assert out["returncode"] == 1
    assert "Failed to parse timestamp" in out["stderr"]


def test_journalctl_call_uses_normalized_timestamps():
    """The subprocess.run argv must carry the normalized form, not the raw input."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _mk_proc(0, "ok\n")

    with patch("src.web.api.routers.diag.subprocess.run", side_effect=fake_run):
        _journalctl_tail(
            "ict-web-api.service",
            lines=10,
            since="2026-05-11T15:40:00Z",
            until="2026-05-11T16:00:00Z",
        )

    cmd = captured["cmd"]
    # --since / --until flag values are normalized
    since_ix = cmd.index("--since")
    until_ix = cmd.index("--until")
    assert cmd[since_ix + 1] == "2026-05-11 15:40:00 UTC"
    assert cmd[until_ix + 1] == "2026-05-11 16:00:00 UTC"
