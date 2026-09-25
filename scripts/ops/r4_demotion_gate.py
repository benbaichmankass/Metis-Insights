#!/usr/bin/env python3
"""R4 as the DEMOTION gate (checklist row B3): turn R4's per-leg verdict into
an enforced Stage-2 -> Stage-1 roster cut, under the granted mandate
MD-DEMOTE-S2-S1, and nothing else.

    # dry run (the default): read a /api/bot/performance payload, print what
    # it WOULD do, write nothing
    python3 scripts/ops/r4_demotion_gate.py --perf-json perf-30d.json

    # enforcing: write the evidence records and apply the roster cut
    python3 scripts/ops/r4_demotion_gate.py --perf-json perf-30d.json --apply

WHY DEMOTION AND NOT PROMOTION
------------------------------
docs/plans/OPERATING-PLAN-2026-09-21.md: "R4 becomes a DEMOTION gate, not a
promotion gate. A mirror that carries identical strategies only ever holds data
for legs already live, so it cannot gate entry. It gates exit." So this gate
looks ONLY at legs already on a Stage-2 real-money roster (``bybit_2``,
``alpaca_live``; ``ib_live`` has an empty roster) and can only ever REMOVE one.
It has no code path that adds a leg anywhere.

WHAT R4 SAYS, AND WHAT THIS GATE ADDS TO IT
-------------------------------------------
R4 is ``src/runtime/research_results_gate.py::combined_leg_verdict``, IMPORTED
here, never re-derived -- the observe-only reporter
(``scripts/research/research_results_gate_report.py``) and this gate read the
same verdict over the same ``performance._aggregate`` rollup
(``perStrategy`` = real money, ``paperPortfolio.perStrategy`` = the mirror).

A leg is a DEMOTION CANDIDATE only when BOTH hold on the source R4 chose:

  1. R4 says ``would_block``: n >= min_trades (20), MEASURED coverage >= the
     floor (0.6), and the {measured, estimated} USD net < 0. Every abstain
     (``abstain_thin`` / ``abstain_unverified``) is a HOLD -- "we could not
     look" is not a demotion signal (the mandate's own ``never:`` clause).
  2. That source's ``totalR`` is stated and < 0. MD-DEMOTE-S2-S1 fires on
     ``net_r_net_of_full_cost < 0``; R4 speaks USD. Requiring the SIGN TO AGREE
     in both units is the conservative join: a leg is cut only when the dollar
     read and the R read both say it lost.

⚠️ What "net of the full cost stack" means for a LIVE read, stated rather than
implied: the journal ``pnl`` of a MEASURED row is the broker's realized fill
PnL, so fees and slippage are inside the fill prices -- nothing is modelled.
``totalR`` is ``sum(pnl / declared risk)`` over every row with an R basis, which
is a WIDER population than R4's MEASURED coverage (the endpoint publishes no
measured-only R). Clause 1's coverage floor is what makes clause 2 trustworthy;
the record carries both populations so a reader can see the gap.

THE MECHANISM: A ROSTER CUT, NOT ``execution: shadow``
------------------------------------------------------
CLAUDE.md § "The two execution gates" names two off-switches. This gate uses
roster membership, for three reasons:

  * MD-DEMOTE-S2-S1 says so in terms: "The edit REMOVES the leg from the
    Stage-2 account and its mirror and returns it to the Stage-1 soak book."
  * ``execution: shadow`` is STRATEGY-level -- it would silence the leg on
    EVERY account, including the Stage-1 soak book it is supposed to return to.
    Demoting S2 -> S1 with it would actually be S2 -> OFF.
  * Stage 2 is one stage: removing from the live account AND its mirror keeps
    ``tests/test_paper_portfolio_accounts.py`` (strict equality) true.

``scripts/check_dry_run_in_diff.py`` guards added ``execution: shadow`` /
``mode: dry_run`` lines and does NOT see a roster removal; B1
(``check_roster_promotion_evidence.py``) keys on roster membership and treats a
removal as free. So the automated cut is visible to neither guard -- which is
why every cut this gate makes carries a committed evidence record and a firing
record, and why it can only ever remove.

THE DECISION IS THE RESOLVER'S, NOT THIS FILE'S
-----------------------------------------------
For each candidate this gate writes the mirror-window record the resolver reads
(``comms/mandate_evidence/mirror_window/<leg>.json``) plus the ``source_run``
it cites, then asks ``scripts/ops/mandate_resolver.py`` whether
MD-DEMOTE-S2-S1 fires. Only a FIRE edits a roster. A resolver REFUSE (mandate
not granted, record unreadable, source_run absent, R not negative, the edit
would ADD to the soak book) leaves the roster untouched.

⚠️ On ``--apply`` the ``source_run`` must be COMMITTED before the resolver will
accept it (``mandate_resolver.in_repo`` requires tracked-ness in a git tree), so
``--apply`` stages the evidence with ``git add`` first. Dry run evaluates
against a scratch copy of the three config files and never touches git.

Exit codes: 0 ran (whether or not anything was demoted) · 2 could not run.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
for _p in (str(REPO), str(REPO / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

import mandate_resolver as mr  # noqa: E402
from src.runtime.research_results_gate import (  # noqa: E402
    COVERAGE_FLOOR,
    MIN_TRADES,
    WOULD_BLOCK,
    combined_leg_verdict,
)

MANDATE_ID = "MD-DEMOTE-S2-S1"
RUNS_DIR_REL = f"{mr.MIRROR_DIR_REL}/runs"
DEMOTE, HOLD = "demote", "hold"

#: The Stage-2 real-money accounts, taken from the resolver so the two can
#: never disagree about what Stage 2 is.
STAGE2_ACCOUNTS = tuple(a for a, s in mr.STAGE_OF_ACCOUNT.items() if s == "S2")


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def stage2_legs(root: Path) -> List[Tuple[str, str]]:
    """``[(account, leg)]`` for every leg on a Stage-2 real-money roster."""
    acfg = (mr._yaml(root, mr.ACCOUNTS_REL) or {}).get("accounts") or {}
    out: List[Tuple[str, str]] = []
    for acct in STAGE2_ACCOUNTS:
        legs = (acfg.get(acct) or {}).get("strategies") or []
        out.extend((acct, str(leg)) for leg in legs)
    return out


def _by_name(block: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {s["name"]: s for s in (block or {}).get("perStrategy") or [] if "name" in s}


# --------------------------------------------------------------------------
# the per-leg verdict
# --------------------------------------------------------------------------
def leg_decision(leg: str, account: str, real: Optional[Dict[str, Any]],
                 mirror: Optional[Dict[str, Any]], *, coverage_floor: float,
                 min_trades: int) -> Dict[str, Any]:
    """R4's verdict for one Stage-2 leg, plus whether it is a demotion candidate."""
    v = combined_leg_verdict(real, mirror, coverage_floor=coverage_floor,
                             min_trades=min_trades)
    chosen_stats = real if v["chosenSource"] == "real_money" else mirror
    total_r = (chosen_stats or {}).get("totalR")
    r_ok = isinstance(total_r, (int, float)) and not isinstance(total_r, bool)
    if v["status"] != WOULD_BLOCK:
        action, why = HOLD, f"R4 {v['status']}: {v['detail']}"
    elif not r_ok:
        action, why = HOLD, "R4 would_block but totalR is not stated -- R unmeasured, no demotion"
    elif total_r >= 0:
        action, why = HOLD, (f"R4 would_block (USD) but totalR {total_r:+.4f} is not negative -- "
                             "the two units disagree, so the evidence does not support a cut")
    else:
        action, why = DEMOTE, (f"R4 would_block on {v['chosenSource']} and totalR "
                               f"{total_r:+.4f} < 0")
    return {"leg": leg, "account": account, "action": action, "why": why,
            "r4": v, "totalR": total_r if r_ok else None,
            "rTradeCount": (chosen_stats or {}).get("rTradeCount")}


