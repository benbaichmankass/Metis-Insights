#!/usr/bin/env python3
"""What window each leg would need to reach the evidence floor — over a COMMITTED day.

WHY THIS EXISTS SEPARATELY FROM THE GENERATOR
---------------------------------------------
`scripts/ml/strategy_review_packet.py` now publishes an ``evidence_horizon``
block per leg, so every FUTURE run carries this diagnosis on the decision
surface. That does nothing for the run the operator has to decide on today: the
one committed index (``comms/strategy_reviews/2026-09-01/INDEX.json``) predates
the field, and **it is not rewritten** — a committed decision record is the
historical artifact of what that run actually said, and back-filling a field
into it would make a later reader believe the run published something it did
not.

So this script reads the committed day AS IT STANDS and computes the horizon
from the numbers already in it. It writes nothing into
``comms/strategy_reviews/``.

⚠️ **THE COMMITTED DAY DIRECTORY JOINS TWO DIFFERENT RUNS, AND THIS SCRIPT
STAMPS THAT RATHER THAN HIDING IT.** Measured on 2026-09-01: ``INDEX.json`` was
written by run #10656 (``generated_at`` 12:51:37Z) while **all 52
per-strategy packets are from run #10652** (12:03:27Z) — #10656 rewrote only
the index. Population: all 52 strategies in both artifacts; **one row already
disagrees** — ``qqq_pullback_1h`` reads ``n_closed=1, pnl=-212.52`` in the index
and ``n_closed=0, pnl=0.0`` in its own packet. The funnel counts
(``n_decisions``/``n_filled``) exist ONLY in the packets and ``n_closed`` is
authoritative in the index, so this report necessarily crosses that boundary —
and every row it emits carries ``sources_agree`` saying whether the two
artifacts agreed about that leg. A row where they disagree is reported, never
silently preferred. Filed as an OPEN-ITEMS row; the durable fix is in the
generator, which now publishes the funnel counts on the index so the join is
not needed at all.

⚠️ **THIS PROPOSES, IT NEVER ENACTS.** It changes no config, retires no leg,
and does not lower ``MIN_CLOSED_FOR_ACTION``. Its output is the evidence behind
an operator decision (``docs/design/evidence-floor-horizon-PROPOSAL.md``).

Usage::

    python3 scripts/ml/evidence_floor_report.py [--date 2026-09-01] [--out PATH]
    python3 scripts/ml/evidence_floor_report.py --self-test

Exit 0 on a report, 1 when the requested day cannot be read.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.evidence_horizon import (  # noqa: E402
    REACHABLE,
    STRUCTURALLY_UNGRADEABLE,
    UNBOUNDED_NO_CLOSES,
    evidence_horizon,
    summarize_horizons,
)

_COMMITTED_ROOT = _REPO_ROOT / "comms" / "strategy_reviews"

#: Mirrors ``strategy_review_packet.MIN_CLOSED_FOR_ACTION``. Imported, not
#: copied — a second literal is how the report and the gate come to disagree
#: about what "gradeable" means.
def _floor() -> int:
    from scripts.ml.strategy_review_packet import MIN_CLOSED_FOR_ACTION  # noqa: PLC0415

    return MIN_CLOSED_FOR_ACTION


def _load_day(day_dir: Path) -> Dict[str, Any]:
    """Read one committed day. Raises on an unreadable index — never returns empty.

    ⚠️ An empty result and a failed read must not render alike; the caller
    exits non-zero rather than printing a clean-looking report over nothing.
    """
    index = json.loads((day_dir / "INDEX.json").read_text(encoding="utf-8"))
    packets: Dict[str, Dict[str, Any]] = {}
    for path in sorted(day_dir.glob("*.json")):
        if path.name == "INDEX.json":
            continue
        try:
            pkt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = pkt.get("strategy")
        if name:
            packets[str(name)] = pkt
    return {"index": index, "packets": packets}


def _window_days(index: Dict[str, Any], packets: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """The review window in days, or None when no artifact states it.

    ⚠️ **NEVER DEFAULTS TO 7.** The whole finding is that a count without its
    exposure cannot be interpreted, and inventing the exposure here would be
    that error committed by the tool reporting it. The index publishes
    ``window_days`` from this change onward; before that the only statement of
    the window is the packets' own ``window_start``/``window_end``.
    """
    stated = index.get("window_days")
    if isinstance(stated, (int, float)) and stated > 0:
        return float(stated)
    from datetime import datetime  # noqa: PLC0415

    for pkt in packets.values():
        start, end = pkt.get("window_start"), pkt.get("window_end")
        if not start or not end:
            continue
        try:
            a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        days = (b - a).total_seconds() / 86400.0
        if days > 0:
            return days
    return None


def build_rows(day: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per graded leg, with its horizon and the provenance of its inputs."""
    index, packets = day["index"], day["packets"]
    floor = _floor()
    window_days = _window_days(index, packets)
    out: List[Dict[str, Any]] = []
    for row in index.get("rows") or []:
        name = str(row.get("strategy") or "")
        pkt = packets.get(name) or {}
        head = pkt.get("headline") or {}

        # `n_closed` is taken from the INDEX — it is the run that produced the
        # decision record. The funnel counts exist only in the packet.
        n_closed = row.get("n_closed")
        pkt_closed = head.get("n_closed")
        sources_agree = (
            None if (pkt_closed is None or n_closed is None) else (pkt_closed == n_closed)
        )

        horizon = row.get("evidence_horizon") or evidence_horizon(
            floor=floor,
            n_closed=n_closed,
            window_days=window_days,
            n_decisions=row.get("n_decisions", head.get("n_decisions")),
            n_filled=row.get("n_filled", head.get("n_filled")),
            execution=row.get("execution", pkt.get("execution")),
        )
        out.append({
            "strategy": name,
            "execution": row.get("execution", pkt.get("execution")),
            "n_decisions": row.get("n_decisions", head.get("n_decisions")),
            "n_filled": row.get("n_filled", head.get("n_filled")),
            "n_closed": n_closed,
            "pnl_total": row.get("pnl_total"),
            "horizon": horizon,
            # ⚠️ False means the index and the packet disagreed about this leg —
            # a CROSS-RUN join, reported rather than resolved. None means one
            # side did not state it.
            "sources_agree": sources_agree,
            "packet_present": bool(pkt),
        })
    return out


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.1f}{suffix}"
    return f"{value}{suffix}"


