#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::spend-meter-guard (--self-test + --check).
# Read by A3a for daily-brief section 5. A1 in docs/claude/work/MANAGER-CHECKLIST.json
# is the spec; its `note` records three corrections already paid for — read them
# before changing the shape of what this reports.
"""A1 — THE SPEND METER: usage against the WEEKLY PLAN ALLOWANCE, plus per-lane dollars.

THE QUESTION THIS MODULE ANSWERS, restated because it is easy to build the
wrong thing that *looks* like a budget meter: **what does §5 print on a day
nobody has read the usage meter?** The answer must be a distinguishable "not
read since <timestamp>" — never `0`, never a carried-forward figure presented
as current. Every design choice below serves that one sentence.

TWO POPULATIONS, NEVER BLENDED (the category error this row already caught
twice on 2026-09-21 — see A1's `note`):

  (a) THE CONTROL VARIABLE — usage against the DAILY budget (10% of the
      weekly plan allowance). This is the only number the manager is
      accountable for, and it is a PERCENT, not a dollar figure or a token
      count — Settings -> Usage exposes three percent-of-allowance bars
      (session ~5h, weekly, and a separate weekly bar for Fable) with no
      absolute number anywhere, and the allowance is model-weighted, so
      tokens-per-percent is not a constant. Converting to tokens would read
      as precise and drift silently; this module refuses to.
  (b) PER-LANE DOLLARS (`cost_usd`) — API-equivalent pricing read off session
      metadata. The right signal for COMPARING lanes to each other; the wrong
      denominator for pacing against the plan. Reported BESIDE the percent
      meters, never folded into them.

THE DENOMINATOR IS NOT MACHINE-READABLE, AND THIS MODULE MUST NOT INVENT IT.
Only a human can open Settings -> Usage and read the three bars. So the
percent side of this meter is a TINY ON-DISK RECORD of OPERATOR-ENTERED
READINGS (`docs/claude/work/USAGE-READINGS.jsonl`), append-only, one line per
reading, same house style as `pipeline.py`: a state change is a new line, not
an edit, so two sessions appending concurrently merge cleanly by construction,
and a malformed line is reported as `unreadable`, never silently dropped (a
corrupted log must never read as an empty, healthy one).

⚠️ A READING IS NEVER "CURRENT" — IT IS DATED. `render_section_5` always
prints a reading's `read_at` timestamp and its age alongside the percent, so a
three-day-old 40% cannot be mistaken for today's number. A window with no
reading EVER prints `never recorded`, explicitly, not a bare `0%` — `0` here
would collapse "we have not looked" into "usage is zero", which is exactly the
class of bug `docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states" names.

TWO WINDOWS BIND, NOT ONE. The weekly cap is what the 10%/day rule governs;
the ~5-hour SESSION window is what actually walls a lane mid-run — a day's
10% spent as one burst can exhaust the session window while the weekly meter
still reads green. `render_section_5` always shows both, never one alone.
FABLE carries a separate, additive weekly allowance (row E11 decides what is
routed there; this module only reports the pool).

PER-LANE DOLLARS: WHAT IS ACTUALLY REACHABLE, ESTABLISHED RATHER THAN GUESSED
-------------------------------------------------------------------------------
`cost_usd` is real and reachable — from an INTERACTIVE session holding the
Claude Code Remote MCP tools (`get_session` / `list_sessions`). It is **NOT**
reachable from a CI runner or a Routine-fired turn. This is not a fresh
finding: it is already established, independently, in two other places in
this repo (REUSED, NOT RE-DERIVED, per `docs/CLAUDE-RULES-CANONICAL.md` §
"Green is not evidence" / obligation 3) —

  * `scripts/ops/render_daily_brief.py` (its own header): *"mcp__* tools are
    unavailable to CI and to Routine-fired turns"* — which is why that
    generator is deliberately NOT a cron and instead a close-out deliverable
    a manager runs and hands observations to via `--session-notes` /
    `--live-sessions`.
  * `.github/workflows/pr-queue-watch.yml` and `scripts/ops/queue_latency.py`:
    *"an mcp__* tool CI does not hold"*, same conclusion, independently
    arrived at for a different consumer.

And confirmed a third way this session: `get_session` / `list_sessions` ARE
callable here, inside this interactive build lane — MEASURED by calling them
directly against this session's own id and its parent, 2026-09-21. No GitHub
Actions secret or workflow in this repo wires a credential for that API to a
CI runner (checked: `grep -rli` over `.github/workflows/`,
`docs/reference/env-vars.md`, `docs/claude/system-actions.md` — the only
hits are the two files above stating the tool is unreachable there).

So: **per-lane dollars follow the SAME observation pattern `render_daily_brief.py`
already uses for its MCP-only inputs.** This module accepts an OPTIONAL
`--session-costs <path>` (a pasted `get_session`/`list_sessions` result, or
`-` for stdin) from a session that holds the tools; absent, it reports
`not_observed` — explicit and manual, never a fabricated `$0.00`.

Self-test:  python3 scripts/ops/spend_meter.py --self-test
Check:      python3 scripts/ops/spend_meter.py --check
Record:     python3 scripts/ops/spend_meter.py --record --window weekly \\
                --percent 68 --read-at 2026-09-21T14:00:00Z --recorded-by operator
Section 5:  python3 scripts/ops/spend_meter.py --section-5 [--session-costs FILE]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# REUSED, NOT RE-DERIVED: the same tolerant parser render_daily_brief.py
# already uses for a pasted MCP observation (handles the `<other-session …>`
# envelope an MCP tool result arrives wrapped in, and the bare-list fallback).
from scripts.ops.session_registry import _load_observation  # noqa: E402

STORE = Path("docs/claude/work/USAGE-READINGS.jsonl")

#: THE THREE DECLARED METERS. Never a fourth invented ad hoc, and
#: `render_section_5` never renders fewer than these three together — the
#: daily 10% rule binds `weekly`, `session` is what actually walls a lane
#: mid-run, and `fable_weekly` is a second, additive pool.
WINDOWS = ("session", "weekly", "fable_weekly")

_WINDOW_LABEL = {
    "session": "SESSION (~5h) — what actually walls a lane mid-run",
    "weekly": "WEEKLY (main pool) — what the 10%/day rule governs",
    "fable_weekly": "FABLE WEEKLY — a SEPARATE, additive pool (routing: E11)",
}

_ISO_FORMATS = (
    "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ".replace("Z", "+00:00"),
    "%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
)


class MeterError(ValueError):
    """A refusal to accept a reading. Carries WHICH field and WHY."""


def _parse_ts(s: Any, field_name: str) -> datetime:
    if not isinstance(s, str) or not s.strip():
        raise MeterError(f"{field_name}: must be a non-empty ISO timestamp string")
    text = s.strip()
    for fmt in _ISO_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise MeterError(
        f"{field_name}: {s!r} is not a timestamp this can parse "
        f"(expected one of {_ISO_FORMATS[:3]}…)"
    )


# ── the on-disk record of operator-entered readings ─────────────────────────

def validate_reading(rec: dict, *, now: datetime | None = None) -> dict:
    """Refuse a reading that cannot be trusted as a dated observation.

    ⚠️ Every check here exists because the alternative renders a fabricated
    or misleading number as if it were measured — see the module docstring.
    """
    if not isinstance(rec, dict):
        raise MeterError(f"reading must be an object, got {type(rec).__name__}")

    for key in ("window", "percent", "read_at", "recorded_by"):
        if key not in rec:
            raise MeterError(f"missing required field {key!r}")

    if rec["window"] not in WINDOWS:
        raise MeterError(f"window: must be one of {WINDOWS}, got {rec['window']!r}")

    pct = rec["percent"]
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        raise MeterError(f"percent: must be a number, got {type(pct).__name__}")
    if not (0.0 <= float(pct) <= 100.0):
        raise MeterError(f"percent: must be within 0..100, got {pct!r}")

    read_at = _parse_ts(rec["read_at"], "read_at")
    now = now or datetime.now(timezone.utc)
    if read_at > now:
        raise MeterError(
            f"read_at: {rec['read_at']!r} is in the future relative to {now.isoformat()} "
            f"— a reading cannot be dated after it was taken"
        )

    if not isinstance(rec["recorded_by"], str) or not rec["recorded_by"].strip():
        raise MeterError(
            "recorded_by: required and non-empty — only a human can read "
            "Settings > Usage, so a reading with no named source is unverifiable"
        )

    if "note" in rec and rec["note"] is not None and not isinstance(rec["note"], str):
        raise MeterError("note: must be a string when present")

    return rec


@dataclass
class LoadResult:
    """⚠️ Three counts, never collapsed. This is a TIME SERIES, not a keyed
    store — unlike `pipeline.py`, records are never folded to "the last one
    per id"; every reading is preserved so a rate can be measured between
    two of them.
    """

    records: list[dict] = field(default_factory=list)
    unreadable: list[tuple[int, str]] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.unreadable


def read_readings(store: Path = STORE) -> LoadResult:
    res = LoadResult()
    if not store.exists():
        return res
    for lineno, raw in enumerate(store.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            rec = json.loads(line)
            validate_reading(rec, now=datetime.now(timezone.utc) + timedelta(days=1))
        except Exception as exc:  # noqa: BLE001 — the message is the payload
            res.unreadable.append((lineno, f"{type(exc).__name__}: {exc}"))
            continue
        res.records.append(rec)
    return res


def append_reading(rec: dict, store: Path = STORE) -> dict:
    """Validate, then append one record. Atomic; never rewrites history."""
    validate_reading(rec)
    store.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
    with open(store, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return rec


def latest_by_window(records: Iterable[dict]) -> dict[str, dict | None]:
    """The most recent reading PER WINDOW, by `read_at` — not by file order,
    since two windows are recorded independently and can arrive interleaved.
    """
    best: dict[str, dict] = {}
    for rec in records:
        w = rec.get("window")
        if w not in WINDOWS:
            continue
        cur = best.get(w)
        if cur is None or _parse_ts(rec["read_at"], "read_at") > _parse_ts(cur["read_at"], "read_at"):
            best[w] = rec
    return {w: best.get(w) for w in WINDOWS}


def _fmt_age(delta: timedelta) -> str:
    total_min = int(delta.total_seconds() // 60)
    if total_min < 0:
        return "in the future (?!)"
    if total_min < 60:
        return f"{total_min}m"
    hours, minutes = divmod(total_min, 60)
    if hours < 48:
        return f"{hours}h{minutes:02d}m"
    days = hours // 24
    return f"{days}d"


def _readings_for_window(records: list[dict], window: str) -> list[dict]:
    return sorted(
        (r for r in records if r.get("window") == window),
        key=lambda r: _parse_ts(r["read_at"], "read_at"),
    )


def _rate_line(records: list[dict], window: str) -> str | None:
    """A measured, drifting rate between the last TWO readings of one window.

    ⚠️ Never computed from a single point — that would fabricate a slope from
    one dot. Labelled MEASURED-AND-DRIFTING, never as the budget: the task
    this repo's own rules warn against is presenting a derived figure with
    the same confidence as the rule it is being compared to.
    """
    rows = _readings_for_window(records, window)
    if len(rows) < 2:
        return None
    a, b = rows[-2], rows[-1]
    t_a, t_b = _parse_ts(a["read_at"], "read_at"), _parse_ts(b["read_at"], "read_at")
    days = (t_b - t_a).total_seconds() / 86400.0
    if days <= 0:
        return None
    rate = (float(b["percent"]) - float(a["percent"])) / days
    return (
        f"  - measured (between the last two `{window}` readings, "
        f"{days:.2f}d apart): **{rate:+.2f} pct/day** — drifting, sanity-check "
        f"only, never the budget"
    )


# ── per-lane dollars: an OPTIONAL, session-supplied observation ─────────────

def normalise_lane_costs(raw: Any) -> list[dict]:
    """Keep `session_id` / `title` / `cost_usd` from a pasted
    `get_session`/`list_sessions` result.

    Peels the same wrapper keys `scripts/ops/queue_latency.py::normalise_sessions`
    already proved against a real MCP result (`ccr`/`sessions`/`data`/`results`/
    `items`) rather than writing a second, drifting unwrap — this function adds
    `cost_usd`, which that one has no reason to carry.
    """
    node = raw
    for _ in range(4):
        if not isinstance(node, dict):
            break
        for key in ("sessions", "data", "results", "items", "ccr"):
            inner = node.get(key)
            if isinstance(inner, (list, dict)):
                node = inner
                break
        else:
            break
        if isinstance(node, list):
            break
    if isinstance(node, dict):
        entries = [node]
    elif isinstance(node, list):
        entries = node
    else:
        entries = []

    out: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        row = entry.get("ccr") if isinstance(entry.get("ccr"), dict) else entry
        sid = row.get("id") or row.get("session_id")
        if not isinstance(sid, str) or not sid.strip() or sid in seen:
            continue
        seen.add(sid)
        usage = ((row.get("external_metadata") or {}).get("usage")) or {}
        cost = usage.get("cost_usd")
        out.append({
            "session_id": sid.strip(),
            "title": row.get("title"),
            # ⚠️ `None` when unknown, NEVER `0.0` — a session this account
            # cannot see the full record for (e.g. another user's, relayed
            # inside an `<other-session>` envelope) is "we do not know its
            # cost", not "it cost nothing".
            "cost_usd": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
            "tags": row.get("tags") if isinstance(row.get("tags"), list) else [],
        })
    return out


# ── rendering: what A3a's section 5 calls ───────────────────────────────────

def render_section_5(
    log: LoadResult,
    *,
    lane_costs: list[dict] | None,
    lane_costs_state: str = "not_observed",
    today: datetime | None = None,
) -> list[str]:
    """Brief section 5 — SPEND. Consumed by A3a.

    `lane_costs_state` follows the same three-state vocabulary
    `render_daily_brief.py` already uses for its MCP-only inputs: `read`
    (an observation was supplied and parsed), `unreadable` (supplied but
    would not parse), `not_observed` (nothing here could look — expected
    outside an interactive/manager session; see the module docstring).
    """
    now = today or datetime.now(timezone.utc)
    L = ["## §5 — SPEND", ""]

    if not log.healthy:
        L += [
            f"> ⚠️ **{len(log.unreadable)} READING(S) COULD NOT BE PARSED** — "
            f"the meters below are a FLOOR over what parsed, not the whole log.",
            "",
        ]
        for lineno, why in log.unreadable[:10]:
            L.append(f"> - line {lineno}: {why}")
        L.append("")

    L += [
        "**(a) THE CONTROL VARIABLE** — usage against the DAILY budget "
        "(10% of the weekly plan allowance). Operator-entered; only a human "
        "can read Settings > Usage. Never `0%` for a window nobody has "
        "read — that would say \"usage is zero\" when the truth is "
        "\"nobody looked\".",
        "",
    ]
    latest = latest_by_window(log.records)
    for w in WINDOWS:
        rec = latest[w]
        L.append(f"- **{_WINDOW_LABEL[w]}**")
        if rec is None:
            L.append(
                "  - **never recorded** — no operator reading exists for this "
                "window. Run `spend_meter.py --record --window "
                f"{w} --percent <p> --read-at <ISO> --recorded-by <who>` "
                "after reading Settings > Usage."
            )
            continue
        read_at = _parse_ts(rec["read_at"], "read_at")
        age = _fmt_age(now - read_at)
        L.append(
            f"  - **{float(rec['percent']):.1f}%** as of `{rec['read_at']}` "
            f"(**{age} old** — recorded by `{rec['recorded_by']}`; this is a "
            f"DATED reading, not a live value)"
        )
        if rec.get("note"):
            L.append(f"  - note: {rec['note']}")
        rate = _rate_line(log.records, w)
        if rate:
            L.append(rate)
    L.append("")
    L += [
        "> Context, restated because it is easy to misread: the daily rule "
        "is **10% of the weekly allowance per day**, spending into the "
        "resulting 30% margin is AUTHORIZED, and an unspent daily budget is "
        "a failure too, not a neutral outcome. This meter reports facts; it "
        "does not grade them.",
        "",
    ]

    L += [
        "**(b) PER-LANE DOLLARS** (`cost_usd`, API-equivalent pricing) — "
        "context for COMPARING lanes to each other. NEVER the budget "
        "denominator; never blended with (a).",
        "",
    ]
    if lane_costs_state != "read":
        note = {
            "not_observed": (
                "◻️ **not_observed** — `cost_usd` is reachable only from an "
                "interactive session holding `get_session`/`list_sessions` "
                "(the Claude Code Remote MCP tools), never from a CI runner "
                "or a Routine-fired turn — established in "
                "`scripts/ops/render_daily_brief.py` and "
                "`scripts/ops/queue_latency.py`, and reconfirmed by this "
                "module calling those tools directly, 2026-09-21. Supply "
                "`--session-costs <file>` from a manager/build-lane session "
                "that pulled `list_sessions`."
            ),
            "unreadable": (
                "⛔ **unreadable** — `--session-costs` was supplied and would "
                "not parse. **We could not look**, this is not \"no lanes ran\"."
            ),
        }.get(lane_costs_state, f"`{lane_costs_state}`")
        L += [note, ""]
    else:
        rows = lane_costs or []
        known = [r for r in rows if r.get("cost_usd") is not None]
        total = sum(r["cost_usd"] for r in known)
        L.append(
            f"{len(known)} of {len(rows)} lane(s) in the supplied observation "
            f"carry a known `cost_usd`; their sum is **${total:.2f}**."
        )
        if len(known) < len(rows):
            L.append(
                f"  - {len(rows) - len(known)} lane(s) have **no known cost** "
                "(never rendered as $0.00) — excluded from the sum, not "
                "assumed free."
            )
        for r in sorted(known, key=lambda r: r["cost_usd"], reverse=True)[:20]:
            L.append(f"  - `{r['session_id']}` — {r.get('title') or '(untitled)'} "
                     f"· **${r['cost_usd']:.2f}**")
        L.append("")

    L += [
        "> Scope of this meter: attributed to this repo's Claude Code lanes "
        "only. A1's checklist note records a 2026-09-21 measurement that "
        "this was ~= the whole account pool — that mix is NOT re-measured "
        "by this module and may have changed since; verify against "
        "Settings > Usage 'What's using your week' before treating lane "
        "accounting as the total.",
        "",
    ]
    return L


# ─────────────────────────────────────────────────────────────── self-test ──
def _selftest() -> int:
    failures: list[str] = []

    def check(label: str, cond: bool) -> None:
        print(f"  {'PASS' if cond else 'FAIL'}: {label}")
        if not cond:
            failures.append(label)

    def base(**over: Any) -> dict:
        rec = {
            "window": "weekly",
            "percent": 42.0,
            "read_at": "2026-09-21T14:00:00Z",
            "recorded_by": "operator",
        }
        rec.update(over)
        return rec

    now = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)

    print("— reading validation, each refusal named —")
    check("a well-formed reading validates (positive control)",
          validate_reading(base(), now=now) is not None)

    def refuses(label: str, rec: dict, needle: str) -> None:
        try:
            validate_reading(rec, now=now)
        except MeterError as exc:
            check(f"{label} (says why: {needle!r})", needle in str(exc))
        else:
            check(f"{label} — REFUSED", False)

    refuses("unknown window is refused", base(window="monthly"), "window")
    refuses("percent > 100 is refused", base(percent=142), "percent")
    refuses("percent < 0 is refused", base(percent=-1), "percent")
    refuses("a bool percent is refused (bool is an int subclass)",
            base(percent=True), "percent")
    refuses("an unparseable read_at is refused", base(read_at="soon"), "read_at")
    refuses("a future read_at is refused",
            base(read_at="2026-09-22T00:00:00Z"), "future")
    refuses("an empty recorded_by is refused", base(recorded_by="  "), "recorded_by")
    refuses("a missing field is refused", {"window": "weekly"}, "missing required field")

    print("— fable is a declared window like any other —")
    check("fable_weekly validates", validate_reading(base(window="fable_weekly"), now=now) is not None)

    print("— append-only round trip: EVERY reading kept, never folded —")
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "R.jsonl"
        append_reading(base(read_at="2026-09-18T14:00:00Z"), store)
        append_reading(base(read_at="2026-09-21T14:00:00Z", percent=70.0), store)
        append_reading(base(window="session", read_at="2026-09-21T14:30:00Z", percent=12.0), store)
        res = read_readings(store)
        check("all three readings are preserved (time series, not folded)",
              len(res.records) == 3)
        latest = latest_by_window(res.records)
        check("latest_by_window picks the later weekly reading",
              latest["weekly"]["percent"] == 70.0)
        check("…and a window with no reading is None, never a fabricated row",
              latest["fable_weekly"] is None)

        print("— rate: never fabricated from one point, computed from two —")
        check("no rate line from a single reading",
              _rate_line(res.records, "session") is None)
        rate = _rate_line(res.records, "weekly")
        check("a rate line exists once two readings exist, and is labelled measured",
              rate is not None and "measured" in rate and "drifting" in rate)

        print("— malformed line handling —")
        store.write_text(store.read_text() + "{not json\n")
        res2 = read_readings(store)
        check("⚠️ a malformed line is REPORTED, never skipped silently",
              len(res2.unreadable) == 1 and len(res2.records) == 3)
        check("…and healthy is False so nothing can call this clean",
              not res2.healthy)

        empty = read_readings(Path(td) / "missing.jsonl")
        check("a missing store is readable-and-empty, not an error",
              empty.healthy and not empty.records)

    print("— append() refuses on the write path too —")
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "R.jsonl"
        try:
            append_reading(base(window="bogus"), store)
        except MeterError:
            check("append_reading() refuses an invalid reading", True)
        else:
            check("append_reading() refuses an invalid reading", False)
        check("…and wrote NOTHING when it refused",
              not store.exists() or store.read_text() == "")

    print("— section 5: the one sentence this module exists to get right —")
    empty_log = LoadResult()
    rendered_empty = "\n".join(render_section_5(empty_log, lane_costs=None, today=now))
    check("⚠️ NEVER a fabricated reading value when nothing has been recorded",
          "**0.0%**" not in rendered_empty and "**0%**" not in rendered_empty)
    check("…every window says it was never recorded",
          rendered_empty.count("never recorded") == len(WINDOWS))
    check("…and per-lane dollars declare not_observed rather than $0.00",
          "not_observed" in rendered_empty and "$0.00" not in rendered_empty)

    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "R.jsonl"
        stale_ts = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        append_reading(base(read_at=stale_ts, percent=55.0), store)
        log = read_readings(store)
        rendered_stale = "\n".join(render_section_5(log, lane_costs=None, today=now))
        check("a stale reading renders its AGE and is never presented as live",
              "3d old" in rendered_stale and "as of" in rendered_stale)
        check("…a healthy log's section 5 does NOT say 'COULD NOT BE PARSED' "
              "(negative control)",
              "COULD NOT BE PARSED" not in rendered_stale)

        lane_rows = [
            {"session_id": "session_A", "title": "A1 lane", "cost_usd": 3.5, "tags": []},
            {"session_id": "session_B", "title": "unknown-cost lane", "cost_usd": None, "tags": []},
        ]
        rendered_lanes = "\n".join(render_section_5(
            log, lane_costs=lane_rows, lane_costs_state="read", today=now))
        check("supplied lane costs sum only the KNOWN ones and state the population",
              "$3.50" in rendered_lanes and "1 of 2 lane" in rendered_lanes)
        check("…an unknown-cost lane is named as unknown, never folded into $0",
              "no known" in rendered_lanes)

    print()
    if failures:
        print(f"SELF-TEST FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELF-TEST PASS")
    return 0


def _check(store: Path) -> int:
    """Validate every reading in the real store. Used by the guard."""
    res = read_readings(store)
    if res.unreadable:
        print(f"::error::spend_meter: {len(res.unreadable)} UNREADABLE reading(s):")
        for lineno, why in res.unreadable:
            print(f"  line {lineno}: {why}")
        return 1
    latest = latest_by_window(res.records)
    print(json.dumps(
        {w: (r["read_at"] if r else None) for w, r in latest.items()},
        indent=2, sort_keys=True,
    ))
    print(f"spend_meter: clean — {len(res.records)} reading(s) validated")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--check", action="store_true", help="validate every reading in the store")
    ap.add_argument("--store", default=str(STORE))

    ap.add_argument("--record", action="store_true", help="append one operator-entered reading")
    ap.add_argument("--window", choices=WINDOWS)
    ap.add_argument("--percent", type=float)
    ap.add_argument("--read-at", help="ISO timestamp for WHEN the operator read Settings > Usage")
    ap.add_argument("--recorded-by", default="operator")
    ap.add_argument("--note", default=None)

    ap.add_argument("--section-5", action="store_true", help="render brief section 5")
    ap.add_argument("--session-costs", default=None,
                     help="path to a pasted get_session/list_sessions result, or '-' for stdin")
    args = ap.parse_args(argv)

    if args.self_test:
        return _selftest()

    store = Path(args.store)

    if args.check:
        return _check(store)

    if args.record:
        if not (args.window and args.percent is not None and args.read_at):
            print("spend_meter: --record needs --window --percent --read-at", file=sys.stderr)
            return 2
        try:
            rec = append_reading({
                "window": args.window,
                "percent": args.percent,
                "read_at": args.read_at,
                "recorded_by": args.recorded_by,
                "note": args.note,
            }, store)
        except MeterError as exc:
            print(f"spend_meter: REFUSED — {exc}", file=sys.stderr)
            return 1
        print(f"spend_meter: recorded {rec['window']} {rec['percent']}% "
              f"as of {rec['read_at']} (by {rec['recorded_by']})")
        return 0

    if args.section_5:
        log = read_readings(store)
        lane_costs: list[dict] | None = None
        lane_state = "not_observed"
        if args.session_costs:
            try:
                raw = _load_observation(args.session_costs)
                lane_costs = normalise_lane_costs(raw) if raw is not None else []
                lane_state = "read"
            except (OSError, ValueError):
                lane_state = "unreadable"
        print("\n".join(render_section_5(log, lane_costs=lane_costs, lane_costs_state=lane_state)))
        return 0 if log.healthy else 1

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
