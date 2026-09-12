#!/usr/bin/env python3
"""MI-278 U18 — is the e35 dose-response GRADEABLE once the venue names the mechanism?

WHAT THIS ANSWERS, AND WHAT IT DELIBERATELY DOES NOT
-----------------------------------------------------
`BL-20260911-BOTH-OF-THE-E35-EXPERIMENTS-DESIGNED-CONTROLS-HAVE-ZERO-OBSERVATIONS`
offers two exits: (a) record e35 as unfalsifiable on its declared controls, or
(b) find a control that CAN report. MI-278 U11 refused BOTH and moved the binding
constraint OFF the control and ONTO the outcome variable: a control that reports
exists and has been trading throughout (`trend_donchian_eth` / `trend_donchian_sol`
-- same family, same symbol, same timeframe, held at 2.5, routed to `bybit_1`
instead of the prop bridge), while most treated closes carry `reconciler_filled`
or `netting_attributed`, which record THAT a row closed and not WHAT closed it.

MI-278 U10 then measured that the venue holds the answer, on
`/api/diag/bybit_raw_order_history`, at the cost of a match step that lost 6 of 17.

So the question this instrument settles is arithmetic: **does venue recovery
return enough of the unlabelled closes to make the dose-response gradeable?**
Both answers are results. It reports whichever one the data gives.

⚠️ THIS IS NOT EVIDENCE FOR
`BL-20260912-FOR-55-PERCENT-OF-WINNERS-THAT-MISSED-TARGET-THE-MECHANISM-THAT-STOPPED-THE-RUN-IS-NOT-IN-THE-JOURNAL`
and must never be cited as such. That row's criterion explicitly excludes a
retrospective re-grade and requires the field PERSISTED at close time with a
reader branching on it (Tier-2, proposed by U10, not taken). A retrospective join
is a legitimate research instrument for grading THIS test and is not a persistence
mechanism.

⚠️ AND THE MISSINGNESS IS NOT RANDOM, WHICH IS WHY RECOVERY IS THE POINT RATHER
THAN A CONVENIENCE. An unlabelled stop is exactly what a `reconciler_filled` close
looks like, so dropping the unlabelled rows conditions on something correlated with
the outcome. U11 recorded the attributable-subset pair and refused to lift it out
as a result for that reason. This instrument therefore reports the arms at THREE
levels -- journal-only, journal+venue, and the residual that stays unnameable --
and the residual is a first-class number, never a footnote.

METHOD
------
* The TREATED set is derived from the ship commit's own diff (`892c9a2c8`), not
  from the inline comments beside the values -- 9 legs, 10 field edits.
* The CONTROL set is U11's, and this module re-verifies it is HELD at 2.5 at the
  ship commit's parent, at the ship commit, and at HEAD before using it. A control
  that moved is not a control.
* The split instant is the ship commit's own timestamp. A trade OPENED before it
  carries the old geometry however late it closes, so the split is on `created_at`.
  ⚠️ MERGE IS NOT DEPLOY: `ict-git-sync` pulls on a ~5-minute timer, so a trade
  opened in that window is labelled treated while carrying the old geometry. That
  biases toward the null (it puts control-geometry rows in the treated arm), and
  the window is reported so a reader can size it rather than assume it away.
* `recover()`, `load_order_history` and `load_venue` are IMPORTED from U10/U9
  unmodified. A second copy of "what the venue calls this close" is exactly how
  two answers to one question start to drift.

Usage:
  python3 scripts/research/e35_dose_response.py --self-test
  python3 scripts/research/e35_dose_response.py --trades t.json --fetch-plan
  python3 scripts/research/e35_dose_response.py --trades t.json --cp cp/ --oh oh/
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import io
import json
import os
import subprocess
import sys
from math import comb

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.research.venue_mechanism_recovery import (  # noqa: E402
    MECHANISM_STATES, emit_fetch_plan, load_order_history, recover,
)
from scripts.research.netting_close_venue_adjudication import load_venue  # noqa: E402

#: The e35 ship commit. Everything about the treated set is derived from ITS DIFF.
SHIP = "892c9a2c8"
SHIP_INSTANT = "2026-08-30T08:53:19+00:00"
GEOMETRY_FIELDS = ("atr_stop_mult", "tp_r")

#: U11's reportable within-family controls. Held at 2.5, same family and symbol as
#: a treated leg, routed to `bybit_1` rather than the prop bridge. Re-verified held.
CONTROL_LEGS = ("trend_donchian_eth", "trend_donchian_sol")
CONTROL_HELD_ATR = 2.5

#: A journal `exit_reason` that records THAT the row closed, not WHAT closed it.
UNNAMED_EXIT_REASONS = ("reconciler_filled", "netting_attributed")

#: How a close is finally adjudicated. Never collapsed: `venue_named` and
#: `journal_named` are both answers but from different witnesses, and the two
#: `unnameable_*` values are "we asked and could not tell" vs "we could not ask".
ADJUDICATION_STATES = (
    "journal_named",          # the journal's own exit_reason names a mechanism
    "venue_named",            # the journal did not; the venue's stop_order_type did
    "venue_plain_order",      # the venue answered: an ordinary order, not a bracket leg
    "unnameable_venue_silent",   # corroborated, but absent from order history
    "unnameable_no_match",       # no corroborated venue row to join from
)

#: Which journal exit_reasons count as a STOP for the outcome variable. The dose
#: question is "did the tightened stop end the trade", so this is the outcome and
#: it is stated here rather than inlined at the comparison.
STOP_REASONS = ("sl", "sl_cross", "stop_loss", "trailing_stop", "giveback_stop")


def _ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() * 1000)


def _cfg_at(ref: str) -> dict:
    out = subprocess.run(["git", "show", f"{ref}:config/strategies.yaml"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return {}
    return (yaml.safe_load(io.StringIO(out.stdout)) or {}).get("strategies") or {}


def treated_legs() -> tuple[dict, list[str]]:
    """The e35 treated set, read off the SHIP COMMIT'S DIFF. Never off comments.

    Returns (leg -> {field: (before, after)}, problems). A comment-only edit --
    the HELD leg in that same commit carries one -- is correctly excluded,
    because this compares VALUES.
    """
    before, after = _cfg_at(f"{SHIP}^"), _cfg_at(SHIP)
    problems: list[str] = []
    if not before or not after:
        problems.append(
            f"could not read config/strategies.yaml at {SHIP}^ and/or {SHIP} — the "
            f"clone is shallow, so this is 'we could not look', NOT 'nothing was treated'."
        )
        return {}, problems
    out = {}
    for leg, a in after.items():
        b = before.get(leg) or {}
        chg = {f: (b.get(f), (a or {}).get(f))
               for f in GEOMETRY_FIELDS if b.get(f) != (a or {}).get(f)}
        if chg:
            out[leg] = chg
    return out, problems


def verify_controls_held() -> list[str]:
    """A control that MOVED is not a control. Checked at three refs, not asserted."""
    problems: list[str] = []
    for ref in (f"{SHIP}^", SHIP, "HEAD"):
        cfg = _cfg_at(ref)
        if not cfg:
            problems.append(f"could not read config/strategies.yaml at {ref} — control "
                            f"held-ness is UNVERIFIED at that ref, which is not the same "
                            f"as verified.")
            continue
        for leg in CONTROL_LEGS:
            got = (cfg.get(leg) or {}).get("atr_stop_mult")
            if got is None:
                problems.append(f"control {leg} is ABSENT from config at {ref}")
            elif float(got) != CONTROL_HELD_ATR:
                problems.append(f"control {leg} reads atr_stop_mult={got} at {ref}, "
                                f"not the held {CONTROL_HELD_ATR} — it is not a control.")
    return problems


def build_arms(trades: list[dict], treated: set[str]) -> tuple[list[dict], dict]:
    """Closed, non-backtest rows OPENED after the ship instant, tagged by arm."""
    split = dt.datetime.fromisoformat(SHIP_INSTANT)
    rows, census = [], collections.Counter()
    for t in trades:
        leg = t.get("strategy_name")
        arm = "treated" if leg in treated else ("control" if leg in CONTROL_LEGS else None)
        if arm is None:
            continue
        census[f"{arm}:seen"] += 1
        if t.get("is_backtest"):
            census[f"{arm}:excluded_backtest"] += 1
            continue
        if str(t.get("status")) != "closed":
            census[f"{arm}:excluded_not_closed"] += 1
            continue
        opened = t.get("created_at")
        if not opened:
            census[f"{arm}:excluded_no_created_at"] += 1
            continue
        if dt.datetime.fromisoformat(str(opened).replace("Z", "+00:00")) < split:
            census[f"{arm}:excluded_opened_before_ship"] += 1
            continue
        if not t.get("closed_at"):
            census[f"{arm}:excluded_no_closed_at"] += 1
            continue
        rows.append({**t, "_arm": arm})
        census[f"{arm}:kept"] += 1
    return rows, dict(census)


def adjudicate(rows: list[dict], recovered: dict) -> list[dict]:
    """Fold the journal's own label and the venue's answer into one verdict."""
    out = []
    for t in rows:
        reason = str(t.get("exit_reason") or "")
        rec = recovered.get(t["id"]) or {}
        if reason and reason not in UNNAMED_EXIT_REASONS:
            state, mech, witness = "journal_named", reason, "journal"
        elif rec.get("state") == "named":
            state, mech, witness = "venue_named", rec.get("stop_order_type"), "venue"
        elif rec.get("state") == "plain_order":
            state, mech, witness = "venue_plain_order", "plain_order", "venue"
        elif rec.get("state") == "absent_from_order_history":
            state, mech, witness = "unnameable_venue_silent", None, None
        else:
            state, mech, witness = "unnameable_no_match", None, None
        out.append({
            "trade": t["id"], "arm": t["_arm"], "leg": t.get("strategy_name"),
            "account": t.get("account_id"), "symbol": t.get("symbol"),
            "journal_exit_reason": reason or None,
            "venue_stop_order_type": rec.get("stop_order_type"),
            "venue_state": rec.get("state"),
            "state": state, "mechanism": mech, "witness": witness,
            "is_stop": _is_stop(state, reason, rec.get("stop_order_type")),
        })
    return out


def _is_stop(state: str, journal_reason: str, venue_sot) -> bool | None:
    """True/False/None — and None is a THIRD value, never folded into False.

    A close nobody can name is not a close that was not a stop. Returning False
    there is the exact substitution that makes an unnameable population read as
    evidence against the mechanism.
    """
    if state == "journal_named":
        return journal_reason in STOP_REASONS
    if state == "venue_named":
        return "stop" in str(venue_sot).lower()
    if state == "venue_plain_order":
        return False          # the venue answered: an ordinary order, not a bracket leg
    return None               # unnameable — we could not tell


def summarise(verdicts: list[dict]) -> dict:
    out = {}
    for arm in ("treated", "control"):
        sub = [v for v in verdicts if v["arm"] == arm]
        gradeable = [v for v in sub if v["is_stop"] is not None]
        stops = [v for v in gradeable if v["is_stop"]]
        out[arm] = {
            "n": len(sub),
            "by_state": dict(collections.Counter(v["state"] for v in sub)),
            "journal_only_gradeable": sum(1 for v in sub if v["state"] == "journal_named"),
            "gradeable_with_venue": len(gradeable),
            "unnameable": len(sub) - len(gradeable),
            "stops": len(stops),
            "stop_rate": (len(stops) / len(gradeable)) if gradeable else None,
        }
    return out


def _fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Exact two-sided p for the 2x2 [[a,b],[c,d]], summing tables no likelier."""
    n = a + b + c + d
    if n == 0 or (a + b) == 0 or (c + d) == 0:
        return 1.0
    obs = comb(a + b, a) * comb(c + d, c) / comb(n, a + c)
    p = 0.0
    for i in range(0, min(a + b, a + c) + 1):
        j = a + c - i
        if j < 0 or j > c + d:
            continue
        pr = comb(a + b, i) * comb(c + d, j) / comb(n, a + c)
        if pr <= obs * (1 + 1e-9):
            p += pr
    return min(p, 1.0)