def render_markdown(day: Dict[str, Any], rows: List[Dict[str, Any]], utc_date: str) -> str:
    index, packets = day["index"], day["packets"]
    window_days = _window_days(index, packets)
    summary = summarize_horizons([r["horizon"] for r in rows])
    by_class = summary["by_horizon_class"]
    by_stage = summary["by_funnel_stage"]
    disagreeing = [r for r in rows if r["sources_agree"] is False]
    losers = [r for r in rows if (r.get("pnl_total") or 0) < 0]
    loser_pnl = sum(float(r["pnl_total"]) for r in losers)

    # ⚠️ A SPAN, not 52 enumerated timestamps. Each packet is stamped at the
    # moment it was written, so listing them all buries the one fact that
    # matters — whether the packets and the index came from the SAME run.
    _stamps = sorted(x for x in (str(p.get("generated_at") or "") for p in packets.values()) if x)
    pkt_span = (
        f"{_stamps[0]} .. {_stamps[-1]} ({len(_stamps)} packets)" if _stamps else "—"
    )
    lines: List[str] = []
    a = lines.append
    a(f"# Evidence-floor horizon — committed run {utc_date}")
    a("")
    a("**Population, stated up front.** Every number below is over the "
      f"**{len(rows)} legs in `comms/strategy_reviews/{utc_date}/INDEX.json`** "
      f"(`generated_at` `{index.get('generated_at')}`), at a review window of "
      f"**{_fmt(window_days)} days** and the generator's own floor of "
      f"**n_closed >= {_floor()}** (`MIN_CLOSED_FOR_ACTION`).")
    a("")
    a("⚠️ **The funnel counts come from the per-strategy PACKETS, which are a "
      f"DIFFERENT RUN.** Packet `generated_at` span: {pkt_span}. "
      f"**{len(disagreeing)} of {len(rows)} legs disagree** between the two "
      "artifacts about `n_closed`"
      + (": " + ", ".join(f"`{r['strategy']}`" for r in disagreeing) if disagreeing else "")
      + ". `n_closed` is taken from the INDEX throughout (it is the run that "
      "produced the decision record); the funnel split is the packets'.")
    a("")
    a("⚠️ **This report proposes nothing and enacts nothing.** No leg is "
      "retired, no floor is lowered, no window is changed.")
    a("")
    a("## 1. Where the fleet actually stops")
    a("")
    a(f"| funnel stage | legs (of {len(rows)}) | what a wider window does |")
    a("|---|---:|---|")
    a(f"| closed >=1 trade | {by_stage['closing']} | **accumulates more evidence** — the only stage waiting reaches |")
    a(f"| filled, closed nothing | {by_stage['filled_not_closed']} | may reach it: the positions are open, not absent |")
    a(f"| signalled, filled nothing | {by_stage['decided_not_filled']} | **nothing** unless the fills start |")
    a(f"| no decision at all | {by_stage['no_decisions']} | **nothing** unless the leg starts signalling |")
    a(f"| ungradeable input | {by_stage['unknown']} | we could not look — not a reading of zero |")
    a("")
    a("## 2. How long until each leg would be gradeable")
    a("")
    a(f"| horizon class | legs (of {len(rows)}) | meaning |")
    a("|---|---:|---|")
    a(f"| `gradeable_now` | {by_class['gradeable_now']} | at or above the floor already |")
    a(f"| `reachable` | {by_class[REACHABLE]} | a finite projection exists — **the only class a wider window reaches** |")
    a(f"| `unbounded_no_closes` | {by_class[UNBOUNDED_NO_CLOSES]} | closed nothing, so NO close rate was measured; no finite window follows |")
    a(f"| `structurally_ungradeable` | {by_class[STRUCTURALLY_UNGRADEABLE]} | cannot close a trade at ANY window under its current config |")
    a(f"| `unknown` | {by_class['unknown']} | an input was missing — we could not look |")
    a("")
    a("**Read the day-counts as an INTERVAL, never as the point estimate.** A "
      "projection off 1 close is a one-sample estimate; quoting its point "
      "value as a plan is the low-n error moved from the grade to the "
      "forecast. `optimistic` and `conservative` are the 95% one-sided "
      "Poisson limits on the close rate; `unbounded` means the lower limit is "
      "zero, so no finite horizon follows from what was observed.")
    a("")
    a("| strategy | exec | dec | fill | clos | pnl | class | days: optimistic / point / conservative |")
    a("|---|---|---:|---:|---:|---:|---|---|")

    def sort_key(r: Dict[str, Any]):
        h = r["horizon"]
        pt = h.get("days_to_floor_point")
        cls = h.get("horizon_class")
        rank = {"gradeable_now": 0, REACHABLE: 1, UNBOUNDED_NO_CLOSES: 2,
                STRUCTURALLY_UNGRADEABLE: 3}.get(cls, 4)
        return (rank, pt if pt is not None else 1e12, r["strategy"])

    for r in sorted(rows, key=sort_key):
        h = r["horizon"]
        cons = h.get("days_to_floor_conservative")
        cons_s = "unbounded" if cons is None and h.get("horizon_class") in (
            REACHABLE, UNBOUNDED_NO_CLOSES) else _fmt(cons)
        a("| `{s}` | {e} | {d} | {f} | {c} | {p} | `{k}` | {o} / {pt} / {cs} |".format(
            s=r["strategy"], e=r.get("execution") or "—",
            d=_fmt(r.get("n_decisions")), f=_fmt(r.get("n_filled")),
            c=_fmt(r.get("n_closed")),
            p=f"{r.get('pnl_total'):,.2f}" if r.get("pnl_total") is not None else "—",
            k=h.get("horizon_class"),
            o=_fmt(h.get("days_to_floor_optimistic")),
            pt=_fmt(h.get("days_to_floor_point")),
            cs=cons_s))
    a("")
    a("## 3. What the fleet's PnL is waiting on")
    a("")
    a(f"**{len(losers)} of {len(rows)} legs carry negative provenance-trusted "
      f"pnl, totalling {loser_pnl:,.2f}** over this window. None of them could "
      "produce a KILL or a DEMOTE, whatever the number, because all of them "
      "sit under the floor. That is the cost of the gap — it is NOT an "
      "argument for lowering the floor, because a KILL fired off 1-8 closes is "
      "the noise the floor exists to refuse.")
    a("")
    reach = [r for r in rows if r["horizon"].get("horizon_class") == REACHABLE]
    if reach:
        widest = max(reach, key=lambda r: r["horizon"]["days_to_floor_point"])
        a(f"**A window that graded every `reachable` leg would need "
          f"{widest['horizon']['days_to_floor_point']:,.1f} days** (`"
          f"{widest['strategy']}`, the slowest of the {len(reach)}), and would "
          f"still grade **none** of the "
          f"{by_class[UNBOUNDED_NO_CLOSES] + by_class[STRUCTURALLY_UNGRADEABLE]} "
          "legs in the other two classes. Read those two numbers together or "
          "the window reads as one that grades the fleet.")
        a("")
    a("---")
    a("")
    a(f"_Generated by `scripts/ml/evidence_floor_report.py` over "
      f"`comms/strategy_reviews/{utc_date}/`. Proposes; enacts nothing._")
    return "\n".join(lines) + "\n"


