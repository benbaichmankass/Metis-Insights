#!/usr/bin/env python3
"""Geometry amend census — "the SL moves, the TP never moves" as a number.

ONE QUESTION: of the trades that CLOSED since ``--since``, how many had their
stop-loss (SL) amended after entry, and how many had their take-profit (TP)
amended, per account class and per leg?

Method (reproduces lane ACTIVE-GEOMETRY-PLAN, 2026-10-07, research/queue/
PLANNING.md § 5):

  * population  = ``trades`` rows with ``status == 'closed'`` and
                  ``timestamp >= --since``, joined to ``order_packages`` on
                  ``trades.order_package_id``;
  * entry-time  = ``order_packages.exit_plan.stop.price`` (SL) and
                  ``order_packages.exit_plan.final.price`` (TP);
  * amended     = ``order_packages.sl`` / ``.tp`` (the CURRENT level) differs
                  from the entry-time level by more than ``--tolerance`` (a
                  fraction of the entry-time level; default 0.05% = 5e-4).

Every count names its denominator, and every class carries a ``read_state`` so
"we could not look" and "we looked and found zero" are different strings
(CLAUDE.md § "Three things from the reference"):

  measured          closed trades with a matching package were counted
  no_closed_rows    rows exist for the class but none closed in the window
  absent            no row for the class at all in the pulled journal
  unreadable        the pull failed / was incomplete (counts are None)

Provenance: ``MEASURED`` (journal rows pulled from the live diag surface).

The TP denominator excludes closes whose package carries no ``final`` price
(e.g. ``squeeze_breakout_4h``, no ``exit_plan``) — they cannot show a TP amend
either way. ``tp_amended == 0`` over a non-empty denominator is a real zero.

Prop is reported in two parts because prop fills live in the prop journal, not
the ``trades`` table: the ``trades``-table closed rows (comparable to the other
classes) and the prop-ticket status counts from ``/api/bot/prop/tickets``.

Usage:
    python3 scripts/research/geometry_amend_census.py [--since 2026-09-01]
        [--tolerance 5e-4] [--out-dir docs/research/geometry-census]
        [--date YYYY-MM-DD] [--from-dir DIR] [--no-write]

``--from-dir DIR`` reads ``trades.json``, ``order_packages.json`` and
(optionally) ``prop_tickets.json`` from DIR instead of the live VM (offline
replay, and the unit-test fixture path). Needs ``DIAG_READ_TOKEN`` for a live
run (scripts/ops/diag_fetch.sh).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SINCE = "2026-09-01"
DEFAULT_TOLERANCE = 5e-4
PAGE = 1000
PUBLIC_BASE = "https://ict-bot.duckdns.org"
CLASS_ORDER = ("real_money", "mirror", "paper", "prop", "unclassified")
# The two mirror accounts carry the live roster at honest size
# (tests/test_paper_portfolio_accounts.py); config marks them account_class
# 'paper', so the mirror split is by name.
MIRROR_ACCOUNTS = frozenset({"bybit_portfolio", "alpaca_portfolio"})


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------
def load_account_classes(path: Path | None = None) -> dict[str, str]:
    """account_id -> account_class from config/accounts.yaml ({} if unreadable)."""
    try:
        import yaml

        doc = yaml.safe_load((path or REPO / "config" / "accounts.yaml").read_text())
        accts = doc.get("accounts", doc) if isinstance(doc, dict) else {}
        return {
            k: str(v.get("account_class"))
            for k, v in accts.items()
            if isinstance(v, dict) and v.get("account_class")
        }
    except Exception:  # noqa: BLE001  # allow-silent: falls back to the row's own account_class; unclassified is counted, never dropped
        return {}


def classify(account_id: str | None, row_class: str | None, cfg: dict[str, str]) -> str:
    if account_id in MIRROR_ACCOUNTS:
        return "mirror"
    klass = cfg.get(account_id or "") or row_class
    if klass in ("real_money", "paper", "prop"):
        return klass
    if account_id and ("prop_" in account_id or account_id.endswith(("_prop",))):
        return "prop"
    return "unclassified"


# --------------------------------------------------------------------------
# data pull
# --------------------------------------------------------------------------
def _diag(path: str) -> dict[str, Any]:
    out = subprocess.run(
        [str(REPO / "scripts" / "ops" / "diag_fetch.sh"), path],
        capture_output=True, text=True, timeout=180, check=True,
    ).stdout
    return json.loads(out)


def pull_table(table: str) -> dict[str, Any]:
    """Page a journal table. Returns {rows, total_rows, state}."""
    rows: list[dict[str, Any]] = []
    total = None
    offset = 0
    while True:
        env = _diag(f"journal?table={table}&limit={PAGE}&offset={offset}&envelope=1")
        page = env.get("rows") or []
        total = env.get("total_rows", total)
        rows.extend(page)
        offset += len(page)
        if not page or not env.get("has_more"):
            break
    return {"rows": rows, "total_rows": total}


def pull_prop_tickets() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{PUBLIC_BASE}/api/bot/prop/tickets?limit=500", timeout=60) as r:
            doc = json.loads(r.read())
        return {"tickets": doc.get("tickets") or [], "present": bool(doc.get("present"))}
    except Exception as exc:  # noqa: BLE001  # allow-silent: surfaced as read_state=unreadable on the prop ticket block
        return {"tickets": None, "present": None, "error": type(exc).__name__}


def load_from_dir(d: Path) -> dict[str, Any]:
    def one(name: str):
        p = d / name
        if not p.exists():
            return None
        doc = json.loads(p.read_text())
        return doc if isinstance(doc, dict) else {"rows": doc, "total_rows": len(doc)}

    pt = one("prop_tickets.json")
    return {
        "trades": one("trades.json"),
        "order_packages": one("order_packages.json"),
        "prop": ({"tickets": pt.get("tickets", pt.get("rows")), "present": True} if pt else None),
    }


# --------------------------------------------------------------------------
# census
# --------------------------------------------------------------------------
def _levels(pkg: dict[str, Any]) -> tuple[float | None, float | None, bool]:
    """(entry_sl, entry_tp, has_exit_plan) from a package's exit_plan JSON."""
    ep = pkg.get("exit_plan")
    if isinstance(ep, str):
        try:
            ep = json.loads(ep)
        except ValueError:
            ep = None
    if not isinstance(ep, dict):
        return None, None, False
    sl = (ep.get("stop") or {}).get("price")
    tp = (ep.get("final") or {}).get("price")
    return (float(sl) if sl else None, float(tp) if tp else None, True)


