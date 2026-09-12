#!/usr/bin/env python3
"""Can any e35 control actually REPORT into a `trades`-based population?

MI-278 U11. The one owner of *"is this leg structurally capable of producing a
live closed-trade observation?"*, and of the search for a within-family control
for the e35 bracket-geometry experiment.

`BL-20260911-BOTH-OF-THE-E35-EXPERIMENTS-DESIGNED-CONTROLS-HAVE-ZERO-OBSERVATIONS`
records that both of e35's declared controls have no data, and its criterion says
the exit is a DECISION or a MEASUREMENT, never a wait:

  (a) e35 is RECORDED as unfalsifiable on its own declared controls; or
  (b) a control that CAN report is chosen and the dose-response re-run against it.

Deciding between them needs one fact nobody had measured: whether a leg capable of
serving as that control exists at all. This module answers it.

THREE THINGS IT DELIBERATELY DOES NOT DO
  * It does not re-derive which legs e35 treated from prose or from a YAML comment.
    The treated set is diffed out of the SHIP COMMIT itself (`E35_SHIP_COMMIT`),
    because the commit is the event and a comment is a claim about it.
  * It does not re-derive strategy FAMILY. That comes from the e35 sweep's own
    corpus, so this module and the sweep can never disagree about what a family is.
  * It proposes no config change. Routing a shadow leg live is Tier-3.

WHY `can_report` IS NOT `execution == "live"`
  A leg reports into `trades` only if it can open a live position AND at least one
  account it routes to actually executes AND writes there. Three independent ways
  that fails, and they are different facts with different remedies:
    - `execution: shadow`            -> the leg cannot open a position at all
    - every route is `mode: dry_run` -> the account refuses the order
    - every route is a PROP account  -> fills land in prop_tickets / prop_fills,
                                        which are ISOLATED from `trades` by design
  Collapsing them would report "no control exists" without saying what would have
  to change for one to.
"""

from __future__ import annotations

# wiring: manual-only - a one-shot census answering a question a specific backlog
# row asked -- BL-20260911-BOTH-OF-THE-E35-EXPERIMENTS-DESIGNED-CONTROLS-HAVE-ZERO-OBSERVATIONS
# (kept on ONE line: a wrapped id reads as a truncated one to check_backlog_refs, and
# to a human). It reads config plus a hand-supplied journal pull and grades the
# reachability of a control for ONE experiment; there is no recurring question for
# a workflow to ask on a cadence, and scheduling it would produce an unread artifact
# on every run. Reproduce the memo's figures with the two commands in its section 1
# and section 4.

import argparse
import collections
import json
import pathlib
import subprocess
import sys

# --- constants, stated once -------------------------------------------------

E35_SHIP_COMMIT = "892c9a2c8"
"""M20 Tier-3: ship the e35 reversed-direction bracket geometry (#10450), 2026-08-30."""

GEOMETRY_FIELDS = ("atr_stop_mult", "tp_r")
"""The two fields e35 edits. Read off the ship commit's own diff, not from prose."""

CORPUS = pathlib.Path("docs/research/e35-bracket-corpus.jsonl")
"""The e35 sweep's own output. Source of truth for a leg's FAMILY."""

REPORTABILITY_STATES = (
    "can_report",
    "blocked_shadow",
    "blocked_prop_only",
    "blocked_dry_run_only",
    "blocked_unrouted",
    "unknown_execution",
)
"""Never collapsed. `unknown_execution` is *we could not look*, never a block."""

PROP_CLASSES = ("prop",)
"""An account class whose fills are isolated from `trades` by design."""


# --- config reads -----------------------------------------------------------


def _git_show(ref: str, path: str) -> str | None:
    r = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True, text=True
    )
    return r.stdout if r.returncode == 0 else None


def load_strategies(ref: str | None = None) -> dict | None:
    """Parse config/strategies.yaml at `ref` (working tree when None).

    Returns None rather than {} when the file cannot be read, so *we could not
    look* never renders as *there are no strategies*.
    """
    import yaml

    if ref is None:
        p = pathlib.Path("config/strategies.yaml")
        if not p.exists():
            return None
        text = p.read_text()
    else:
        text = _git_show(ref, "config/strategies.yaml")
        if text is None:
            return None
    return (yaml.safe_load(text) or {}).get("strategies") or {}


