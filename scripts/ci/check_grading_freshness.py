#!/usr/bin/env python3
"""The GRADED hop of the trade pipeline must not go quiet — JC-SA-01 (2026-09-29).

ALERT-ONLY (operator, 2026-09-29 ~13:50Z, "let's do b")
-------------------------------------------------------
Originally a stale grade FAILED every PR. Grading going stale is not the fault
of an unrelated PR, so the guard now has three modes:

  default   (the PR / run_guards path) — prints a GitHub ``::warning::``
            annotation when grades are stale or unreadable and ALWAYS exits 0.
  --strict  the raw exit codes below (0 fresh / 1 stale / 2 could not look).
  --alert   the daily scheduled path (.github/workflows/grading-freshness-alert.yml):
            when stale OR unreadable, dispatches ONE ``send-ping`` system-action
            (Telegram). One run per day = one ping per day while stale, never
            one per PR. Exit 0 once the ping is dispatched, 1 if it could not be.

Detection is unchanged. ⚠️ "Could not read" still warns loudly and is never
printed as fresh: only a fresh grade is silent.

WHAT THIS CATCHES
-----------------
``comms/claude_strategy_scores.jsonl`` is the last hop of the trade pipeline
(signal → order → fill → managed exit → close reason → **graded**). It went
21 days without a row: ``max(reviewed_at)`` = 2026-09-08T10:50:51Z, measured
2026-09-29 over 3,446 graded rows on ``origin/main``, while 498 non-backtest
trades closed across 7 accounts (diag journal pull, ``closed_at`` >=
2026-09-08). Nothing failed, because nothing looked.

ROOT CAUSE, SO THE THRESHOLD MEANS SOMETHING
--------------------------------------------
Grading was never on a schedule. Every commit that ever touched the score file
was a ``/system-review`` (or ``/performance-review``) session running the
``grade-closed-trades`` system-action by hand; the last was #11379 on
2026-09-08. The next ``/system-review`` (#11818, 2026-09-11) did not grade, and
the 2026-09-21 reset retired ``/system-review`` — and with it the only
"grading-freshness guard", which lived in that skill's prose. The grader itself
still works: re-run against the 2026-09-29 journal pull it emits 491 rows.
The schedule is now ``.github/workflows/grade-closed-trades.yml`` (daily).

THE RULE
--------
``max(reviewed_at)`` older than ``MAX_AGE_DAYS`` (3) is STALE (``--strict``: FAILS;
default: warns). Three days is
three missed daily runs — not a delay, a stop (the same margin
``check_cadence_liveness.STALE_MARGIN`` uses). It lives in CI rather than only
in the grading workflow because a scheduled job can be the thing that did not
fire (``check_cadence_liveness.py`` records why).

⚠️ ONE FALSE-POSITIVE SHAPE, STATED: if no trade closes on any of the 7 accounts
for 3 days, the grader correctly appends nothing and this reds. On this fleet
(bybit_1 alone closed 408 trades in 21 days) three close-free days is itself a
pipeline outage worth a look, so it is left loud on purpose.

THREE OUTCOMES, NEVER COLLAPSED (``--strict`` exit codes)
---------------------------------------------------------
  exit 0  fresh — the newest grade is within the window
  exit 1  stale — the newest grade is older than the window
  exit 2  could not look — file missing, or no row carries a parseable
          ``reviewed_at``. ⚠️ Not ``fresh``: "we did not look" is not "we
          looked and found it current".

Run ``--self-test`` to plant each outcome.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]
SCORES = REPO / "comms" / "claude_strategy_scores.jsonl"
MAX_AGE_DAYS = 3

FRESH, STALE, COULD_NOT_LOOK = 0, 1, 2


def _parse_ts(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def newest_reviewed_at(path: Path) -> tuple[Optional[datetime], int]:
    """(max reviewed_at, rows carrying one). (None, 0) when there is none."""
    newest: Optional[datetime] = None
    n = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            ts = _parse_ts(row.get("reviewed_at")) if isinstance(row, dict) else None
            if ts is None:
                continue
            n += 1
            if newest is None or ts > newest:
                newest = ts
    return newest, n


def check(path: Path, now: datetime, max_age_days: int = MAX_AGE_DAYS) -> tuple[int, str]:
    if not path.exists():
        return COULD_NOT_LOOK, f"could not look: {path} does not exist"
    newest, n = newest_reviewed_at(path)
    if newest is None:
        return COULD_NOT_LOOK, f"could not look: no row in {path} has a parseable reviewed_at"
    age = now - newest
    days = age.total_seconds() / 86400
    head = (f"max(reviewed_at)={newest.isoformat()} over {n} graded rows; "
            f"age {days:.1f}d vs limit {max_age_days}d")
    if age > timedelta(days=max_age_days):
        return STALE, (f"STALE — {head}. The grading hop has stopped: check the "
                       f"grade-closed-trades.yml schedule's last run.")
    return FRESH, f"fresh — {head}"


def annotate(code: int, msg: str) -> str:
    """The GitHub ``::warning::`` line for a non-fresh outcome; '' when fresh."""
    if code == FRESH:
        return ""
    title = "grading STALE" if code == STALE else "grading UNREADABLE (not fresh)"
    return f"::warning title={title}::grading-freshness: {msg}"


def report(path: Path, now: datetime, max_age_days: int, strict: bool) -> int:
    """Print the result. Default (alert-only) exits 0 whatever the outcome."""
    code, msg = check(path, now, max_age_days)
    print(f"grading-freshness: {msg}")
    warning = annotate(code, msg)
    if warning:
        print(warning)
    return code if strict else 0


def alert(path: Path, now: datetime, max_age_days: int = MAX_AGE_DAYS,
          runner=subprocess.run) -> int:
    """Daily path: ONE send-ping when stale or unreadable; quiet when fresh."""
    code, msg = check(path, now, max_age_days)
    print(f"grading-freshness: {msg}")
    if code == FRESH:
        return 0
    print(annotate(code, msg))
    kind = "STALE" if code == STALE else "UNREADABLE"
    text = (f"Grading {kind}: {msg} Check the grade-closed-trades.yml runs: "
            "https://github.com/benbaichmankass/Metis-Insights/actions/workflows/grade-closed-trades.yml")
    cmd = ["gh", "workflow", "run", "system-actions.yml", "-f", "action=send-ping",
           "-f", f"message={text}", "-f", "priority=high", "-f", "target=claude"]
    try:
        res = runner(cmd, check=False)
    except OSError as exc:
        print(f"::error::could not dispatch the send-ping system-action: {exc}")
        return 1
    if getattr(res, "returncode", 0) != 0:
        print("::error::could not dispatch the send-ping system-action")
        return 1
    return 0


def _self_test() -> int:
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    ok = True
    with tempfile.TemporaryDirectory() as d:
        def plant(name: str, rows: list) -> Path:
            p = Path(d) / name
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
            return p
        # (label, path, detected outcome, warning expected)
        cases = [
            ("fresh", plant("f.jsonl", [{"_meta": "x"},
                                         {"reviewed_at": "2026-09-01T00:00:00+00:00"},
                                         {"reviewed_at": "2026-09-28T00:00:00Z"}]), FRESH, False),
            ("stale (the 2026-09-29 state)",
             plant("s.jsonl", [{"reviewed_at": "2026-09-08T10:50:51.741903+00:00"}]), STALE, True),
            ("no parseable reviewed_at", plant("n.jsonl", [{"_meta": "x"}, {"reviewed_at": "garbage"}]),
             COULD_NOT_LOOK, True),
            ("missing file", Path(d) / "absent.jsonl", COULD_NOT_LOOK, True),
        ]
        for label, path, want, want_warn in cases:
            got, _ = check(path, now)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = report(path, now, MAX_AGE_DAYS, strict=False)
            out = buf.getvalue()
            warned = "::warning" in out
            passed = got == want and warned == want_warn and rc == 0
            ok &= passed
            print(f"  [{'ok' if passed else 'FAIL'}] {label}: detected {got} (want {want}), "
                  f"warning={warned} (want {want_warn}), default exit {rc} (want 0)")
    print("self-test: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--path", type=Path, default=SCORES)
    ap.add_argument("--now", help="ISO timestamp to evaluate at (default: now, UTC)")
    ap.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    ap.add_argument("--strict", action="store_true",
                    help="exit 0 fresh / 1 stale / 2 could not look (default: always 0, warn only)")
    ap.add_argument("--alert", action="store_true",
                    help="daily path: dispatch ONE send-ping when stale or unreadable")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    now = _parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print(f"invalid --now: {args.now!r}", file=sys.stderr)
        return COULD_NOT_LOOK
    if args.alert:
        return alert(args.path, now, args.max_age_days)
    return report(args.path, now, args.max_age_days, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
