#!/usr/bin/env python3
"""R4 as the DEMOTION gate (checklist row B3): turn R4's per-leg verdict into
an enforced Stage-2 -> Stage-1 roster cut, under the granted mandate
MD-DEMOTE-S2-S1, and nothing else.

    # dry run (the default): read a /api/bot/performance/recent?n=40 payload,
    # print what it WOULD do, write nothing
    python3 scripts/ops/r4_demotion_gate.py --recent-json recent.json

    # enforcing: write the evidence records and apply the roster cut
    python3 scripts/ops/r4_demotion_gate.py --recent-json recent.json --apply

THE RULE: T3 AND net<0 (OPERATOR, 2026-09-29)
---------------------------------------------
Operator popup, manager session session_01HYq6XtfesZ57VyaQrK6CL1, ~14:17Z,
verbatim **"Last 20 + strict test (Recommended)"** (pipeline row
PI-20260929-VOLSKIP-SIGNAL-0007; priced by
``scripts/research/r4_trigger_pricing_sim.py``, SIGNAL-0005: a healthy
frequent leg is falsely demoted within a year ~5-12% of the time; a zero-edge
leg is caught with P 0.79-0.97 within 5 years). Each Stage-2 leg is read on its
LAST 40 closed trades as two non-overlapping 20-trade windows, and is a
candidate only when BOTH windows' net R is below that leg's own p10 of the
20-trade bootstrap from its Stage-0 evidence record AND below 0. The mandate's
never-clause "Never on a non-negative window" stays -- it is what keeps the
legs whose p10 is POSITIVE (trend_donchian_xrp_4h, ief_pullback_1d,
iaum_pullback_1d) from ever being cut while they make money.

The threshold is ``mandate_resolver.stage0_block_p10`` -- ONE implementation,
fixed seed, imported here and re-run by the resolver on every replay, so the
gate and the resolver cannot disagree about it.

  * fewer than 40 closed trades on the chosen book -> ABSTAIN (counted);
  * nothing closed on either book within MAX_STALENESS_HOURS (72h) -> every
    leg ABSTAINS as ``stale`` (counted): a stalled trader re-serves the same
    40 trades, and re-judging them is not new evidence;
  * no usable Stage-0 evidence -> ABSTAIN, LOUDLY (a ``::warning::`` line and
    its own count) -- a threshold is never guessed;
  * the window/30d rollup of ``/api/bot/performance`` is no longer read, and
    the workflow's ``window`` input is fixed to the label ``last40``.

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
(real money and the ``paper_role: portfolio`` mirror; since 2026-09-29 the
per-leg rows come from ``GET /api/bot/performance/recent``, each an
``_aggregate`` output over that leg's last 40 / each 20-trade window).

A leg is a DEMOTION CANDIDATE only when ALL hold on the source R4 chose,
over the leg's last 40 closed trades (``last`` in the route's payload):

  1. R4 says ``would_block``: n >= 40, MEASURED coverage >= the floor (0.6),
     and the {measured, estimated} USD net < 0. Every abstain
     (``abstain_thin`` / ``abstain_unverified``) is an ABSTAIN -- "we could not
     look" is not a demotion signal (the mandate's own ``never:`` clause).
  2. That source's 40-trade ``totalR`` is stated and < 0. Requiring the SIGN TO
     AGREE in both units is the conservative join: a leg is cut only when the
     dollar read and the R read both say it lost.
  3. T3: both 20-trade windows' ``totalR`` < min(the leg's Stage-0 p10, 0).

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
    ABSTAIN_THIN,
    ABSTAIN_UNVERIFIED,
    COVERAGE_FLOOR,
    WOULD_BLOCK,
    combined_leg_verdict,
)

MANDATE_ID = "MD-DEMOTE-S2-S1"
RUNS_DIR_REL = f"{mr.MIRROR_DIR_REL}/runs"
DEMOTE, HOLD, ABSTAIN = "demote", "hold", "abstain"
#: The only window label this gate accepts. It is what the workflow's (now
#: fixed) `window` input declares and what check_mandate_autoland A7 binds the
#: run's provenance to; any other label is a caller choosing its own evidence.
WINDOW_LABEL = f"last{mr.T3_N}"
#: Freshness bound (manager review N4, 2026-09-29): if NOTHING closed on either
#: Stage-2 book for this long, the trader or the journal is stalled and the
#: payload is the same 40 trades as yesterday -- every leg abstains ("stale").
#: 72h: Stage-2 spans 7 legs on crypto (24/7) and equities; a fleet-wide 3-day
#: silence is a stall, not a slow leg. A per-leg bound would silence the 1d legs.
MAX_STALENESS_HOURS = 72

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


def _leg_row(entry: Optional[Dict[str, Any]], leg: str) -> Optional[Dict[str, Any]]:
    """The route's per-leg aggregate -> the ``perStrategy`` row R4 reads."""
    for s in (entry or {}).get("perStrategy") or []:
        if s.get("name") == leg:
            return s
    return None


