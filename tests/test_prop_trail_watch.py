"""TRAIL-PAUSE-PULSE: the pulse's trail_paused_since and the prop_trail_paused_<account> probe."""
from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.ops import attention_watch as aw
from scripts.ops import prop_trail_watch as w
from src.prop import trail_pause as tp

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
LATCH_AT = 1_790_000_000.0


def _latch(d: Path, **kw):
    (d / tp.ROLLOUT_FILE).write_text(json.dumps({"state": "verified", "at": LATCH_AT, **kw}))


def test_rollout_filename_matches_the_writer():
    from src.prop.prop_trail import ROLLOUT_FILE
    assert tp.ROLLOUT_FILE == ROLLOUT_FILE


def test_since_three_values(tmp_path):
    assert tp.read_trail_paused_since(tmp_path) is None                      # absent: not paused
    _latch(tmp_path)
    assert tp.read_trail_paused_since(tmp_path) == datetime.fromtimestamp(LATCH_AT, tz=timezone.utc).isoformat()
    (tmp_path / tp.ROLLOUT_FILE).write_text("{garbled")                      # present, no 'at': mtime fallback
    assert tp.read_trail_paused_since(tmp_path) not in (None, tp.UNKNOWN)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unreadable_is_unknown_not_none(tmp_path):
    d = tmp_path / "ex"; d.mkdir(); _latch(d)
    os.chmod(d, 0)
    try:
        assert tp.read_trail_paused_since(d) == tp.UNKNOWN
    finally:
        os.chmod(d, stat.S_IRWXU)


def test_unknown_when_path_component_is_a_file(tmp_path):
    f = tmp_path / "afile"; f.write_text("x")
    assert tp.read_trail_paused_since(f / "sub") in (None, tp.UNKNOWN)  # ENOTDIR path: never a crash


def test_pulse_counts_consecutive_ticks_and_resets(tmp_path):
    p = tp.build_pulse("tradeify_1", tmp_path, now=100.0)
    assert p["trail_paused_since"] is None and p["trail_paused_ticks"] == 0
    _latch(tmp_path)
    for expect in (1, 2, 3):
        p = tp.build_pulse("tradeify_1", tmp_path, now=100.0)
        assert tp.write_pulse(tmp_path, p) and p["trail_paused_ticks"] == expect
    (tmp_path / tp.ROLLOUT_FILE).unlink()
    p = tp.build_pulse("tradeify_1", tmp_path, now=100.0)
    assert p["trail_paused_since"] is None and p["trail_paused_ticks"] == 0


def test_executor_state_dir_matches_the_shell(tmp_path):
    assert tp.executor_state_dir("breakout_1", tmp_path) == tmp_path / "executor"
    assert tp.executor_state_dir("tradeify_1", tmp_path) == tmp_path / "accounts" / "tradeify_1" / "executor"


def _pulse(d: Path, since, ticks, age_h=0.0):
    (d / tp.PULSE_FILE).write_text(json.dumps({
        "account": "tradeify_1", "at": (NOW - timedelta(hours=age_h)).isoformat(),
        "trail_paused_since": since, "trail_paused_ticks": ticks}))


def test_probe_edges(tmp_path):
    _pulse(tmp_path, None, 0)
    assert w.probe_trail_paused("tradeify_1", NOW, tmp_path)["status"] == w.OK
    _pulse(tmp_path, "2026-10-07T10:00:00+00:00", 1)
    one = w.probe_trail_paused("tradeify_1", NOW, tmp_path)
    assert one["status"] == w.OK and one["ticks"] == 1                      # one tick: not yet
    _pulse(tmp_path, "2026-10-07T10:00:00+00:00", 2)
    two = w.probe_trail_paused("tradeify_1", NOW, tmp_path)
    assert (two["status"], two["level"], two["priority"]) == (w.BREACHED, "urgent", "urgent")
    _pulse(tmp_path, None, 0)                                                # back to null: clears
    assert w.probe_trail_paused("tradeify_1", NOW, tmp_path)["status"] == w.OK


def test_probe_unknown_is_never_not_paused(tmp_path):
    assert w.probe_trail_paused("tradeify_1", NOW, tmp_path)["status"] == w.UNKNOWN   # no pulse file
    (tmp_path / tp.PULSE_FILE).write_text("{nope")
    assert w.probe_trail_paused("tradeify_1", NOW, tmp_path)["status"] == w.UNKNOWN
    _pulse(tmp_path, tp.UNKNOWN, 3)
    assert w.probe_trail_paused("tradeify_1", NOW, tmp_path)["status"] == w.UNKNOWN   # executor could not look
    _pulse(tmp_path, "2026-10-07T01:00:00+00:00", 9, age_h=w.PULSE_MAX_HOURS + 1)
    assert w.probe_trail_paused("tradeify_1", NOW, tmp_path)["status"] == w.UNKNOWN   # stale: executor silent
    (tmp_path / tp.PULSE_FILE).write_text(json.dumps({"at": NOW.isoformat()}))        # key absent != null
    assert w.probe_trail_paused("tradeify_1", NOW, tmp_path)["status"] == w.UNKNOWN


def test_attention_watch_edge_class_carries_it(tmp_path):
    k = w.key("tradeify_1")
    assert k in aw.PROBE_LABEL
    probes = {n: {"status": aw.OK, "detail": "fine"} for n in aw.PROBE_LABEL}
    view = {"now": NOW, "today": NOW.date(), "pipeline_readable": True, "pipeline_unreadable": 0,
            "stats": {}, "ranked": [], "ask_operator": [], "soak_read": "ok", "soaks": [], "probes": probes}
    st = {"last_digest_date": NOW.date().isoformat()}
    probes[k] = w.probe_trail_paused("tradeify_1", NOW, _mk(tmp_path, "2026-10-07T10:00:00+00:00", 2))
    msgs, st = aw.plan_messages(view, st)
    assert any("prop trail paused" in b for _, b in msgs)
    probes[k] = w.probe_trail_paused("tradeify_1", NOW, _mk(tmp_path, None, 0))
    msgs, st = aw.plan_messages(view, st)
    assert any("Cleared" in b and "prop trail paused" in b for _, b in msgs)


def _mk(d: Path, since, ticks):
    _pulse(d, since, ticks); return d


def test_tick_emits_pulse_line(tmp_path, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.prop import prop_executor_tick as t
    _latch(tmp_path)
    t.emit_trail_pulse("tradeify_1", tmp_path)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["pulse"]
    assert out["trail_paused_since"] and out["trail_paused_ticks"] == 1 and out["written"] is True
    assert tp.read_pulse(tmp_path)["trail_paused_since"] == out["trail_paused_since"]
