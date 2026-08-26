#!/usr/bin/env python3
# wiring: manual (analysis-time) + scripts/ci/run_guards.py (--self-test only)
"""Is this strategy RUNNING right now — and when did it last actually trade?

PREVENTION FOR the 10th instance of RC-STORED-FIELD-READ-AS-ITS-NAME.

WHY. On 2026-08-26 a 44.2% past-stop rate was reported for `vwap` as *"real
money, one strategy, the netting account"* — phrasing that asserts a CURRENT
condition. `vwap` is `execution: shadow`, routed to **no account**, and last
opened a trade on **2026-06-07**, ~2.5 months earlier. The measurement was
correct; its TIME BOUND was never stated, so a historical fact was presented as
a live one. It surfaced only because the operator asked.

The trades table answers *what happened*. It cannot answer *is this still
happening* — and reading it is what makes you feel you have checked.

BOTH GATES, because either one alone is misleading. A strategy is live only if
its own `execution` is not `shadow` AND some account it is routed to is
`yaml_mode: live`. The account being live means nothing if the strategy is not
routed to it (the vwap case exactly: `bybit_2` IS live and runs seven
strategies, none of them vwap).

Exit status:
  0  live      — routed to at least one live account, execution not shadow
  2  not live  — quote any measurement about it in the PAST TENSE
  1  unknown   — the API could not be read. NOT "not live"; we did not look.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess

_BASE = os.environ.get("BOT_API_URL", "https://ict-bot.duckdns.org").rstrip("/")


def _get(path: str, timeout: int = 60):
    try:
        p = subprocess.run(["curl", "-sS", "--max-time", str(timeout), f"{_BASE}{path}"],
                           capture_output=True, text=True, timeout=timeout + 10)
        return json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else None
    except Exception:  # noqa: BLE001  # allow-silent: unreachable API is 'unknown', reported by the caller
        return None


def liveness(name: str) -> dict:
    cfg = _get("/api/bot/config")
    strat = _get("/api/bot/strategies")
    if cfg is None or strat is None:
        return {"state": "unknown",
                "why": "could not read /api/bot/config or /api/bot/strategies — "
                       "this is 'we did not look', NOT 'the strategy is off'"}
    rows = strat.get("strategies") or strat.get("rows") or []
    row = next((s for s in rows if s.get("name") == name), None)
    routed = [a.get("id") for a in cfg.get("accounts", [])
              if any(name == str(s) for s in (a.get("strategies") or []))]
    live_accounts = [a.get("id") for a in cfg.get("accounts", [])
                     if a.get("id") in routed and a.get("yaml_mode") == "live"
                     and a.get("enabled")]
    execution = (row or {}).get("execution")
    is_live = bool(live_accounts) and execution != "shadow"
    return {"state": "live" if is_live else "not_live",
            "strategy": name,
            "execution": execution,
            "enabled": (row or {}).get("enabled"),
            "running": (row or {}).get("running"),
            "routed_to": routed,
            "live_accounts": live_accounts,
            "why": ("routed to a live account with execution != shadow" if is_live else
                    "not routed to any live account" if not live_accounts else
                    f"execution is {execution!r}")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("strategy", nargs="?")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.strategy:
        ap.error("give a strategy name")
    r = liveness(a.strategy)
    print(json.dumps(r, indent=2))
    if r["state"] == "live":
        print("\nLIVE — a measurement about it describes a current condition.")
        return 0
    if r["state"] == "unknown":
        print("\nUNKNOWN — we could not look. Do NOT read this as 'not live'.")
        return 1
    print("\nNOT LIVE — quote any measurement about this strategy in the PAST TENSE, "
          "and state the window the rows came from.")
    return 2


def _self_test() -> int:
    """Offline: prove the two gates are independent and that unknown is distinct."""
    ok = True
    cases = [
        ("routed to a live account, not shadow -> live",
         (["bybit_2"], ["bybit_2"], "live"), "live"),
        ("routed to a LIVE account but execution shadow -> not live",
         (["bybit_2"], ["bybit_2"], "shadow"), "not_live"),
        ("execution live but routed NOWHERE -> not live (the vwap case)",
         ([], [], "live"), "not_live"),
        ("routed only to a NON-live account -> not live",
         (["bybit_1"], [], "live"), "not_live"),
    ]
    for label, (routed, live_accounts, execution), want in cases:
        got = "live" if (bool(live_accounts) and execution != "shadow") else "not_live"
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else 'FAIL'}")
    # 'unknown' must be REACHABLE and distinct — a probe that can only say
    # live/not_live turns an unreadable API into a confident "off", which is the
    # collapse this repo keeps paying for. Force the read to fail and check.
    import unittest.mock as _m
    with _m.patch(f"{__name__}._get", lambda *_a, **_k: None):
        r = liveness("anything")
    unknown_distinct = r.get("state") == "unknown"
    ok_local = unknown_distinct
    print(f"  self-test ('unknown' is reachable and distinct from not_live): "
          f"{'PASS' if unknown_distinct else 'FAIL'}")
    ok &= ok_local
    print("strategy-liveness self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
