#!/usr/bin/env python3
# wiring: called once per hourly pass by scripts/ops/work_digest_now.py::run
# (deploy/ict-work-digest.timer — the ONE scheduled carrier; this module has no
# unit of its own, by decision, see the docstring). Receipt readable at
# /api/diag/log_file?name=attention_watch_receipt. Guard:
# scripts/ci/run_guards.py::attention-watch (--self-test).
"""THE ATTENTION WATCH — the VM tells people when something needs them.

Design of record: ``docs/plans/work-system-2026-10-04.md`` (WORK-SYSTEM row).

WHY THIS EXISTS (measured 2026-10-04 by Manager Session 2026-10-04): 366
pipeline items due, 151 unrouted, 25 ``ask_operator`` items due that had never
reached the operator; the R5 weekly soak grade's last record is 2026-09-26 and
this week's run landed nothing; the brief at ``GET /api/bot/work/brief`` was
737 KB and nobody read it. Everything that was supposed to ask for attention
was a PAGE someone had to choose to open. This module PUSHES.

ONE MESSAGE, ONE CARRIER (operator decision 2026-10-04 ~15:30Z, relayed by the
manager): fold into the EXISTING hourly digest — ``ict-work-digest.timer`` →
``work_digest_now.py``. No timer of its own. ``work_digest_now.run()`` calls
``prepare()``, PREPENDS the block to its change digest, sends ONE message, and
calls ``commit()`` only after the enqueue succeeded. The block puts the
ACTIONABLE things first:

  * NEW since the last digest, marked 🆕 (edge items, only on change):
      - a new ``ask_operator`` pipeline item;
      - a soak moving into ``ready`` / ``overdue`` / ``dead``
        (``scripts/ops/soak_state.py::soak_states()``, SOAK-WATCH's live
        computation — never computed here);
      - a silence alarm breaching (re-sent at most every ``REALERT_HOURS``)
        or clearing. The alarms cover: the R5 weekly soak grade
        (``SOAK_GRADE_MAX_DAYS``), research results (``RESEARCH_MAX_HOURS``),
        quiet ``in_flight`` rows (``INFLIGHT_STALE_DAYS``), the checklist
        unwritten (``MANAGER_SILENT_HOURS``), and the 05:30Z work report
        missing, stale, errored or oversized.
  * once a day at/after ``DIGEST_HOUR_UTC``: the ranked summary (counts + top
    items, ask_operator first);
  * otherwise ONE line: "no new actionable items", plus open counts.

So a new actionable thing is unmissable and the steady state is quiet.

MANAGER READS IT: a session cannot receive Telegram, so each committed block is
appended to ``runtime_logs/work_reports/digest_log.jsonl`` and served at
``GET /api/bot/work/report?since=<iso>``.

NEVER COLLAPSED: every probe returns ``ok`` / ``breached`` / ``unknown``.
``unknown`` (we could not look — shallow clone, unreadable file, missing
module) is reported as such and is never read as ``ok``. A digest that cannot
read the pipeline says so in its first line.

Read-only against the repo. Its only writes are under ``runtime_logs/``
(state, receipt, digest log) and, standalone only, the ping inbox. Sends go to the
@claude_ict_comms_bot inbox (``send_ping.enqueue(target="claude")``) — the
operator's Claude-comms channel, separate from trade alerts.

Usage::

    python3 scripts/ops/attention_watch.py              # one standalone pass (sends)
    python3 scripts/ops/attention_watch.py --dry-run    # print, send nothing, keep state
    python3 scripts/ops/attention_watch.py --digest-now # force today's digest
    python3 scripts/ops/attention_watch.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.ops import pipeline  # noqa: E402
from scripts.ops import prop_silence  # noqa: E402

# ⚠️ Anchored to the repo root, like work_digest_now.py: the unit carries no
# data-dir drop-in and diag reads the receipt through repo_root().
STATE = REPO_ROOT / "runtime_logs" / "attention_watch_state.json"
RECEIPT = REPO_ROOT / "runtime_logs" / "attention_watch_receipt.json"

CHECKLIST = REPO_ROOT / "docs" / "claude" / "work" / "MANAGER-CHECKLIST.json"
SOAK_GRADE_DIR = REPO_ROOT / "comms" / "research" / "soak_book_grade"
RESEARCH_RESULTS = "research/results"

# Bounds. Constants, so moving one is a reviewed diff, not a tuning knob.
DIGEST_HOUR_UTC = 6          # before the operator's day; after the 03:10Z grade slot
SOAK_GRADE_MAX_DAYS = 8      # weekly (Sun 03:00Z) + one day of grace
RESEARCH_MAX_HOURS = 36      # dispatcher lands results several times a day when healthy
INFLIGHT_STALE_DAYS = 3      # an in_flight row nobody has touched in 3 days is not in flight
MANAGER_SILENT_HOURS = 30    # one daily review + 6h grace
REALERT_HOURS = 24
TOP_N = 8
ALERT_SOAK_STATES = ("ready", "overdue", "dead")

OK, BREACHED, UNKNOWN = "ok", "breached", "unknown"


# ── small helpers ────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                             text=True, check=False, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return None
    return out.stdout if out.returncode == 0 else None


def _is_shallow() -> bool | None:
    out = _git("rev-parse", "--is-shallow-repository")
    return None if out is None else out.strip() == "true"


def _last_commit_time(path: str) -> datetime | None:
    out = _git("log", "-1", "--format=%ct", "--", path)
    if not out or not out.strip():
        return None
    return datetime.fromtimestamp(int(out.strip()), tz=timezone.utc)


def _short(s: Any, n: int = 140) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        print(f"attention-watch: WARNING could not write {path.name}: {exc}")


# ── pipeline: ranking + ask_operator ─────────────────────────────────────────
def rank_attention(items: list[dict], today: date) -> list[dict]:
    """Due items, ranked for a HUMAN: ask_operator first, then unrouted
    oldest-filed first, then routed-and-still-due oldest first."""
    rows = pipeline.due(items, today)
    far = date.max

    def key(i: dict) -> tuple:
        return (i.get("next_action") != "ask_operator",
                i.get("state") == "routed",
                pipeline.filed_date(i) or far, i.get("id", ""))
    return sorted(rows, key=key)


def _ranked(res: Any, items: list[dict], today: date) -> list[dict]:
    """Use BRIEF-FIX's ranking when it exists (PR #16384), so the digest, the
    report and the brief rank identically; fall back to ``rank_attention``."""
    try:
        from scripts.ops import render_daily_brief as rdb  # noqa: PLC0415
        fn = getattr(rdb, "ranked_items", None)
        if callable(fn):
            return list(fn(res, today))
    except Exception as exc:  # noqa: BLE001 — fall back, loudly
        print(f"attention-watch: render_daily_brief.ranked_items failed, using fallback: {exc}")
    return rank_attention(items, today)


def open_ask_operator(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("next_action") == "ask_operator"
            and i.get("state") not in pipeline.TERMINAL_STATES]


# ── soak state (owned by SOAK-WATCH; consumed, never computed, here) ─────────
# SETTLED by the manager 2026-10-04 15:25Z for all three lanes: CONTRACTS are a
# committed file SOAK-WATCH owns; STATE is COMPUTED LIVE on the VM by
# ``scripts/ops/soak_state.py::soak_states()`` (contracts + journal DB). The brief,
# the report and this pass all call it. Never a session-written state field.
SOAK_STATES = ("accruing", "ready", "overdue", "dead", "unknown")


def read_soak_states() -> tuple[str, list[dict]]:
    """``(read_state, rows)``. read_state ∈ read / absent / unreadable.
    ``absent`` = we looked and the module is not there — never "no soaks"."""
    try:
        from scripts.ops import soak_state  # noqa: PLC0415
    except ImportError:
        return "absent", []
    fn = getattr(soak_state, "soak_states", None)
    if not callable(fn):
        return "absent", []
    try:
        rows = [r for r in fn() if isinstance(r, dict)]
    except Exception as exc:  # noqa: BLE001 — a broken producer is "unreadable", loud
        print(f"attention-watch: soak_state.soak_states() raised: {exc}")
        return "unreadable", []
    out = []
    for r in rows:
        st = str(r.get("state") or "unknown")
        out.append({**r, "state": st if st in SOAK_STATES else "unknown"})
    return "read", out


QUEUE_DIR = REPO_ROOT / "research" / "queue"


def read_needs_review(queue_dir: Path | None = None) -> tuple[str, list[dict]]:
    """Units whose mechanical grading could not decide (``grading.needs_review: true``).

    PI-20261006-LCEVL8D5-0003: this bucket was only ``grep -l 'needs_review: true'
    research/queue/*.yaml`` -- reachable only by a session that thought to run it.
    Returns ``("read", rows)`` / ``("absent", [])`` (no queue dir) /
    ``("unreadable", [])`` -- never an empty list for a failed read.
    """
    d = queue_dir or QUEUE_DIR
    if not d.is_dir():
        return "absent", []
    try:
        import yaml
    except ImportError:
        return "unreadable", []
    rows: list[dict] = []
    for f in sorted(d.glob("*.yaml")):
        try:
            text = f.read_text(encoding="utf-8")
            if "needs_review: true" not in text:
                continue
            u = yaml.safe_load(text)
        except (OSError, yaml.YAMLError):
            return "unreadable", []
        g = (u or {}).get("grading") if isinstance(u, dict) else None
        if isinstance(g, dict) and g.get("needs_review") is True:
            rows.append({"id": str(u.get("id") or f.stem), "graded_at": str(g.get("graded_at") or ""),
                         "reason": g.get("reason") or g.get("note") or ""})
    return "read", rows


def _age_days(graded_at: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(graded_at[:10])).days
    except ValueError:
        return None


def _needs_review_line(v: dict) -> str | None:
    read, rows = v.get("needs_review_read", "absent"), v.get("needs_review", [])
    if read == "unreadable":
        return "❔ needs_review bucket: research/queue UNREADABLE (not zero — not measured)"
    if read != "read" or not rows:
        return None
    ages = [a for a in (_age_days(r["graded_at"], v["today"]) for r in rows) if a is not None]
    oldest = f", oldest flagged {max(ages)}d ago" if ages else ""
    ids = ", ".join(r["id"] for r in rows[:4])
    return (f"🔎 {len(rows)} research unit(s) need a read (grading.needs_review){oldest}: "
            f"{ids}{' …' if len(rows) > 4 else ''}")


def _soak_key(r: dict) -> str:
    return str(r.get("id") or f"{r.get('account', '?')}:{r.get('leg', '?')}")


# ── silence probes ───────────────────────────────────────────────────────────
def probe_soak_grade(now: datetime) -> dict:
    files = sorted(SOAK_GRADE_DIR.glob("*.json")) if SOAK_GRADE_DIR.is_dir() else []
    dated = []
    for f in files:
        try:
            dated.append(datetime.strptime(f.stem[:10], "%Y-%m-%d").date())
        except ValueError:
            continue
    if not dated:
        return {"status": UNKNOWN if not SOAK_GRADE_DIR.is_dir() else BREACHED,
                "detail": f"no dated record under {SOAK_GRADE_DIR.relative_to(REPO_ROOT)}"}
    newest = max(dated)
    age = (now.date() - newest).days
    st = BREACHED if age >= SOAK_GRADE_MAX_DAYS else OK
    return {"status": st, "detail": f"newest R5 weekly soak grade {newest} ({age}d old; "
                                    f"bound {SOAK_GRADE_MAX_DAYS}d)"}


def probe_research(now: datetime, shallow: bool | None) -> dict:
    t = _last_commit_time(RESEARCH_RESULTS)
    if t is None:
        return {"status": UNKNOWN, "detail": "could not read the last research/results commit"
                + (" (shallow clone)" if shallow else "")}
    hours = (now - t).total_seconds() / 3600
    st = BREACHED if hours > RESEARCH_MAX_HOURS else OK
    return {"status": st, "detail": f"last research result landed {hours:.0f}h ago "
                                    f"(bound {RESEARCH_MAX_HOURS}h)"}


def probe_manager(now: datetime) -> dict:
    t = _last_commit_time(str(CHECKLIST.relative_to(REPO_ROOT)))
    if t is None:
        return {"status": UNKNOWN, "detail": "could not read the checklist's last commit"}
    hours = (now - t).total_seconds() / 3600
    st = BREACHED if hours > MANAGER_SILENT_HOURS else OK
    return {"status": st, "detail": f"checklist last written {hours:.0f}h ago "
                                    f"(bound {MANAGER_SILENT_HOURS}h — no daily review?)"}


PROP_TRIP_MAX_HOURS = 2      # the trip ping fires once; a tripped feed still trading nothing 2h later re-pages


def probe_prop_feed(now: datetime) -> dict:
    """A tripped prop feed means that account's executor places NOTHING (it exits
    before running while ``feed/tripped`` exists) and the silence probe cannot see
    it (it keys on fills). Reads the markers scripts/ops/prop_feed_tick.sh writes;
    no base dir = we could not look (UNKNOWN), never OK."""
    base = Path(os.environ.get("PROP_BROWSER_BASE") or Path.home() / ".cache" / "metis-prop-browser")
    if not base.is_dir():
        return {"status": UNKNOWN, "detail": f"no prop browser base dir ({base.name})"}
    marks = [*base.glob("accounts/*/feed/tripped"), base / "feed" / "tripped"]
    hit = []
    for m in marks:
        try:
            age = (now - datetime.fromtimestamp(m.stat().st_mtime, timezone.utc)).total_seconds() / 3600
        except OSError:
            continue
        name = "breakout_1" if m.parent.parent == base else m.parent.parent.name
        hit.append((age, f"{name} ({age:.0f}h: {m.read_text(errors='replace')[:90].strip()})"))
    old = [d for a, d in hit if a >= PROP_TRIP_MAX_HOURS]
    return {"status": BREACHED if old else OK,
            "detail": ("tripped, executor idle: " + "; ".join(old) + " — re-arm: breakout-login-check apply: reset-feed")
            if old else f"{len(hit)} prop feed(s) tripped under {PROP_TRIP_MAX_HOURS}h" if hit else "no prop feed tripped"}


