#!/usr/bin/env python3
"""Which live legs place an EXPECTATION, and which place a venue limit?

The machine-checkable form of the 2026-08-23 finding. `CLAUDE.md` and
`docs/design/exit-mechanism-construction-PROCESS.md` § 2 both state that a
bracket must carry an expectation at entry and that the fleet does not do this;
that claim was counted by hand twice, at two different numbers, over two
different populations. This counts it, states its population, and can be re-run.

Two views, because they answer different questions:

  --config  (default) per ENABLED strategy: does the config declare an
            expectation at all? Answers "how much of the fleet is on the
            sentinel idiom". Needs no live data.

  --live    per OPEN order package, using each trade's OWN entry and stop:
            did the target that actually RESTED express the expectation, or did
            the venue cap replace it? Answers "is this trade's take-profit a
            prediction". The clamp binds as a function of risk/entry, so a leg
            can be `declared` on a calm bar and `clamped` on a violent one —
            which the config view cannot see.

⚠️ `clamped` and `sentinel_no_expectation` are NEVER merged. A clamped leg had
an expectation the venue refused to place; a sentinel leg never had one. The
remedies differ.
"""
# wiring: manual-only - a REVIEW/audit tool, invoked by a session when it needs the fleet's expectation coverage. Nothing schedules it because the number only means something next to a decision about the legs it names.

from __future__ import annotations

import argparse
import collections
import json
import sys
import urllib.request
from typing import Any, Dict, List

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from src.runtime.target_expectation import (  # noqa: E402
    STATE_CLAMPED, STATE_DECLARED, STATE_NO_TARGET_KEY, STATE_SENTINEL,
    STATE_UNMEASURABLE, resolve_expectation,
)

DEFAULT_API = "https://ict-bot.duckdns.org"

_LABEL = {
    STATE_DECLARED: "declared — the target expresses an expectation",
    STATE_CLAMPED: "CLAMPED — an expectation the venue cap replaced",
    STATE_SENTINEL: "SENTINEL — no expectation was ever declared",
    STATE_NO_TARGET_KEY: "no target key — the strategy computes its target elsewhere",
    STATE_UNMEASURABLE: "unmeasurable — entry/stop unreadable",
}


