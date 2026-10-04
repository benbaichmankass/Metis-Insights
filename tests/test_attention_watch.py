"""WORK-SYSTEM: the attention watch — digest, edge alerts, silence alarms."""
from __future__ import annotations

from scripts.ops import attention_watch as a


def test_self_test_passes():
    assert a._self_test() == 0


def test_run_sends_through_claude_inbox_and_advances_state(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("scripts.send_ping.enqueue",
                        lambda body, priority="normal", target="trader": sent.append((target, priority)))
    monkeypatch.setattr("send_ping.enqueue",
                        lambda body, priority="normal", target="trader": sent.append((target, priority)),
                        raising=False)
    monkeypatch.setattr(a, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(a, "RECEIPT", tmp_path / "receipt.json")
    assert a.run(digest_now=True) == 0
    assert sent and all(t == "claude" for t, _ in sent)
    assert (tmp_path / "state.json").exists() and (tmp_path / "receipt.json").exists()


def test_failed_send_does_not_advance_state(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("inbox gone")
    monkeypatch.setattr("send_ping.enqueue", boom, raising=False)
    monkeypatch.setattr(a, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(a, "RECEIPT", tmp_path / "receipt.json")
    assert a.run(digest_now=True) == 1
    assert not (tmp_path / "state.json").exists()


def test_work_report_self_test_passes():
    from scripts.ops import work_report
    assert work_report._self_test() == 0


def test_report_route_absent_then_present(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from scripts.ops import work_report
    from src.web.api.routers import work

    monkeypatch.setattr(work_report, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(work_report.read_latest, "__defaults__", (tmp_path,))
    monkeypatch.setattr(work_report.read_report, "__defaults__", (tmp_path,))
    out = work.get_work_report()
    assert out["present"] is False and out["readState"] == "absent"
    rep = work_report.generate(datetime(2026, 10, 5, 5, 30, tzinfo=timezone.utc))
    work_report.persist(rep, tmp_path)
    out = work.get_work_report()
    assert out["present"] is True and out["reportId"] == "WR-20261005-0530Z"
    assert work.get_work_report(report_id="WR-20261005-0530Z")["present"] is True
    assert all("trace" not in v for v in out["sections"].values())


def test_report_probe_breaches_when_absent_and_when_errored(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from scripts.ops import work_report
    monkeypatch.setattr(work_report.read_latest, "__defaults__", (tmp_path,))
    now = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)
    assert a.probe_report(now)["status"] == a.BREACHED
    rep = work_report.generate(datetime(2026, 10, 5, 5, 30, tzinfo=timezone.utc))
    rep["errored_sections"], rep["bytes"] = [], 10_000
    work_report.persist(rep, tmp_path)
    assert a.probe_report(now)["status"] == a.OK
    rep["errored_sections"] = ["soaks"]
    work_report.persist(rep, tmp_path)
    assert a.probe_report(now)["status"] == a.BREACHED
    # yesterday's report at 06:00 today = today's was not produced
    rep2 = work_report.generate(datetime(2026, 10, 4, 5, 30, tzinfo=timezone.utc))
    rep2["errored_sections"], rep2["bytes"] = [], 10_000
    (tmp_path / "latest.json").unlink()
    work_report.persist(rep2, tmp_path)
    assert a.probe_report(now)["status"] == a.BREACHED