REPORT_MAX_AGE_HOURS = 26     # daily 05:30Z + grace
REPORT_DUE_UTC = (5, 50)      # today's report must exist by 05:50Z (review at 05:52Z)
#: A report nobody can read in one sitting is the 737 KB brief again. BRIEF-FIX
#: caps the brief at 16 KB; the report adds the attention summary on top.
REPORT_MAX_BYTES = 64_000


def probe_report(now: datetime) -> dict:
    """The scheduled work report (scripts/ops/work_report.py) exists, is fresh,
    and has no errored section. An absent or unreadable report is BREACHED:
    the report is an expected signal, and its absence is the finding."""
    from scripts.ops import work_report  # noqa: PLC0415
    st, rep = work_report.read_latest()
    if st != "read" or rep is None:
        return {"status": BREACHED, "detail": f"latest work report {st}"}
    try:
        gen = datetime.fromisoformat(rep["generated_at"])
    except (KeyError, ValueError):
        return {"status": BREACHED, "detail": f"{rep.get('report_id')} has no readable generated_at"}
    age_h = (now - gen).total_seconds() / 3600
    due_today = now.replace(hour=REPORT_DUE_UTC[0], minute=REPORT_DUE_UTC[1], second=0,
                            microsecond=0)
    missed_today = now >= due_today and gen < due_today - timedelta(minutes=30)
    errs = rep.get("errored_sections") or []
    if age_h > REPORT_MAX_AGE_HOURS or missed_today:
        return {"status": BREACHED, "detail": f"latest report {rep['report_id']} is {age_h:.0f}h old "
                                              f"— today's 05:30Z report was not produced"}
    if errs:
        return {"status": BREACHED, "detail": f"{rep['report_id']} has errored section(s): "
                                              f"{', '.join(errs)}"}
    size = rep.get("bytes")
    if isinstance(size, int) and size > REPORT_MAX_BYTES:
        return {"status": BREACHED, "detail": f"{rep['report_id']} is {size // 1000} KB "
                                              f"(> {REPORT_MAX_BYTES // 1000} KB — too long to be read)"}
    return {"status": OK, "detail": f"{rep['report_id']} ({age_h:.0f}h old, all sections built)"}