def _self_test() -> int:
    """Prove the report can find a positive before its silence is trusted."""
    floor = 20
    reachable = evidence_horizon(floor=floor, n_closed=8, window_days=7,
                                 n_decisions=21, n_filled=8, execution="live")
    assert reachable["horizon_class"] == REACHABLE, reachable
    assert reachable["days_to_floor_point"] == 17.5, reachable
    zero = evidence_horizon(floor=floor, n_closed=0, window_days=7,
                            n_decisions=0, n_filled=0, execution="live")
    assert zero["horizon_class"] == UNBOUNDED_NO_CLOSES, zero
    assert zero["days_to_floor_point"] is None, zero
    assert zero["observed_close_rate_per_day"] is None, zero
    shadow = evidence_horizon(floor=floor, n_closed=0, window_days=7,
                              n_decisions=11, n_filled=0, execution="shadow")
    assert shadow["horizon_class"] == STRUCTURALLY_UNGRADEABLE, shadow
    day = {"index": {"generated_at": "x", "window_days": 7, "rows": [
        {"strategy": "a", "n_closed": 8, "n_decisions": 21, "n_filled": 8,
         "execution": "live", "pnl_total": -1.0},
    ]}, "packets": {}}
    rows = build_rows(day)
    assert len(rows) == 1 and rows[0]["horizon"]["horizon_class"] == REACHABLE
    md = render_markdown(day, rows, "self-test")
    assert "Population, stated up front" in md
    print("evidence_floor_report --self-test: OK (3 horizon classes + a rendered report)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--date", default=None, help="committed UTC date (default: newest).")
    p.add_argument("--out", default=None, help="write the markdown here instead of stdout.")
    p.add_argument("--self-test", action="store_true", help="prove the probe finds a positive.")
    args = p.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not _COMMITTED_ROOT.exists():
        print(f"no committed reviews at {_COMMITTED_ROOT}", file=sys.stderr)
        return 1
    dates = sorted((d.name for d in _COMMITTED_ROOT.iterdir() if d.is_dir()), reverse=True)
    target = args.date or (dates[0] if dates else None)
    if not target or not (_COMMITTED_ROOT / target / "INDEX.json").exists():
        print(f"no committed INDEX.json for date={target!r} (available: {dates})", file=sys.stderr)
        return 1

    day = _load_day(_COMMITTED_ROOT / target)
    rows = build_rows(day)
    md = render_markdown(day, rows, target)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"wrote {args.out} ({len(rows)} legs)")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
