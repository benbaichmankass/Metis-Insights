"""FIX-SA-12: 90%-used trainer disk alarm — three states, one ping per cooldown."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.ops import trainer_disk_alarm as a

NOW = 1_800_000_000.0


def _payload(pct, age=60, measured=True, at=NOW):
    ts = datetime.fromtimestamp(at - age, timezone.utc).isoformat()
    return {"ts": ts, "disk": {"measured": measured, "used_pct": pct, "free_gb": 3.6,
                               "total_gb": 48.3, "reason": None if measured else "OSError"}}


def test_grades_ok_alarm_and_boundary():
    assert a.grade(_payload(89.9), NOW)[0] == "ok"
    assert a.grade(_payload(90.0), NOW)[0] == "alarm"      # >= threshold
    assert a.grade(_payload(93.0), NOW)[0] == "alarm"


def test_could_not_look_is_unknown_never_ok():
    assert a.grade(None, NOW)[0] == "unknown"
    assert a.grade({"ts": _payload(1)["ts"]}, NOW)[0] == "unknown"                 # no disk block
    assert a.grade(_payload(None, measured=False), NOW)[0] == "unknown"            # measure failed
    assert a.grade(_payload("93"), NOW)[0] == "unknown"                            # unusable figure
    assert a.grade(_payload(50.0, age=a.STALE_S + 1), NOW)[0] == "unknown"         # stale mirror
    assert a.grade({"ts": "garbage", "disk": {}}, NOW)[0] == "unknown"


def test_alarm_pings_once_per_cooldown_and_unknown_never_pings(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("scripts.send_ping.enqueue", lambda body, priority="normal", target="trader": sent.append(body))
    st = tmp_path / "s.json"
    assert a.run(_payload(93.0), st, NOW) == "alarm" and len(sent) == 1
    assert a.run(_payload(93.0, at=NOW + 3600), st, NOW + 3600) == "alarm" and len(sent) == 1      # cooled down
    assert a.run(_payload(93.0, at=NOW + a.COOLDOWN_S + 1), st, NOW + a.COOLDOWN_S + 1) == "alarm" and len(sent) == 2
    assert a.run(None, st, NOW + a.COOLDOWN_S + 2) == "unknown" and len(sent) == 2
    assert json.loads(st.read_text())["state"] == "unknown"
