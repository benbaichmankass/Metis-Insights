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
    # New 3-tuple shape: every survivor carries a content hash so the
    # caller can record it after a successful enqueue.
    assert all(len(t[2]) == 64 for t in out)
    assert out[0][2] != out[1][2]


def test_drain_pending_pings_skips_malformed(tmp_path):
    p = tmp_path / "pending-pings.jsonl"
    p.write_text("not json\n" + json.dumps({"event": "x"}) + "\n", encoding="utf-8")
    out = nop._drain_pending_pings(p)
    assert len(out) == 1


def test_drain_pending_pings_empty_for_missing(tmp_path):
    assert nop._drain_pending_pings(tmp_path / "missing.jsonl") == []


def test_drain_skips_already_delivered_lines(tmp_path):
    """Hash-based dedupe: a line whose sha256 is already in the
    delivered set must be skipped on the next drain. This is the fix
    for the "old pings re-fire on every merge" bug — pre-fix, the file
    was tracked in git and every pull re-emitted every line."""
    p = tmp_path / "pending-pings.jsonl"
    line1 = json.dumps({"event": "x", "priority": "high", "summary": "one"})
    line2 = json.dumps({"event": "y", "priority": "normal", "summary": "two"})
    p.write_text(line1 + "\n" + line2 + "\n", encoding="utf-8")

    # First drain — delivered set empty → both lines come through.
    first = nop._drain_pending_pings(p, delivered=set())
    assert len(first) == 2
    h1, h2 = first[0][2], first[1][2]

    # Mark line1 as delivered. Second drain should yield only line2.
    second = nop._drain_pending_pings(p, delivered={h1})
    assert len(second) == 1
    assert second[0][2] == h2

    # Both delivered — nothing comes through.
    assert nop._drain_pending_pings(p, delivered={h1, h2}) == []


def test_record_and_load_delivered_hashes_round_trip(tmp_path):
    """``_record_delivered_hash`` appends; ``_load_delivered_hashes``
    reads back the set. Append is the only write op — the dedupe log
    grows but never rewrites itself, so multiple processes (extremely
    unlikely on the VM, but cheap to keep safe) can't race-clobber."""
    state = tmp_path / "delivered.txt"
    assert nop._load_delivered_hashes(state) == set()

    nop._record_delivered_hash(state, "a" * 64)
    nop._record_delivered_hash(state, "b" * 64)
    assert nop._load_delivered_hashes(state) == {"a" * 64, "b" * 64}

    # Re-record an existing hash — `set()` semantics keep it idempotent
    # at the read side; the file simply has a duplicate line.
    nop._record_delivered_hash(state, "a" * 64)
    assert nop._load_delivered_hashes(state) == {"a" * 64, "b" * 64}


def test_line_hash_stable_across_calls():
    raw = '{"event":"x","priority":"high"}'
    h1 = nop._line_hash(raw)
    h2 = nop._line_hash(raw)
    assert h1 == h2
    assert len(h1) == 64
    # Different content → different hash.
    assert nop._line_hash(raw + " ") != h1


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
    # The diff added the same CP-ID that's currently topmost — gating
    # condition for the new "only ping when a NEW topmost CP was added"
    # rule.
    monkeypatch.setattr(nop, "_diff_added_cp_ids",
                        lambda pre, post: ["CP-2026-04-30-13"])
    pings = nop.collect_pings("pre", "post")
    assert pings[0][0] == "urgent"  # blocker first
    assert any(p == "normal" for p, _, _ in pings)  # cp ping present


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


