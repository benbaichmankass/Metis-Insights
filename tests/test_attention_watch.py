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
