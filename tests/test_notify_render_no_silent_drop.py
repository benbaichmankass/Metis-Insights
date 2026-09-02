"""The ping renderer must never silently drop a body.

THE DEFECT THIS PINS (2026-09-02). ``scripts/ops/work_digest.py`` queued its
digest under the key ``"message"``. ``notify_on_pull._render_event_body`` built
the operator-visible text from a fixed list of CONTENT keys, and ``"message"``
was on none of them — so the operator's ClaudeBot channel received a message at
03:24 reading, in its entirety, ``work_digest``. No body. The night behind it
had merged 52 PRs, retired two backlog classes, cleared two register rows and
closed a capability gap.

⚠️ **THE FIX IS NOT "ADD ``message`` TO THE LIST."** That is the same
hand-maintained list one entry longer, and the next producer that picks a
different key fails identically and just as silently. Measured over the
committed queue file on 2026-09-02 (population: all 50 rows in
``docs/claude/pending-pings.jsonl``), **21 rows — 42% — rendered to a bare label
under the old renderer**, across five different event types (``health-review``
×6, ``performance-review`` ×6, ``ml-review`` ×5, ``ping`` ×2, ``session-wrap``
×1, ``work_digest`` ×1), written by at least two producers
(``work_phase_ping.py`` and ``work_digest.py``, which both key on ``message``).
The whole review-session notification channel was mute.

So the list that is hand-maintained is now the ENVELOPE, and these tests hold it
to that — the same way ``tests/test_backlog_append.py`` pins ``LIVE_BACKLOGS``
against the backlogs actually on disk rather than trusting the constant.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import notify_on_pull as nop  # noqa: E402

PENDING = REPO_ROOT / "docs" / "claude" / "pending-pings.jsonl"


def _live_rows() -> list[dict]:
    if not PENDING.exists():
        return []
    out = []
    for line in PENDING.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _render(row: dict) -> str:
    return nop._render_event_body(str(row.get("event") or "ping"), row)


# ── the reported defect, as a regression ─────────────────────────────────

def test_a_work_digest_row_renders_its_body_not_a_bare_label():
    """The exact 2026-09-02 03:24 shape."""
    row = {
        "at": "2026-09-02T02:20:00+00:00", "target": "claude",
        "priority": "normal", "event": "work_digest",
        "digest_state": "no_changes",
        "message": "[work digest] 2026-09-02\nNo state change in a..b.",
    }
    body = _render(row)
    assert "[work digest] 2026-09-02" in body
    assert "No state change in a..b." in body
    assert body.strip() != "work_digest", "the reported defect, back again"


def test_the_old_renderer_really_did_drop_it_positive_control():
    """A probe that cannot find the positive proves nothing about the fix.

    Reconstructs the pre-fix body-key list and shows it produces the bare label
    for the same row the test above renders correctly.
    """
    pre_fix_keys = ("cp_id", "next_cp", "phase", "strategy", "model",
                    "result", "grade", "question", "summary")
    row = {"at": "x", "target": "claude", "priority": "normal",
           "event": "work_digest", "message": "the body that vanished"}
    old_lines = [nop.EVENT_LABELS.get("work_digest", "work_digest")]
    old_lines += [str(row[k]) for k in ("sprint", "title") if row.get(k)]
    old_body = " — ".join(old_lines)
    old_body += "".join(f"\n{row[k]}" for k in pre_fix_keys if row.get(k))
    assert old_body == "work_digest", "positive control: the old shape drops it"
    assert "the body that vanished" in _render(row)


# ── the pin: nothing in the live queue may render to a bare label ────────

def test_no_live_queue_row_renders_to_a_bare_label():
    """Pinned against the committed queue, not against a constant.

    FAILS when a producer appends a row this renderer cannot say anything
    about — which is the condition, not a formatting nit.
    """
    rows = _live_rows()
    assert rows, "positive control: the queue file must be readable and non-empty"
    bare = []
    for row in rows:
        event = str(row.get("event") or "ping")
        body = _render(row)
        if body.strip() == event.strip():
            bare.append(row)
    assert not bare, (
        f"{len(bare)} of {len(rows)} queued pings render to nothing but their "
        f"event label. Offending rows: {[sorted(r) for r in bare[:3]]}\n"
        "A row is either content (and must render) or a producer defect (and "
        "must say so). It is never a silent bare label."
    )


def test_every_live_queue_key_is_either_envelope_or_rendered():
    """The coverage claim, checked rather than asserted."""
    keys = {k for row in _live_rows() for k in row}
    assert keys, "positive control: rows carry keys"
    unaccounted = sorted(
        k for k in keys
        if k not in nop.ENVELOPE_KEYS and k not in nop.CURATED_KEYS
        # An unrecognised key is FINE — it renders. This asserts the renderer
        # actually reaches it, which is the property that was missing.
        and nop._render_unknown_value(k, "probe") is None
    )
    assert not unaccounted, (
        f"keys neither envelope, curated, nor renderable: {unaccounted}")


# ── the envelope may not quietly grow to swallow content ─────────────────

def test_envelope_and_curated_keys_are_disjoint():
    overlap = nop.ENVELOPE_KEYS & nop.CURATED_KEYS
    assert not overlap, f"a key cannot be both envelope and content: {overlap}"


def test_envelope_is_justified_by_the_transport_signature():
    """`priority` and `target` are envelope because `enqueue` consumes them.

    Pinning against the real signature is what stops the envelope becoming a
    dumping ground: a key only belongs there if the transport itself uses it.
    """
    spec = importlib.util.spec_from_file_location(
        "send_ping_probe", REPO_ROOT / "scripts" / "send_ping.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    params = set(inspect.signature(mod.enqueue).parameters)
    assert {"priority", "target"} <= params
    assert {"priority", "target"} <= nop.ENVELOPE_KEYS
    # Everything else in the envelope is a timestamp or the discriminator.
    assert nop.ENVELOPE_KEYS - params == {"at", "reviewed_at", "event"}


def test_envelope_stays_small():
    """A growing envelope is the failure mode inverted: it hides content."""
    assert len(nop.ENVELOPE_KEYS) <= 6, sorted(nop.ENVELOPE_KEYS)


# ── how unknown content renders ──────────────────────────────────────────

def test_single_line_unknown_scalar_is_labelled():
    assert nop._render_unknown_value("digest_state", "no_changes") == \
        "digest_state: no_changes"


def test_multi_line_unknown_string_renders_bare_because_it_is_prose():
    out = nop._render_unknown_value("message", "line one\nline two")
    assert out == "line one\nline two"
    assert not out.startswith("message:")


def test_unknown_container_is_shown_not_dropped():
    out = nop._render_unknown_value("refs", ["a", "b"])
    assert out is not None and "a" in out and "b" in out


def test_empty_and_none_unknowns_say_nothing():
    assert nop._render_unknown_value("k", "") is None
    assert nop._render_unknown_value("k", "   ") is None
    assert nop._render_unknown_value("k", None) is None


# ── a genuinely empty row fails LOUD, and is still delivered ─────────────

def test_a_body_less_row_announces_itself_rather_than_arriving_blank():
    row = {"at": "x", "target": "claude", "priority": "normal", "event": "ping"}
    body = _render(row)
    assert "EMPTY PING" in body
    assert "PRODUCER defect" in body
    # It is DELIVERED, not dropped: refusing here would move the silent drop
    # into the VM's journald, which this repo has measured as unread.
    assert body.strip() != "ping"


def test_a_row_with_only_a_title_is_not_treated_as_empty():
    """Positive control: `sprint`/`title` land on the head line and ARE content.

    An earlier draft of the emptiness counter missed this and flagged every
    well-formed `sprint-start` row as an empty ping.
    """
    row = {"event": "sprint-start", "priority": "normal",
           "sprint": "S-042", "title": "M1 verify ClaudeBot channel"}
    body = _render(row)
    assert "EMPTY PING" not in body
    assert body == "🟢 Sprint started — S-042 — M1 verify ClaudeBot channel"


# ── existing well-formed pings must not move ─────────────────────────────

def test_a_fully_curated_ping_renders_exactly_as_before():
    """Byte-identical golden. This path carries CRITICAL safety pages."""
    row = {
        "event": "sprint-complete", "priority": "high", "sprint": "S-099",
        "title": "A thing", "cp_id": "CP-2026-01-01-1", "next_cp": "CP-2",
        "phase": "P1", "strategy": "vwap", "model": "m1", "result": "ok",
        "grade": "A", "question": "why", "summary": "it worked",
        "pr_url": "https://x/pr", "commit_url": "https://x/c",
        "chat_url": "https://x/chat", "summary_url": "https://x/s",
    }
    assert _render(row) == (
        "✅ Sprint complete — S-099 — A thing\n"
        "CP: CP-2026-01-01-1\nNext: CP-2\nPhase: P1\nStrategy: vwap\n"
        "Model: m1\nResult: ok\nGrade: A\nQ: why\nit worked\n"
        "https://x/pr\nhttps://x/c\nhttps://x/chat\nhttps://x/s"
    )


def test_urls_stay_last_so_unknown_keys_cannot_split_the_link_block():
    row = {"event": "checkpoint", "priority": "normal", "title": "t",
           "summary": "s", "some_new_key": "v", "pr_url": "https://x/pr"}
    lines = _render(row).splitlines()
    assert lines[-1] == "https://x/pr"
    assert "some_new_key: v" in lines