def test_main_does_not_re_enqueue_already_delivered_pending_line(
    monkeypatch, tmp_path,
):
    """End-to-end regression for the "old pings re-fire on every merge"
    bug. Same pending-pings.jsonl line, two consecutive pulls — the
    second pull must enqueue zero pings because the first pull's hash
    is already in the delivered log."""
    inbox = tmp_path / "pending_pings"
    queue = tmp_path / "queue.jsonl"
    queue.write_text(
        json.dumps({
            "event": "blocker_pm", "priority": "high", "sprint": "S-014",
            "question": "approve M2 PR",
        }) + "\n",
        encoding="utf-8",
    )
    delivered = tmp_path / "delivered.txt"

    monkeypatch.setattr(nop, "CHECKPOINT_LOG", tmp_path / "log.md")
    monkeypatch.setattr(nop, "PENDING_PINGS", queue)
    monkeypatch.setattr(nop, "DELIVERED_HASHES", delivered)
    monkeypatch.setattr(nop, "_blocker_pings", lambda pre, post: [])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log", lambda pre, post: False)

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import send_ping
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", inbox)

    # First pull — line is fresh, enqueue exactly once.
    rc = nop.main(["--pre", "abc", "--post", "def"])
    assert rc == 0
    first_queued = sorted(inbox.glob("*.json"))
    assert len(first_queued) == 1, "first pull must enqueue the new line"
    assert delivered.read_text(encoding="utf-8").strip()  # hash recorded

    # Second pull — same line still in the file (it's git-tracked, the
    # next session's PR didn't truncate it). The hash is now in the
    # delivered log so no new ping fires.
    rc = nop.main(["--pre", "def", "--post", "ghi"])
    assert rc == 0
    all_queued = sorted(inbox.glob("*.json"))
    assert len(all_queued) == 1, (
        "second pull must NOT re-enqueue the already-delivered line; "
        "got %d queued files" % len(all_queued)
    )


def test_force_checkpoint_emits_cp_ping_without_log_diff(monkeypatch, tmp_path):
    """S-020 T3: --force-checkpoint emits a checkpoint ping even when the
    diff didn't touch CHECKPOINT_LOG.md. Used by the auto_ping_test.flag
    path to verify the auto-ping leg without waiting for a real CP commit.
    """
    log = tmp_path / "log.md"
    log.write_text(
        "# Checkpoint log\n\n---\n\n"
        "## CP-2026-04-30-99 — force-trigger smoke\n\n"
        "- **Sprint:** S-020\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", log)
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "queue.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings", lambda pre, post: [])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log", lambda pre, post: False)

    pings = nop.collect_pings("pre", "post", force_checkpoint=True)
    assert any("CP-2026-04-30-99" in body for _, body, _ in pings)


def test_main_force_checkpoint_runs_when_pre_equals_post(monkeypatch, tmp_path):
    """The flag-triggered path may pass --pre == --post (state file already
    at HEAD, but operator wants to verify the leg). The script must still
    enqueue when --force-checkpoint is set."""
    inbox = tmp_path / "pending_pings"
    log = tmp_path / "log.md"
    log.write_text(
        "# Checkpoint log\n\n---\n\n"
        "## CP-2026-04-30-99 — force-trigger\n\n- **Sprint:** S-020\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", log)
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "queue.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings", lambda pre, post: [])

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import send_ping
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", inbox)

    rc = nop.main(["--pre", "abc", "--post", "abc", "--force-checkpoint"])
    assert rc == 0
    queued = sorted(inbox.glob("*.json"))
    assert len(queued) == 1
    assert "CP-2026-04-30-99" in queued[0].read_text()