def _book(recent: Dict[str, Any], key: str, leg: str) -> Dict[str, Any]:
    """``{"last": row|None, "blocks": [row|None, ...], "complete": bool, ...}``
    for one leg on one book of a /performance/recent payload."""
    ent = ((recent.get(key) or {}).get("perStrategy") or {}).get(leg) or {}
    blocks = ent.get("blocks") or []
    return {"closedAvailable": ent.get("closedAvailable", 0),
            "complete": bool(ent.get("complete")),
            "last": _leg_row(ent.get("last"), leg),
            "blocks": [_leg_row(b, leg) for b in blocks],
            "blockSpans": [(b.get("closedFrom"), b.get("closedTo")) for b in blocks],
            "blockMeta": [{"label": b.get("label"), "first_trade_id": b.get("firstTradeId"),
                           "last_trade_id": b.get("lastTradeId")} for b in blocks]}


def _parse_ts(v: Any) -> Optional[_dt.datetime]:
    """A journal close time (``YYYY-MM-DD HH:MM:SS`` or ISO) as aware UTC."""
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        ts = _dt.datetime.fromisoformat(v.strip().replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=_dt.timezone.utc)


def staleness(recent: Dict[str, Any], now: _dt.datetime) -> Dict[str, Any]:
    """Is the payload FRESH? The newest close across both books must be within
    ``MAX_STALENESS_HOURS`` of ``now``. A stalled trader (or a frozen journal)
    re-serves the same last 40 trades every day; judging them again is not new
    evidence, so every leg abstains -- counted -- until closes resume."""
    newest = [ts for ts in (_parse_ts((recent.get(k) or {}).get("newestClosedAt"))
                            for k in ("realMoney", "mirror")) if ts]
    if not newest:
        return {"stale": True, "newest_closed_at": None,
                "why": "no newestClosedAt on either book -- freshness cannot be read"}
    top = max(newest)
    age_h = (now - top).total_seconds() / 3600.0
    return {"stale": age_h > MAX_STALENESS_HOURS, "newest_closed_at": top.isoformat(),
            "age_hours": round(age_h, 2),
            "why": (f"newest close {top.isoformat()} is {age_h:.1f}h old > the stated "
                    f"{MAX_STALENESS_HOURS}h bound" if age_h > MAX_STALENESS_HOURS else None)}