def sensitivity(summary: dict) -> dict:
    """The band the UNNAMEABLE closes leave the comparison in — the whole point.

    A stop rate computed on the gradeable subset alone is a rate over a
    population selected by whether anyone could name the close, and an unlabelled
    stop is exactly what a `reconciler_filled` close looks like. So the
    missingness is correlated with the outcome and the subset rate is not an
    estimate of anything. This imputes the unnameable rows at BOTH extremes and
    reports the interval.

    ⚠️ The verdict is `gradeable` ONLY when the band does not contain the null.
    If the worst case reverses the sign, the observed p is not a finding however
    small it is — that is the number a reader will otherwise lift out.
    """
    t, c = summary["treated"], summary["control"]
    ts, tn, tu = t["stops"], t["gradeable_with_venue"], t["unnameable"]
    cs, cn, cu = c["stops"], c["gradeable_with_venue"], c["unnameable"]
    if tn == 0 or cn == 0:
        return {"verdict": "not_gradeable_no_subset",
                "why": "an arm has nothing gradeable at all"}
    out = {
        "observed": {"treated": [ts, tn], "control": [cs, cn],
                     "treated_rate": ts / tn, "control_rate": cs / cn,
                     "p": _fisher_two_sided(ts, tn - ts, cs, cn - cs)},
        "treated_band": [ts / (tn + tu), (ts + tu) / (tn + tu)],
        "control_band": [cs / (cn + cu), (cs + cu) / (cn + cu)],
    }
    out["worst_case_p"] = _fisher_two_sided(
        ts, tn + tu - ts, cs + cu, cn + cu - cs - cu)
    out["best_case_p"] = _fisher_two_sided(
        ts + tu, tn - ts, cs, cn + cu - cs)
    sign_survives = out["treated_band"][0] > out["control_band"][1]
    out["sign_survives_worst_case"] = sign_survives
    out["verdict"] = "gradeable" if sign_survives else "not_gradeable_missingness_dominates"
    return out