def evaluate(perf: Dict[str, Any], root: Path, *, coverage_floor: float = COVERAGE_FLOOR,
             min_trades: int = MIN_TRADES) -> List[Dict[str, Any]]:
    real_by = _by_name(perf)
    mirror_by = _by_name(perf.get("paperPortfolio"))
    return [leg_decision(leg, acct, real_by.get(leg), mirror_by.get(leg),
                         coverage_floor=coverage_floor, min_trades=min_trades)
            for acct, leg in stage2_legs(root)]


# --------------------------------------------------------------------------
# the evidence records
# --------------------------------------------------------------------------
def source_run_payload(perf: Dict[str, Any], legs: List[str], window: str) -> Dict[str, Any]:
    """The committed snapshot a mirror-window record cites: the exact per-leg
    rows R4 read, from both books, trimmed to the Stage-2 legs."""
    keep = set(legs)
    return {
        "kind": "r4_demotion_gate_source_run",
        "source": "GET /api/bot/performance",
        "window": window,
        "since": perf.get("since"),
        "real_money_perStrategy": [s for s in perf.get("perStrategy") or [] if s.get("name") in keep],
        "mirror_perStrategy": [s for s in (perf.get("paperPortfolio") or {}).get("perStrategy") or []
                               if s.get("name") in keep],
    }


