#!/usr/bin/env python3
# wiring: CONTRACTS docs/claude/work/SOAKS.json (committed, guarded by
#   scripts/ci/check_soak_contracts.py) -> STATE soak_states() computed LIVE from
#   the contracts + the journal DB. Consumers: the brief (BRIEF-FIX), WORK-SYSTEM's
#   /api/bot/work/report and its Telegram attention watch. Nobody else computes
#   soak state; state is NEVER written into the committed file.
"""THE SOAK CONTRACT — every soak knows, at the moment it starts, when it is done.

Operator, 2026-10-04: "we can't set something to soak if we don't know when it's
done. It has to have a definition of done so that it knows to create an alert …
that is basic." And: everything is proven offline before it soaks; a soak only
verifies that live matches what the backtest predicted, so it is SHORT.

A CONTRACT (one row of ``docs/claude/work/SOAKS.json``) carries:
  id, leg, account, kind        what is soaking
  verifies                      the live-vs-backtest question it answers
  metric                        the event counted (closed trades / shadow packages)
  n_needed, power               the sample, and what that sample can detect
  expected_per_week             the event rate, FROM THE BACKTEST record
  started, end_date             end_date = started + n_needed / rate (computed)
  pass_rule, fail_rule          what decides it once n_needed is reached
  design                        ``ok`` | ``too_long`` | ``no_backtest_rate`` |
                                ``not_soaking`` — anything but ``ok`` carries a
                                ``recommended_fix``: a soak longer than
                                MAX_SOAK_DAYS is a DESIGN ERROR, not a wait.

STATE (``soak_states()``), computed per call, never stored:
  ready     n_needed reached — the pass/fail rule is due to be applied
  overdue   end_date passed and n_needed not reached
  dead      the contract expected >= DEAD_EXPECTED events by now and saw none
  accruing  on schedule
  unknown   the DB could not be read, or the contract has no rate to grade
            against (no_backtest_rate). NEVER folded into dead or accruing.
  A ``too_long`` contract is STILL graded (it is running, and can go ready,
  overdue or dead); ``design`` travels beside ``state`` so a consumer shows both.
  A ``not_soaking`` contract (disabled / unrostered shadow) is a recorded
  decision, not a soak: ``soak_states()`` omits it.

``expected_per_week`` is INFERRED as ``n_trades_oos / window_days * 7`` from the
leg's ``comms/strategy_evidence/<leg>.json`` — the harness's OOS trade count over
its stated window. If the OOS folds span less than the window the true rate is
higher, so the end date errs LATE, never early.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
CONTRACTS = REPO / "docs/claude/work/SOAKS.json"
EVIDENCE = REPO / "comms/strategy_evidence"
STAGE1_ACCOUNTS = ("bybit_1", "alpaca_paper")
MAX_SOAK_DAYS = 14      # operator, 2026-10-04: "a soak should be SHORT ... never weeks"
N_CLOSES = 10           # r3_cost_fidelity.N_FLOOR — fills per side for a cost-fidelity read
N_PACKAGES = 10         # shadow: enough packages to compare signal rate to the backtest's
DEAD_EXPECTED = 3.0     # events the backtest says we should have seen, with none seen
#: A journal whose newest row is older than this is not the live journal (a test
#: fixture, a stale copy): reading 0 from it would manufacture "dead". MEASURED
#: 2026-10-04: a pytest-left 192 KB trade_journal.db at the repo root graded 32
#: of 48 soaks dead until this gate existed.
JOURNAL_FRESH_DAYS = 3
STATES = ("accruing", "ready", "overdue", "dead", "unknown")
REQUIRED = ("id", "leg", "account", "kind", "verifies", "metric", "n_needed", "power",
            "started", "end_date", "pass_rule", "fail_rule", "design")


# ── contracts ───────────────────────────────────────────────────────────────
def backtest_rate_per_day(leg: str) -> Optional[float]:
    p = EVIDENCE / f"{leg}.json"
    try:
        e = json.loads(p.read_text())
        n, w = e.get("n_trades_oos"), e.get("window_days")
        if isinstance(n, (int, float)) and isinstance(w, (int, float)) and n > 0 and w > 0:
            return n / w
    except (OSError, ValueError):
        pass
    return None


def make_contract(*, kind: str, leg: str, account: str, started: str,
                  enabled: bool = True, rostered: bool = True) -> Dict[str, Any]:
    """One contract. ``started`` is ISO (a ``<=`` floor is stripped)."""
    started = started.lstrip("<=")
    rate = backtest_rate_per_day(leg)
    if kind == "stage1":
        n, metric = N_CLOSES, f"closed trades on {account}/{leg} (each = one entry + one exit fill)"
        verifies = ("live mechanics + cost fidelity: orders flow, and realized round-trip slippage "
                    "matches the Stage-0 record's modelled cost")
        power = (f"R3 floor n={N_CLOSES}/side (RULE-R5-SOAK-BOOK-GRADE-v1). A floor, not a computed "
                 "power: it resolves only a divergence larger than the CI95 width at n=10 "
                 "(~several bps on these venues)")
        pass_rule = "R3 verdict consistent: CI95 upper <= modelled + cost_tolerance_bps (2.0, MD-PROMOTE-S1-S2)"
        fail_rule = "R3 verdict divergent (CI95 lower > modelled + tolerance), or 0 orders for actionable intents"
    else:
        n, metric = N_PACKAGES, f"shadow order packages logged for {leg} (any account)"
        verifies = "signal parity: the live shadow signal rate matches the backtest's trade rate"
        power = (f"n={N_PACKAGES} packages: a Poisson count — 95% CI roughly x0.5..x1.8 of the rate, so it "
                 "detects only a gross (>2x) rate mismatch")
        pass_rule = "observed package rate inside the Poisson 95% CI of the backtest rate"
        fail_rule = "observed rate outside that CI (signal not reproducing offline behaviour)"
    c: Dict[str, Any] = {
        "id": f"SOAK-{kind}-{account}-{leg}" if kind == "stage1" else f"SOAK-shadow-{leg}",
        "leg": leg, "account": account, "kind": kind, "verifies": verifies, "metric": metric,
        "n_needed": n, "power": power, "started": started, "pass_rule": pass_rule,
        "fail_rule": fail_rule,
        "expected_per_week": round(rate * 7, 2) if rate else None,
    }
    if kind == "shadow" and (not enabled or not rostered):
        c.update(end_date=None, design="not_soaking",
                 recommended_fix=("enabled: false" if not enabled else "on no account roster")
                 + " — nothing runs it; retire the strategy block or re-roster it with a fresh contract")
        return c
    if rate is None:
        c.update(end_date=None, design="no_backtest_rate",
                 recommended_fix="no comms/strategy_evidence record with n_trades_oos/window_days — "
                                 "produce the Stage-0 record first (nothing should soak unproven offline)")
        return c
    days = math.ceil(n / rate)
    c["duration_days"] = days
    c["end_date"] = (date.fromisoformat(started) + timedelta(days=days)).isoformat()
    if days > MAX_SOAK_DAYS:
        c["design"] = "too_long"
        c["recommended_fix"] = (
            f"{days}d at {rate * 7:.2f} events/week exceeds {MAX_SOAK_DAYS}d: pool cost fidelity at the "
            "venue level (R3 per_venue cells, n>=20 pooled across legs) instead of per leg, or verify "
            "via replay/parity, or do not soak this leg" if kind == "stage1" else
            f"{days}d exceeds {MAX_SOAK_DAYS}d: verify signal parity by replaying the harness over live "
            "candles instead of waiting for packages, or do not shadow this leg")
    else:
        c["design"] = "ok"
    return c


def load_contracts(path: Path = CONTRACTS) -> List[Dict[str, Any]]:
    return json.loads(path.read_text()).get("soaks") or []


def contract_problems(c: Dict[str, Any]) -> List[str]:
    """Why a contract is not a definition of done. Empty = admissible."""
    p = [f"missing `{k}`" for k in REQUIRED if k not in c]
    if c.get("design") == "ok" and not c.get("end_date"):
        p.append("design ok but no end_date — a soak with no end date is not a soak")
    if c.get("design") not in ("ok", "too_long", "no_backtest_rate", "not_soaking"):
        p.append(f"design must be ok|too_long|no_backtest_rate|not_soaking, got {c.get('design')!r}")
    if c.get("design") != "ok" and not str(c.get("recommended_fix") or "").strip():
        p.append(f"design {c.get('design')!r} needs a recommended_fix — the tool must say what to change")
    if c.get("end_date") and c.get("started"):
        try:
            if (date.fromisoformat(c["end_date"]) - date.fromisoformat(c["started"])).days > MAX_SOAK_DAYS \
                    and c.get("design") == "ok":
                p.append(f"longer than {MAX_SOAK_DAYS}d yet design ok — mark it too_long with a fix")
        except ValueError:
            p.append("started/end_date not ISO dates")
    return p


# ── live state ──────────────────────────────────────────────────────────────
def _db_path() -> Optional[str]:
    try:
        sys.path.insert(0, str(REPO))
        from src.utils.paths import trade_journal_db_path  # noqa: PLC0415
        return trade_journal_db_path()
    except Exception:  # noqa: BLE001
        return None


def _count(conn: sqlite3.Connection, c: Dict[str, Any]) -> int:
    if c["kind"] == "stage1":
        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE account_id=? AND strategy_name=? AND status='closed' "
            "AND COALESCE(is_backtest,0)=0 AND datetime(closed_at) >= datetime(?)",
            (c["account"], c["leg"], c["started"])).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM order_packages WHERE strategy_name=? "
                           "AND datetime(created_at) >= datetime(?)",
                           (c["leg"], c["started"])).fetchone()
    return int(row[0])


def _open_positions(conn: sqlite3.Connection, c: Dict[str, Any]) -> int:
    if c["kind"] != "stage1":
        return 0
    row = conn.execute("SELECT COUNT(*) FROM trades WHERE account_id=? AND strategy_name=? "
                       "AND status='open' AND COALESCE(is_backtest,0)=0",
                       (c["account"], c["leg"])).fetchone()
    return int(row[0])


def grade(c: Dict[str, Any], observed: Optional[int], today: date,
          open_positions: int = 0) -> Dict[str, Any]:
    """Pure: one contract + an observed count (None = could not read) -> a state row.
    ``open_positions`` > 0 is activity: a held position makes no close until it
    exits (MEASURED 2026-10-04: spy_trend_long_1d read dead at 0 closes in 115d
    while holding a long — a daily trend leg's normal state)."""
    out = {"id": c["id"], "leg": c["leg"], "account": c["account"], "kind": c["kind"],
           "started": c["started"], "end_date": c.get("end_date"), "design": c.get("design"),
           "progress": None, "state": "unknown", "reason": ""}
    if c.get("design") == "not_soaking":
        out["state"], out["reason"] = "dead", f"not soaking: {c.get('recommended_fix', '')}"
        return out
    if c.get("design") not in ("ok", "too_long"):
        out["reason"] = f"contract design {c.get('design')}: {c.get('recommended_fix', '')}"
        return out
    if observed is None:
        out["reason"] = "journal DB unreadable — we could not look (not dead, not accruing)"
        return out
    n = c["n_needed"]
    out["progress"] = f"{observed}/{n} {c['kind'] == 'stage1' and 'closes' or 'packages'}"
    days_in = (today - date.fromisoformat(c["started"])).days
    expected = (c.get("expected_per_week") or 0) / 7 * max(days_in, 0)
    end = date.fromisoformat(c["end_date"])
    if observed >= n:
        out["state"], out["reason"] = "ready", f"n reached ({observed}/{n}) — apply: {c['pass_rule']}"
    elif observed == 0 and open_positions:
        out["state"], out["reason"] = ("accruing", f"0 closes but {open_positions} position(s) open — "
                                       f"a close is pending, not absent")
    elif observed == 0 and expected >= DEAD_EXPECTED:
        out["state"], out["reason"] = "dead", (f"0 events in {days_in}d; the backtest rate expected "
                                               f"~{expected:.1f}")
    elif today > end:
        out["state"], out["reason"] = "overdue", (f"end date {end} passed at {observed}/{n}; backtest "
                                                  f"expected ~{expected:.1f} by now")
    else:
        out["state"], out["reason"] = "accruing", f"{observed}/{n}, on schedule for {end}"
    return out


def soak_states(today: Optional[date] = None, db_path: Optional[str] = None,
                contracts_path: Path = CONTRACTS) -> List[Dict[str, Any]]:
    """THE soak state computation. One row per contract:
    {id, leg, account, kind, state, started, end_date, progress, reason, design}.
    ``state`` is one of STATES. A DB that cannot be opened makes every gradeable
    row ``unknown`` — never ``dead``."""
    today = today or datetime.now(timezone.utc).date()
    contracts = load_contracts(contracts_path)
    path = db_path or _db_path()
    conn = None
    if path and Path(path).exists():
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error:
            conn = None
    stale = None
    if conn is not None:
        try:
            newest = conn.execute("SELECT MAX(created_at) FROM order_packages").fetchone()[0]
            nd = datetime.fromisoformat(str(newest).replace("Z", "+00:00")).date() if newest else None
            if nd is None or (today - nd).days > JOURNAL_FRESH_DAYS:
                stale = f"journal at {path} is not live (newest order package {newest})"
        except (sqlite3.Error, ValueError):
            stale = f"journal at {path} unreadable"
        if stale:
            conn.close()
            conn = None
    rows = []
    for c in contracts:
        if c.get("design") == "not_soaking":
            # Not a soak (disabled / on no roster): its contract records that
            # decision, but it is never reported as a soak — a permanent "dead"
            # row for a thing nothing runs is noise, not an alarm (manager,
            # 2026-10-04 17:52Z review).
            continue
        observed, held = None, 0
        if conn is not None and c.get("design") in ("ok", "too_long"):
            try:
                observed = _count(conn, c)
                held = _open_positions(conn, c)
            except sqlite3.Error:
                observed = None
        row = grade(c, observed, today, held)
        if stale and row["state"] == "unknown" and c.get("design") in ("ok", "too_long"):
            row["reason"] = stale + " — we could not look"
        rows.append(row)
    if conn is not None:
        conn.close()
    return rows


# ── backfill ────────────────────────────────────────────────────────────────
def _strategies() -> Dict[str, Any]:
    import yaml  # noqa: PLC0415
    return yaml.safe_load(open(REPO / "config/strategies.yaml"))["strategies"]


def backfill(ref: str = "HEAD") -> Dict[str, Any]:
    """Contracts for every CURRENT Stage-1 (non-shadow) leg and shadow strategy.
    Start dates are measured from git (soak_report.start_dates)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import soak_report as sr  # noqa: PLC0415

    sys.path.insert(0, str(REPO))
    from src.config.accounts_loader import load_accounts_dict  # noqa: PLC0415
    acc = load_accounts_dict()
    if not acc:
        raise SystemExit("soak_state --backfill: the accounts config is unreadable — refusing to write contracts")
    strat = _strategies()
    shadow = sorted(n for n, v in strat.items() if isinstance(v, dict) and v.get("execution") == "shadow")
    stage1 = sorted(f"{a}/{s}" for a in STAGE1_ACCOUNTS for s in (acc.get(a) or {}).get("strategies") or []
                    if s not in shadow)
    s1 = sr.start_dates(set(stage1), "config/accounts.yaml", sr.roster_members, ref)
    sh = sr.start_dates(set(shadow), "config/strategies.yaml", sr.shadow_members, ref)
    out = []
    for subj in stage1:
        a, leg = subj.split("/", 1)
        out.append(make_contract(kind="stage1", leg=leg, account=a, started=s1[subj]))
    for leg in shadow:
        accts = [a for a, x in acc.items() if leg in (x.get("strategies") or [])]
        out.append(make_contract(kind="shadow", leg=leg, account=",".join(accts) or "-",
                                 started=sh[leg], enabled=strat[leg].get("enabled") is not False,
                                 rostered=bool(accts)))
    return {"as_of": datetime.now(timezone.utc).date().isoformat(),
            "_comment": "SOAK CONTRACTS — definitions of done, NOT state. State is computed live by "
                        "scripts/ops/soak_state.py::soak_states(). Guarded by scripts/ci/check_soak_contracts.py.",
            "soaks": out}


def _self_test() -> int:
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= cond
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    t = date(2026, 10, 4)
    c = {"id": "x", "leg": "l", "account": "a", "kind": "stage1", "n_needed": 10, "design": "ok",
         "started": "2026-09-30", "end_date": "2026-10-10", "expected_per_week": 7.0, "pass_rule": "p"}
    ck("n reached -> ready", grade(c, 10, t)["state"] == "ready")
    ck("on schedule -> accruing", grade(c, 2, t)["state"] == "accruing")
    ck("0 events when ~4 expected -> dead", grade(c, 0, t)["state"] == "dead")
    ck("past end, short of n -> overdue", grade(dict(c, end_date="2026-10-01"), 2, t)["state"] == "overdue")
    ck("unreadable DB -> unknown, never dead", grade(c, None, t)["state"] == "unknown")
    ck("0 closes but a position open -> accruing, not dead", grade(c, 0, t, 1)["state"] == "accruing")
    ck("design too_long is still graded (running soaks can die)",
       grade(dict(c, design="too_long", recommended_fix="pool"), 0, t)["state"] == "dead")
    ck("no_backtest_rate -> unknown", grade(dict(c, design="no_backtest_rate"), 5, t)["state"] == "unknown")
    ck("not_soaking -> dead", grade(dict(c, design="not_soaking"), None, t)["state"] == "dead")
    ck("a contract without an end date is refused",
       any("end_date" in p for p in contract_problems({k: c.get(k, "v") for k in REQUIRED} | {"end_date": None})))
    ck("a >14d contract marked ok is refused",
       any("too_long" in p for p in contract_problems(dict({k: "v" for k in REQUIRED}, design="ok",
                                                           started="2026-09-01", end_date="2026-10-01"))))
    print("soak_state self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backfill", action="store_true",
                    help=f"(re)write {CONTRACTS.relative_to(REPO)} for current Stage-1 + shadow soaks, "
                         "keeping existing contracts' started/end_date")
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--db", default=None)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.backfill:
        doc = backfill(a.ref)
        if CONTRACTS.exists():  # a contract, once registered, keeps its dates
            old = {c["id"]: c for c in load_contracts()}
            doc["soaks"] = [old.get(c["id"], c) for c in doc["soaks"]]
            keep = [c for c in old.values() if c.get("kind") not in ("stage1", "shadow")]
            doc["soaks"] += keep
        CONTRACTS.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(f"wrote {len(doc['soaks'])} contracts to {CONTRACTS.relative_to(REPO)}")
        return 0
    print(json.dumps(soak_states(db_path=a.db), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
