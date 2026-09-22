#!/usr/bin/env python3
"""E32 — the ONE procedure that answers *"what is the running trader trading?"*

Operator, 2026-09-22:

    "we need to be able to verify what's actually trading... it's being
    operated from our VM, which means there is a single source of truth —
    whatever the VM is using is de facto the single source of truth for what's
    actually trading. And if we can't verify that, then we need to build the
    mechanism to verify that... there's no reason that that question should
    just stay open-ended."

This is that mechanism for the layers it can reach. The full six-layer map,
including the three layers this script does NOT answer and why, is
``docs/reference/verifying-what-is-trading.md`` — read it before quoting any
output from here, because what this tool cannot see is as load-bearing as
what it can.

WHAT IT ANSWERS
    Layer 4 — the roster and per-account gates the trading PROCESS holds.
    Layer 5 — the config files on the VM's disk.
    Layer 6 — the git revision that checkout is at.
and, above all, **whether 4 and 5 agree** — which is the fact that turns
"merged" into "deployed" into "observed".

WHAT IT DOES NOT ANSWER
    Layers 1–3 (broker positions, closed trades, decision-level emissions).
    Those have their own endpoints and their own caveats; the mapping doc
    names each one. This tool NEVER infers roster membership from trades —
    a roster is a list fact, not a behaviour fact, and a leg that has not
    traded may simply be sidelined.

READ-ONLY. Opens three files and shells one ``git rev-parse``. Writes nothing.

Usage
    python3 scripts/ops/verify_running_roster.py            # on the VM
    python3 scripts/ops/verify_running_roster.py --json     # machine-readable
    python3 scripts/ops/verify_running_roster.py --status-json <path>

Off the VM, read the same payload through the API instead — the endpoint is
the same composition:

    GET https://ict-bot.duckdns.org/api/bot/runtime-config

Exit codes — the three ``reload_state`` values, never collapsed:
    0  current        — the process loaded the bytes that are on disk
    2  pending_reload — the file moved and the process has not taken it
    1  unknown        — we could not establish it. NOT a pass, NOT a failure
                        of the trader: a failure of the OBSERVATION.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from src.runtime.loaded_config import (  # noqa: E402
    PROCESS_NOT_WRITTEN,
    PROCESS_OBSERVED,
    PROCESS_UNREADABLE,
    RELOAD_CURRENT,
    RELOAD_PENDING,
    RELOAD_UNKNOWN,
)
from src.web.api.routers.runtime_config import build_runtime_config  # noqa: E402


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO), capture_output=True, text=True, timeout=5, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    # Deliberately not "unknown" spelled as an empty string: layer 6 being
    # unreadable is a state, and it must survive into the output.
    return "unreadable"


def _render(payload: dict) -> int:
    state = payload["process_state"]
    print("=" * 72)
    print("WHAT IS THE RUNNING TRADER ACTUALLY TRADING?")
    print("=" * 72)
    print()

    # ---- Layer 4 -----------------------------------------------------------
    print("LAYER 4 — the process's own loaded state")
    print(f"  process_state : {state}")
    print(f"  why           : {payload['process_state_why']}")
    if state == PROCESS_OBSERVED:
        held = payload["loaded"]
        print(f"  pid           : {held.get('pid')}   booted {held.get('boot_utc')}")
        sy = held.get("strategies_yaml") or {}
        ay = held.get("accounts_yaml") or {}
        names = sy.get("names")
        print(f"  registry      : {sy.get('held_state')} "
              f"({'no roster observed' if names is None else str(len(names)) + ' strategies'})"
              f" loaded_at={sy.get('loaded_at_utc')}")
        resolved = ay.get("resolved")
        print(f"  accounts      : {ay.get('held_state')} "
              f"({'no accounts observed' if resolved is None else str(len(resolved)) + ' accounts'})"
              f" loaded_at={ay.get('loaded_at_utc')}")
        if resolved:
            print()
            print("  PER-ACCOUNT ROSTER THE PROCESS HOLDS "
                  "(gate = the RiskManager the process built, not the file):")
            for aid in sorted(resolved):
                row = resolved[aid]
                gate = "dry_run" if row.get("dry_run") else "LIVE"
                legs = row.get("strategies")
                shown = ("— no `strategies:` key (legacy fallthrough)"
                         if legs is None else
                         ("(explicitly empty — blocks all)" if legs == []
                          else ", ".join(legs)))
                print(f"    {aid:<22} {gate:<8} {row.get('account_class','?'):<12} {shown}")
    elif state == PROCESS_NOT_WRITTEN:
        print("  ⚠️  The artifact was READ and carries no process block. The running")
        print("      build predates E31, or has not ticked since boot.")
        print("      THIS IS NOT `the trader holds no strategies`.")
    elif state == PROCESS_UNREADABLE:
        print("  ⚠️  WE COULD NOT LOOK — the artifact itself is missing or malformed.")
        print("      No claim is being made about the process.")
    else:
        print(f"  ⚠️  UNRECOGNISED process_state {state!r}. Treated as `we could")
        print("      not look`; no claim is being made about the process.")
    print()

    # ---- Layer 5 + the comparison -----------------------------------------
    print("LAYER 5 — the config files on this VM, and whether layer 4 has them")
    exit_code = 0
    worst = RELOAD_CURRENT
    for key in ("strategies_yaml", "accounts_yaml"):
        rep = payload["reload"][key]
        disk = payload["on_disk"][key]
        rs = rep["reload_state"]
        mark = {RELOAD_CURRENT: "✅", RELOAD_PENDING: "🔴", RELOAD_UNKNOWN: "❔"}[rs]
        print(f"  {mark} {key:<17} {rs}")
        print(f"      on disk : {disk['digest_state']} {disk['digest']} mtime={disk['mtime_utc']}")
        print(f"      loaded  : {rep['loaded_digest']}")
        print(f"      why     : {rep['why']}")
        if rs == RELOAD_PENDING:
            worst = RELOAD_PENDING
        elif rs == RELOAD_UNKNOWN and worst != RELOAD_PENDING:
            worst = RELOAD_UNKNOWN
    print()

    # ---- Layer 6 -----------------------------------------------------------
    print("LAYER 6 — git")
    print(f"  checkout HEAD here        : {_git_head()}")
    print(f"  git_sha the process wrote : {payload.get('git_sha')}")
    print("  (a matching sha says the CHECKOUT moved, never that the PROCESS did —")
    print("   that is what layer 4 above is for.)")
    print()

    print("TICK")
    print(f"  last_tick_utc={payload.get('last_tick_utc')} "
          f"age={payload.get('tick_age_seconds')}s ticking={payload.get('bot_ticking')}")
    print()
    print("VERDICT")
    print(f"  {payload['verdict']}")
    print()
    print("NOT ANSWERED HERE: layers 1–3 (broker truth, closed trades, decision")
    print("emissions). See docs/reference/verifying-what-is-trading.md § 'What we")
    print("still cannot see' — two of those three carry measured defects.")

    exit_code = {RELOAD_CURRENT: 0, RELOAD_UNKNOWN: 1, RELOAD_PENDING: 2}[worst]
    return exit_code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit the raw payload")
    ap.add_argument("--status-json", default=None,
                    help="override runtime_status.json (tests / a relayed copy)")
    args = ap.parse_args()

    payload = build_runtime_config(
        status_json=Path(args.status_json) if args.status_json else None,
    )
    if args.json:
        payload["git_head_here"] = _git_head()
        print(json.dumps(payload, indent=2, sort_keys=True))
        rs = {r["reload_state"] for r in payload["reload"].values()}
        if RELOAD_PENDING in rs:
            return 2
        return 0 if rs == {RELOAD_CURRENT} else 1
    return _render(payload)


if __name__ == "__main__":
    raise SystemExit(main())