def mirror_window_record(dec: Dict[str, Any], source_run_rel: str, window: str,
                         since: Optional[str], generated_at: str) -> Dict[str, Any]:
    v = dec["r4"]
    chosen = v["real"] if v["chosenSource"] == "real_money" else v["mirror"]
    return {
        "leg": dec["leg"],
        "account": dec["account"],
        "mandate": MANDATE_ID,
        "generated_at": generated_at,
        "generated_by": "scripts/ops/r4_demotion_gate.py",
        "window": window,
        "window_since": since,
        "source_run": source_run_rel,
        "chosen_source": v["chosenSource"],
        "n_closed": chosen.get("trades"),
        "net_r_net_of_full_cost": dec["totalR"],
        "r_population": dec["rTradeCount"],
        "net_usd_measured": chosen.get("totalPnlMeasured"),
        "pnl_coverage_measured": chosen.get("pnlCoverage"),
        "coverage_floor": chosen.get("coverageFloor"),
        "min_trades": chosen.get("minTrades"),
        "r4_status": v["status"],
        "r4_detail": v["detail"],
        "cost_basis": ("live realized: MEASURED rows carry the broker fill PnL, so fees and "
                       "slippage are in the fill prices. totalR spans r_population rows, a "
                       "wider population than the MEASURED coverage above."),
    }


# --------------------------------------------------------------------------
# the roster cut
# --------------------------------------------------------------------------
_ACCT_RE = re.compile(r"^  ([A-Za-z0-9_]+):\s*(#.*)?$")


def remove_from_roster(text: str, account: str, leg: str) -> str:
    """Remove ``leg`` from ``account``'s ``strategies:`` roster in accounts.yaml
    TEXT, preserving every other byte. Handles the flow form
    (``strategies: [a, b]``) and the block form (``- a`` lines). Raises
    ValueError when it cannot find exactly one occurrence -- an edit it cannot
    make exactly is an edit it does not make."""
    lines = text.splitlines(keepends=True)
    start = end = None
    for i, ln in enumerate(lines):
        m = _ACCT_RE.match(ln.rstrip("\n"))
        if m and start is None and m.group(1) == account:
            start = i
        elif m and start is not None:
            end = i
            break
    if start is None:
        raise ValueError(f"account {account!r} not found")
    end = end if end is not None else len(lines)
    hits = []
    for i in range(start, end):
        ln = lines[i]
        flow = re.match(r"^(\s+strategies:\s*)\[([^\]]*)\](.*)$", ln.rstrip("\n"))
        if flow:
            items = [x.strip() for x in flow.group(2).split(",") if x.strip()]
            if leg in items:
                hits.append(("flow", i, flow, items))
        elif re.match(rf"^\s+-\s+{re.escape(leg)}\s*(#.*)?$", ln.rstrip("\n")):
            hits.append(("block", i, None, None))
    if len(hits) != 1:
        raise ValueError(f"{leg!r} occurs {len(hits)} times in {account}'s roster text; expected 1")
    kind, i, flow, items = hits[0]
    if kind == "flow":
        items = [x for x in items if x != leg]
        nl = "\n" if lines[i].endswith("\n") else ""
        lines[i] = f"{flow.group(1)}[{', '.join(items)}]{flow.group(3)}{nl}"
    else:
        # A trailing comment on the removed line may annotate its neighbours
        # (alpaca_paper's roster reads that way). Keep it as a bare comment
        # line so no prose is lost.
        comment = re.search(r"(#.*)$", lines[i].rstrip("\n"))
        indent = re.match(r"^(\s*)", lines[i]).group(1)
        lines[i] = f"{indent}  {comment.group(1)}\n" if comment else ""
    return "".join(lines)


def apply_proposal(root: Path, proposal: Dict[str, Any], leg: str) -> List[str]:
    """Apply a resolver FIRE proposal: removals only. Verifies by re-parsing."""
    if proposal.get("roster_add"):
        raise ValueError(f"refusing a proposal that ADDS: {proposal['roster_add']}")
    path = root / mr.ACCOUNTS_REL
    text = path.read_text(encoding="utf-8")
    accounts = sorted(proposal.get("roster_remove") or {})
    for acct in accounts:
        text = remove_from_roster(text, acct, leg)
    parsed = (yaml.safe_load(text) or {}).get("accounts") or {}
    for acct in accounts:
        if leg in ((parsed.get(acct) or {}).get("strategies") or []):
            raise ValueError(f"post-edit parse still shows {leg} on {acct}")
    path.write_text(text, encoding="utf-8")
    return accounts


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_add(root: Path, rels: List[str]) -> None:
    if (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "add", "--", *rels], check=True)


def _scratch_root(root: Path) -> Path:
    """A plain (non-git) copy of the three files the resolver reads for S2->S1,
    so a dry run exercises the real resolver without writing the repo."""
    tmp = Path(tempfile.mkdtemp(prefix="r4-demotion-dry-"))
    for rel in (mr.MANDATES_REL, mr.ACCOUNTS_REL, mr.STRATEGIES_REL):
        src = root / rel
        if src.is_file():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tmp / rel)
    return tmp


