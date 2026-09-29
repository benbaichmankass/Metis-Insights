"""FIX-SA-08: the failed-unit alert logs every failure and pings once per cooldown."""
from __future__ import annotations

import json

from scripts.ops import notify_unit_failure as n


T0 = 10_000_000.0


def test_first_failure_pings_and_repeat_within_cooldown_is_suppressed(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("scripts.send_ping.enqueue", lambda body, priority="normal", target="trader": sent.append((body, priority)))
    monkeypatch.setattr(n, "_unit_facts", lambda u: {"Result": "exit-code", "ExecMainStatus": "1"})
    assert n.run("ict-x.service", now=T0, logs=tmp_path) == "pinged"
    assert n.run("ict-x.service", now=T0 + 60, logs=tmp_path) == "suppressed"
    assert n.run("ict-y.service", now=T0 + 61, logs=tmp_path) == "pinged"   # other unit unaffected
    assert n.run("ict-x.service", now=T0 + n.COOLDOWN_S + 1, logs=tmp_path) == "pinged"
    assert len(sent) == 3 and all(p == "high" for _, p in sent) and "ict-x.service" in sent[0][0]
    rows = [json.loads(l) for l in (tmp_path / "unit_failures.jsonl").read_text().splitlines()]
    assert [r["pinged"] for r in rows] == [True, False, True, True]   # every failure logged, not only pinged ones


def test_bad_argument_and_exceptions_never_raise(tmp_path, monkeypatch, capsys):
    assert n.main([]) == 0 and n.main(["a b; rm -rf /"]) == 0
    monkeypatch.setattr(n, "run", lambda u: (_ for _ in ()).throw(RuntimeError("boom")))
    assert n.main(["ict-x.service"]) == 0
    assert "boom" in capsys.readouterr().err