def test_enqueue_writes_atomically_into_inbox(tmp_path, monkeypatch):
    """S-020 T2: pin the actual on-disk file-write path of send_ping.enqueue
    (not just a stubbed enqueue). The bot's drain loop reads from this exact
    directory, with the exact filename pattern, so any drift here breaks
    the auto-ping path silently — like the bug we just fixed."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import send_ping

    inbox = tmp_path / "pending_pings"
    monkeypatch.setattr(send_ping, "PENDING_PINGS_DIR", inbox)

    # No tmp left over, dir created on demand, atomic rename to .json.
    path = send_ping.enqueue("hello operator", priority="normal")
    assert path.exists()
    assert path.suffix == ".json"
    assert not list(inbox.glob("*.json.tmp"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"priority": "normal", "body": "hello operator"}

    # Drain-loop filename filter: the bot picks files ending in .json
    # but NOT .json.tmp (see telegram_query_bot._drain_pending_pings).
    # If we ever change the suffix here, the drainer goes silent — pin it.
    assert path.name.endswith(".json")
    assert not path.name.endswith(".tmp")


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


# ---------------------------------------------------------------------------
# CP-2026-05-02 — training/improvement workflow stage pings
# ---------------------------------------------------------------------------


def test_training_workflow_pings_detect_each_stage(monkeypatch):
    """Each documented training-improvement stage prefix must produce
    its own ping via _commit_subjects + _training_workflow_pings."""
    fake_subjects = [
        ("sha111", "[TRAINING-START] vwap regime filter"),
        ("sha222", "TRAINING-PLAN: 2026-05-02-vwap-htf"),
        ("sha333", "TRAINING-RESULTS: 2026-05-02-vwap-htf"),
        ("sha444", "TRAINING-RESULTS [FAILED]: 2026-05-02-vwap-htf"),
        ("sha555", "RECOMMENDATIONS (PM REVIEW): 2026-05-02-vwap-htf"),
        ("sha666", "IMPLEMENT: 2026-05-02-vwap-htf"),
        ("sha777", "boring docs commit"),
    ]
    monkeypatch.setattr(nop, "_commit_subjects",
                        lambda pre, post: fake_subjects)

    out = nop._training_workflow_pings("pre", "post")
    bodies = "\n".join(b for _, b in out)
    assert len(out) == 6
    assert "TRAINING-START" in bodies
    assert "TRAINING-PLAN" in bodies
    assert "TRAINING-RESULTS — run finished" in bodies
    assert "TRAINING-RESULTS [FAILED] — run errored" in bodies
    assert "RECOMMENDATIONS (PM REVIEW)" in bodies
    assert "IMPLEMENT" in bodies
    # Priorities: only TRAINING-START is normal; all others are high.
    assert sum(1 for p, _ in out if p == "high") == 5
    assert sum(1 for p, _ in out if p == "normal") == 1


def test_training_workflow_pings_empty_for_normal_commits(monkeypatch):
    monkeypatch.setattr(
        nop, "_commit_subjects",
        lambda pre, post: [("sha", "fix: typo"), ("sha2", "chore: bump")],
    )
    assert nop._training_workflow_pings("pre", "post") == []


def test_collect_pings_includes_training_pings(monkeypatch, tmp_path):
    """Top-level dispatch must surface training-stage pings between
    blockers and the checkpoint ping."""
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", tmp_path / "log.md")
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "missing.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings", lambda pre, post: [])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log", lambda pre, post: False)
    monkeypatch.setattr(
        nop, "_commit_subjects",
        lambda pre, post: [("sha", "TRAINING-PLAN: smoke run")],
    )
    pings = nop.collect_pings("pre", "post")
    assert any("TRAINING-PLAN" in body for _, body, _ in pings)


# ---------------------------------------------------------------------------
# CP ping gating — only fire when a NEW topmost CP-ID was added in the diff.
# Pre-fix: any commit in the pull window touching CHECKPOINT_LOG.md re-pinged
# the file's current topmost entry, including merges of feature branches that
# carried an old sprint's checkpoint commit, and in-place edits to existing
# entries. Both shapes spammed the operator with already-announced content.
# ---------------------------------------------------------------------------


def test_collect_pings_skips_cp_when_topmost_not_in_added_set(
    monkeypatch, tmp_path,
):
    """File touched by a commit that *edits* an existing entry (no new
    CP header in the diff) must NOT fire a checkpoint ping — the
    topmost entry was already pinged on its original commit."""
    log = tmp_path / "log.md"
    log.write_text(
        "# Checkpoint log\n\n---\n\n"
        "## CP-2026-05-03-20 — already-announced\n\n"
        "- **Sprint:** S-old\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", log)
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "missing.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings", lambda pre, post: [])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log",
                        lambda pre, post: True)
    # Diff added no CP header — only edits to existing entries.
    monkeypatch.setattr(nop, "_diff_added_cp_ids", lambda pre, post: [])

    pings = nop.collect_pings("pre", "post")
    assert all("CP-2026-05-03-20" not in body for _, body, _ in pings)


def test_collect_pings_skips_cp_when_old_id_added_but_not_topmost(
    monkeypatch, tmp_path,
):
    """A merge commit may bring in an old sprint's checkpoint commit
    (so its CP header IS in the diff's added set) while a newer
    checkpoint already sits at the top of the file. Re-announcing the
    old CP would be noise — skip."""
    log = tmp_path / "log.md"
    log.write_text(
        "# Checkpoint log\n\n---\n\n"
        "## CP-2026-05-03-21 — newest\n\n- **Sprint:** S-new\n\n"
        "---\n\n"
        "## CP-2026-04-30-12 — old branch checkpoint\n\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", log)
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "missing.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings", lambda pre, post: [])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log",
                        lambda pre, post: True)
    # Merge brought CP-2026-04-30-12 into main but the file's topmost
    # entry is the newer CP-2026-05-03-21.
    monkeypatch.setattr(nop, "_diff_added_cp_ids",
                        lambda pre, post: ["CP-2026-04-30-12"])

    pings = nop.collect_pings("pre", "post")
    assert all("CP-2026-04-30-12" not in body for _, body, _ in pings)
    assert all("CP-2026-05-03-21" not in body for _, body, _ in pings)


def test_collect_pings_fires_cp_when_new_topmost_id_added(
    monkeypatch, tmp_path,
):
    """The happy path: an end-of-session commit appends a NEW CP-ID at
    the top of the file. The diff's added set contains that CP-ID and
    it matches the file's current topmost entry — fire the ping."""
    log = tmp_path / "log.md"
    log.write_text(
        "# Checkpoint log\n\n---\n\n"
        "## CP-2026-05-03-22 — fresh\n\n- **Sprint:** S-new\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", log)
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "missing.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings", lambda pre, post: [])
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log",
                        lambda pre, post: True)
    monkeypatch.setattr(nop, "_diff_added_cp_ids",
                        lambda pre, post: ["CP-2026-05-03-22"])

    pings = nop.collect_pings("pre", "post")
    assert any("CP-2026-05-03-22" in body for _, body, _ in pings)