def run(perf: Dict[str, Any], *, root: Path = REPO, window: str, apply: bool,
        coverage_floor: float = COVERAGE_FLOOR, min_trades: int = MIN_TRADES,
        now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    decisions = evaluate(perf, root, coverage_floor=coverage_floor, min_trades=min_trades)
    candidates = [d for d in decisions if d["action"] == DEMOTE]
    work = root if apply else _scratch_root(root)
    out: Dict[str, Any] = {
        "mode": "apply" if apply else "dry_run", "window": window, "since": perf.get("since"),
        "generated_at": now.isoformat(), "coverage_floor": coverage_floor,
        "min_trades": min_trades, "stage2_accounts": list(STAGE2_ACCOUNTS),
        "legs": decisions, "demoted": [], "refused": [], "written": [],
    }
    if not candidates:
        return out

    run_rel = f"{RUNS_DIR_REL}/{stamp}-{window}.json"
    _write_json(work / run_rel, source_run_payload(perf, [d["leg"] for d in decisions], window))
    written = [run_rel]
    for d in candidates:
        rec_rel = f"{mr.MIRROR_DIR_REL}/{d['leg']}.json"
        _write_json(work / rec_rel, mirror_window_record(d, run_rel, window, perf.get("since"),
                                                         now.isoformat()))
        written.append(rec_rel)
    if apply:
        _git_add(work, written)

    for d in candidates:
        res = mr.resolve(d["leg"], "S2", "S1", d["account"], root=work, mandate_id=MANDATE_ID)
        d["resolver"] = {k: res.get(k) for k in ("verdict", "clause", "detail", "proposal")}
        if res["verdict"] != mr.FIRE:
            out["refused"].append({"leg": d["leg"], "clause": res["clause"], "detail": res["detail"]})
            continue
        removed = apply_proposal(work, res["proposal"], d["leg"])
        firing_rel = f"{mr.FIRINGS_DIR_REL}/{stamp}-{d['leg']}-demote.json"
        _write_json(work / firing_rel, {
            "mandate": MANDATE_ID, "action": "remove", "leg": d["leg"], "accounts": removed,
            "fired_at": now.isoformat(), "evidence": f"{mr.MIRROR_DIR_REL}/{d['leg']}.json",
            "source_run": run_rel, "then": res["proposal"].get("then"),
        })
        written.append(firing_rel)
        out["demoted"].append({"leg": d["leg"], "accounts": removed})
    if apply:
        _git_add(work, written + [mr.ACCOUNTS_REL])
    else:
        shutil.rmtree(work, ignore_errors=True)
    out["written"] = written if apply else []
    return out


def render(out: Dict[str, Any]) -> str:
    lines = [f"R4 demotion gate [{out['mode']}] window={out['window']} since={out['since']} "
             f"floor={out['coverage_floor']} min_trades={out['min_trades']}"]
    for d in out["legs"]:
        v = d["r4"]
        c = v["real"] if v["chosenSource"] == "real_money" else v["mirror"]
        lines.append(f"  {d['action'].upper():6s} {d['account']:12s} {d['leg']:24s} "
                     f"r4={v['status']:18s} src={v['chosenSource']:10s} n={c.get('trades')} "
                     f"usd_meas={c.get('totalPnlMeasured')} cov={c.get('pnlCoverage')} "
                     f"R={d['totalR']} | {d['why']}")
    lines.append(f"  demoted: {out['demoted'] or 'none'}")
    lines.append(f"  refused by resolver: {out['refused'] or 'none'}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--perf-json", required=True, type=Path,
                    help="a GET /api/bot/performance?window=<w> payload")
    ap.add_argument("--window", default=None, help="label for the window (default: payload's)")
    ap.add_argument("--apply", action="store_true", help="write records + apply the roster cut")
    ap.add_argument("--coverage-floor", type=float, default=COVERAGE_FLOOR)
    ap.add_argument("--min-trades", type=int, default=MIN_TRADES)
    ap.add_argument("--root", type=Path, default=REPO)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        perf = json.loads(a.perf_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read {a.perf_json}: {e}", file=sys.stderr)
        return 2
    if not isinstance(perf, dict) or "perStrategy" not in perf:
        print("error: payload has no perStrategy -- not a /api/bot/performance body", file=sys.stderr)
        return 2
    window = a.window or str(perf.get("window") or "unknown")
    out = run(perf, root=a.root, window=window, apply=a.apply,
              coverage_floor=a.coverage_floor, min_trades=a.min_trades)
    print(json.dumps(out, indent=2, sort_keys=True, default=str) if a.json else render(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
