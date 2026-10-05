#!/usr/bin/env python3
"""Commit-ready snapshot of the LIVE Stage-1 soak states, for MD-S1-CUT-ON-STAGE0-FAIL.

    python3 scripts/ops/snapshot_soak_state_for_mandate.py            # fetch the live brief
    python3 scripts/ops/snapshot_soak_state_for_mandate.py --from-file brief.json
    → writes comms/mandate_evidence/soak_state/<UTC-date>.json

WHY A SNAPSHOT. `scripts/ops/mandate_resolver.py` reads only committed files
("a claim in a PR body is not a record"), but soak state is computed LIVE on the
VM by `soak_state.soak_states()` from the contracts plus the journal DB, and is
never written into the repo. The mandate's soak-complete clause therefore reads
this snapshot, and refuses one older than `bar.soak_snapshot_max_age_days`.

SOURCE. `GET /api/bot/work/brief` (Tier-1, read-only, served through Caddy), § 4
"🌱 SOAKS". The brief lists every NON-accruing soak as one line and folds the
accruing ones into a count, so a soak absent here is NOT complete -- the
resolver reads absence as NEEDS-DATA, never as ready.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "comms/mandate_evidence/soak_state"
BRIEF_URL = "https://ict-bot.duckdns.org/api/bot/work/brief"
#: `- **SOAK-stage1-bybit_1-eth_pullback_2h** eth_pullback_2h @bybit_1 · ready · started
#: 2026-06-11 · day 115 · 38/10 closes · ends 2026-08-09 · <reason>`
LINE = re.compile(r"^- \*\*(?P<id>SOAK-[^*]+)\*\* (?P<leg>\S+) @(?P<accounts>\S+) · "
                  r"(?P<state>[a-z_]+) · started (?P<started>\S+) · day (?P<day>\d+) · "
                  r"(?P<progress>[^·]+?) · ends (?P<end>\S+) · (?P<reason>.*)$")


def parse(brief: Dict[str, Any]) -> List[Dict[str, Any]]:
    md = brief.get("markdown") or ""
    start = md.find("### 🌱 SOAKS")
    if start < 0:
        raise SystemExit("brief carries no '### 🌱 SOAKS' section -- could not look")
    end = md.find("\n### ", start + 1)
    rows = []
    for line in md[start:end if end > 0 else None].splitlines():
        mt = LINE.match(line.strip())
        if not mt:
            continue
        g = mt.groupdict()
        kind = "stage1" if g["id"].startswith("SOAK-stage1-") else g["id"].split("-")[1]
        rows.append({"id": g["id"], "leg": g["leg"], "accounts": g["accounts"].split(","),
                     "kind": kind, "state": g["state"], "started": g["started"],
                     "progress": g["progress"].strip(), "end_date": g["end"]})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from-file", help="a saved /api/bot/work/brief JSON response")
    ap.add_argument("--url", default=BRIEF_URL)
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    if a.from_file:
        brief = json.loads(Path(a.from_file).read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(a.url, timeout=30) as r:  # noqa: S310 (fixed https URL)
            brief = json.loads(r.read().decode("utf-8"))
    if not brief.get("present") or not brief.get("generatedAt"):
        raise SystemExit(f"brief not present ({brief.get('reason')!r}) -- could not look")
    rows = parse(brief)
    if not rows:
        raise SystemExit("SOAKS section parsed to 0 rows -- the line format changed; refusing "
                         "to write an empty snapshot that would read as 'nothing complete'")
    snap = {
        "captured_at": brief["generatedAt"],
        "source": "GET /api/bot/work/brief § 4 SOAKS (soak_state.soak_states(), computed live "
                  "on the VM)",
        "note": "Accruing soaks are folded into a count by the brief and are NOT listed; a "
                "soak absent here is not complete.",
        "captured_by": "scripts/ops/snapshot_soak_state_for_mandate.py",
        "soaks": rows,
    }
    day = datetime.fromisoformat(brief["generatedAt"].replace("Z", "+00:00")) \
        .astimezone(timezone.utc).date().isoformat()
    out = Path(a.out_dir) / f"{day}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(rows)} soaks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