def load_accounts() -> dict | None:
    """`{account_id: cfg}` from config/accounts.yaml, via the CANONICAL loader.

    `src.config.accounts_loader.load_accounts_dict` is the one sanctioned parser
    (`canonical-config-loaders` forbids a hand-rolled one, after a script's own
    loader assumed the wrong shape). It returns `{}` on a read failure, which
    collapses *we could not look* into *there are no accounts* -- so its `errors`
    out-parameter is passed and a non-empty error list is surfaced as None. That
    keeps the canonical parser AND the distinction; neither is given up.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from src.config.accounts_loader import load_accounts_dict

    errors: list = []
    accounts = load_accounts_dict(errors=errors)
    if errors:
        return None
    return accounts


def load_families(path: pathlib.Path = CORPUS) -> dict[str, str]:
    """leg -> family, from the e35 sweep's own corpus.

    A leg absent here has family None and grades `family_unknown` downstream --
    deliberately not pooled with 'no same-family leg exists'.
    """
    fam: dict[str, str] = {}
    if not path.exists():
        return fam
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            leg, f = row.get("leg"), row.get("family")
            if leg and f:
                fam[leg] = f
    return fam


# --- the treated set, diffed out of the ship commit -------------------------


def treated_legs(ship_commit: str = E35_SHIP_COMMIT) -> dict[str, dict] | None:
    """Legs whose geometry the e35 ship commit changed, with before/after.

    Returns None when either side of the diff cannot be read -- *we could not
    look*, never an empty treated set (which would make every leg look like a
    valid untreated control).
    """
    before = load_strategies(f"{ship_commit}^")
    after = load_strategies(ship_commit)
    if before is None or after is None:
        return None
    out: dict[str, dict] = {}
    for leg, cfg in after.items():
        prev = before.get(leg)
        if not isinstance(cfg, dict) or not isinstance(prev, dict):
            continue
        changed = {
            f: (prev.get(f), cfg.get(f))
            for f in GEOMETRY_FIELDS
            if prev.get(f) != cfg.get(f)
        }
        if changed:
            out[leg] = changed
    return out


# --- reportability ----------------------------------------------------------


def routes_for(accounts: dict) -> dict[str, list[str]]:
    r: dict[str, list[str]] = collections.defaultdict(list)
    for aid, a in (accounts or {}).items():
        for leg in (a.get("strategies") or []):
            r[leg].append(aid)
    return dict(r)


def classify_reportability(leg_cfg: dict, route_ids: list[str], accounts: dict) -> dict:
    """One of REPORTABILITY_STATES, with the evidence that decided it.

    Order matters and is deliberate: `execution` is checked FIRST, because a
    shadow leg cannot open a position however it is routed, so reporting a
    routing block for it would name a cause that is not the binding one.
    """
    execution = leg_cfg.get("execution")
    if execution not in ("live", "shadow"):
        return {
            "state": "unknown_execution",
            "execution": execution,
            "why": "execution is absent or outside the known vocabulary; we did not look further",
            "reporting_routes": [],
        }
    if execution == "shadow":
        return {
            "state": "blocked_shadow",
            "execution": execution,
            "why": "execution: shadow -- the leg runs and logs but never opens a live position",
            "reporting_routes": [],
        }

    if not route_ids:
        return {
            "state": "blocked_unrouted",
            "execution": execution,
            "why": "no account names this leg in its strategies list",
            "reporting_routes": [],
        }

    reporting, dry, prop = [], [], []
    for aid in route_ids:
        a = accounts.get(aid) or {}
        if a.get("mode") != "live":
            dry.append(aid)
        elif a.get("account_class") in PROP_CLASSES:
            prop.append(aid)
        else:
            reporting.append(aid)

    if reporting:
        return {
            "state": "can_report",
            "execution": execution,
            "why": f"routed to {len(reporting)} live non-prop account(s) that write into `trades`",
            "reporting_routes": reporting,
        }
    if prop and not dry:
        return {
            "state": "blocked_prop_only",
            "execution": execution,
            "why": "every live route is a PROP account -- fills land in prop_tickets/prop_fills, isolated from `trades` by design",
            "reporting_routes": [],
        }
    if dry and not prop:
        return {
            "state": "blocked_dry_run_only",
            "execution": execution,
            "why": "every route is a mode: dry_run account -- the order is refused before it reaches a venue",
            "reporting_routes": [],
        }
    return {
        "state": "blocked_prop_only" if prop else "blocked_dry_run_only",
        "execution": execution,
        "why": "no route both executes and writes into `trades` (mixed dry_run/prop routing)",
        "reporting_routes": [],
    }


def census(strategies: dict, accounts: dict, families: dict) -> dict[str, dict]:
    routes = routes_for(accounts)
    out = {}
    for leg, cfg in (strategies or {}).items():
        if not isinstance(cfg, dict):
            continue
        rid = routes.get(leg, [])
        row = classify_reportability(cfg, rid, accounts)
        row.update(
            leg=leg,
            family=families.get(leg),
            symbols=cfg.get("symbols") or [],
            timeframe=cfg.get("timeframe"),
            atr_stop_mult=cfg.get("atr_stop_mult"),
            tp_r=cfg.get("tp_r"),
            routes=rid,
        )
        out[leg] = row
    return out


# --- the control search -----------------------------------------------------


def find_controls(rows: dict[str, dict], treated: dict[str, dict]) -> dict:
    """For each treated leg, the untreated same-family legs that CAN report.

    A control must satisfy all three: same family, NOT treated by e35 (so it still
    carries its own pre-e35 geometry), and `can_report`. Same-symbol and
    same-timeframe are reported as STRENGTH, not as requirements -- the backlog
    row wants a within-FAMILY control, and demanding same-symbol as well would
    silently narrow what it asked for.
    """
    by_family: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows.values():
        if r["family"]:
            by_family[r["family"]].append(r)

    out = {}
    for leg in treated:
        t = rows.get(leg)
        if t is None:
            out[leg] = {"state": "treated_leg_absent_from_config", "candidates": []}
            continue
        if not t["family"]:
            out[leg] = {"state": "family_unknown", "candidates": []}
            continue
        cands = []
        for c in by_family[t["family"]]:
            if c["leg"] == leg or c["leg"] in treated:
                continue
            if c["state"] != "can_report":
                continue
            cands.append(
                {
                    "leg": c["leg"],
                    "atr_stop_mult": c["atr_stop_mult"],
                    "tp_r": c["tp_r"],
                    "same_symbol": bool(set(c["symbols"]) & set(t["symbols"])),
                    "same_timeframe": c["timeframe"] == t["timeframe"],
                    "routes": c["reporting_routes"],
                }
            )
        cands.sort(key=lambda d: (not d["same_symbol"], not d["same_timeframe"], d["leg"]))
        out[leg] = {
            "state": "candidates_found" if cands else "no_reporting_same_family_leg",
            "family": t["family"],
            "treated_to": {"atr_stop_mult": t["atr_stop_mult"], "tp_r": t["tp_r"]},
            "candidates": cands,
        }
    return out


# --- reporting --------------------------------------------------------------


def report(rows: dict, treated: dict, controls: dict) -> str:
    L = []
    n = len(rows)
    counts = collections.Counter(r["state"] for r in rows.values())
    L.append(f"POPULATION: {n} legs in config/strategies.yaml; "
             f"{len(treated)} treated by e35 ({E35_SHIP_COMMIT}); "
             f"{sum(1 for r in rows.values() if r['family'])} carry a family from the e35 corpus")
    L.append("")
    L.append("CAN THIS LEG REPORT INTO A `trades`-BASED POPULATION?")
    for st in REPORTABILITY_STATES:
        L.append(f"  {st:24s} {counts.get(st, 0):3d}")
    L.append(f"  -> {counts.get('can_report', 0)} of {n} legs can produce a live closed-trade observation")
    L.append("")
    L.append("THE e35 TREATED SET, AND WHETHER EACH COULD BE CONTROLLED")
    any_ctrl = False
    for leg in sorted(treated):
        c = controls.get(leg, {})
        r = rows.get(leg, {})
        ch = ", ".join(f"{f} {a}->{b}" for f, (a, b) in treated[leg].items())
        L.append(f"  {leg:26s} [{r.get('state','?'):20s}] {ch}")
        if c.get("state") == "candidates_found":
            any_ctrl = True
            for d in c["candidates"]:
                tag = ("same-symbol " if d["same_symbol"] else "") + ("same-tf" if d["same_timeframe"] else "")
                L.append(f"      candidate: {d['leg']:24s} asm={d['atr_stop_mult']} tp_r={d['tp_r']} {tag} via {','.join(d['routes'])}")
        else:
            L.append(f"      {c.get('state','?')}")
    L.append("")
    L.append("VERDICT: a within-family control that CAN report "
             + ("EXISTS for at least one treated leg" if any_ctrl else "EXISTS FOR NO TREATED LEG"))
    return "\n".join(L)


# --- power: does the control that exists actually BUY anything? --------------

E35_DEPLOY_UTC = "2026-08-30T08:53"
"""Ship commit 892c9a2c8 reached the trader at this instant (sprint log
S-E35-SHIP-REVERSED-GEOMETRY-2026-08-30). Rows are split on `created_at`, i.e.
on when the trade was OPENED, because a trade opened before the deploy carries
the OLD geometry however late it closes."""

UNATTRIBUTED_EXIT_REASONS = ("reconciler_filled", "netting_attributed")
"""Exit labels that do NOT record what ended the trade -- see
`BL-20260912-FOR-55-PERCENT-OF-WINNERS-THAT-MISSED-TARGET-THE-MECHANISM-THAT-STOPPED-THE-RUN-IS-NOT-IN-THE-JOURNAL`. Counted, never
silently dropped: excluding them is NOT a neutral filter, because an unlabelled
stop is exactly what a `reconciler_filled` close looks like."""


def _fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    from math import comb

    n = a + b + c + d
    if n == 0 or (a + b) == 0 or (c + d) == 0:
        return 1.0

    def p(x: int) -> float:
        return comb(a + b, x) * comb(c + d, a + c - x) / comb(n, a + c)

    lo, hi = max(0, a + c - (c + d)), min(a + b, a + c)
    obs = p(a)
    return sum(p(x) for x in range(lo, hi + 1) if p(x) <= obs + 1e-12)


def minimum_detectable_difference(n_treated: int, n_control: int, alpha: float = 0.05):
    """Smallest |rate difference| any 2x2 split at these n reaches `alpha` on.

    Returns None when NO split is significant -- *this comparison cannot answer
    the question at any effect size*, which is a different and more useful fact
    than a large MDE, and is never rendered as one.
    """
    if n_treated <= 0 or n_control <= 0:
        return None
    best = None
    for kt in range(n_treated + 1):
        for kc in range(n_control + 1):
            if _fisher_two_sided(kt, n_treated - kt, kc, n_control - kc) < alpha:
                diff = abs(kt / n_treated - kc / n_control)
                if best is None or diff < best:
                    best = diff
    return best


def arm_rows(journal_rows, legs, since=E35_DEPLOY_UTC):
    """Closed, non-backtest, priced rows for `legs` opened at or after `since`."""
    return [
        r
        for r in journal_rows
        if r.get("strategy_name") in legs
        and not r.get("is_backtest")
        and r.get("status") == "closed"
        and r.get("pnl") is not None
        and (r.get("created_at") or "") >= since
    ]


def grade_power(journal_rows, treated_legs_set, control_legs_set, since=E35_DEPLOY_UTC):
    """What a dose-response run against this control could and could not detect."""
    t = arm_rows(journal_rows, treated_legs_set, since)
    c = arm_rows(journal_rows, control_legs_set, since)
    def un(rs):
        return sum(1 for r in rs if r.get("exit_reason") in UNATTRIBUTED_EXIT_REASONS)

    def att(rs):
        return [r for r in rs if r.get("exit_reason") not in UNATTRIBUTED_EXIT_REASONS]
    return {
        "since": since,
        "treated_n": len(t),
        "control_n": len(c),
        "treated_unattributed": un(t),
        "control_unattributed": un(c),
        "mde_all_closes": minimum_detectable_difference(len(t), len(c)),
        "treated_n_attributable": len(att(t)),
        "control_n_attributable": len(att(c)),
        "mde_attributable_only": minimum_detectable_difference(len(att(t)), len(att(c))),
    }


# --- self-test --------------------------------------------------------------


def self_test() -> int:
    acc = {
        "live_paper": {"mode": "live", "account_class": "paper", "strategies": ["a", "d"]},
        "dry": {"mode": "dry_run", "account_class": "real_money", "strategies": ["b"]},
        "prop": {"mode": "live", "account_class": "prop", "strategies": ["c"]},
    }
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        print(("  ok  " if cond else "  FAIL ") + msg)
        ok += 0 if cond else 1

    r = routes_for(acc)
    chk(r["a"] == ["live_paper"], "routing is read off the ACCOUNT side")

    chk(classify_reportability({"execution": "live"}, ["live_paper"], acc)["state"] == "can_report",
        "live + a live non-prop route can report")
    chk(classify_reportability({"execution": "shadow"}, ["live_paper"], acc)["state"] == "blocked_shadow",
        "shadow is blocked however well it is routed")
    chk(classify_reportability({"execution": "shadow"}, [], acc)["state"] == "blocked_shadow",
        "  and execution is graded BEFORE routing, so the binding cause is named")
    chk(classify_reportability({"execution": "live"}, ["prop"], acc)["state"] == "blocked_prop_only",
        "a prop-only route is blocked, not can_report")
    chk(classify_reportability({"execution": "live"}, ["dry"], acc)["state"] == "blocked_dry_run_only",
        "a dry_run-only route is blocked")
    chk(classify_reportability({"execution": "live"}, [], acc)["state"] == "blocked_unrouted",
        "an unrouted live leg is its own state")
    chk(classify_reportability({}, ["live_paper"], acc)["state"] == "unknown_execution",
        "a missing execution is `we could not look`, never a block")
    chk(classify_reportability({"execution": "live"}, ["prop", "live_paper"], acc)["state"] == "can_report",
        "one reporting route is enough even beside a prop route")

    strat = {
        "t": {"execution": "live", "symbols": ["X"], "timeframe": "4h", "atr_stop_mult": 2},
        "ctl": {"execution": "live", "symbols": ["X"], "timeframe": "4h", "atr_stop_mult": 2.5},
        "shadow_ctl": {"execution": "shadow", "symbols": ["X"], "timeframe": "4h", "atr_stop_mult": 2.5},
    }
    acc2 = {"lp": {"mode": "live", "account_class": "paper",
                   "strategies": ["t", "ctl", "shadow_ctl"]}}
    fam = {"t": "F", "ctl": "F", "shadow_ctl": "F"}
    rows = census(strat, acc2, fam)
    got = find_controls(rows, {"t": {"atr_stop_mult": (2.5, 2)}})
    chk(got["t"]["state"] == "candidates_found", "an untreated reporting same-family leg IS a candidate")
    chk([d["leg"] for d in got["t"]["candidates"]] == ["ctl"],
        "  and the shadow sibling is NOT offered as one")

    got2 = find_controls(rows, {"t": {"atr_stop_mult": (2.5, 2)}, "ctl": {"atr_stop_mult": (2.5, 2)}})
    chk(got2["t"]["state"] == "no_reporting_same_family_leg",
        "a TREATED sibling is not a control -- it carries the treatment")

    rows_nf = census({"t": {"execution": "live"}}, {}, {})
    chk(find_controls(rows_nf, {"t": {}})["t"]["state"] == "family_unknown",
        "an unfamilied leg grades family_unknown, never `no control exists`")

    chk(minimum_detectable_difference(3, 3) is None,
        "an n at which NOTHING is significant returns None, never a large MDE")
    chk(minimum_detectable_difference(0, 9) is None,
        "an empty arm cannot detect anything")
    m1, m2 = minimum_detectable_difference(23, 11), minimum_detectable_difference(7, 7)
    chk(m1 is not None and m2 is not None and m1 < m2,
        "dropping rows widens the MDE -- the cost of an unattributable close is measurable")
    jr = [
        {"strategy_name": "t", "status": "closed", "pnl": 1, "created_at": "2026-09-01", "exit_reason": "sl"},
        {"strategy_name": "t", "status": "closed", "pnl": 1, "created_at": "2026-08-01", "exit_reason": "sl"},
        {"strategy_name": "t", "status": "closed", "pnl": 1, "created_at": "2026-09-02", "exit_reason": "reconciler_filled"},
        {"strategy_name": "t", "status": "open", "pnl": None, "created_at": "2026-09-03", "exit_reason": None},
    ]
    g = grade_power(jr, {"t"}, {"c"})
    chk(g["treated_n"] == 2, "a row OPENED before the deploy is excluded, however late it closed")
    chk(g["treated_unattributed"] == 1 and g["treated_n_attributable"] == 1,
        "unattributable closes are COUNTED, not silently dropped")

    print("self-test: OK" if ok == 0 else f"self-test: {ok} FAILURE(S)")
    return 1 if ok else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit the full census as JSON")
    ap.add_argument("--ship-commit", default=E35_SHIP_COMMIT)
    ap.add_argument("--power", metavar="JOURNAL_JSON",
                    help="a saved /api/diag/journal?table=trades pull; grade what a "
                         "dose-response against the discovered controls could detect")
    ap.add_argument("--control", action="append", default=[],
                    help="leg to use as an untreated control (repeatable); "
                         "required with --power, because this module must not GUESS one")
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    strategies = load_strategies()
    accounts = load_accounts()
    if strategies is None or accounts is None:
        print("could not read config/strategies.yaml or config/accounts.yaml "
              "-- refusing to report a census over a file we did not read", file=sys.stderr)
        return 2
    treated = treated_legs(a.ship_commit)
    if treated is None:
        print(f"could not read config/strategies.yaml at {a.ship_commit}^ or {a.ship_commit} "
              "-- refusing to grade controls against an empty treated set", file=sys.stderr)
        return 2

    fam = load_families()
    rows = census(strategies, accounts, fam)
    controls = find_controls(rows, treated)

    if a.power:
        if not a.control:
            print("--power needs at least one --control: naming the control is a "
                  "judgement this module refuses to make for you", file=sys.stderr)
            return 2
        bad = [c for c in a.control if rows.get(c, {}).get("state") != "can_report"]
        if bad:
            print("refusing: these --control legs cannot report -- "
                  + ", ".join(f"{c}={rows.get(c, {}).get('state', 'not-in-config')}" for c in bad),
                  file=sys.stderr)
            return 2
        jr = json.loads(pathlib.Path(a.power).read_text())
        if isinstance(jr, dict):
            jr = jr.get("rows") or []
        # A WITHIN-FAMILY control only controls its own family. Comparing a treated
        # pullback leg against a donchian control would be a cross-family comparison
        # wearing a control's name, so the family is derived from the control arm and
        # the treated arm is restricted to it rather than defaulting to every treated leg.
        cfams = {rows[c]["family"] for c in a.control}
        if len(cfams) != 1 or None in cfams:
            print("refusing: the --control legs must share exactly one known family "
                  f"(got {sorted(str(f) for f in cfams)}) -- a control only controls its own family",
                  file=sys.stderr)
            return 2
        family = cfams.pop()
        treated_reporting = {
            leg for leg in treated
            if rows.get(leg, {}).get("state") == "can_report"
            and rows.get(leg, {}).get("family") == family
        }
        if not treated_reporting:
            print(f"refusing: no treated leg in family {family!r} can report -- "
                  "there is nothing for this control to control", file=sys.stderr)
            return 2
        g = grade_power(jr, treated_reporting, set(a.control))
        if a.json:
            print(json.dumps(g, indent=2, sort_keys=True))
        else:
            def fmt(m):
                return "NOTHING is detectable at this n" if m is None else f"{m:.0%}"

            print(f"POWER, rows OPENED at or after {g['since']} (the e35 deploy) -- family {family!r}")
            print(f"  treated arm  {sorted(treated_reporting)}")
            print(f"  control arm  {sorted(a.control)}")
            print(f"  all closes        treated n={g['treated_n']:3d} "
                  f"(unattributed {g['treated_unattributed']})   "
                  f"control n={g['control_n']:3d} (unattributed {g['control_unattributed']})"
                  f"   MDE {fmt(g['mde_all_closes'])}")
            print(f"  attributable only treated n={g['treated_n_attributable']:3d}"
                  f"                       control n={g['control_n_attributable']:3d}"
                  f"                    MDE {fmt(g['mde_attributable_only'])}")
            print("  NOTE: excluding unattributable closes is NOT a neutral filter -- an "
                  "unlabelled stop is exactly what a `reconciler_filled` close looks like, so "
                  "a rate computed over the attributable subset is not an estimate of the rate.")
        return 0
    if a.json:
        print(json.dumps({"census": rows, "treated": treated, "controls": controls},
                         indent=2, sort_keys=True))
    else:
        print(report(rows, treated, controls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
