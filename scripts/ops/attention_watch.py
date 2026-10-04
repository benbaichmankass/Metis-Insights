#!/usr/bin/env python3
# wiring: deploy/ict-attention-watch.service <- deploy/ict-attention-watch.timer
# (OnCalendar=hourly). Installed by the deploy/*.timer glob in
# scripts/install_systemd_units.sh. Receipt readable at
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

It does three things, every hour, on the VM's own clock (GitHub cron is not a
clock in this repo — see ``scripts/ops/work_digest_now.py``):

  (a) DAILY DIGEST — once per UTC day at/after ``DIGEST_HOUR_UTC``: counts plus
      the top items that need the operator, ranked. Short by construction.
  (b) EDGE ALERTS — sent when a thing CHANGES, not while it stays the same:
        * a new ``ask_operator`` pipeline item appears;
        * a soak leg moves into ``ready`` / ``overdue`` / ``dead``
          (state is computed by SOAK-WATCH's ``scripts/ops/soak_state.py`` —
          this module never computes soak state itself).
  (c) SILENCE ALARMS — "an expected signal did NOT arrive":
        * the R5 weekly soak grade has not landed in ``SOAK_GRADE_MAX_DAYS``;
        * no research result has landed in ``RESEARCH_MAX_HOURS``;
        * a checklist row is ``in_flight`` with no change to it in
          ``INFLIGHT_STALE_DAYS``;
        * the checklist itself (the manager's register) has not been written
          in ``MANAGER_SILENT_HOURS`` — the manager's daily review did not run.
      A breached alarm re-sends at most once per ``REALERT_HOURS`` while it
      stays breached, and sends one "cleared" line when it clears.

NEVER COLLAPSED: every probe returns ``ok`` / ``breached`` / ``unknown``.
``unknown`` (we could not look — shallow clone, unreadable file, missing
module) is reported as such and is never read as ``ok``. A digest that cannot
read the pipeline says so in its first line.

Read-only against the repo. Its only writes are two files under
``runtime_logs/`` (state + receipt) and the ping inbox. Sends go to the
@claude_ict_comms_bot inbox (``send_ping.enqueue(target="claude")``) — the
operator's Claude-comms channel, separate from trade alerts.

Usage::

    python3 scripts/ops/attention_watch.py              # one hourly pass
    python3 scripts/ops/attention_watch.py --dry-run    # print, send nothing, keep state
    python3 scripts/ops/attention_watch.py --digest-now # force today's digest
    python3 scripts/ops/attention_watch.py --self-test
"""
from __future__ import annotations

import argparse
import json
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


def open_ask_operator(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("next_action") == "ask_operator"
            and i.get("state") not in pipeline.TERMINAL_STATES]


# ── soak state (owned by SOAK-WATCH; consumed, never computed, here) ─────────
def read_soak_states() -> tuple[str, list[dict]]:
    """``(read_state, rows)``. read_state ∈ read / absent / unreadable."""
    try:
        from scripts.ops import soak_state  # noqa: PLC0415
    except ImportError:
        return "absent", []
    fn = getattr(soak_state, "soak_states", None)
    if not callable(fn):
        return "absent", []
    try:
        rows = fn()
        return "read", [r for r in rows if isinstance(r, dict)]
    except Exception as exc:  # noqa: BLE001 — a broken producer is "unreadable", loud
        print(f"attention-watch: soak_state.soak_states() raised: {exc}")
        return "unreadable", []


def _soak_key(r: dict) -> str:
    return f"{r.get('account', '?')}:{r.get('leg', '?')}"


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
    return {
        "now": now, "today": today, "pipeline_readable": res.healthy,
        "pipeline_unreadable": len(res.unreadable),
        "stats": pipeline.stats(res, today),
        "ranked": rank_attention(items, today),
        "ask_operator": open_ask_operator(items),
        "soak_read": soak_read, "soaks": soaks,
        "probes": {
            "soak_grade": probe_soak_grade(now),
            "research": probe_research(now, shallow),
            "inflight": probe_inflight(now, shallow),
            "manager": probe_manager(now),
        },
    }


PROBE_LABEL = {
    "soak_grade": "R5 weekly soak grade missing",
    "research": "research results not landing",
    "inflight": "in_flight rows gone quiet",
    "manager": "manager's register not written",
}


def render_digest(v: dict) -> str:
    s = v["stats"]
    L = [f"📋 Daily work digest {v['today']}"]
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
    bad = [k for k, p in v["probes"].items() if p["status"] != OK]
    for k in bad:
        p = v["probes"][k]
        L.append(f"{'🔕' if p['status'] == BREACHED else '❔'} {PROBE_LABEL[k]}: {p['detail']}")
    L.append("")
    L.append(f"Top {min(TOP_N, len(v['ranked']))} of {len(v['ranked'])} due:")
    for i in v["ranked"][:TOP_N]:
        tag = "❓ASK" if i.get("next_action") == "ask_operator" else i.get("state")
        L.append(f"• [{tag}] {i.get('id')}: {_short(i.get('what'), 120)}")
    L.append("Full brief: /api/bot/work/brief · all due: pipeline.py --due --all")
    return "\n".join(L)


def plan_messages(v: dict, state: dict, digest_now: bool = False) -> tuple[list[tuple[str, str]], dict]:
    """Return ``([(priority, body)], new_state)``. Pure — no I/O."""
    now: datetime = v["now"]
    today_s = v["today"].isoformat()
    new = dict(state)
    msgs: list[tuple[str, str]] = []
    first_run = "seen_ask_operator" not in state

    # (b1) new ask_operator items — edge-triggered. The first run seeds
    # silently: the backlog it would otherwise flood is in the digest instead.
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
        new["soak_states"] = cur_s

    # (c) silence alarms — send on breach, re-send every REALERT_HOURS, one clear.
    alarms = dict(state.get("alarms", {}))
    for k, p in v["probes"].items():
        a = alarms.get(k, {})
        if p["status"] == BREACHED:
            last = a.get("last_sent")
            due = (last is None or
                   now - datetime.fromisoformat(last) >= timedelta(hours=REALERT_HOURS))
            if due:
                msgs.append(("high", f"🔕 Expected signal missing — {PROBE_LABEL[k]}: {p['detail']}"))
                a = {"status": BREACHED, "breached": True, "last_sent": now.isoformat()}
            else:
                a = {**a, "status": BREACHED, "breached": True}
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


def run(dry_run: bool = False, digest_now: bool = False) -> int:
    v = build()
    state = _read_json(STATE)
    msgs, new_state = plan_messages(v, state, digest_now=digest_now)
    for pri, body in msgs:
        print(f"--- [{pri}] ---\n{body}\n")
    receipt = {"at": v["now"].isoformat(), "messages": len(msgs),
               "probes": {k: p["status"] for k, p in v["probes"].items()},
               "soak_read": v["soak_read"], "due": v["stats"]["due"],
               "unrouted": v["stats"]["unrouted"],
               "ask_operator_open": len(v["ask_operator"]),
               "pipeline_readable": v["pipeline_readable"]}
    if dry_run:
        _write_json(RECEIPT, {**receipt, "outcome": "dry_run"})
        return 0
    from send_ping import enqueue  # noqa: PLC0415
    sent = 0
    try:
        for pri, body in msgs:
            enqueue(body, priority=pri, target="claude")
            sent += 1
    except (OSError, ValueError) as exc:
        # State is NOT advanced on a failed send, so the next pass retries.
        _write_json(RECEIPT, {**receipt, "outcome": "enqueue_failed", "sent": sent,
                              "error": str(exc)})
        print(f"attention-watch: enqueue failed after {sent}: {exc}")
        return 1
    _write_json(STATE, new_state)
    _write_json(RECEIPT, {**receipt, "outcome": "sent" if sent else "quiet", "sent": sent})
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
    check("first run sends the digest and does NOT flood ask_operator alerts",
          len(msgs) == 1 and msgs[0][1].startswith("📋"), msgs)
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
    _, st8 = plan_messages(view(soak_read="read", soaks=[s1]), st2)
    msgs9, _ = plan_messages(view(soak_read="read", soaks=[{**s1, "state": "dead"}]), st8)
    check("a soak moving to dead alerts high",
          len(msgs9) == 1 and msgs9[0][0] == "high" and "dead" in msgs9[0][1], msgs9)
    later = view(now=now + timedelta(days=1), today=today + timedelta(days=1))
    msgs10, _ = plan_messages(later, st2)
    check("next day sends the next digest", len(msgs10) == 1 and msgs10[0][1].startswith("📋"), msgs10)
    early = view(now=now.replace(hour=2))
    check("no digest before DIGEST_HOUR_UTC", plan_messages(early, {})[0] == [], "")
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