def report(treated: dict, verdicts: list[dict], summary: dict,
           census: dict, problems: list[str]) -> int:
    print("PROBLEMS FIRST, because an unread window renders identically to an empty one")
    print("=" * 78)
    if problems:
        for p in problems:
            print(f"  !! {p}")
    else:
        print("  none — every declared read returned, and the controls verify HELD.")
    print()

    print(f"TREATED SET — derived from {SHIP}'s own diff, not from the comments beside it")
    print("=" * 78)
    for leg, chg in sorted(treated.items()):
        print(f"  {leg:26s} " + ", ".join(f"{f}: {b} -> {a}" for f, (b, a) in chg.items()))
    print(f"  {len(treated)} leg(s), "
          f"{sum(len(c) for c in treated.values())} field edit(s)")
    print(f"  CONTROL: {', '.join(CONTROL_LEGS)} — held at {CONTROL_HELD_ATR}, verified at "
          f"{SHIP}^, {SHIP} and HEAD")
    print()

    print("POPULATION — every row accounted for, so a filter cannot silently shrink an arm")
    print("=" * 78)
    for k in sorted(census):
        print(f"  {k:42s} {census[k]}")
    print()

    print("ADJUDICATION — journal first, venue second, and what stays unnameable")
    print("=" * 78)
    hdr = f"  {'arm':8s} {'n':>3s} {'jrnl':>5s} {'+venue':>7s} {'unnam':>6s} {'stops':>6s} {'rate':>7s}"
    print(hdr)
    for arm in ("treated", "control"):
        s = summary[arm]
        rate = "—" if s["stop_rate"] is None else f"{s['stop_rate']:.3f}"
        print(f"  {arm:8s} {s['n']:3d} {s['journal_only_gradeable']:5d} "
              f"{s['gradeable_with_venue']:7d} {s['unnameable']:6d} {s['stops']:6d} {rate:>7s}")
    print()
    for arm in ("treated", "control"):
        print(f"  {arm} by state: {summary[arm]['by_state']}")
    print()

    t, c = summary["treated"], summary["control"]
    print("THE VERDICT THIS UNIT OWES")
    print("=" * 78)
    gain_t = t["gradeable_with_venue"] - t["journal_only_gradeable"]
    gain_c = c["gradeable_with_venue"] - c["journal_only_gradeable"]
    print(f"  venue recovery added {gain_t} treated and {gain_c} control close(s) to the "
          f"gradeable set")
    print(f"  gradeable: treated {t['journal_only_gradeable']} -> {t['gradeable_with_venue']} "
          f"of {t['n']}   ·   control {c['journal_only_gradeable']} -> "
          f"{c['gradeable_with_venue']} of {c['n']}")
    if t["unnameable"] or c["unnameable"]:
        print(f"  !! STILL UNNAMEABLE: {t['unnameable']} treated, {c['unnameable']} control. "
              f"These are NOT non-stops. An unlabelled stop is exactly what a "
              f"reconciler_filled close looks like, so the missingness is correlated with "
              f"the outcome and dropping them conditions on it.")
    print()
    sens = sensitivity(summary)
    print("  IS THE DOSE-RESPONSE GRADEABLE?  " + sens["verdict"].upper())
    o = sens.get("observed")
    if o:
        print(f"    on the gradeable subset: treated {o['treated'][0]}/{o['treated'][1]}"
              f"={o['treated_rate']:.3f} vs control {o['control'][0]}/{o['control'][1]}"
              f"={o['control_rate']:.3f}, two-sided Fisher p={o['p']:.4f}")
        print(f"    imputing the unnameable at BOTH extremes: treated in "
              f"[{sens['treated_band'][0]:.3f}, {sens['treated_band'][1]:.3f}], control in "
              f"[{sens['control_band'][0]:.3f}, {sens['control_band'][1]:.3f}]")
        print(f"    p ranges {sens['best_case_p']:.4f} .. {sens['worst_case_p']:.4f}; "
              f"sign survives the worst case: {sens['sign_survives_worst_case']}")
        if not sens["sign_survives_worst_case"]:
            print(f"    !! SO p={o['p']:.4f} IS NOT A FINDING. The bands overlap, so an "
                  f"imputation of the unnameable rows reverses the sign. That p is exactly "
                  f"the number a reader would lift out; it is reported here only beside "
                  f"the band that disqualifies it.")
    print()
    # The id is on ONE unbroken line on purpose: `check_backlog_refs` scans the
    # SOURCE, so a wrapped id is a reference to a row that does not exist. This
    # cost a guard cycle when it was first written across two string literals.
    not_evidence_for = (
        "BL-20260912-FOR-55-PERCENT-OF-WINNERS-THAT-MISSED-TARGET-THE-MECHANISM-THAT-STOPPED-THE-RUN-IS-NOT-IN-THE-JOURNAL"  # noqa: E501
    )
    print(f"  ⚠️ NOT evidence for {not_evidence_for}: that row's criterion excludes a "
          f"retrospective re-grade and requires the field persisted at close time with a "
          f"reader branching on it.")
    return 1 if problems else 0


