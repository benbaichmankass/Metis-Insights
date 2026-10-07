#!/usr/bin/env python3
"""Flag Stage-2 MIRROR positions that the live account does not hold.

Stage 2 is one stage: ``alpaca_portfolio`` / ``bybit_portfolio`` carry the
IDENTICAL trades as ``alpaca_live`` / ``bybit_2`` (CLAUDE.md § promotion
ladder), and the mirror's net-of-cost window is the DEMOTION signal. A mirror
position with no live twin pollutes that window. Two such legacy shorts (TLT,
IEF) sat on ``alpaca_portfolio`` from the 2026-09-21 roster cut until noticed
2026-10-04 (PI-20261004-J4SFBKU9-0001) because nothing compared the two books.

    scripts/ops/diag_fetch.sh exchange_positions > pos.json
    python3 scripts/ops/check_mirror_orphans.py pos.json

Per mirror pair the verdict for each mirror position (symbol, side) is:

  live_twin     the live account holds the same symbol+side            -> fine
  acknowledged  no twin, but listed in config/mirror_window_exclusions.yaml
                (an operator-decided, stop-managed legacy position)    -> noted
  ORPHAN        no twin, not acknowledged                              -> FINDING

and each acknowledgement whose position is gone is ``stale_ack`` (remove the
entry). A pair whose accounts could not be read is ``unreadable`` -- "we could
not look" is NEVER folded into "no orphans". Exit 1 on any ORPHAN or
unreadable pair, else 0.

Mirror pairs come from ``config/accounts.yaml`` (``paper_role: portfolio`` plus
``mandate_resolver.MIRROR_OF``) -- not a second hard-coded list.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ops.mandate_resolver import MIRROR_OF  # noqa: E402
from src.config.mirror_exclusions import load_mirror_exclusions  # noqa: E402

_SIDES = {"buy": "long", "long": "long", "sell": "short", "short": "short"}


def _key(p: Dict[str, Any]) -> Tuple[str, str]:
    return (str(p.get("symbol") or "").upper(),
            _SIDES.get(str(p.get("side") or "").lower(), str(p.get("side") or "").lower()))


def check(payload: Dict[str, Any], exclusions: Dict[str, Any],
          pairs: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Pure: ``/api/diag/exchange_positions`` body + exclusions -> verdicts."""
    pairs = MIRROR_OF if pairs is None else pairs
    by_id = {a.get("account_id"): a for a in payload.get("accounts") or []}
    acks = exclusions.get("exclusions") or []
    findings: List[Dict[str, Any]] = []
    unreadable: List[str] = []
    for live_id, mirror_id in sorted(pairs.items()):
        live, mirror = by_id.get(live_id), by_id.get(mirror_id)
        bad = [i for i, a in ((live_id, live), (mirror_id, mirror))
               if a is None or a.get("error") or a.get("positions") is None]
        if bad:
            unreadable.append(f"{live_id}->{mirror_id}: unreadable {bad}")
            continue
        live_keys = {_key(p) for p in live["positions"]}
        ack_keys = {(str(e.get("symbol", "")).upper(), _key({"side": e.get("side")})[1])
                    for e in acks if e.get("account_id") == mirror_id}
        seen_ack = set()
        for p in mirror["positions"]:
            k = _key(p)
            row = {"pair": f"{live_id}->{mirror_id}", "symbol": k[0], "side": k[1],
                   "size": p.get("size"), "unrealised_pnl": p.get("unrealised_pnl")}
            if k in live_keys:
                continue
            if k in ack_keys:
                seen_ack.add(k)
                findings.append({**row, "verdict": "acknowledged"})
            else:
                findings.append({**row, "verdict": "ORPHAN"})
        for k in sorted(ack_keys - seen_ack):
            findings.append({"pair": f"{live_id}->{mirror_id}", "symbol": k[0], "side": k[1],
                             "verdict": "stale_ack"})
    return {
        "exclusionsReadState": exclusions.get("readState"),
        "unreadable": unreadable,
        "findings": findings,
        "orphans": [f for f in findings if f["verdict"] == "ORPHAN"],
        "ok": not unreadable and exclusions.get("readState") != "unreadable"
              and not any(f["verdict"] == "ORPHAN" for f in findings),
    }


def render(res: Dict[str, Any]) -> str:
    lines = [f"mirror-orphan check: {'OK' if res['ok'] else 'FINDING'} "
             f"(exclusions: {res['exclusionsReadState']})"]
    lines += [f"  unreadable: {u}" for u in res["unreadable"]]
    for f in res["findings"]:
        lines.append(f"  {f['verdict']:<12} {f['pair']} {f['symbol']} {f['side']}"
                     + (f" size={f.get('size')} upl={f.get('unrealised_pnl')}" if "size" in f else ""))
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("usage: check_mirror_orphans.py <exchange_positions.json>", file=sys.stderr)
        return 2
    try:
        payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read {argv[1]}: {e} -- we could not look", file=sys.stderr)
        return 2
    res = check(payload, load_mirror_exclusions())
    print(render(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
