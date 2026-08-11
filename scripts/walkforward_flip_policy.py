#!/usr/bin/env python3
"""Walk-forward driver for the flip-policy conflict-resolution investigation.

Implements the scope in
`docs/sprint-plans/CONFLICT-POLICY-WALKFORWARD-SCOPE-2026-05-30.md`:

  Two anchored folds × {4-member, 6-member} × {reverse, hold, flat}
  = 12 system/portfolio backtests, each in train + OOS half.

  Fold A: train 2020-06..2023-12   OOS 2024-01..2026-02   (5.7y / 2y)
  Fold B: train 2022-01..2024-06   OOS 2024-07..2026-02   (2.5y / 1.7y)

Reads the per-(strategy, window) signal cache built by
`scripts/backtest_system.py`'s `generate_signal_stream`; pre-warm with
`--prebuild-cache` (recommended) before doing the policy comparison.

Emits a combined JSON at `runtime_logs/system_backtest/walkforward/
walkforward_<UTC>.json` and prints a Markdown summary table.

Pass criteria (from the scope doc, also checked here):
  1. 4-member: hold > reverse in net AND maxDD% in BOTH train AND OOS in
     BOTH folds (4 cells).
  2. 6-member: hold not worse than reverse in OOS for both folds (2 cells).

Tier-1 research tooling — composition over scripts/backtest_system.py's
engine; no engine change, no live-path imports beyond the existing
aggregate_intents call.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import after sys.path adjustment so the live aggregator import chain works.
from scripts.backtest_system import (  # noqa: E402
    _load_candles,
    _parse_tf_classes,
    generate_signal_stream,
    run_system_backtest,
)

ROSTERS = {
    "4mem": [
        "trend_donchian",
        "fade_breakout_4h",
        "squeeze_breakout_4h",
        "fvg_range_15m",
    ],
    "6mem": [
        "trend_donchian",
        "fade_breakout_4h",
        "squeeze_breakout_4h",
        "fvg_range_15m",
        "turtle_soup",
        "ict_scalp_5m",
    ],
}

# Folds as anchored (train begin -> train end / OOS begin -> OOS end).
# Each tuple covers one fold; the windows match the scope doc.
FOLDS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "A": {
        "train": ("2020-06-01", "2023-12-31"),
        "oos": ("2024-01-01", "2026-02-28"),
    },
    "B": {
        "train": ("2022-01-01", "2024-06-30"),
        "oos": ("2024-07-01", "2026-02-28"),
    },
}

POLICIES = ("reverse", "hold", "flat")

# BL-20260811 — the LIVE flip-confidence override as its own ARM.
#
# It is deliberately NOT a fourth `flip_policy`: live it is `hold` PLUS a
# predicate (`FLIP_CONFIDENCE_THRESHOLD` / `FLIP_MIN_POSITION_AGE_HOURS`), so
# the arm is the triple (flip_policy="hold", threshold>0, min_age>0). Carrying
# it as a named arm here keeps the May 2026 fold / roster / cell structure
# byte-identical, which is the point: the incumbent `hold` earned its place on
# these exact cells (docs/audits/walkforward-flip-policy-2026-05-30.md), and an
# arm that displaced it has to be judged on the SAME population, not a fresh
# one chosen after the fact.
CONFGAP_ARM = "hold_confgap"

# M26 P1 counterfactual arms: the SAME live predicate, restricted to one TF
# class. NO LIVE CODE PATH IMPLEMENTS THESE -- the deployed override is TF-blind
# (`CONFGAP_ARM` is the arm that mirrors production). They exist because the
# blind arm's loss is a single number over a mixed population, and M26 P0 says
# the split is where the signal lives: same/near-TF conflicts bled -$2.3k held
# while cross-TF (>=4x) made +$3.5k held. Running the restriction as its own arm
# answers "is the loss confined to one class?" with an A/B on the same cells,
# rather than by attributing PnL to conflicts after the fact -- which would need
# a counterfactual per fire and could not be checked.
CONFGAP_CROSSCLOCK_ARM = "hold_confgap_crossclock"
CONFGAP_SAMECLOCK_ARM = "hold_confgap_sameclock"

# Every arm that arms the override predicate. Membership here (not a name
# comparison) decides whether a cell gets the live threshold/age injected, so
# adding an arm cannot silently produce a row labelled as the override while
# running the inert incumbent.
_CONFGAP_ARMS: Dict[str, Optional[str]] = {
    CONFGAP_ARM: None,                       # None => unrestricted == the LIVE shape
    CONFGAP_CROSSCLOCK_ARM: "cross_clock",
    CONFGAP_SAMECLOCK_ARM: "same_clock",
}

# Arm name -> (flip_policy, threshold, min_age_hours). `None` threshold/age
# means "leave at 0" (the override is inert), so the three legacy policies keep
# byte-identical behaviour.
_ARM_SPECS: Dict[str, Tuple[str, Optional[float], Optional[float]]] = {
    "reverse": ("reverse", None, None),
    "hold": ("hold", None, None),
    "flat": ("flat", None, None),
    CONFGAP_ARM: ("hold", None, None),   # thresholds injected from CLI at run time
    CONFGAP_CROSSCLOCK_ARM: ("hold", None, None),
    CONFGAP_SAMECLOCK_ARM: ("hold", None, None),
}

OUT_DIR = _REPO_ROOT / "runtime_logs" / "system_backtest" / "walkforward"


@dataclass
class Cell:
    fold: str
    half: str  # "train" | "oos"
    roster: str
    policy: str
    start: str
    end: str
    summary: Dict[str, Any] = field(default_factory=dict)


def _prebuild_cache(base5m, roster: List[str], folds: Dict[str, Dict[str, Tuple[str, str]]],
                    refresh: bool) -> None:
    """Generate the signal cache for every (strategy, window) the run needs.

    Cache keys are `(strategy, base_path, start, end, overrides)` — so each
    fold half is its own cache file. Doing this up front lets the 12 policy
    runs reuse the streams without re-running the per-bar order_package.
    """
    seen: set[Tuple[str, str, str]] = set()
    for fold_id, halves in folds.items():
        for half_id, (s, e) in halves.items():
            for strat in roster:
                key = (strat, s, e)
                if key in seen:
                    continue
                seen.add(key)
                print(f"[cache] {fold_id}/{half_id}/{strat}  {s} -> {e}", flush=True)
                generate_signal_stream(strat, base5m, start=s, end=e,
                                       overrides={}, refresh=refresh)


def _run_cell(base5m, *, fold: str, half: str, roster_name: str, policy: str,
              start: str, end: str, balance: float, risk_pct: float,
              daily_loss_pct: float, ttl: int,
              confgap_threshold: float = 0.0,
              confgap_min_age_hours: float = 0.0) -> Cell:
    roster = ROSTERS[roster_name]
    flip_policy, _, _ = _ARM_SPECS.get(policy, (policy, None, None))
    # Only the confgap arm arms the predicate; every other arm passes 0/0 so the
    # override is inert and the legacy cells stay byte-identical.
    is_confgap = policy in _CONFGAP_ARMS
    thr = confgap_threshold if is_confgap else 0.0
    age = confgap_min_age_hours if is_confgap else 0.0
    tf_spec = _CONFGAP_ARMS.get(policy) if is_confgap else None
    tf_classes = _parse_tf_classes(tf_spec)
    print(f"[run ] fold={fold} half={half} roster={roster_name} policy={policy}"
          f"{f' (gap>={thr} age>={age}h' if is_confgap else ''}"
          f"{f' tf={tf_spec}' if tf_spec else ''}"
          f"{')' if is_confgap else ''} ...", flush=True)
    out = run_system_backtest(
        base5m, roster=roster, start=start, end=end,
        initial_balance=balance, risk_pct=risk_pct,
        daily_loss_pct=daily_loss_pct, signal_ttl_bars=ttl,
        overrides={}, refresh=False, clock_tf="15m",
        flip_policy=flip_policy,
        flip_confidence_threshold=thr,
        flip_min_position_age_hours=age,
        flip_confgap_tf_classes=tf_classes,
    )
    cell = Cell(fold=fold, half=half, roster=roster_name, policy=policy,
                start=start, end=end, summary=out)
    s = out
    # Report BOTH flip kinds. A confgap arm's churn lands under `flip_confgap`,
    # so printing only `flip` would show the override arm as flips=0 — visually
    # identical to the incumbent `hold` while it was actively flipping.
    flips = s["by_exit_reason"].get("flip", 0)
    confgap_flips = s["by_exit_reason"].get("flip_confgap", 0)
    ov = (s.get("evidence", {}).get("flip_override", {}) or {})
    fired = ov.get("overrides_fired")
    # by_tf_class was CAPTURED by the first production run and never printed, so
    # the one question M26 P0 calls decisive could not be read off the output at
    # all (the artifacts were unreachable from a PM-side session). Printing it
    # per cell is the cheap half of the fix.
    tfc = ov.get("by_tf_class") or {}
    tf_str = " ".join(
        f"{k}={v.get('overrides_fired', 0)}/{v.get('conflicts', 0)}"
        for k, v in sorted(tfc.items())) or "-"
    print(f"[done] {fold}/{half}/{roster_name}/{policy}  "
          f"net=${s['net_pnl']:.0f}  maxDD={s['max_drawdown_pct']:.2f}%  "
          f"ret/DD={s.get('return_dd_ratio')}  trades={s['total_trades']}  "
          f"flips={flips}  confgap_flips={confgap_flips}  fired={fired}  "
          f"suppressed_by_tf={ov.get('suppressed_by_tf_filter')}  "
          f"tf[fired/conflicts]: {tf_str}", flush=True)
    return cell


def _evaluate_pass_criteria(cells: List[Cell]) -> Dict[str, Any]:
    """Apply the scope doc's pass / fail criteria to the result grid.

    SCOPED TO WHAT THE RUN ACTUALLY COVERED. Both criteria compare `hold` vs
    `reverse` -- the MAY 2026 question -- over the full fold x roster grid. A run
    that does not span that grid cannot answer them, and until 2026-08-11 this
    function said so by calling them FAILED: it looped over the module-level
    `FOLDS` and a hardcoded roster regardless of the run, so every absent cell
    became `missing_cell` and `overall_pass` was False by construction.

    That mattered in production, not in theory. The flip-override walk-forward
    (run 31523739722) shards ONE FOLD PER JOB for wall-clock, so each job saw the
    other fold's cells as missing and printed `Overall: FAIL` -- next to a result
    about a DIFFERENT arm (the confgap override) that the criteria never test.
    A reader has to already know the harness to discount it, and the ones who do
    not read a red verdict as the finding.

    So the applicability is now three-state, the same discipline
    `_m26_tf_class`/`exit_anchor` apply: `pass=True` (tested, met) / `pass=False`
    (tested, not met) / `pass=None` + `applicable=False` (NOT TESTED -- the arms
    or rosters this criterion needs were not in the run). "We did not look" and
    "we looked and it failed" are opposite statements and neither may wear the
    other's label. `overall_pass` is likewise None when nothing was applicable,
    never a vacuous True and never a fabricated False.
    """
    by_key = {(c.fold, c.half, c.roster, c.policy): c for c in cells}
    ran_folds = sorted({c.fold for c in cells}) or list(FOLDS)
    ran_policies = {c.policy for c in cells}
    ran_rosters = {c.roster for c in cells}
    # Both criteria are hold-vs-reverse tests; without BOTH arms present there is
    # nothing to compare, whatever else the run measured.
    hold_reverse_ran = {"hold", "reverse"} <= ran_policies

    # Criterion 1: 4-member hold > reverse in NET AND maxDD% across all
    # (fold, half) cells.
    crit1_cells: List[Dict[str, Any]] = []
    crit1_pass = True
    crit1_applicable = hold_reverse_ran and "4mem" in ran_rosters
    for fold_id in (ran_folds if crit1_applicable else []):
        for half_id in ("train", "oos"):
            h = by_key.get((fold_id, half_id, "4mem", "hold"))
            r = by_key.get((fold_id, half_id, "4mem", "reverse"))
            if h is None or r is None:
                crit1_pass = False
                crit1_cells.append({"fold": fold_id, "half": half_id,
                                    "ok": False, "reason": "missing_cell"})
                continue
            ok_net = h.summary["net_pnl"] > r.summary["net_pnl"]
            ok_dd = h.summary["max_drawdown_pct"] < r.summary["max_drawdown_pct"]
            ok = ok_net and ok_dd
            crit1_pass = crit1_pass and ok
            crit1_cells.append({
                "fold": fold_id, "half": half_id, "ok": ok,
                "hold_net": h.summary["net_pnl"],
                "reverse_net": r.summary["net_pnl"],
                "hold_maxDD_pct": h.summary["max_drawdown_pct"],
                "reverse_maxDD_pct": r.summary["max_drawdown_pct"],
            })

    # Criterion 2: 6-member hold not worse than reverse in NET for OOS in
    # both folds (looser test because the 6-member book bleeds anyway).
    crit2_cells: List[Dict[str, Any]] = []
    crit2_pass = True
    crit2_applicable = hold_reverse_ran and "6mem" in ran_rosters
    for fold_id in (ran_folds if crit2_applicable else []):
        h = by_key.get((fold_id, "oos", "6mem", "hold"))
        r = by_key.get((fold_id, "oos", "6mem", "reverse"))
        if h is None or r is None:
            crit2_pass = False
            crit2_cells.append({"fold": fold_id, "ok": False,
                                "reason": "missing_cell"})
            continue
        ok = h.summary["net_pnl"] >= r.summary["net_pnl"]
        crit2_pass = crit2_pass and ok
        crit2_cells.append({
            "fold": fold_id, "ok": ok,
            "hold_oos_net": h.summary["net_pnl"],
            "reverse_oos_net": r.summary["net_pnl"],
        })

    applicable = [p for a, p in ((crit1_applicable, crit1_pass),
                                 (crit2_applicable, crit2_pass)) if a]
    def _blk(ok: bool, applic: bool, cells_: List[Dict[str, Any]],
             need: str) -> Dict[str, Any]:
        return {
            # None, not False: an untested criterion has no verdict.
            "pass": (ok if applic else None),
            "applicable": applic,
            "not_applicable_reason": (None if applic else
                                      f"run did not include {need}"),
            "cells": cells_,
        }
    return {
        "scope": {"folds_in_run": ran_folds,
                  "rosters_in_run": sorted(ran_rosters),
                  "policies_in_run": sorted(ran_policies),
                  # Stated so a single-fold shard can never read as a full grid.
                  "is_full_fold_grid": sorted(ran_folds) == sorted(FOLDS)},
        "criterion_1_4member_hold_dominates_reverse":
            _blk(crit1_pass, crit1_applicable, crit1_cells,
                 "both `hold` and `reverse` on the 4mem roster"),
        "criterion_2_6member_hold_not_worse_than_reverse_oos":
            _blk(crit2_pass, crit2_applicable, crit2_cells,
                 "both `hold` and `reverse` on the 6mem roster"),
        # None => nothing applicable was run. Deliberately not True (which would
        # be a vacuous pass) and not False (which is the bug this replaces).
        "overall_pass": (all(applicable) if applicable else None),
    }


def _exit_code_for(verdict: Dict[str, Any]) -> int:
    """Three-state exit, matching the verdict: 0 = passed OR not applicable,
    2 = genuinely failed.

    Exiting 2 on "nothing to judge" would reproduce the collapsed state one
    level up, where a caller (a CI step, a workflow) sees only the code and
    cannot tell an untested criterion from a failed one. Split out of `main`
    so it is testable without running a walk-forward.
    """
    return 2 if verdict.get("overall_pass") is False else 0


def _markdown_summary(cells: List[Cell], verdict: Dict[str, Any],
                      policies: Optional[List[str]] = None) -> str:
    policies = list(policies or POLICIES)
    lines: List[str] = ["# Walk-forward — flip-policy conflict resolution\n"]
    lines.append(f"Generated {datetime.now(tz=timezone.utc).isoformat()}\n")
    rosters_present = [r for r in ROSTERS if any(c.roster == r for c in cells)]
    for roster_name in rosters_present:
        lines.append(f"\n## Roster = {roster_name}\n")
        # `conflicts` / `fired` are the override arm's DENOMINATOR. Without them
        # a confgap row that never fired is indistinguishable from one that
        # fired and tied — the two have opposite meanings for the decision.
        lines.append("| fold | half | policy | net | maxDD% | ret/DD | trades "
                     "| flips | confgap flips | conflicts | fired |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for fold_id in FOLDS:
            for half_id in ("train", "oos"):
                for pol in policies:
                    c = next((x for x in cells
                              if x.fold == fold_id and x.half == half_id
                              and x.roster == roster_name and x.policy == pol),
                             None)
                    if c is None:
                        lines.append(f"| {fold_id} | {half_id} | {pol} | n/a | n/a "
                                     f"| n/a | n/a | n/a | n/a | n/a | n/a |")
                        continue
                    s = c.summary
                    flips = s["by_exit_reason"].get("flip", 0)
                    confgap_flips = s["by_exit_reason"].get("flip_confgap", 0)
                    ov = (s.get("evidence", {}).get("flip_override", {}) or {})
                    lines.append(
                        f"| {fold_id} | {half_id} | {pol} | "
                        f"${s['net_pnl']:.0f} | {s['max_drawdown_pct']:.2f}% | "
                        f"{s.get('return_dd_ratio')} | {s['total_trades']} | {flips} | "
                        f"{confgap_flips} | {ov.get('conflicts_observed', '—')} | "
                        f"{ov.get('overrides_fired', '—')} |"
                    )
    lines.append("\n## Verdict\n")

    def _badge(block_or_pass) -> str:
        """PASS / FAIL / NOT TESTED -- three states, never two.

        A criterion the run could not evaluate renders as NOT TESTED, not FAIL.
        Rendering it as FAIL is what made every single-fold shard of the
        flip-override run print `Overall: FAIL` beside a result about a
        different arm entirely.
        """
        ok = (block_or_pass.get("pass") if isinstance(block_or_pass, dict)
              else block_or_pass)
        if ok is None:
            reason = (block_or_pass.get("not_applicable_reason")
                      if isinstance(block_or_pass, dict) else None)
            return f"NOT TESTED**{f' — {reason}' if reason else ''}" if reason \
                else "NOT TESTED"
        return "PASS" if ok else "FAIL"

    c1 = verdict["criterion_1_4member_hold_dominates_reverse"]
    c2 = verdict["criterion_2_6member_hold_not_worse_than_reverse_oos"]
    scope = verdict.get("scope", {})
    if scope and not scope.get("is_full_fold_grid", True):
        lines.append(
            f"> This run covered folds {scope.get('folds_in_run')} only, so any "
            f"criterion spanning the full grid is reported NOT TESTED rather "
            f"than failed.\n")
    lines.append(f"- Criterion 1 (4-member hold dominates reverse, all 4 cells): "
                 f"**{_badge(c1)}**")
    lines.append(f"- Criterion 2 (6-member hold not worse than reverse OOS, both folds): "
                 f"**{_badge(c2)}**")
    lines.append(f"- Overall: **{_badge(verdict['overall_pass'])}**")
    lines.append("\n_Both criteria test `hold` vs `reverse` (the May 2026 question). "
                 "They say NOTHING about the `hold_confgap*` override arms — read "
                 "those from the per-cell table above._")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data", required=True,
                   help="5m OHLCV parquet/CSV (resampled per strategy TF internally).")
    p.add_argument("--rosters", default="4mem,6mem",
                   help="Comma list of rosters to test (subset of {4mem,6mem}).")
    p.add_argument("--folds", default="A,B",
                   help="Comma list of folds to test (subset of {A,B}).")
    p.add_argument("--policies", default=",".join(POLICIES),
                   help=f"Comma list of arms to run (subset of "
                        f"{{{','.join(POLICIES)},{CONFGAP_ARM}}}). Default = the "
                        f"three legacy policies, so an unchanged invocation "
                        f"reproduces the May 2026 run exactly.")
    p.add_argument("--confgap-threshold", type=float, default=0.15,
                   help="Confidence gap for the '%s' arm (live value 0.15)."
                        % CONFGAP_ARM)
    p.add_argument("--confgap-min-age-hours", type=float, default=4.0,
                   help="Minimum held-position age for the '%s' arm (live value 4.0)."
                        % CONFGAP_ARM)
    p.add_argument("--initial-balance", type=float, default=10_000.0)
    p.add_argument("--risk-pct", type=float, default=0.3)
    p.add_argument("--daily-loss-pct", type=float, default=3.0)
    p.add_argument("--signal-ttl-bars", type=int, default=1)
    p.add_argument("--prebuild-cache", action="store_true",
                   help="Pre-generate signal caches for every (strategy, window). "
                        "Recommended for the first run.")
    p.add_argument("--refresh-cache", action="store_true",
                   help="Force regenerate caches (implies --prebuild-cache).")
    p.add_argument("--json-out", default=None,
                   help="Override the default walkforward_<UTC>.json output path.")
    p.add_argument("--md-out", default=None,
                   help="Override the default walkforward_<UTC>.md output path.")
    args = p.parse_args(argv[1:])

    rosters = [r for r in args.rosters.split(",") if r in ROSTERS]
    folds = {f: FOLDS[f] for f in args.folds.split(",") if f in FOLDS}
    requested = [p.strip() for p in args.policies.split(",") if p.strip()]
    policies = [p for p in requested if p in _ARM_SPECS]
    unknown = [p for p in requested if p not in _ARM_SPECS]
    if unknown:
        # Loud, not silent. A typo'd arm name that is quietly dropped produces a
        # run whose comparison table is simply missing a column — which reads as
        # "the arm was tested and tied" to anyone who did not count the rows.
        print(f"ERROR: unknown arm(s) {unknown}; valid = {sorted(_ARM_SPECS)}",
              file=sys.stderr)
        return 1
    if not rosters or not folds or not policies:
        print("ERROR: --rosters, --folds and --policies must each pick at least "
              "one valid value", file=sys.stderr)
        return 1

    try:
        base5m = _load_candles(args.data)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: data load failed: {exc}", file=sys.stderr)
        return 1
    print(f"loaded base 5m: rows={len(base5m):,} "
          f"({base5m['timestamp'].iloc[0]} -> {base5m['timestamp'].iloc[-1]})", flush=True)

    if args.prebuild_cache or args.refresh_cache:
        all_strats = sorted({s for r in rosters for s in ROSTERS[r]})
        _prebuild_cache(base5m, all_strats, folds, refresh=args.refresh_cache)

    cells: List[Cell] = []
    for fold_id, halves in folds.items():
        for half_id, (s, e) in halves.items():
            for roster_name in rosters:
                for policy in policies:
                    cells.append(_run_cell(
                        base5m, fold=fold_id, half=half_id,
                        roster_name=roster_name, policy=policy,
                        start=s, end=e,
                        balance=args.initial_balance,
                        risk_pct=args.risk_pct,
                        daily_loss_pct=args.daily_loss_pct,
                        ttl=args.signal_ttl_bars,
                        confgap_threshold=args.confgap_threshold,
                        confgap_min_age_hours=args.confgap_min_age_hours,
                    ))

    verdict = _evaluate_pass_criteria(cells)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = Path(args.json_out) if args.json_out else OUT_DIR / f"walkforward_{ts}.json"
    md_path = Path(args.md_out) if args.md_out else OUT_DIR / f"walkforward_{ts}.md"
    payload = {
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "rosters": rosters,
        "folds": {k: {h: list(v) for h, v in halves.items()} for k, halves in folds.items()},
        "params": {
            "initial_balance": args.initial_balance,
            "risk_pct": args.risk_pct,
            "daily_loss_pct": args.daily_loss_pct,
            "signal_ttl_bars": args.signal_ttl_bars,
            "policies": policies,
            "confgap_threshold": args.confgap_threshold,
            "confgap_min_age_hours": args.confgap_min_age_hours,
        },
        "cells": [
            {"fold": c.fold, "half": c.half, "roster": c.roster, "policy": c.policy,
             "start": c.start, "end": c.end, "summary": c.summary}
            for c in cells
        ],
        "verdict": verdict,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str))
    md = _markdown_summary(cells, verdict, policies)
    md_path.write_text(md)
    print(f"\nJSON -> {json_path}", file=sys.stderr)
    print(f"MD   -> {md_path}", file=sys.stderr)
    print("\n" + md)
    return _exit_code_for(verdict)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