def self_test() -> int:
    """Plant each behaviour and require the module to REFUSE or report it exactly.

    The bar is the RETURN VALUE of the pure functions, never their printed text.
    Every control isolates one thing, and each refusal has a positive control
    beside it so a probe that fires on the wrong thing is caught, not credited.
    """
    fails: list[str] = []

    def ck(label, got, want):
        if got != want:
            fails.append(f"  FAIL - {label}: got {got!r}, want {want!r}")
        else:
            print(f"  PASS - {label}")

    # --- _is_stop: the three-valued outcome ------------------------------
    ck("journal 'sl' is a stop", _is_stop("journal_named", "sl", None), True)
    ck("journal 'exit_head' is not a stop", _is_stop("journal_named", "exit_head", None), False)
    ck("journal 'intent_reduce' is not a stop",
       _is_stop("journal_named", "intent_reduce", None), False)
    ck("venue 'StopLoss' is a stop", _is_stop("venue_named", "", "StopLoss"), True)
    ck("venue 'TakeProfit' is not a stop", _is_stop("venue_named", "", "TakeProfit"), False)
    ck("venue plain_order is not a stop", _is_stop("venue_plain_order", "", None), False)
    # THE ONE THAT MATTERS: unnameable must be None, never False. Folding it into
    # False makes an unnameable population read as evidence against the mechanism.
    ck("unnameable (venue silent) is None, NOT False",
       _is_stop("unnameable_venue_silent", "", None), None)
    ck("unnameable (no match) is None, NOT False",
       _is_stop("unnameable_no_match", "", None), None)

    # --- adjudicate: precedence, and that the venue never overrides the journal ---
    def T(i, arm="treated", **kw):
        return {"id": i, "_arm": arm, "strategy_name": "L", "account_id": "bybit_1",
                "symbol": "S", **kw}
    v = adjudicate([T(1, exit_reason="sl")], {})
    ck("a journal-named close needs no venue row", v[0]["state"], "journal_named")
    ck("...and its witness is the journal", v[0]["witness"], "journal")
    # A venue answer must NOT override a journal label — otherwise the venue
    # quietly re-grades closes the journal already named, which is a different
    # and much larger claim than the one this unit makes.
    v = adjudicate([T(1, exit_reason="sl")], {1: {"state": "named", "stop_order_type": "TakeProfit"}})
    ck("the venue does NOT override a journal-named close", v[0]["state"], "journal_named")
    ck("...and the journal's own answer stands", v[0]["is_stop"], True)
    v = adjudicate([T(1, exit_reason="reconciler_filled")],
                   {1: {"state": "named", "stop_order_type": "StopLoss"}})
    ck("an unnamed close IS recovered from the venue", v[0]["state"], "venue_named")
    ck("...and reads as a stop", v[0]["is_stop"], True)
    v = adjudicate([T(1, exit_reason="netting_attributed")],
                   {1: {"state": "absent_from_order_history"}})
    ck("netting_attributed is unnamed too", v[0]["state"], "unnameable_venue_silent")
    v = adjudicate([T(1, exit_reason="reconciler_filled")], {})
    ck("no venue row at all -> unnameable_no_match", v[0]["state"], "unnameable_no_match")
    v = adjudicate([T(1, exit_reason=None)], {1: {"state": "plain_order"}})
    ck("an EMPTY journal exit_reason is treated as unnamed, not as a mechanism",
       v[0]["state"], "venue_plain_order")

    # --- build_arms: every exclusion is counted, never silent -------------
    base = {"strategy_name": "trend_donchian", "status": "closed", "is_backtest": 0,
            "created_at": "2026-09-01T00:00:00+00:00", "closed_at": "2026-09-02T00:00:00+00:00"}
    rows, cen = build_arms([{**base, "id": 1}], {"trend_donchian"})
    ck("a qualifying treated row is kept", len(rows), 1)
    rows, cen = build_arms([{**base, "id": 1, "created_at": "2026-08-01T00:00:00+00:00"}],
                           {"trend_donchian"})
    ck("a row OPENED before the ship instant is excluded", len(rows), 0)
    ck("...and the exclusion is COUNTED, not silent",
       cen.get("treated:excluded_opened_before_ship"), 1)
    rows, cen = build_arms([{**base, "id": 1, "status": "rejected"}], {"trend_donchian"})
    ck("a rejected row is excluded and counted", cen.get("treated:excluded_not_closed"), 1)
    rows, cen = build_arms([{**base, "id": 1, "is_backtest": 1}], {"trend_donchian"})
    ck("a backtest row is excluded and counted", cen.get("treated:excluded_backtest"), 1)
    rows, cen = build_arms([{**base, "id": 1, "closed_at": None}], {"trend_donchian"})
    ck("a closed row with no closed_at is excluded and counted",
       cen.get("treated:excluded_no_closed_at"), 1)
    rows, cen = build_arms([{**base, "id": 1, "strategy_name": "trend_donchian_eth"}],
                           {"trend_donchian"})
    ck("a control leg lands in the CONTROL arm", rows[0]["_arm"] if rows else None, "control")
    rows, cen = build_arms([{**base, "id": 1, "strategy_name": "something_else"}],
                           {"trend_donchian"})
    ck("an unrelated leg is in NEITHER arm", len(rows), 0)
    ck("...and is not censused either (it was never a candidate)", cen, {})

    # --- summarise: the unnameable rows must not inflate a rate ----------
    vs = [{"arm": "treated", "state": "journal_named", "is_stop": True},
          {"arm": "treated", "state": "venue_named", "is_stop": True},
          {"arm": "treated", "state": "unnameable_no_match", "is_stop": None},
          {"arm": "control", "state": "journal_named", "is_stop": False}]
    s = summarise(vs)
    ck("the rate's denominator EXCLUDES the unnameable", s["treated"]["stop_rate"], 1.0)
    ck("...and n still counts them", s["treated"]["n"], 3)
    ck("...and they are reported as their own number", s["treated"]["unnameable"], 1)
    ck("an arm with nothing gradeable has rate None, NOT 0.0",
       summarise([{"arm": "treated", "state": "unnameable_no_match", "is_stop": None},
                  {"arm": "control", "state": "journal_named", "is_stop": True}]
                 )["treated"]["stop_rate"], None)

    # --- sensitivity: the band, and the verdict it forces ----------------
    # A perfectly separated pair with NOTHING unnameable must grade gradeable...
    S = {"treated": {"stops": 10, "gradeable_with_venue": 10, "unnameable": 0},
         "control": {"stops": 0, "gradeable_with_venue": 10, "unnameable": 0}}
    ck("a clean, fully-named separation grades gradeable",
       sensitivity(S)["verdict"], "gradeable")
    ck("...with the worst case equal to the observed when nothing is missing",
       round(sensitivity(S)["worst_case_p"], 6), round(sensitivity(S)["observed"]["p"], 6))
    # ...and a pair whose SIGN the unnameable rows can reverse must not. The
    # fixture is the LIVE 2026-09-12 shape, because an invented one would not
    # show that the criterion actually bites on the case it was written for: an
    # observed p under 0.05 that a reader would lift out.
    S2 = {"treated": {"stops": 11, "gradeable_with_venue": 14, "unnameable": 13},
          "control": {"stops": 4, "gradeable_with_venue": 11, "unnameable": 1}}
    s2 = sensitivity(S2)
    ck("the live shape grades NOT gradeable",
       s2["verdict"], "not_gradeable_missingness_dominates")
    ck("...because the bands OVERLAP, treated-low below control-high",
       s2["treated_band"][0] < s2["control_band"][1], True)
    ck("...and the worst case is a flat p=1.0", round(s2["worst_case_p"], 4), 1.0)
    ck("...while the nominally-significant observed p is STILL reported beside it",
       s2["observed"]["p"] < 0.05, True)
    # The criterion is sign-survival, deliberately the WEAKEST reasonable test —
    # it invents no alpha and asks only that the direction hold. Failing it is
    # therefore decisive rather than a matter of where a threshold was put.
    S2b = {"treated": {"stops": 11, "gradeable_with_venue": 14, "unnameable": 0},
           "control": {"stops": 4, "gradeable_with_venue": 11, "unnameable": 1}}
    ck("the SAME observed pair with the unnameable rows removed grades gradeable",
       sensitivity(S2b)["verdict"], "gradeable")
    # An arm with nothing gradeable is its own refusal, never a rate of 0.
    S3 = {"treated": {"stops": 0, "gradeable_with_venue": 0, "unnameable": 9},
          "control": {"stops": 3, "gradeable_with_venue": 5, "unnameable": 0}}
    ck("an arm with nothing gradeable refuses rather than scoring 0",
       sensitivity(S3)["verdict"], "not_gradeable_no_subset")
    # Fisher itself, against a hand-checkable table.
    ck("Fisher on a 2x2 with no association is 1.0",
       round(_fisher_two_sided(5, 5, 5, 5), 4), 1.0)
    ck("Fisher on a perfectly separated 10-vs-10 is tiny",
       _fisher_two_sided(10, 0, 0, 10) < 1e-4, True)

    # --- the imports are the REAL ones, not a local re-derivation --------
    ck("MECHANISM_STATES comes from U10 unmodified",
       MECHANISM_STATES[:2], ("named", "plain_order"))
    # Not a tautology: DRIVE every declared state and require the set produced to
    # equal the set declared. A state that can never be reached is a state nobody
    # tests, and a state produced but undeclared escapes the vocabulary entirely.
    drive = [
        (T(1, exit_reason="sl"), {}),
        (T(2, exit_reason="reconciler_filled"), {2: {"state": "named", "stop_order_type": "StopLoss"}}),
        (T(3, exit_reason="reconciler_filled"), {3: {"state": "plain_order"}}),
        (T(4, exit_reason="reconciler_filled"), {4: {"state": "absent_from_order_history"}}),
        (T(5, exit_reason="reconciler_filled"), {5: {"state": "no_corroborated_match"}}),
    ]
    produced = {adjudicate([r], m)[0]["state"] for r, m in drive}
    ck("every declared adjudication state is REACHABLE, and none escapes the vocabulary",
       sorted(produced), sorted(ADJUDICATION_STATES))

    # --- the live tree: controls really are held -------------------------
    held = verify_controls_held()
    ck("the live tree's controls verify HELD at all three refs", held, [])
    tl, tp = treated_legs()
    ck("the ship commit's diff yields a non-empty treated set", bool(tl) and not tp, True)
    ck("...of exactly 9 legs", len(tl), 9)

    if fails:
        print("\n".join(fails))
        print("\nSELF-TEST FAILED")
        return 1
    print("\nALL PASS")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", help="/api/diag/journal?table=trades response")
    ap.add_argument("--cp", help="directory of /api/diag/bybit_raw_closed_pnl responses")
    ap.add_argument("--oh", help="directory of /api/diag/bybit_raw_order_history responses")
    ap.add_argument("--fetch-plan", action="store_true",
                    help="print the diag_fetch lines for this population and exit")
    ap.add_argument("--json", help="write the per-close verdicts here")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.trades:
        ap.error("--trades is required")

    problems = verify_controls_held()
    treated, tp = treated_legs()
    problems += tp
    with open(args.trades) as fh:
        trades = json.load(fh)
    rows, census = build_arms(trades, set(treated))

    if args.fetch_plan:
        emit_fetch_plan([r for r in rows
                         if str(r.get("exit_reason") or "") in UNNAMED_EXIT_REASONS
                         or not r.get("exit_reason")])
        return 0

    recovered: dict = {}
    if args.cp and args.oh:
        need = [r for r in rows if str(r.get("exit_reason") or "") in UNNAMED_EXIT_REASONS
                or not r.get("exit_reason")]
        # load_venue returns (records, per-pair query states, problems) — the
        # states are its own collapsed-state register and are folded into
        # `problems` here rather than dropped, so an UNREAD pair cannot render
        # as a pair the venue had nothing for.
        closed_pnl, vstates, p1 = load_venue(args.cp)
        # `rows_returned` and `no_rows` are both ANSWERS; anything else is the
        # venue query not having happened, which must not read as an empty book.
        # The vocabulary is READ off load_venue rather than guessed — the first
        # draft of this guessed ('ok', 'empty') and reported every healthy pair
        # as a problem.
        for pair, sts in sorted(vstates.items()):
            bad = [x for x in sts if x not in ("rows_returned", "no_rows")]
            if bad:
                p1 = p1 + [f"closed-pnl query_state {sorted(set(bad))} on {pair} — "
                           f"'we could not look', NOT an empty book"]
        orders, p2 = load_order_history(args.oh)
        problems += p1 + p2
        # `recover` wants U2's two annotation keys; supply them from the trade
        # itself rather than editing the imported function.
        staged = [{**r, "_u2_class": r["_arm"], "_u2_pnl": r.get("pnl")} for r in need]
        for rec in recover(staged, closed_pnl, orders):
            recovered[rec["trade"]] = rec
    else:
        problems.append("no --cp/--oh given: the venue was NOT consulted, so every "
                        "unnamed close reads `unnameable_no_match` — that is 'we did not "
                        "look', not 'the venue is silent'.")

    verdicts = adjudicate(rows, recovered)
    summary = summarise(verdicts)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"treated_legs": treated, "census": census,
                       "verdicts": verdicts, "summary": summary,
                       "sensitivity": sensitivity(summary),
                       "problems": problems}, fh, indent=2)
    return report(treated, verdicts, summary, census, problems)


if __name__ == "__main__":
    raise SystemExit(main())
