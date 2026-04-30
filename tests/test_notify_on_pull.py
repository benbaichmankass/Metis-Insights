"""Tests for ``scripts/notify_on_pull.py`` — the VM-side ping fanout.

Stubs out the Telegram HTTPS POST and ``git`` subprocess so the suite
runs in any sandbox without network or repo state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import notify_on_pull as nop  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


SAMPLE_LOG = """\
# Checkpoint log

Append-only log.


---

## CP-2026-04-30-12 — S-015 Session A WRAPPED (10 PRs merged)

- **Session date:** 2026-04-30
- **Sprint:** S-015 — strategy + model improvement pass.
- **Current sprint phase:** Session A wrapped.
- **Next checkpoint:** **CP-YYYY-MM-DD-NN — housekeeping** — small.

Body line.

---

## CP-2026-04-30-11 — earlier entry (must be ignored)

- old body
"""


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    p = tmp_path / "CHECKPOINT_LOG.md"
    p.write_text(SAMPLE_LOG, encoding="utf-8")
    return p


@pytest.fixture
def stub_post(monkeypatch):
    """Capture POST args; return success unless a per-call override sets a failure."""
    sent: List[dict] = []

    class _Resp:
        def __init__(self, status: int = 200, text: str = "ok"):
            self.status_code = status
            self.text = text

    def _post(url, json=None, timeout=None):
        sent.append({"url": url, "json": json, "timeout": timeout})
        return _Resp()

    monkeypatch.setattr(nop.requests, "post", _post)
    return sent


# ---------------------------------------------------------------------------
# Source 3 — checkpoint parsing
# ---------------------------------------------------------------------------


def test_latest_cp_entry_parses_top_entry(log_path):
    cp_id, title, body = nop._latest_cp_entry(log_path)
    assert cp_id == "CP-2026-04-30-12"
    assert "WRAPPED" in title
    # Body must STOP at the next ## header — the older entry should not leak in.
    assert all("CP-2026-04-30-11" not in line for line in body)
    assert any("Sprint:" in line for line in body)


def test_latest_cp_entry_returns_none_for_missing_file(tmp_path):
    assert nop._latest_cp_entry(tmp_path / "nope.md") is None


def test_checkpoint_ping_marks_completion_high_priority(log_path, monkeypatch):
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", log_path)
    pri, body = nop._checkpoint_ping("abc1234")
    assert pri == "high"  # title contains WRAPPED
    assert "CP-2026-04-30-12" in body
    assert "S-015" in body
    assert "abc1234" in body


def test_checkpoint_ping_normal_priority_for_mid_session(tmp_path, monkeypatch):
    log = tmp_path / "log.md"
    log.write_text(
        "# Checkpoint log\n\n---\n\n"
        "## CP-2026-04-30-13 — mid-session checkpoint\n\n"
        "- **Sprint:** S-016\n"
        "- **Next checkpoint:** **CP-…**\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", log)
    pri, _ = nop._checkpoint_ping("def5678")
    assert pri == "normal"


# ---------------------------------------------------------------------------
# Source 1 — blocker commits
# ---------------------------------------------------------------------------


def test_blocker_pings_extract_question(monkeypatch):
    fake_log = MagicMock(returncode=0, stdout=(
        "abc111\t[BLOCKED-PM] need API key for prop account\n"
        "abc222\tnormal commit subject\n"
    ), stderr="")
    monkeypatch.setattr(nop.subprocess, "run", lambda *a, **kw: fake_log)
    out = nop._blocker_pings("pre", "post")
    assert len(out) == 1
    pri, body = out[0]
    assert pri == "urgent"
    assert "need API key for prop account" in body
    assert "abc111" in body


def test_blocker_pings_empty_when_no_blocker(monkeypatch):
    fake_log = MagicMock(returncode=0, stdout="abc111\tnormal subject\n", stderr="")
    monkeypatch.setattr(nop.subprocess, "run", lambda *a, **kw: fake_log)
    assert nop._blocker_pings("pre", "post") == []


def test_blocker_pings_skip_when_pre_unknown():
    """First-ever pull has no pre — skip rather than diff against unknown."""
    assert nop._blocker_pings("unknown", "post") == []


# ---------------------------------------------------------------------------
# Source 2 — pending-pings.jsonl drain
# ---------------------------------------------------------------------------


def test_drain_pending_pings_parses_each_line(tmp_path):
    p = tmp_path / "pending-pings.jsonl"
    p.write_text(
        json.dumps({
            "event": "checkpoint_appended", "priority": "normal",
            "cp_id": "CP-2026-04-30-13", "sprint": "S-016",
            "title": "audit pass",
            "commit_url": "https://github.com/x/y/commit/abc",
        }) + "\n"
        + json.dumps({
            "event": "blocker_pm", "priority": "urgent",
            "question": "need approval for X",
        }) + "\n",
        encoding="utf-8",
    )
    out = nop._drain_pending_pings(p)
    assert len(out) == 2
    assert out[0][0] == "normal" and "CP-2026-04-30-13" in out[0][1]
    assert out[1][0] == "urgent" and "need approval for X" in out[1][1]


def test_drain_pending_pings_skips_malformed(tmp_path):
    p = tmp_path / "pending-pings.jsonl"
    p.write_text("not json\n" + json.dumps({"event": "x"}) + "\n", encoding="utf-8")
    out = nop._drain_pending_pings(p)
    assert len(out) == 1


def test_drain_pending_pings_empty_for_missing(tmp_path):
    assert nop._drain_pending_pings(tmp_path / "missing.jsonl") == []


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------


def test_collect_pings_orders_blocker_first(monkeypatch, tmp_path):
    log = tmp_path / "log.md"
    log.write_text("# Checkpoint log\n\n---\n\n## CP-2026-04-30-13 — t\n\n", encoding="utf-8")
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", log)
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "missing.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings", lambda pre, post: [("urgent", "B")])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log", lambda pre, post: True)
    pings = nop.collect_pings("pre", "post")
    assert pings[0][0] == "urgent"  # blocker first
    assert any(p == "normal" for p, _ in pings)  # cp ping present


def test_main_no_advance_returns_zero(stub_post):
    rc = nop.main(["--pre", "abc", "--post", "abc"])
    assert rc == 0
    assert stub_post == []


def test_main_dry_run_skips_enqueue(monkeypatch, tmp_path):
    """S-019: --dry-run no longer takes a Telegram path; just must not enqueue."""
    inbox = tmp_path / "pending_pings"
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", tmp_path / "log.md")
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "queue.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings", lambda pre, post: [("urgent", "B")])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log", lambda pre, post: False)

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import send_ping
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", inbox)

    rc = nop.main(["--pre", "abc", "--post", "def", "--dry-run"])
    assert rc == 0
    assert not inbox.exists() or not list(inbox.iterdir())


def test_main_enqueues_pings_via_send_ping(monkeypatch, tmp_path):
    """S-019: notify_on_pull writes JSON files to send_ping's queue dir
    instead of POSTing direct to Telegram. The bot's drain loop sends
    them; this script's job is just to enqueue."""
    inbox = tmp_path / "pending_pings"
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", tmp_path / "log.md")
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "queue.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings",
                        lambda pre, post: [("urgent", "BLOCKED — need help")])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log", lambda pre, post: False)

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import send_ping
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", inbox)

    rc = nop.main(["--pre", "abc", "--post", "def"])
    assert rc == 0
    queued = sorted(inbox.glob("*.json"))
    assert len(queued) == 1
    payload = json.loads(queued[0].read_text())
    assert payload["priority"] == "urgent"
    assert "BLOCKED" in payload["body"]


def test_main_returns_one_when_enqueue_fails(monkeypatch, tmp_path):
    """If the inbox can't be written, the script returns nonzero so the
    deploy log shows the failure."""
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", tmp_path / "log.md")
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "queue.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings",
                        lambda pre, post: [("normal", "x")])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log", lambda pre, post: False)

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import send_ping

    def _boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(send_ping, "enqueue", _boom)
    rc = nop.main(["--pre", "abc", "--post", "def"])
    assert rc == 1