def _r(row: Optional[Dict[str, Any]]) -> Optional[float]:
    v = (row or {}).get("totalR")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def leg_decision(leg: str, account: str, real: Dict[str, Any], mirror: Dict[str, Any],
                 threshold: Dict[str, Any], *, coverage_floor: float,
                 min_trades: int = mr.T3_N,
                 fresh: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """R4 over the last 40, then T3 AND net<0 over the two 20-trade windows."""
    v = combined_leg_verdict(real["last"], mirror["last"], coverage_floor=coverage_floor,
                             min_trades=min_trades)
    book = real if v["chosenSource"] == "real_money" else mirror
    total_r = _r(book["last"])
    win_r = [_r(b) for b in book["blocks"]]
    p10 = threshold.get("p10")
    base = {"leg": leg, "account": account, "r4": v, "totalR": total_r,
            "rTradeCount": (book["last"] or {}).get("rTradeCount"),
            "windowsR": win_r, "book": book, "threshold": threshold, "abstain": None}

    def out(action: str, why: str, abstain: Optional[str] = None) -> Dict[str, Any]:
        return {**base, "action": action, "why": why, "abstain": abstain}

    if p10 is None:
        return out(ABSTAIN, f"NO USABLE STAGE-0 EVIDENCE -- {threshold.get('why')}; the T3 "
                            "threshold is never guessed", "no_evidence")
    if fresh and fresh.get("stale"):
        return out(ABSTAIN, f"STALE PAYLOAD -- {fresh.get('why')}; the same trades are not new "
                            "evidence", "stale")
    if v["status"] == ABSTAIN_THIN:
        return out(ABSTAIN, f"fewer than {min_trades} closed trades on either book: {v['detail']}",
                   "thin")
    if v["status"] == ABSTAIN_UNVERIFIED:
        return out(ABSTAIN, f"R4 {v['status']}: {v['detail']}", "unverified")
    if v["status"] != WOULD_BLOCK:
        return out(HOLD, f"R4 {v['status']}: {v['detail']}")
    if total_r is None:
        return out(ABSTAIN, "R4 would_block but the 40-trade totalR is not stated -- R "
                            "unmeasured, no demotion", "r_unmeasured")
    if total_r >= 0:
        return out(HOLD, f"R4 would_block (USD) but 40-trade totalR {total_r:+.4f} is not negative "
                         "-- never on a non-negative window")
    if len(win_r) != mr.T3_WINDOWS or not book["complete"] or any(x is None for x in win_r):
        return out(ABSTAIN, f"the chosen book does not carry {mr.T3_WINDOWS} full "
                            f"{mr.T3_BLOCK}-trade windows with stated R ({win_r})", "thin")
    bar = min(p10, 0.0)
    if not all(x < bar for x in win_r):
        return out(HOLD, f"T3 not met: window R {win_r} not both below min(p10 {p10:+.4f}, 0)")
    return out(DEMOTE, f"R4 would_block on {v['chosenSource']}; 40-trade R {total_r:+.4f} < 0; "
                       f"both windows {win_r} < min(p10 {p10:+.4f}, 0)")


def evaluate(recent: Dict[str, Any], root: Path, *, coverage_floor: float = COVERAGE_FLOOR,
             min_trades: int = mr.T3_N,
             now: Optional[_dt.datetime] = None) -> List[Dict[str, Any]]:
    fresh = staleness(recent, now or _dt.datetime.now(_dt.timezone.utc))
    out = []
    for acct, leg in stage2_legs(root):
        out.append(leg_decision(leg, acct, _book(recent, "realMoney", leg),
                                _book(recent, "mirror", leg), mr.stage0_block_p10(leg, root),
                                coverage_floor=coverage_floor, min_trades=min_trades,
                                fresh=fresh))
    return out


# --------------------------------------------------------------------------
# the evidence records
# --------------------------------------------------------------------------
def source_run_payload(recent: Dict[str, Any], legs: List[str], window: str) -> Dict[str, Any]:
    """The committed snapshot a mirror-window record cites: the exact per-leg
    entries the gate read from both books, trimmed to the Stage-2 legs."""
    keep = set(legs)

    def trim(key: str) -> Dict[str, Any]:
        per = (recent.get(key) or {}).get("perStrategy") or {}
        return {k: v for k, v in per.items() if k in keep}
    return {
        "kind": "r4_demotion_gate_source_run",
        "source": f"GET /api/bot/performance/recent?n={recent.get('n')}",
        "window": window,
        "rule": mr.T3_RULE_ID,
        "n": recent.get("n"),
        "block": recent.get("block"),
        "real_money_perStrategy": trim("realMoney"),
        "mirror_perStrategy": trim("mirror"),
        "mirror_account_ids": (recent.get("mirror") or {}).get("accountIds"),
    }


def mirror_window_record(dec: Dict[str, Any], source_run_rel: str, window: str,
                         generated_at: str) -> Dict[str, Any]:
    v = dec["r4"]
    chosen = v["real"] if v["chosenSource"] == "real_money" else v["mirror"]
    book, th = dec["book"], dec["threshold"]
    last = book["last"] or {}
    return {
        "leg": dec["leg"],
        "account": dec["account"],
        "mandate": MANDATE_ID,
        "rule": mr.T3_RULE_ID,
        "generated_at": generated_at,
        "generated_by": "scripts/ops/r4_demotion_gate.py",
        "window": window,
        "source_run": source_run_rel,
        "chosen_source": v["chosenSource"],
        "n_closed": last.get("trades"),
        "net_r_net_of_full_cost": dec["totalR"],
        "r_population": dec["rTradeCount"],
        "windows": [
            {"n_closed": (b or {}).get("trades"), "net_r": _r(b),
             "r_population": (b or {}).get("rTradeCount"),
             "net_usd_measured": (b or {}).get("totalPnlMeasured"),
             "closed_from": span[0], "closed_to": span[1], **meta}
            for b, span, meta in zip(book["blocks"], book["blockSpans"],
                                     book.get("blockMeta") or [{}] * len(book["blocks"]))
        ],
        # The threshold, its seed and its source -- re-derived by the resolver
        # on every replay (R-T3-THRESHOLD refuses a mismatch).
        "p10_threshold": th.get("p10"),
        "bootstrap_seed": th.get("seed"),
        "bootstrap_draws": th.get("draws"),
        "bootstrap_block": th.get("block"),
        "evidence_record": th.get("evidence_record"),
        "evidence_source_run": th.get("source_run"),
        "evidence_n": th.get("source_n"),
        # MEASURED+ESTIMATED (the name understates it -- JC-SA-03 keeps it and
        # records both). `net_usd_measured_only` is the MEASURED half, over the
        # same population as pnl_coverage_measured; `n_estimated` is how many
        # ESTIMATED rows make up the difference. No gate logic reads either.
        "net_usd_measured": chosen.get("totalPnlMeasured"),
        "net_usd_measured_only": chosen.get("totalPnlMeasuredOnly"),
        "n_estimated": chosen.get("pnlEstimatedCount"),
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


def _scratch_root(root: Path, legs: List[str]) -> Path:
    """A plain (non-git) copy of what the resolver reads for S2->S1 -- the
    three configs plus each leg's Stage-0 evidence record and its source_run
    (the T3 threshold) -- so a dry run exercises the real resolver without
    writing the repo."""
    tmp = Path(tempfile.mkdtemp(prefix="r4-demotion-dry-"))
    rels = [mr.MANDATES_REL, mr.ACCOUNTS_REL, mr.STRATEGIES_REL]
    for leg in legs:
        ev = f"{mr.EVIDENCE_DIR_REL}/{leg}.json"
        rels.append(ev)
        src = (mr._json(root / ev) or {}).get("source_run")
        if isinstance(src, str) and mr.in_repo(root, src):
            rels.append(src)
    for rel in rels:
        src = root / rel
        if src.is_file():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tmp / rel)
    return tmp


def _counts(decisions: List[Dict[str, Any]]) -> Dict[str, int]:
    c: Dict[str, int] = {DEMOTE: 0, HOLD: 0, ABSTAIN: 0}
    for d in decisions:
        c[d["action"]] += 1
        if d["abstain"]:
            c[f"abstain_{d['abstain']}"] = c.get(f"abstain_{d['abstain']}", 0) + 1
    return c


def run(recent: Dict[str, Any], *, root: Path = REPO, window: str = WINDOW_LABEL, apply: bool,
        coverage_floor: float = COVERAGE_FLOOR, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    decisions = evaluate(recent, root, coverage_floor=coverage_floor, now=now)
    candidates = [d for d in decisions if d["action"] == DEMOTE]
    out: Dict[str, Any] = {
        "mode": "apply" if apply else "dry_run", "window": window, "rule": mr.T3_RULE_ID,
        "n": recent.get("n"), "block": recent.get("block"),
        "generated_at": now.isoformat(), "coverage_floor": coverage_floor,
        "min_trades": mr.T3_N, "stage2_accounts": list(STAGE2_ACCOUNTS),
        "freshness": staleness(recent, now), "max_staleness_hours": MAX_STALENESS_HOURS,
        "legs": decisions, "counts": _counts(decisions),
        "no_evidence": [d["leg"] for d in decisions if d["abstain"] == "no_evidence"],
        "demoted": [], "refused": [], "written": [],
    }
    if not candidates:
        return out
    work = root if apply else _scratch_root(root, [d["leg"] for d in candidates])

    run_rel = f"{RUNS_DIR_REL}/{stamp}-{window}.json"
    _write_json(work / run_rel, source_run_payload(recent, [d["leg"] for d in decisions], window))
    written = [run_rel]
    for d in candidates:
        rec_rel = f"{mr.MIRROR_DIR_REL}/{d['leg']}.json"
        _write_json(work / rec_rel, mirror_window_record(d, run_rel, window, now.isoformat()))
        written.append(rec_rel)
    if apply:
        _git_add(work, written)

    for d in candidates:
        res = mr.resolve(d["leg"], "S2", "S1", d["account"], root=work, mandate_id=MANDATE_ID)
        d["resolver"] = {k: res.get(k) for k in ("verdict", "clause", "detail", "proposal")}
        if res["verdict"] != mr.FIRE:
            out["refused"].append({"leg": d["leg"], "verdict": res["verdict"],
                                   "clause": res["clause"], "detail": res["detail"]})
            continue
        removed = apply_proposal(work, res["proposal"], d["leg"])
        firing_rel = f"{mr.FIRINGS_DIR_REL}/{stamp}-{d['leg']}-demote.json"
        _write_json(work / firing_rel, {
            "mandate": MANDATE_ID, "action": "remove", "leg": d["leg"], "accounts": removed,
            "fired_at": now.isoformat(), "evidence": f"{mr.MIRROR_DIR_REL}/{d['leg']}.json",
            "source_run": run_rel, "rule": mr.T3_RULE_ID, "then": res["proposal"].get("then"),
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
    lines = [f"R4 demotion gate [{out['mode']}] rule={out['rule']} window={out['window']} "
             f"n={out['n']} block={out['block']} floor={out['coverage_floor']}"]
    for d in out["legs"]:
        v = d["r4"]
        c = v["real"] if v["chosenSource"] == "real_money" else v["mirror"]
        p10 = d["threshold"].get("p10")
        lines.append(f"  {d['action'].upper():7s} {d['account']:12s} {d['leg']:24s} "
                     f"r4={v['status']:18s} src={v['chosenSource']:10s} n={c.get('trades')} "
                     f"usd_meas={c.get('totalPnlMeasured')} cov={c.get('pnlCoverage')} "
                     f"R40={d['totalR']} windowsR={d['windowsR']} p10={p10} | {d['why']}")
    lines.append(f"  counts: {out['counts']}")
    for leg in out.get("no_evidence") or []:
        lines.append(f"  ⚠️ NO USABLE STAGE-0 EVIDENCE for {leg} -- it ABSTAINS and cannot be "
                     f"demoted by this gate until its evidence record is fixed")
    lines.append(f"  demoted: {out['demoted'] or 'none'}")
    lines.append(f"  refused by resolver: {out['refused'] or 'none'}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--recent-json", required=True, type=Path,
                    help=f"a GET /api/bot/performance/recent?n={mr.T3_N} payload")
    ap.add_argument("--window", default=WINDOW_LABEL,
                    help=f"DEPRECATED label; only {WINDOW_LABEL!r} is accepted")
    ap.add_argument("--apply", action="store_true", help="write records + apply the roster cut")
    ap.add_argument("--coverage-floor", type=float, default=COVERAGE_FLOOR)
    ap.add_argument("--root", type=Path, default=REPO)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.window != WINDOW_LABEL:
        print(f"error: window {a.window!r} refused -- the T3 rule's window is fixed at "
              f"{WINDOW_LABEL!r}; a caller may not choose its own evidence window", file=sys.stderr)
        return 2
    try:
        recent = json.loads(a.recent_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read {a.recent_json}: {e}", file=sys.stderr)
        return 2
    if (not isinstance(recent, dict) or "realMoney" not in recent or "mirror" not in recent
            or recent.get("n") != mr.T3_N or recent.get("block") != mr.T3_BLOCK):
        print(f"error: not a /api/bot/performance/recent?n={mr.T3_N} body (needs realMoney, "
              f"mirror, n={mr.T3_N}, block={mr.T3_BLOCK})", file=sys.stderr)
        return 2
    if recent.get("error") or any((recent.get(k) or {}).get("readState") != "ok"
                                  for k in ("realMoney", "mirror")):
        print(f"error: the route could not read both books (error={recent.get('error')!r}, "
              f"realMoney={(recent.get('realMoney') or {}).get('readState')!r}, "
              f"mirror={(recent.get('mirror') or {}).get('readState')!r}) -- we could not look",
              file=sys.stderr)
        return 2
    if (recent.get("mirror") or {}).get("exclusionsReadState") == "unreadable":
        # The mirror window drops acknowledged legacy trades
        # (config/mirror_window_exclusions.yaml); if that list could not be read
        # the window may still contain them -- we could not look.
        print("error: mirror exclusions list unreadable -- the Gate-2 window may include "
              "legacy mirror-only trades; refusing to demote on it", file=sys.stderr)
        return 2
    out = run(recent, root=a.root, window=a.window, apply=a.apply,
              coverage_floor=a.coverage_floor)
    for leg in out["no_evidence"]:
        print(f"::warning::r4-demotion-gate: {leg} has NO USABLE STAGE-0 EVIDENCE -- abstaining; "
              f"it cannot be demoted until comms/strategy_evidence/{leg}.json is fixed",
              file=sys.stderr)
    print(json.dumps(out, indent=2, sort_keys=True, default=str) if a.json else render(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