def _moved(current: Any, entry: float | None, tol: float) -> bool | None:
    """True/False when comparable; None when either side is missing."""
    if entry is None or current is None:
        return None
    return abs(float(current) - entry) / abs(entry) > tol


def _blank() -> dict[str, Any]:
    return {
        "closed_in_window": 0, "matched_package": 0, "unmatched_package": 0,
        "no_exit_plan": 0, "sl_denominator": 0, "sl_amended": 0,
        "tp_denominator": 0, "tp_amended": 0,
    }


def _finish(c: dict[str, Any], rows_seen: int) -> dict[str, Any]:
    c = dict(c)
    if c["matched_package"] > 0:
        state = "measured"
    elif rows_seen == 0:
        state = "absent"
    elif c["closed_in_window"] == 0:
        state = "no_closed_rows"
    else:
        state = "no_matched_package"
    c["read_state"] = state
    for k in ("sl", "tp"):
        d = c[f"{k}_denominator"]
        c[f"{k}_amended_rate"] = (c[f"{k}_amended"] / d) if d else None
        if state != "measured":  # "could not look" is None, never 0
            c[f"{k}_amended"] = c[f"{k}_denominator"] = None
    return c


def compute(trades: list[dict], packages: list[dict], since: str, tol: float,
            cfg: dict[str, str]) -> dict[str, Any]:
    pk = {p["order_package_id"]: p for p in packages}
    cls_c: dict[str, dict] = defaultdict(_blank)
    leg_c: dict[tuple[str, str], dict] = defaultdict(_blank)
    seen: dict[str, int] = defaultdict(int)
    statuses: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in trades:
        klass = classify(t.get("account_id"), t.get("account_class"), cfg)
        seen[klass] += 1
        if (t.get("timestamp") or "") >= since:
            statuses[klass][t.get("status") or "null"] += 1
        if t.get("status") != "closed" or (t.get("timestamp") or "") < since:
            continue
        leg = (klass, t.get("strategy_name") or "unknown")
        for c in (cls_c[klass], leg_c[leg]):
            c["closed_in_window"] += 1
        p = pk.get(t.get("order_package_id"))
        if p is None:
            for c in (cls_c[klass], leg_c[leg]):
                c["unmatched_package"] += 1
            continue
        sl0, tp0, has_plan = _levels(p)
        for c in (cls_c[klass], leg_c[leg]):
            c["matched_package"] += 1
            if not has_plan:
                c["no_exit_plan"] += 1
                continue
            sm = _moved(p.get("sl"), sl0, tol)
            tm = _moved(p.get("tp"), tp0, tol)
            if sm is not None:
                c["sl_denominator"] += 1
                c["sl_amended"] += int(sm)
            if tm is not None:
                c["tp_denominator"] += 1
                c["tp_amended"] += int(tm)
    classes = {k: _finish(cls_c[k], seen[k]) for k in CLASS_ORDER if k in seen or k in cls_c}
    for k in CLASS_ORDER:
        classes.setdefault(k, _finish(_blank(), 0))
        classes[k]["window_status_counts"] = dict(sorted(statuses[k].items()))
    legs = [
        {"class": k[0], "leg": k[1], **_finish(v, 1)}
        for k, v in sorted(leg_c.items(), key=lambda kv: (CLASS_ORDER.index(kv[0][0]), -kv[1]["closed_in_window"], kv[0][1]))
    ]
    fleet = _blank()
    for k, v in cls_c.items():
        for f in fleet:
            fleet[f] += v[f]
    return {"classes": classes, "legs": legs, "fleet": _finish(fleet, sum(seen.values()))}