def _row_line_ranges(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Map row id -> (first, last) 1-based line of its object in the items array.
    Relies on the checklist's indent=2 layout: rows open with ``    {``."""
    out: dict[str, tuple[int, int]] = {}
    start = None
    rid = None
    for n, line in enumerate(lines, 1):
        if line.rstrip() == "    {":
            start, rid = n, None
        elif start is not None and rid is None and line.startswith('      "id": "'):
            rid = line.split('"id": "', 1)[1].split('"', 1)[0]
        elif start is not None and line.rstrip() in ("    }", "    },"):
            if rid:
                out[rid] = (start, n)
            start = None
    return out


def probe_inflight(now: datetime, shallow: bool | None) -> dict:
    """In_flight rows whose checklist text has not changed in INFLIGHT_STALE_DAYS.

    Activity = the newest commit touching any line of the row (``git blame``).
    ⚠️ On a shallow clone blame attributes old lines to the boundary commit,
    which makes a stale row look FRESH — so a shallow clone reads ``unknown``.
    """
    try:
        d = json.loads(CHECKLIST.read_text(encoding="utf-8"))
        text_lines = CHECKLIST.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as exc:
        return {"status": UNKNOWN, "detail": f"checklist unreadable: {exc}", "rows": []}
    inflight = [r for r in d.get("items", []) if r.get("state") == "in_flight"]
    if shallow:
        return {"status": UNKNOWN, "detail": f"{len(inflight)} in_flight rows; clone is "
                "shallow so row age cannot be read", "rows": []}
    blame = _git("blame", "--line-porcelain", "--", str(CHECKLIST.relative_to(REPO_ROOT)))
    if blame is None:
        return {"status": UNKNOWN, "detail": "git blame failed", "rows": []}
    times: list[int] = []
    for line in blame.splitlines():
        if line.startswith("committer-time "):
            times.append(int(line.split()[1]))
    ranges = _row_line_ranges(text_lines)
    stale = []
    for r in inflight:
        rng = ranges.get(r.get("id"))
        if not rng or rng[1] > len(times):
            continue
        last = datetime.fromtimestamp(max(times[rng[0] - 1: rng[1]]), tz=timezone.utc)
        age = (now - last).total_seconds() / 86400
        if age >= INFLIGHT_STALE_DAYS:
            stale.append({"id": r.get("id"), "days": round(age, 1), "lane": r.get("lane")})
    stale.sort(key=lambda x: -x["days"])
    st = BREACHED if stale else OK
    return {"status": st, "rows": stale,
            "detail": f"{len(stale)} of {len(inflight)} in_flight rows untouched "
                      f">= {INFLIGHT_STALE_DAYS}d"
                      + (": " + ", ".join(f"{s['id']} {s['days']:.0f}d" for s in stale[:6])
                         if stale else "")}


# ── the pass ─────────────────────────────────────────────────────────────────
def build(now: datetime | None = None) -> dict:
    now = now or _now()
    today = now.date()
    res = pipeline.load(REPO_ROOT / pipeline.STORE)
    items = list(res.items.values())
    shallow = _is_shallow()
    soak_read, soaks = read_soak_states()
    nr_read, nr_rows = read_needs_review()
    return {
        "needs_review_read": nr_read, "needs_review": nr_rows,
        "now": now, "today": today, "pipeline_readable": res.healthy,
        "pipeline_unreadable": len(res.unreadable),
        "stats": pipeline.stats(res, today),
        "ranked": _ranked(res, items, today),
        "ask_operator": open_ask_operator(items),
        "soak_read": soak_read, "soaks": soaks,
        "probes": {
            "soak_grade": probe_soak_grade(now),
            "research": probe_research(now, shallow),
            "inflight": probe_inflight(now, shallow),
            "manager": probe_manager(now),
            "report": probe_report(now),
            # PROP-SILENCE-ALERTS: idle-fill (warn 14 d / urgent 21 d) per prop
            # account + breakout_2's phone heartbeat. Each carries `level` and
            # `priority`; see scripts/ops/prop_silence.py.
            **prop_silence.all_probes(now),
            "prop_feed": probe_prop_feed(now),
        },
    }


PROBE_LABEL = {
    "soak_grade": "R5 weekly soak grade missing",
    "research": "research results not landing",
    "inflight": "in_flight rows gone quiet",
    "manager": "manager's register not written",
    "report": "scheduled work report missing or broken",
    **prop_silence.LABELS,
    "prop_feed": "a prop account feed is tripped (its executor is not trading)",
}


def render_digest(v: dict) -> str:
    s = v["stats"]
    rep = v["probes"].get("report", {})
    L = [f"📋 Daily summary {v['today']}"
         + (f" — {rep['detail'].split(' ')[0]}" if rep.get("status") == OK else "")]
    if not v["pipeline_readable"]:
        L.append(f"⚠️ {v['pipeline_unreadable']} pipeline record(s) UNREADABLE — counts are a floor.")
    ao_due = [i for i in v["ask_operator"] if pipeline.is_due(i, v["today"])]
    L.append(f"Due {s['due']} · unrouted {s['unrouted']} · needs you {len(ao_due)} "
             f"(open ask_operator {len(v['ask_operator'])})")
    if s["unrouted_alarm"]["breached"]:
        L.append("🚨 routing alarm: " + "; ".join(s["unrouted_alarm"]["breached"]))
    if v["soak_read"] == "read":
        by: dict[str, int] = {}
        for r in v["soaks"]:
            by[str(r.get("state"))] = by.get(str(r.get("state")), 0) + 1
        L.append("Soaks: " + (" · ".join(f"{k} {n}" for k, n in sorted(by.items())) or "none"))
    else:
        L.append(f"Soaks: state source {v['soak_read'].upper()} (not zero — not measured)")
    nr_line = _needs_review_line(v)
    if nr_line:
        L.append(nr_line)
    bad = [k for k, p in v["probes"].items() if p["status"] != OK]
    for k in bad:
        p = v["probes"][k]
        L.append(f"{'🔕' if p['status'] == BREACHED else '❔'} {PROBE_LABEL[k]}: {p['detail']}")
    L.append("")
    L.append(f"Top {min(TOP_N, len(v['ranked']))} of {len(v['ranked'])} due:")
    for i in v["ranked"][:TOP_N]:
        tag = "❓ASK" if i.get("next_action") == "ask_operator" else i.get("state")
        L.append(f"• [{tag}] {i.get('id')}: {_short(i.get('what'), 120)}")
    L.append("Reviewed report: /api/bot/work/report · live: /api/bot/work/brief")
    return "\n".join(L)


def plan_messages(v: dict, state: dict, digest_now: bool = False) -> tuple[list[tuple[str, str]], dict]:
    """Return ``([(priority, body)], new_state)``. Pure — no I/O."""
    now: datetime = v["now"]
    today_s = v["today"].isoformat()
    new = dict(state)
    msgs: list[tuple[str, str]] = []
    first_run = "seen_ask_operator" not in state

    # (b1) new ask_operator items — edge-triggered. The first run SEEDS, but
    # never silently (manager, 2026-10-04: seeding an actionable set without a
    # word is the "things exist and nobody is told" pattern) — it sends ONE
    # count line instead of one line per item.
    seen = set(state.get("seen_ask_operator", []))
    cur = {i["id"]: i for i in v["ask_operator"]}
    fresh = [cur[k] for k in sorted(cur) if k not in seen]
    if fresh and not first_run:
        L = [f"❓ {len(fresh)} new decision(s) for you (ask_operator):"]
        for i in fresh[:6]:
            L.append(f"• {i['id']}: {_short(i.get('what'), 160)}")
        if len(fresh) > 6:
            L.append(f"…and {len(fresh) - 6} more — see the daily digest.")
        msgs.append(("high", "\n".join(L)))
    # ORDER-INDEPENDENT (manager, 2026-10-04 18:16Z): the seed line keys on its
    # OWN marker, not on whether the set was already seeded — so it fires once
    # even if an earlier pass (shipped without it) seeded the set silently.
    if "seed_ask_sent_at" not in state:
        n_due = sum(1 for i in cur.values() if pipeline.is_due(i, v["today"]))
        msgs.append(("high", f"❓ ask_operator at deploy: {len(cur)} open ({n_due} due) — "
                             f"see the report (GET /api/bot/work/report)"))
        new["seed_ask_sent_at"] = now.isoformat()
    new["seen_ask_operator"] = sorted(seen | set(cur))

    # (b2) soak transitions into ready / overdue / dead.
    if v["soak_read"] == "read":
        prior = state.get("soak_states", {})
        cur_s = {_soak_key(r): str(r.get("state")) for r in v["soaks"]}
        moved = [r for r in v["soaks"]
                 if str(r.get("state")) in ALERT_SOAK_STATES
                 and prior.get(_soak_key(r)) != str(r.get("state"))]
        if moved and "soak_states" in state:
            L = [f"🧪 {len(moved)} soak(s) changed state:"]
            for r in moved[:8]:
                L.append(f"• {_soak_key(r)} → {r.get('state')}"
                         f"{' (end ' + str(r.get('end_date')) + ')' if r.get('end_date') else ''}"
                         f" {_short(r.get('reason'), 100)}")
            pri = "high" if any(str(r.get("state")) in ("overdue", "dead") for r in moved) else "normal"
            msgs.append((pri, "\n".join(L)))
        if "seed_soaks_sent_at" not in state:
            # Seed line, same rule as ask_operator above: one count line, keyed
            # on its own marker — and only once the soak source was READ, so an
            # absent/unreadable first pass does not burn it.
            n = {k: sum(1 for s in cur_s.values() if s == k) for k in ALERT_SOAK_STATES}
            msgs.append(("high" if n["overdue"] or n["dead"] else "normal",
                         f"🧪 Soaks at deploy: {n['ready']} ready · {n['overdue']} overdue · "
                         f"{n['dead']} dead (see report)"))
            new["seed_soaks_sent_at"] = now.isoformat()
        new["soak_states"] = cur_s

    # (b3) research units newly needing a read — edge-triggered, same seeding rule
    # as (b1): the first pass sends ONE count line, never silently, never per item.
    if v.get("needs_review_read") == "read":
        cur_nr = {r["id"]: r for r in v.get("needs_review", [])}
        seen_nr = set(state.get("seen_needs_review", []))
        fresh_nr = [cur_nr[k] for k in sorted(cur_nr) if k not in seen_nr]
        if fresh_nr and "seen_needs_review" in state:
            L = [f"🔎 {len(fresh_nr)} research unit(s) newly need a read (needs_review):"]
            for r in fresh_nr[:6]:
                L.append(f"• {r['id']}: {_short(r.get('reason'), 140)}")
            if len(fresh_nr) > 6:
                L.append(f"…and {len(fresh_nr) - 6} more — see the daily digest.")
            msgs.append(("normal", "\n".join(L)))
        if "seed_needs_review_sent_at" not in state:
            msgs.append(("normal", f"🔎 needs_review at deploy: {len(cur_nr)} research unit(s) flagged "
                                   f"(listed in the daily digest)"))
            new["seed_needs_review_sent_at"] = now.isoformat()
        new["seen_needs_review"] = sorted(cur_nr)

    # (c) silence alarms — send on breach, re-send every REALERT_HOURS, one clear.
    alarms = dict(state.get("alarms", {}))
    for k, p in v["probes"].items():
        a = alarms.get(k, {})
        if p["status"] == BREACHED:
            last = a.get("last_sent")
            level = p.get("level")
            # A warn -> urgent step is a NEW fact: send it now, not in 24 h.
            escalated = bool(a.get("breached")) and level is not None and a.get("level") != level
            due = (last is None or escalated or
                   now - datetime.fromisoformat(last) >= timedelta(hours=REALERT_HOURS))
            if due:
                msgs.append((p.get("priority", "high"),
                             f"🔕 Expected signal missing — {PROBE_LABEL[k]}: {p['detail']}"))
                a = {"status": BREACHED, "breached": True, "last_sent": now.isoformat(),
                     "level": level}
            else:
                a = {**a, "status": BREACHED, "breached": True, "level": level}
        elif p["status"] == OK:
            if a.get("breached"):
                msgs.append(("normal", f"✅ Cleared — {PROBE_LABEL[k]}: {p['detail']}"))
            a = {"status": OK}
        else:
            # ⚠️ unknown keeps `breached` — "we could not look" never clears an alarm.
            a = {**a, "status": UNKNOWN}
        alarms[k] = a
    new["alarms"] = alarms

    # (a) the daily digest.
    if digest_now or (now.hour >= DIGEST_HOUR_UTC and state.get("last_digest_date") != today_s):
        msgs.insert(0, ("normal", render_digest(v)))
        new["last_digest_date"] = today_s
    return msgs, new


DIGEST_LOG_NAME = "digest_log.jsonl"


def compose_block(v: dict, msgs: list[tuple[str, str]]) -> tuple[str, str, list[str]]:
    """Turn planned messages into ONE block for the hourly digest.

    Returns ``(block, priority, new_items)``. NEW actionable items first and
    marked; the daily summary after them; and when nothing is new, ONE line —
    so a new actionable thing is unmissable and the steady state is quiet."""
    summary = [b for _, b in msgs if b.startswith("📋")]
    new = [b for _, b in msgs if not b.startswith("📋")]
    pris = {p for p, b in msgs if not b.startswith("📋")}
    pri = "urgent" if "urgent" in pris else "high" if "high" in pris else "normal"
    L: list[str] = []
    if new:
        L.append(f"🆕 NEW since last digest ({len(new)}):")
        L += new
    else:
        breached = sum(1 for p in v["probes"].values() if p["status"] == BREACHED)
        L.append(f"🟢 No new actionable items · open: {len(v['ask_operator'])} ask_operator, "
                 f"{v['stats']['due']} due, {breached} alarm(s) still breached "
                 f"(GET /api/bot/work/report)")
    if summary:
        L += ["", *summary]
    return "\n".join(L), pri, new


def prepare(dry_run: bool = False, digest_now: bool = False) -> dict:
    """Build this hour's attention block. Writes nothing; ``commit`` does,
    and only after the digest carrying the block was enqueued."""
    v = build()
    state = _read_json(STATE)
    msgs, new_state = plan_messages(v, state, digest_now=digest_now)
    block, pri, new = compose_block(v, msgs)
    receipt = {"at": v["now"].isoformat(), "new_items": len(new),
               "summary": any(b.startswith("📋") for _, b in msgs),
               "probes": {k: p["status"] for k, p in v["probes"].items()},
               "soak_read": v["soak_read"], "due": v["stats"]["due"],
               "unrouted": v["stats"]["unrouted"],
               "ask_operator_open": len(v["ask_operator"]),
               "pipeline_readable": v["pipeline_readable"]}
    log_entry = {"at": v["now"].isoformat(), "priority": pri, "new": new,
                 "summary": receipt["summary"],
                 "counts": {"due": v["stats"]["due"], "unrouted": v["stats"]["unrouted"],
                            "ask_operator_open": len(v["ask_operator"]),
                            "alarms_breached": sorted(k for k, p in v["probes"].items()
                                                      if p["status"] == BREACHED)}}
    return {"block": block, "priority": pri, "state": new_state,
            "receipt": receipt, "log": log_entry, "dry_run": dry_run}


def commit(prep: dict, outcome: str) -> None:
    """Advance state, append the machine-readable digest log (what the manager
    reads, since a session cannot receive Telegram), stamp the receipt."""
    if prep["dry_run"] or outcome != "sent":
        _write_json(RECEIPT, {**prep["receipt"], "outcome": "dry_run" if prep["dry_run"] else outcome})
        return
    _write_json(STATE, prep["state"])
    from scripts.ops import work_report  # noqa: PLC0415
    try:
        work_report.append_digest_log(prep["log"])
    except OSError as exc:
        print(f"attention-watch: WARNING could not append digest log: {exc}")
    _write_json(RECEIPT, {**prep["receipt"], "outcome": outcome})


def run(dry_run: bool = False, digest_now: bool = False) -> int:
    """Standalone pass (manual / debugging). The scheduled path is
    ``work_digest_now.run``, which prepends the block to its hourly digest."""
    prep = prepare(dry_run=dry_run, digest_now=digest_now)
    print(prep["block"])
    if dry_run:
        commit(prep, "dry_run")
        return 0
    from send_ping import enqueue  # noqa: PLC0415
    try:
        enqueue(prep["block"], priority=prep["priority"], target="claude")
    except (OSError, ValueError) as exc:
        commit(prep, "enqueue_failed")
        print(f"attention-watch: enqueue failed: {exc}")
        return 1
    commit(prep, "sent")
    return 0


# ── self-test ────────────────────────────────────────────────────────────────
def _self_test() -> int:
    ok = True

    def check(label: str, passed: bool, detail: Any = "") -> None:
        nonlocal ok
        ok &= bool(passed)
        print(f"  {'PASS' if passed else 'FAIL'} {label}" + ("" if passed else f" — {detail}"))

    now = datetime(2026, 10, 4, 7, 0, tzinfo=timezone.utc)
    today = now.date()
    ao = {"id": "PI-20261001-x-0001", "state": "queued", "next_action": "ask_operator",
          "what": "decide X", "due_when": {"kind": "date", "due_date": "2026-10-01"}}
    old = {"id": "PI-20260920-x-0001", "state": "queued", "next_action": "dispatch_lane",
           "what": "old", "due_when": {"kind": "date", "due_date": "2026-09-21"}}
    routed = {"id": "PI-20260901-x-0001", "state": "routed", "next_action": "dispatch_lane",
              "what": "r", "due_when": {"kind": "date", "due_date": "2026-09-02"}}
    ranked = rank_attention([routed, old, ao], today)
    check("ask_operator ranks first, routed last",
          [r["id"] for r in ranked] == [ao["id"], old["id"], routed["id"]], ranked)

    def view(**kw: Any) -> dict:
        base = {"now": now, "today": today, "pipeline_readable": True, "pipeline_unreadable": 0,
                "stats": {"due": 3, "unrouted": 2,
                          "unrouted_alarm": {"breached": []}},
                "ranked": ranked, "ask_operator": [ao], "soak_read": "absent", "soaks": [],
                "probes": {k: {"status": OK, "detail": "fine"} for k in PROBE_LABEL}}
        base.update(kw)
        return base

    msgs, st = plan_messages(view(), {})
    check("first run sends the digest plus ONE ask_operator seed line, not one per item",
          len(msgs) == 2 and msgs[0][1].startswith("📋")
          and msgs[1][1].startswith("❓ ask_operator at deploy: 1 open"), msgs)
    check("absent soak source is said, never rendered as zero",
          "ABSENT" in msgs[0][1], msgs[0][1])
    msgs2, st2 = plan_messages(view(), st)
    check("second pass same day is quiet", msgs2 == [], msgs2)
    ao2 = {**ao, "id": "PI-20261004-x-0002"}
    msgs3, st3 = plan_messages(view(ask_operator=[ao, ao2]), st2)
    check("a NEW ask_operator item alerts once, high",
          len(msgs3) == 1 and msgs3[0][0] == "high" and ao2["id"] in msgs3[0][1], msgs3)
    probes = {k: {"status": OK, "detail": "fine"} for k in PROBE_LABEL}
    probes["soak_grade"] = {"status": BREACHED, "detail": "9d old"}
    msgs4, st4 = plan_messages(view(probes=probes, ask_operator=[ao, ao2]), st3)
    check("a breached silence probe alerts", any("Expected signal missing" in b for _, b in msgs4), msgs4)
    msgs5, st5 = plan_messages(view(probes=probes, ask_operator=[ao, ao2]), st4)
    check("…and does not re-alert inside REALERT_HOURS", msgs5 == [], msgs5)
    probes_u = dict(probes, soak_grade={"status": UNKNOWN, "detail": "?"})
    _, st6 = plan_messages(view(probes=probes_u, ask_operator=[ao, ao2]), st5)
    msgs7, _ = plan_messages(view(ask_operator=[ao, ao2]), st6)
    check("unknown does not silently clear a breach; the later OK sends one clear",
          len(msgs7) == 1 and msgs7[0][1].startswith("✅"), msgs7)
    s1 = {"leg": "a", "account": "bybit_1", "state": "accruing"}
    d1 = {"leg": "b", "account": "bybit_1", "state": "dead"}
    seed, _ = plan_messages(view(soak_read="read", soaks=[s1, d1]), st2)
    check("first soak read sends ONE seed line with the counts",
          [b for _, b in seed] == ["🧪 Soaks at deploy: 0 ready · 0 overdue · 1 dead (see report)"], seed)
    legacy = {k: v for k, v in st2.items() if not k.startswith("seed_")}
    legacy["soak_states"] = {"bybit_1:b": "dead"}
    late, late_st = plan_messages(view(soak_read="read", soaks=[d1]), legacy)
    check("order-independent: a state seeded WITHOUT markers still gets both seed lines once",
          sum(1 for _, b in late if "at deploy" in b) == 2, late)
    again, _ = plan_messages(view(soak_read="read", soaks=[d1]), late_st)
    check("…and never again once the markers are set",
          not any("at deploy" in b for _, b in again), again)
    absent_first, absent_st = plan_messages(view(), {k: v for k, v in st2.items()
                                                    if k != "seed_soaks_sent_at"})
    check("an absent soak source does not burn the soak seed marker",
          "seed_soaks_sent_at" not in absent_st, absent_st)
    _, st8 = plan_messages(view(soak_read="read", soaks=[s1]), st2)
    msgs9, _ = plan_messages(view(soak_read="read", soaks=[{**s1, "state": "dead"}]), st8)
    check("a soak moving to dead alerts high",
          len(msgs9) == 1 and msgs9[0][0] == "high" and "dead" in msgs9[0][1], msgs9)
    later = view(now=now + timedelta(days=1), today=today + timedelta(days=1))
    msgs10, _ = plan_messages(later, st2)
    check("next day sends the next digest", len(msgs10) == 1 and msgs10[0][1].startswith("📋"), msgs10)
    early = view(now=now.replace(hour=2))
    check("no digest before DIGEST_HOUR_UTC",
          not any(b.startswith("📋") for _, b in plan_messages(early, {})[0]), "")
    nr1 = {"id": "RQ-1", "graded_at": "2026-10-02", "reason": "n 9 < 39"}
    nr2 = {"id": "RQ-2", "graded_at": "2026-10-04", "reason": "mixed"}
    nseed, nst = plan_messages(view(needs_review_read="read", needs_review=[nr1]), st2)
    check("first needs_review read sends ONE count line, not one per unit",
          [b for _, b in nseed if "needs_review at deploy" in b] == [
              "🔎 needs_review at deploy: 1 research unit(s) flagged (listed in the daily digest)"], nseed)
    nnew, nst2 = plan_messages(view(needs_review_read="read", needs_review=[nr1, nr2]), nst)
    check("a newly flagged unit alerts once, naming it",
          len(nnew) == 1 and "RQ-2" in nnew[0][1] and "RQ-1" not in nnew[0][1], nnew)
    check("…and is quiet on the next pass",
          plan_messages(view(needs_review_read="read", needs_review=[nr1, nr2]), nst2)[0] == [], "")
    check("an UNREADABLE queue neither alerts nor burns the seed marker",
          plan_messages(view(needs_review_read="unreadable"), st2)[1].get("seed_needs_review_sent_at") is None, "")
    dig = render_digest(view(needs_review_read="read", needs_review=[nr1, nr2]))
    check("the digest carries a loud line with count, age and ids",
          "🔎 2 research unit(s) need a read" in dig and "oldest flagged 2d ago" in dig and "RQ-1" in dig, dig)
    check("unreadable is said, not rendered as zero",
          "UNREADABLE" in render_digest(view(needs_review_read="unreadable")), "")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "RQ-9.yaml").write_text("id: RQ-9\ngrading:\n  needs_review: true\n  graded_at: '2026-10-01'\n  reason: r\n")
        (Path(td) / "RQ-8.yaml").write_text("id: RQ-8\ngrading:\n  needs_review: false\n")
        rd, rows = read_needs_review(Path(td))
        check("the reader finds the flagged unit and skips the unflagged one",
              rd == "read" and [r["id"] for r in rows] == ["RQ-9"], (rd, rows))
        (Path(td) / "RQ-7.yaml").write_text("id: [unclosed\nneeds_review: true\n")
        check("a corrupt unit makes the read UNREADABLE, never a shorter list",
              read_needs_review(Path(td))[0] == "unreadable", "")
    blk, pri, new_items = compose_block(view(), [])
    check("nothing new → ONE quiet line", blk.startswith("🟢") and "\n" not in blk and not new_items, blk)
    blk2, pri2, _ = compose_block(view(), [("high", "❓ 1 new decision"), ("normal", "📋 Daily")])
    check("new items lead, marked 🆕, summary after, priority high",
          blk2.startswith("🆕 NEW since last digest (1)") and blk2.index("❓") < blk2.index("📋")
          and pri2 == "high", blk2)
    lines = ['{', '  "items": [', '    {', '      "id": "A1",', '      "state": "x"',
             '    },', '    {', '      "id": "B2"', '    }', '  ]', '}']
    check("row line ranges parse", _row_line_ranges(lines) == {"A1": (3, 6), "B2": (7, 9)},
          _row_line_ranges(lines))
    check("receipt and state anchored at repo root",
          RECEIPT.parent == REPO_ROOT / "runtime_logs" and STATE.parent == RECEIPT.parent)
    print("attention-watch self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--digest-now", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    return run(dry_run=a.dry_run, digest_now=a.digest_now)


if __name__ == "__main__":
    sys.exit(main())
