#!/usr/bin/env python3
"""Every row in the ping queue must render to more than its event label.

WHY THIS IS A GUARD AND NOT A TEST — the same reason, verbatim, that
``backlog_append.py::check_live_backlogs`` is one, and it is not a style
preference: ``pytest-run`` **short-circuits on a diff that touches only
``docs/``**, and ``docs/claude/pending-pings.jsonl`` is exactly such a file. A
PR that appends a body-less row would therefore skip the suite entirely and
merge green, so the pytest version of this check could not fire on the one PR
that introduced the defect it exists to catch
(``BL-20260814-COVERAGE-MATRIX-SHORTCIRCUITS-THE-SUITE-THAT-READS-IT``, and
three instances before it).

⚠️ **This file exists because I tried the pytest version first and CI refused
it.** ``tests/test_pytest_run_filter.py::test_docs_committed_readers_are_all_covered``
is derived from what the tests actually read, and it named the exact file — so
the fourth recurrence of that class was caught by a mechanism rather than by an
incident. Recording that here so the next person does not move the check back.

The other available fix — widening ``pytest-run``'s relevance filter to include
this path — was rejected: the ``work-digest`` workflow appends to this file
every four hours through an auto-merge PR, so it would put a ~15-minute full
suite on a routine generated commit. Guards are diff-scoped and cheap.

WHAT IT CHECKS. For every row in the queue, ``_render_event_body`` must produce
something beyond the event label. A row that does not is a **producer defect**:
something queued a notification with nothing in it. The renderer already fails
loud at send time (it delivers an explicit EMPTY PING notice rather than a bare
label), and this stops such a row reaching ``main`` in the first place.

    python3 scripts/ci/check_pending_pings_render.py --self-test
    python3 scripts/ci/check_pending_pings_render.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

QUEUE = REPO_ROOT / "docs" / "claude" / "pending-pings.jsonl"


def _renderer():
    """Import the ping renderer, and REFUSE distinctly if we cannot.

    ⚠️ `notify_on_pull` imports `requests` at module scope (its Telegram
    transport, which this guard never reaches), and the guards CI job installs
    only `requirements-dev.txt`. The first CI run of this guard died on exactly
    that — an ImportError traceback and exit 1, which reads as "the queue is
    bad" when it means "we could not look". `requests` is now installed in
    .github/workflows/guards.yml; this branch makes the remaining failure mode
    SAY which one it is instead of blaming the data.
    """
    try:
        import notify_on_pull  # noqa: PLC0415 — kept local so --help never needs it
    except ImportError as exc:  # pragma: no cover — exercised by the CI failure above
        raise SystemExit(
            f"pending-pings-render: CANNOT IMPORT the renderer ({exc}). This is "
            f"'we could not look', NOT 'the queue is clean' and NOT 'the queue "
            f"is bad'. Install the ping path's deps in this job."
        ) from exc
    return notify_on_pull


def bare_label_rows(rows: list[dict]) -> list[tuple[int, dict]]:
    """Rows that render no content at all. Pure.

    ⚠️ The emptiness predicate is IMPORTED, never re-derived — this asks
    ``notify_on_pull.render_event_parts`` for its own content count. The first
    draft of this guard re-derived it by testing whether the rendered body
    equalled the bare event label, which was ALREADY WRONG against the
    renderer's empty-ping notice: a planted body-less row rendered the notice,
    so it no longer equalled the label, so the guard reported it clean. The
    self-test caught that; review had not.
    """
    nop = _renderer()
    bad: list[tuple[int, dict]] = []
    for i, row in enumerate(rows, start=1):
        event = str(row.get("event") or "ping")
        _lines, content = nop.render_event_parts(event, row)
        if content == 0:
            bad.append((i, row))
    return bad


def check(path: Path = QUEUE) -> int:
    if not path.exists():
        # MISSING is not CLEAN. The queue is committed; its absence means we
        # could not look, and a guard that reports a pass for a file it never
        # read is the failure this repo names by name.
        print(f"pending-pings-render: MISSING {path} — cannot be checked "
              f"(not the same as clean)")
        return 1
    rows: list[dict] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            print(f"pending-pings-render: line {lineno} is not JSON: {exc}")
            return 1
    if not rows:
        # A genuinely empty queue is a real, quiet observation — distinct from
        # the missing case above, which is not.
        print("pending-pings-render: queue is empty (0 rows) — nothing to check")
        return 0
    bad = bare_label_rows(rows)
    if bad:
        print(f"pending-pings-render: {len(bad)} of {len(rows)} queued ping(s) "
              f"render to nothing but their event label.")
        for lineno, row in bad[:5]:
            print(f"  row {lineno}: event={row.get('event')!r} keys={sorted(row)}")
        print("A queued ping is either content (and must render) or a producer "
              "defect (and must say so). It is never a silent bare label. See "
              "ENVELOPE_KEYS in scripts/notify_on_pull.py.")
        return 1
    print(f"pending-pings-render: OK — all {len(rows)} queued ping(s) render a body")
    return 0


def _self_test() -> int:
    """A detector whose failure path is never exercised is indistinguishable
    from one that always passes."""
    ok = True

    def report(n: int, label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  self-test {n} ({label}): {'PASS' if passed else f'FAIL {detail}'}")

    # 1: the probe FINDS a planted positive. Without this, a silent pass proves
    # nothing — the queue could be clean or the probe could be broken.
    planted = [{"at": "x", "target": "claude", "priority": "normal",
                "event": "some_new_producer"}]
    report(1, "planted body-less row is caught", len(bare_label_rows(planted)) == 1)

    # 2: and it does NOT flag a row that carries content.
    fine = [{"event": "sprint-start", "priority": "normal", "sprint": "S-1",
             "title": "a thing"},
            {"event": "x", "priority": "normal", "message": "a body"}]
    report(2, "rows with content are not flagged", not bare_label_rows(fine),
           str(bare_label_rows(fine)))

    # 3: a missing queue REFUSES rather than passing.
    report(3, "missing queue is a failure, not a clean pass",
           check(REPO_ROOT / "does" / "not" / "exist.jsonl") == 1)

    print("pending-pings-render self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    return _self_test() if a.self_test else check()


if __name__ == "__main__":
    sys.exit(main())