def prop_ticket_block(prop: dict[str, Any] | None) -> dict[str, Any]:
    if not prop or prop.get("tickets") is None:
        return {"read_state": "unreadable", "ticket_status_counts": None,
                "note": "prop tickets not readable; prop amend state is unknown, not zero"}
    counts: dict[str, int] = defaultdict(int)
    for t in prop["tickets"]:
        counts[f"{t.get('account_id') or 'none'}:{t.get('status')}"] += 1
    return {"read_state": "measured" if prop["tickets"] else "absent",
            "tickets_total": len(prop["tickets"]),
            "ticket_status_counts": dict(sorted(counts.items())),
            "note": "ticket rows carry the as-emitted sl/tp only; a prop amend needs the "
                    "prop journal (trail_amend rows), not this table"}


def run(data: dict[str, Any], since: str, tol: float, cfg: dict[str, str],
        generated: str) -> dict[str, Any]:
    tr, pk = data.get("trades"), data.get("order_packages")
    complete = bool(tr and pk)
    if complete:
        for blob in (tr, pk):
            if blob.get("total_rows") is not None and len(blob["rows"]) < blob["total_rows"]:
                complete = False
    if not complete:
        classes = {k: {"read_state": "unreadable", **{f: None for f in _blank()}} for k in CLASS_ORDER}
        body = {"classes": classes, "legs": [], "fleet": {"read_state": "unreadable"}}
    else:
        body = compute(tr["rows"], pk["rows"], since, tol, cfg)
    return {
        "census": "geometry_amend", "provenance": "MEASURED", "generated_utc": generated,
        "since": since, "tolerance_fraction": tol,
        "population": ("closed trades with timestamp >= since and a matching order package "
                       "(trades.order_package_id = order_packages.order_package_id); amend = current "
                       "order_packages.sl/.tp vs exit_plan.stop.price/.final.price entry-time level"),
        "pull": {
            "trades_rows": len(tr["rows"]) if tr else None,
            "trades_total_rows": tr.get("total_rows") if tr else None,
            "order_packages_rows": len(pk["rows"]) if pk else None,
            "order_packages_total_rows": pk.get("total_rows") if pk else None,
            "complete": complete,
        },
        **body,
        "prop_tickets": prop_ticket_block(data.get("prop")),
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def _cell(c: dict[str, Any], k: str) -> str:
    n, d = c.get(f"{k}_amended"), c.get(f"{k}_denominator")
    return "—" if not d or n is None else f"{n} / {d}"


def render_md(r: dict[str, Any]) -> str:
    out = [
        f"# Geometry amend census — {r['generated_utc'][:10]}",
        "",
        f"**Provenance:** {r['provenance']} · **since** `{r['since']}` · amend tolerance "
        f"{r['tolerance_fraction']:.2%} of the entry-time level.",
        "",
        f"**Population:** {r['population']}.",
        "",
        f"**Pull:** trades {r['pull']['trades_rows']} of {r['pull']['trades_total_rows']} rows, "
        f"order_packages {r['pull']['order_packages_rows']} of {r['pull']['order_packages_total_rows']}; "
        f"complete = {r['pull']['complete']}.",
        "",
        "Cells read `amended / denominator`. `—` means not measured (never 0).",
        "",
        "| class | read_state | closed in window | matched pkg | SL amended | TP amended |",
        "|---|---|--:|--:|--:|--:|",
    ]
    rows = list(r["classes"].items()) + [("fleet", r["fleet"])]
    for k, c in rows:
        out.append(f"| {k} | {c.get('read_state')} | {c.get('closed_in_window', '—')} | "
                   f"{c.get('matched_package', '—')} | {_cell(c, 'sl')} | {_cell(c, 'tp')} |")
    out += ["", "Window status counts per class (all `trades` rows since the date, any status):", ""]
    for k, c in r["classes"].items():
        out.append(f"- **{k}**: {c.get('window_status_counts') or '—'}")
    pt = r["prop_tickets"]
    out += ["", "## Prop", "",
            "Prop fills live in the prop journal, not `trades`; the `trades`-table row above is "
            "therefore the comparable denominator, and prop amend state beyond it is read from "
            "the executor journals (`trail_amend` / `trail_skip` rows).", "",
            f"- prop tickets read_state: **{pt['read_state']}** — {pt.get('ticket_status_counts')}",
            f"- {pt['note']}", "",
            "## Per leg (closed trades with a matching package)", "",
            "| class | leg | closed | SL amended | TP amended |", "|---|---|--:|--:|--:|"]
    for g in r["legs"]:
        out.append(f"| {g['class']} | {g['leg']} | {g['closed_in_window']} | {_cell(g, 'sl')} | {_cell(g, 'tp')} |")
    out += ["", "Rerun: `python3 scripts/research/geometry_amend_census.py` "
            "(needs `DIAG_READ_TOKEN`; see `.claude/skills/diag-data/SKILL.md`).", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    ap.add_argument("--out-dir", default=str(REPO / "docs" / "research" / "geometry-census"))
    ap.add_argument("--date", default=None, help="stamp for output files (default: today UTC)")
    ap.add_argument("--from-dir", default=None)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)

    now = dt.datetime.now(dt.timezone.utc)
    stamp = a.date or now.strftime("%Y-%m-%d")
    if a.from_dir:
        data = load_from_dir(Path(a.from_dir))
    else:
        data = {"trades": None, "order_packages": None, "prop": pull_prop_tickets()}
        for key in ("trades", "order_packages"):
            try:
                data[key] = pull_table(key)
            except Exception as exc:  # noqa: BLE001  # allow-silent: surfaced as read_state=unreadable on every class
                print(f"pull failed for {key}: {type(exc).__name__}", file=sys.stderr)
    result = run(data, a.since, a.tolerance, load_account_classes(), now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    md = render_md(result)
    if not a.no_write:
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"geometry-amend-census-{stamp}.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        (out / f"geometry-amend-census-{stamp}.md").write_text(md)
    print(md)
    return 0 if result["pull"]["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