def _load_config() -> Dict[str, Any]:
    import yaml
    with open("config/strategies.yaml", encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    return (d or {}).get("strategies", d) or {}


def config_view() -> Dict[str, Any]:
    """Per enabled strategy. Uses a NOTIONAL entry/stop, deliberately.

    A config carries no entry price, so `clamped` cannot be decided here — only
    whether the config declares the sentinel idiom. The notional pair is chosen
    so a non-sentinel `tp_r` lands `declared`; the live view is what grades the
    clamp honestly, and this view says so rather than implying otherwise.
    """
    rows: List[Dict[str, Any]] = []
    for name, cfg in sorted((_load_config() or {}).items()):
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            continue
        # notional 1R = 1% of entry: small enough that any tp_r under the
        # sentinel floor clears the cap, so this view isolates the sentinel.
        res = resolve_expectation(cfg, entry=100.0, sl=99.0, direction="long")
        rows.append({
            "strategy": name,
            "execution": cfg.get("execution", "live"),
            "timeframe": cfg.get("timeframe"),
            "target_r": res["target_r"],
            "source_key": res["source_key"],
            "state": res["state"],
        })
    by_state = collections.Counter(r["state"] for r in rows)
    live = [r for r in rows if r["execution"] == "live"]
    return {
        "view": "config",
        "population": {
            "enabled": len(rows),
            "enabled_live": len(live),
        },
        "by_state": dict(by_state),
        "by_state_live_only": dict(collections.Counter(r["state"] for r in live)),
        "rows": rows,
    }


def live_view(api_base: str) -> Dict[str, Any]:
    cfgs = _load_config()
    url = f"{api_base}/api/bot/positions?include_paper=true"
    with urllib.request.urlopen(url, timeout=60) as resp:
        positions = json.load(resp)
    rows: List[Dict[str, Any]] = []
    for p in positions:
        strat = p.get("pattern")
        res = resolve_expectation(
            cfgs.get(strat) or {},
            entry=p.get("entryPrice"), sl=p.get("stopLoss"),
            direction=p.get("side"),
        )
        rows.append({
            "trade": p.get("id"),
            "account": p.get("account"),
            "account_class": p.get("accountClass"),
            "strategy": strat,
            "symbol": p.get("symbol"),
            "state": res["state"],
            "target_r": res["target_r"],
            "cap_r": None if res["cap_r"] is None else round(res["cap_r"], 4),
            "risk_pct_of_entry": (None if res["risk_over_entry"] is None
                                  else round(res["risk_over_entry"] * 100, 2)),
            "placed_tp": p.get("takeProfit"),
        })
    return {
        "view": "live",
        "population": {"open_positions": len(rows)},
        "by_state": dict(collections.Counter(r["state"] for r in rows)),
        "rows": rows,
    }


def render(res: Dict[str, Any]) -> str:
    L: List[str] = []
    if res["view"] == "config":
        p = res["population"]
        L.append("# Target-expectation coverage — CONFIG view\n")
        L.append(f"_Population: {p['enabled']} enabled strategies, "
                 f"{p['enabled_live']} of them `execution: live`._\n")
        L.append("\n| state | enabled | of those, live |")
        L.append("|---|---:|---:|")
        for st in (STATE_SENTINEL, STATE_CLAMPED, STATE_DECLARED,
                   STATE_NO_TARGET_KEY, STATE_UNMEASURABLE):
            n = res["by_state"].get(st, 0)
            if not n:
                continue
            L.append(f"| {_LABEL[st]} | {n} | {res['by_state_live_only'].get(st, 0)} |")
        sent = res["by_state"].get(STATE_SENTINEL, 0)
        if sent:
            L.append(
                f"\n> ⚠️ **{sent} of {p['enabled']} enabled legs "
                f"({100*sent/p['enabled']:.1f}%) declare no expectation** — their "
                "placed take-profit is the exchange's rejection threshold, not a "
                "prediction of where the trade ends.\n")
        L.append("\n<details><summary>per leg</summary>\n")
        L.append("\n| strategy | exec | tf | target_r | key | state |")
        L.append("|---|---|---|---:|---|---|")
        for r in res["rows"]:
            L.append(f"| `{r['strategy']}` | {r['execution']} | {r['timeframe']} | "
                     f"{r['target_r']} | {r['source_key'] or '—'} | {r['state']} |")
        L.append("\n</details>")
        L.append(
            "\n> The CONFIG view cannot decide `clamped` — a config carries no "
            "entry price, and the cap binds as a function of `risk/entry`. Run "
            "`--live` for that."
        )
    else:
        L.append("# Target-expectation coverage — LIVE view\n")
        L.append(f"_Population: {res['population']['open_positions']} open "
                 "positions, each graded on its OWN entry and stop._\n")
        L.append("\n| trade | account | strategy | symbol | risk %entry | cap_r | state |")
        L.append("|---|---|---|---|---:|---:|---|")
        for r in res["rows"]:
            L.append(f"| {r['trade']} | {r['account']} | `{r['strategy']}` | "
                     f"{r['symbol']} | {r['risk_pct_of_entry']} | {r['cap_r']} | "
                     f"{r['state']} |")
        sub1 = [r for r in res["rows"]
                if r["cap_r"] is not None and r["cap_r"] < 1.0]
        if sub1:
            L.append(
                f"\n> ⚠️ **{len(sub1)} open position(s) have `cap_r < 1`** — the "
                "target that rests is NEARER than the stop, so the upside is "
                "capped below the risk:\n")
            for r in sub1:
                L.append(f"> - trade {r['trade']} ({r['account_class']}) "
                         f"`{r['strategy']}` {r['symbol']} — cap_r {r['cap_r']}")
    return "\n".join(L) + "\n"


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true",
                    help="Grade OPEN positions on their own entry/stop "
                         "(reads /api/bot/positions) instead of the config.")
    ap.add_argument("--api-base", default=DEFAULT_API)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])
    try:
        res = live_view(args.api_base) if args.live else config_view()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2
    print(json.dumps(res, indent=2) if args.json else render(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