def test_diff_added_cp_ids_returns_only_added_headers(monkeypatch):
    """Parser pulls CP-IDs from `+` diff lines only — context lines
    (no leading `+`/`-`) and `+++` file headers are ignored."""
    fake_diff = (
        "diff --git a/docs/claude/checkpoints/CHECKPOINT_LOG.md "
        "b/docs/claude/checkpoints/CHECKPOINT_LOG.md\n"
        "--- a/docs/claude/checkpoints/CHECKPOINT_LOG.md\n"
        "+++ b/docs/claude/checkpoints/CHECKPOINT_LOG.md\n"
        "@@ -1,0 +1,3 @@\n"
        "+## CP-2026-05-03-22 — fresh entry\n"
        "+\n"
        "+- **Sprint:** S-new\n"
        " ## CP-2026-05-03-21 — older context (unchanged)\n"
    )
    monkeypatch.setattr(
        nop.subprocess, "run",
        lambda *a, **kw: MagicMock(returncode=0, stdout=fake_diff, stderr=""),
    )
    out = nop._diff_added_cp_ids("pre", "post")
    assert out == ["CP-2026-05-03-22"]


def test_diff_added_cp_ids_skips_when_pre_unknown():
    assert nop._diff_added_cp_ids("unknown", "post") == []


def test_force_checkpoint_bypasses_added_id_gate(monkeypatch, tmp_path):
    """The auto_ping_test.flag verification path passes
    --force-checkpoint and must still fire even though the diff added
    no new CP header."""
    log = tmp_path / "log.md"
    log.write_text(
        "# Checkpoint log\n\n---\n\n"
        "## CP-2026-05-03-99 — flag-trigger\n\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nop, "CHECKPOINT_LOG", log)
    monkeypatch.setattr(nop, "PENDING_PINGS", tmp_path / "missing.jsonl")
    monkeypatch.setattr(nop, "_blocker_pings", lambda pre, post: [])
    # Force path bypasses the touched/added-ids gate entirely.
    monkeypatch.setattr(nop, "_diff_touched_checkpoint_log",
                        lambda pre, post: False)
    monkeypatch.setattr(nop, "_diff_added_cp_ids", lambda pre, post: [])

    pings = nop.collect_pings("pre", "post", force_checkpoint=True)
    assert any("CP-2026-05-03-99" in body for _, body, _ in pings)
