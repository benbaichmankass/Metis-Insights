#!/usr/bin/env python3
# wiring: research/queue/RQ-20261007-020 + RQ-20261007-021 via .github/workflows/research-script-run.yml
"""EXIT-PARITY-RQ: re-measure a leg's Stage-0 evidence WITH and WITHOUT an exit
lever, on the same corpus the committed record used, net of the full cost
stack, and grade the delta against a rule registered before the run.

WHY (lane EXIT-PARITY-RQ, 2026-10-07; findings PI-20261006-KX6ZKFNA-0001 and
-0004). Two live legs declare an exit lever their execution path cannot fire:

  * trend_donchian_eth_prop declares ``stale_exit_bars: 12`` (a bar-CLOSE exit).
    ``src/prop/prop_trail.py`` lists it in ``_CLOSE_LEVERS`` and only ALERTS when
    it would fire; ``EXCHANGE_MANAGEMENT_CAPS["breakout"]`` is empty, so the
    live prop book trades the leg WITHOUT the exit its record (79 of 355 exits
    on the 2026-10-05-730d run) was measured with.
  * xrp_pullback_2h (bybit_2 real money) declares ``trail_decay_arm_r: 4.49``
    with ``tp_r: 3.0``: the resting TP closes every trade before the arm can be
    reached, so the decay is dead config.

THE ONE QUESTION this script answers: what does the leg's evidence look like
without the lever it cannot execute -- and, for a dead lever, with a
reachable one? It is an ABLATION over the committed-record argv, not a new
harness: every arm is built by ``regime_debt_matrix.build_harness_cmd`` (the
same function ``scripts/ops/build_strategy_evidence.py`` runs), so the
baseline arm reproduces the committed record byte-for-byte on the same
candles, and that reproduction is asserted as a POSITIVE CONTROL before any
delta is read (``--control``).

ARMS. ``--arm "LABEL=tok;tok;..."``; an empty spec (``"W="``) is the leg as
declared. Tokens:

  * ``key=value``   set a ``config/strategies.yaml`` key for this arm (value
                    parsed as YAML, so ``1.5`` is a float and ``none`` is None);
  * ``-key``        unset a key for this arm (the lever is REMOVED, which is
                    what "the live path cannot fire it" means in the harness);
  * ``--flag=value`` / ``--flag``  a raw harness flag appended to the argv (for
                    a lever the argv builder does not forward, e.g. the pullback
                    builder omits ``--trail-decay-*``, see
                    ``regime_debt_matrix._PB_LEVER_FLAG``).

FOLDS. Four equal CALENDAR spans of the measured window, a trade binned by
entry_time. Calendar, not equal-count: the arms have different trade sets, so
equal-count folds would put different dates in "fold 2" for each arm and the
per-fold comparison would compare nothing. Every arm is folded on the same
four date boundaries.

GRADES (``--grade``), each writing ``<out>/verdict.json`` in the
``script_run.py`` contract -- the rule's thresholds are the UNIT's, passed as
flags, never defaults chosen here:

  * ``parity`` -- baseline B (lever on, the record) vs candidate C (lever off,
    what the live path trades). ``dR = net_r(B) - net_r(C)``,
    ``dDD = maxDD(C) - maxDD(B)``, ``fold_wins`` = folds where B out-earns C.
      pass / ``stage0_fails_without``  if net_r(C) <= 0 (the executable book has
                                        no Stage-0 edge: a roster INPUT, never
                                        a roster act);
      pass / ``build_close_verb``      if (dR >= delta_r or dDD >= delta_r) and
                                        fold_wins >= fold_majority;
      fail / ``live_without_lever``    otherwise (the lever's measured value is
                                        inside the declared materiality bar, so
                                        declared == executable is the cheaper
                                        fix: strip it, Tier-3, not done here);
      indeterminate                    if the control fails or n(B) < n_floor.
  * ``sweep`` -- baseline B (lever removed) vs every other non-control arm.
    A candidate C passes iff ``net_r(C) - net_r(B) >= delta_r`` AND
    ``maxDD(C) <= maxDD(B)`` AND fold_wins(C over B) >= fold_majority.
      pass   if any candidate passes (best by delta named);
      fail   if none does (the removed lever's lines are dead: strip them);
      indeterminate if the control fails, a ``--same-as`` pair differs, or
                    n(B) < n_floor.
    ``--same-as S=N`` asserts two arms are IDENTICAL (n and net_r): the shipped
    unreachable-arm config must equal the no-decay arm, or the premise that the
    lever is dead is wrong and nothing below it may be read.

Honesty notes. ``maxDD`` and ``net_total_r`` are read from the harness's own
``--json`` summary, never re-derived. ``delta_r`` is a MATERIALITY bar in R
over the whole window (the unit says why that value), not a significance test;
the fold-majority clause is what stands between a one-period artifact and a
verdict. Nothing here edits config, and a ``pass`` is a PROPOSAL input.

Tier-1 research tooling: fetches public candles (Binance Vision), runs the
research harnesses, writes only under ``--out``. No live path, no config write.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "scripts" / "research"), str(REPO / "scripts"), str(REPO)]

VERDICTS = ("pass", "fail", "indeterminate")
GRADES = ("parity", "sweep")
N_FOLDS = 4


# ── arm specs ───────────────────────────────────────────────────────────────
def parse_arm(spec: str) -> tuple[str, dict[str, Any], list[str], list[str]]:
    """``LABEL=tok;tok`` -> (label, cfg_set, cfg_unset, extra_argv)."""
    import yaml
    if "=" not in spec:
        raise ValueError(f"arm spec {spec!r} must be LABEL=tok;tok;... (LABEL= for the leg as declared)")
    label, _, body = spec.partition("=")
    label = label.strip()
    if not label:
        raise ValueError(f"arm spec {spec!r} has an empty label")
    sets: dict[str, Any] = {}
    unsets: list[str] = []
    extra: list[str] = []
    for tok in (t.strip() for t in body.split(";")):
        if not tok:
            continue
        if tok.startswith("--"):
            flag, has_val, val = tok.partition("=")
            extra.append(flag)
            if has_val:
                extra.append(val)
        elif tok.startswith("-"):
            unsets.append(tok[1:])
        elif "=" in tok:
            k, _, v = tok.partition("=")
            sets[k.strip()] = yaml.safe_load(v)
        else:
            raise ValueError(f"arm {label!r}: token {tok!r} is not key=value, -key or --flag[=value]")
    return label, sets, unsets, extra


def arm_cfg(base: dict[str, Any], sets: dict[str, Any], unsets: list[str]) -> dict[str, Any]:
    cfg = dict(base)
    for k in unsets:
        cfg.pop(k, None)
    cfg.update(sets)
    return cfg


# ── harness runs ────────────────────────────────────────────────────────────
def _replace_opt(argv: list[str], flag: str, value: str) -> list[str]:
    out = list(argv)
    if flag in out:
        out[out.index(flag) + 1] = value
    else:
        out += [flag, value]
    return out


def build_arm_argv(leg: str, cfg: dict[str, Any], csv: str, resample: str, emit: str, jout: str,
                   extra: list[str], start: str | None, end: str | None) -> tuple[list[str], list[str]]:
    """The committed-record argv for ``cfg`` (via build_harness_cmd), plus the
    arm's raw flags and the pinned window. Returns (argv, omitted_levers)."""
    import regime_debt_matrix as rdm
    harness = rdm.classify(cfg)
    if harness not in ("trend", "pullback"):
        raise ValueError(f"{leg}: harness {harness!r} -- this ablation covers the trend and pullback "
                         "harnesses (the two with the exit levers in question)")
    argv, _faithful, omitted = rdm.build_harness_cmd(leg, cfg, harness, csv, resample, emit, jout)
    argv = list(argv)
    i = 0
    while i < len(extra):
        flag = extra[i]
        if i + 1 < len(extra) and not extra[i + 1].startswith("--"):
            argv = _replace_opt(argv, flag, extra[i + 1])
            i += 2
        else:
            if flag not in argv:
                argv.append(flag)
            i += 1
    if start:
        argv += ["--start", start]
    if end:
        argv += ["--end", end]
    return argv, list(omitted)


def fetch_candles(leg: str, cfg: dict[str, Any], days: int, csv: str) -> dict[str, Any]:
    """The SAME fetch the evidence builder makes (regime_debt_matrix._fetch_csv)."""
    import regime_debt_matrix as rdm
    sym = (cfg.get("symbols") or [None])[0]
    tf = cfg.get("timeframe")
    if not sym or not tf:
        raise ValueError(f"{leg}: no symbols[0]/timeframe in config")
    feed = rdm.resolve_feed(sym, tf)
    rdm._fetch_csv(feed, days, csv)
    return feed


def _load_trades(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def _ts(s: Any):
    import pandas as pd
    return pd.Timestamp(str(s)).tz_localize("UTC") if pd.Timestamp(str(s)).tzinfo is None else pd.Timestamp(str(s))


def fold_edges(csv: str, start: str | None, end: str | None) -> list[Any]:
    """N_FOLDS+1 calendar boundaries over the measured candle window."""
    import pandas as pd
    df = pd.read_csv(csv, usecols=["timestamp"])
    ts = df["timestamp"]
    ts = pd.to_datetime(ts, unit="ms", utc=True) if pd.api.types.is_numeric_dtype(ts) else pd.to_datetime(ts, utc=True)
    lo, hi = ts.min(), ts.max()
    if start:
        lo = max(lo, _ts(start))
    if end:
        hi = min(hi, _ts(end))
    if hi <= lo:
        raise ValueError(f"empty window: {lo} .. {hi}")
    step = (hi - lo) / N_FOLDS
    return [lo + step * i for i in range(N_FOLDS)] + [hi]


def fold_net_r(trades: list[dict[str, Any]], edges: list[Any]) -> list[dict[str, Any]]:
    out = []
    for i in range(N_FOLDS):
        lo, hi = edges[i], edges[i + 1]
        last = i == N_FOLDS - 1
        rs = [float(t.get("net_r") or 0.0) for t in trades
              if (lo <= _ts(t["entry_time"]) < hi) or (last and _ts(t["entry_time"]) == hi)]
        out.append({"fold": i + 1, "start": str(lo), "end": str(hi), "trades": len(rs),
                    "net_r": round(sum(rs), 4)})
    return out


def summarize_arm(jout: str, emit: str, edges: list[Any]) -> dict[str, Any]:
    with open(jout, encoding="utf-8") as fh:
        bt = json.load(fh)
    trades = _load_trades(emit)
    return {
        "n": int(bt.get("total_trades", len(trades))),
        "net_r": float(bt["net_total_r"]),
        "net_r_fee_only": bt.get("net_total_r_fee_only"),
        "max_drawdown_r": float(bt["max_drawdown_r"]),
        "by_outcome": bt.get("by_outcome"),
        "mean_bars_held": bt.get("mean_bars_held"),
        "net_r_per_capital_day": bt.get("net_r_per_capital_day"),
        "cost_stack": {"fee_bps_roundtrip": bt.get("fee_bps_roundtrip"),
                       "slippage_bps_roundtrip": bt.get("slippage_bps_roundtrip"),
                       "funding_bps_per_window": bt.get("funding_bps_per_window")},
        "params": bt.get("params"),
        "data_start": bt.get("data_start"), "data_end": bt.get("data_end"),
        "folds": fold_net_r(trades, edges),
    }


# ── grading (pure) ──────────────────────────────────────────────────────────
def check_control(arm: dict[str, Any], record_bt: dict[str, Any], tol_n: float, tol_r: float) -> dict[str, Any]:
    """Does the arm reproduce the committed record? (n within tol_n fraction, net_r within tol_r R)."""
    rn, rr = int(record_bt["total_trades"]), float(record_bt["net_total_r"])
    dn = abs(arm["n"] - rn) / max(rn, 1)
    dr = abs(arm["net_r"] - rr)
    ok = dn <= tol_n and dr <= tol_r
    return {"ok": ok, "record_n": rn, "record_net_r": rr, "arm_n": arm["n"], "arm_net_r": arm["net_r"],
            "n_frac_diff": round(dn, 4), "net_r_abs_diff": round(dr, 4), "tol_n": tol_n, "tol_r": tol_r}


def _fold_wins(a: dict[str, Any], b: dict[str, Any]) -> int:
    """Folds where arm a out-earns arm b (strictly)."""
    return sum(1 for fa, fb in zip(a["folds"], b["folds"]) if fa["net_r"] > fb["net_r"])


def grade_parity(arms: dict[str, dict[str, Any]], *, baseline: str, candidate: str, delta_r: float,
                 fold_majority: int, n_floor: int, control: dict[str, Any] | None) -> dict[str, Any]:
    b, c = arms[baseline], arms[candidate]
    d_r = round(b["net_r"] - c["net_r"], 4)
    d_dd = round(c["max_drawdown_r"] - b["max_drawdown_r"], 4)
    wins = _fold_wins(b, c)
    m = {"baseline": baseline, "candidate": candidate, "delta_net_r": d_r, "delta_max_drawdown_r": d_dd,
         "fold_wins_baseline": wins, "n_folds": N_FOLDS, "delta_r_bar": delta_r, "fold_majority": fold_majority,
         "n_floor": n_floor, "control": control}
    if control is not None and not control["ok"]:
        return {"verdict": "indeterminate", "label": "control_failed", **m,
                "why": "the baseline arm does not reproduce the committed record: the corpus differs, so no delta is read"}
    if b["n"] < n_floor:
        return {"verdict": "indeterminate", "label": "underpowered", **m,
                "why": f"baseline n={b['n']} < n_floor {n_floor}"}
    if c["net_r"] <= 0.0:
        return {"verdict": "pass", "label": "stage0_fails_without", **m,
                "why": f"without the lever the book reads net_r={c['net_r']} <= 0: the executable book has no "
                       "Stage-0 edge (a Gate-2 / roster INPUT for the manager, not a roster act)"}
    if (d_r >= delta_r or d_dd >= delta_r) and wins >= fold_majority:
        return {"verdict": "pass", "label": "build_close_verb", **m,
                "why": f"the lever is worth dR={d_r} / dDD={d_dd} (bar {delta_r}) and out-earns in {wins}/{N_FOLDS} "
                       "folds: cheaper to BUILD the close op than to live without it"}
    return {"verdict": "fail", "label": "live_without_lever", **m,
            "why": f"dR={d_r} / dDD={d_dd} against bar {delta_r} with {wins}/{N_FOLDS} fold wins: the lever's "
                   "measured value is inside the materiality bar, so declared == executable (strip it) is the cheaper fix"}


def grade_sweep(arms: dict[str, dict[str, Any]], *, baseline: str, candidates: list[str], delta_r: float,
                fold_majority: int, n_floor: int, control: dict[str, Any] | None,
                same_as: tuple[str, str] | None) -> dict[str, Any]:
    b = arms[baseline]
    m: dict[str, Any] = {"baseline": baseline, "candidates": {}, "delta_r_bar": delta_r,
                         "fold_majority": fold_majority, "n_floor": n_floor, "control": control}
    if same_as:
        x, y = same_as
        ident = arms[x]["n"] == arms[y]["n"] and abs(arms[x]["net_r"] - arms[y]["net_r"]) < 1e-9
        m["same_as"] = {"arms": [x, y], "identical": ident, "n": [arms[x]["n"], arms[y]["n"]],
                        "net_r": [arms[x]["net_r"], arms[y]["net_r"]]}
        if not ident:
            return {"verdict": "indeterminate", "label": "premise_failed", **m,
                    "why": f"arms {x} and {y} were declared identical (the lever cannot fire) and are not: "
                           "the dead-lever premise is wrong; nothing below it is read"}
    if control is not None and not control["ok"]:
        return {"verdict": "indeterminate", "label": "control_failed", **m,
                "why": "the control arm does not reproduce the committed record: the corpus differs"}
    if b["n"] < n_floor:
        return {"verdict": "indeterminate", "label": "underpowered", **m, "why": f"baseline n={b['n']} < n_floor {n_floor}"}
    best, best_d = None, None
    for cl in candidates:
        c = arms[cl]
        d_r = round(c["net_r"] - b["net_r"], 4)
        d_dd = round(c["max_drawdown_r"] - b["max_drawdown_r"], 4)
        wins = _fold_wins(c, b)
        ok = d_r >= delta_r and d_dd <= 0.0 and wins >= fold_majority
        m["candidates"][cl] = {"delta_net_r": d_r, "delta_max_drawdown_r": d_dd, "fold_wins": wins, "passes": ok}
        if ok and (best_d is None or d_r > best_d):
            best, best_d = cl, d_r
    if best is not None:
        return {"verdict": "pass", "label": "reachable_lever_beats_removed", "best": best, **m,
                "why": f"{best} beats the removed-lever baseline by {best_d} R with no worse drawdown and "
                       f">= {fold_majority}/{N_FOLDS} fold wins: a Tier-3 proposal to declare it (not done here)"}
    return {"verdict": "fail", "label": "no_reachable_lever_beats_removed", **m,
            "why": "no candidate clears the bar: the declared lever's lines are dead config and nothing reachable "
                   "earns its place -- strip them (Tier-3, not done here)"}


def verdict_record(grade: dict[str, Any], *, leg: str, arms: dict[str, dict[str, Any]], population: str,
                   n: int, extra: dict[str, Any]) -> dict[str, Any]:
    return {"verdict": grade["verdict"], "read_state": "measured", "population": population, "n": n,
            "measurement": {"leg": leg, "label": grade.get("label"), "grade": grade, "arms": arms, **extra},
            "note": f"{grade['verdict'].upper()} / {grade.get('label')}: {grade.get('why')}"}


# ── main ────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    if "--self-test" in (sys.argv[1:] if argv is None else argv):
        return _self_test()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--leg", required=True, help="config/strategies.yaml key")
    ap.add_argument("--arm", action="append", required=True, help='LABEL=tok;tok (see module docstring)')
    ap.add_argument("--days", type=int, default=740, help="trailing days to FETCH (the window is pinned by --start/--end)")
    ap.add_argument("--start", default=None, help="harness --start (ISO), pinned to the record's data_start")
    ap.add_argument("--end", default=None, help="harness --end (ISO datetime), pinned to the record's data_end")
    ap.add_argument("--data-csv", default=None, help="skip the fetch and use this candle CSV (tests)")
    ap.add_argument("--control", default=None, help="LABEL=<committed __bt.json> the arm must reproduce")
    ap.add_argument("--control-tol-n", type=float, default=0.02)
    ap.add_argument("--control-tol-r", type=float, default=0.5)
    ap.add_argument("--grade", choices=GRADES, required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", default=None, help="parity: the lever-off arm")
    ap.add_argument("--same-as", default=None, help="sweep: X=Y, two arms asserted identical")
    ap.add_argument("--delta-r", type=float, required=True, help="materiality bar in R over the window (the unit's)")
    ap.add_argument("--fold-majority", type=int, required=True)
    ap.add_argument("--n-floor", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    import yaml
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    legs = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text(encoding="utf-8"))
    legs = legs.get("strategies", legs)
    base = legs.get(a.leg)
    if not isinstance(base, dict):
        print(f"ERROR: leg {a.leg!r} not in config/strategies.yaml", file=sys.stderr)
        return 2
    specs = [parse_arm(s) for s in a.arm]
    labels = [s[0] for s in specs]
    if len(set(labels)) != len(labels):
        print(f"ERROR: duplicate arm labels {labels}", file=sys.stderr)
        return 2
    for need in [a.baseline] + ([a.candidate] if a.candidate else []):
        if need not in labels:
            print(f"ERROR: arm {need!r} is not declared ({labels})", file=sys.stderr)
            return 2
    if a.grade == "parity" and not a.candidate:
        print("ERROR: --grade parity needs --candidate", file=sys.stderr)
        return 2
    same_as = None
    if a.same_as:
        x, _, y = a.same_as.partition("=")
        if x not in labels or y not in labels:
            print(f"ERROR: --same-as names an undeclared arm ({a.same_as})", file=sys.stderr)
            return 2
        same_as = (x, y)
    control_label, control_bt = None, None
    if a.control:
        control_label, _, p = a.control.partition("=")
        if control_label not in labels:
            print(f"ERROR: --control names an undeclared arm ({control_label})", file=sys.stderr)
            return 2
        with open(REPO / p if not os.path.isabs(p) else p, encoding="utf-8") as fh:
            control_bt = json.load(fh)

    wd = Path(tempfile.mkdtemp(prefix="exit_lever_ablation_"))
    csv = a.data_csv or str(wd / f"{a.leg}__data.csv")
    feed: dict[str, Any] = {"resample": base.get("timeframe"), "source": "explicit --data-csv"}
    if not a.data_csv:
        feed = fetch_candles(a.leg, base, a.days, csv)
    edges = fold_edges(csv, a.start, a.end)
    arms_dir = out / "arms"
    arms_dir.mkdir(exist_ok=True)
    arms: dict[str, dict[str, Any]] = {}
    argvs: dict[str, Any] = {}
    for label, sets, unsets, extra in specs:
        cfg = arm_cfg(base, sets, unsets)
        emit, jout = str(arms_dir / f"{label}__trades.jsonl"), str(arms_dir / f"{label}__bt.json")
        argv_i, omitted = build_arm_argv(a.leg, cfg, csv, str(feed["resample"]), emit, jout, extra, a.start, a.end)
        shown = [("<data>" if t == csv else t) for t in argv_i]
        argvs[label] = {"argv": shown, "cfg_set": sets, "cfg_unset": unsets, "extra_flags": extra,
                        "omitted_levers_per_builder": omitted}
        print(f"[{label}] {' '.join(shown)}", flush=True)
        r = subprocess.run(argv_i, cwd=str(REPO), capture_output=True, text=True, check=False)
        (arms_dir / f"{label}__stderr.log").write_text(r.stderr[-20000:], encoding="utf-8")
        if r.returncode != 0:
            print(f"ERROR: arm {label} harness exit {r.returncode}: {r.stderr[-800:]}", file=sys.stderr)
            return 1
        arms[label] = summarize_arm(jout, emit, edges)
        arms[label]["argv"] = shown
    control = check_control(arms[control_label], control_bt, a.control_tol_n, a.control_tol_r) if control_bt else None
    if a.grade == "parity":
        g = grade_parity(arms, baseline=a.baseline, candidate=a.candidate, delta_r=a.delta_r,
                         fold_majority=a.fold_majority, n_floor=a.n_floor, control=control)
    else:
        cands = [l for l in labels if l != a.baseline and (not same_as or l not in same_as)]
        g = grade_sweep(arms, baseline=a.baseline, candidates=cands, delta_r=a.delta_r,
                        fold_majority=a.fold_majority, n_floor=a.n_floor, control=control, same_as=same_as)
    window = {"start": a.start, "end": a.end, "fetch_days": a.days, "feed": feed,
              "fold_edges": [str(e) for e in edges]}
    pop = (f"{a.leg}: {arms[a.baseline]['n']} trades in the baseline arm {a.baseline!r} on "
           f"{feed.get('ticker', base.get('symbols', ['?'])[0])} {base.get('timeframe')} candles "
           f"{a.start or arms[a.baseline].get('data_start')} .. {a.end or arms[a.baseline].get('data_end')}, "
           f"net of fee+slippage+funding (the harness's venue-aware stack); arms {labels}")
    rec = verdict_record(g, leg=a.leg, arms=arms, population=pop, n=arms[a.baseline]["n"],
                         extra={"window": window, "argv": argvs})
    (out / "verdict.json").write_text(json.dumps(rec, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({"leg": a.leg, "grade": g, "arms": arms, "window": window},
                                                 indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": g["verdict"], "label": g.get("label"), "why": g.get("why")}, indent=1))
    return 0


# ── self-test: planted controls for every grading branch, no network ─────────
def _arm(n: int, net_r: float, dd: float, folds: list[float]) -> dict[str, Any]:
    return {"n": n, "net_r": net_r, "max_drawdown_r": dd,
            "folds": [{"fold": i + 1, "net_r": f} for i, f in enumerate(folds)]}


def _self_test() -> int:
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= bool(cond)
        print(f"  self-test {'ok  ' if cond else 'FAIL'}: {label}")

    lab, sets, unsets, extra = parse_arm("X=-stale_exit_bars;-stale_exit_below_r;--timeout-bars=100000;tp_r=3.0")
    check("arm spec parses sets/unsets/extra", lab == "X" and unsets == ["stale_exit_bars", "stale_exit_below_r"]
          and extra == ["--timeout-bars", "100000"] and sets == {"tp_r": 3.0})
    check("empty spec is the leg as declared", parse_arm("W=")[1:] == ({}, [], []))
    c = arm_cfg({"a": 1, "stale_exit_bars": 12}, {"b": 2}, ["stale_exit_bars"])
    check("arm_cfg removes and sets", c == {"a": 1, "b": 2})

    good = {"total_trades": 355, "net_total_r": 13.0223}
    ctl_ok = check_control(_arm(356, 13.1, 13.0, [1, 1, 1, 1]), good, 0.02, 0.5)
    ctl_bad = check_control(_arm(300, 13.1, 13.0, [1, 1, 1, 1]), good, 0.02, 0.5)
    check("control passes within tolerance", ctl_ok["ok"] and not ctl_bad["ok"])

    W = _arm(355, 13.0, 13.0, [2.0, -3.0, 20.0, -6.0])
    # lever off: 3R worse pooled, worse in 3 of 4 folds -> build
    X = _arm(340, 10.0, 13.5, [1.0, -3.5, 18.0, -5.5])
    g = grade_parity({"W": W, "X": X}, baseline="W", candidate="X", delta_r=2.0, fold_majority=3, n_floor=88, control=ctl_ok)
    check("parity: material + fold-consistent -> pass/build_close_verb", g["verdict"] == "pass" and g["label"] == "build_close_verb")
    X2 = _arm(340, 12.5, 13.0, [2.5, -3.0, 19.0, -6.0])   # 0.5R worse, 1 fold win -> live without
    g = grade_parity({"W": W, "X2": X2}, baseline="W", candidate="X2", delta_r=2.0, fold_majority=3, n_floor=88, control=ctl_ok)
    check("parity: inside the bar -> fail/live_without_lever", g["verdict"] == "fail" and g["label"] == "live_without_lever")
    X3 = _arm(340, 10.0, 13.5, [2.5, -3.5, 25.0, -14.0])  # 3R worse pooled but only 2 fold wins -> not build
    g = grade_parity({"W": W, "X3": X3}, baseline="W", candidate="X3", delta_r=2.0, fold_majority=3, n_floor=88, control=ctl_ok)
    check("parity: material but one-period -> fail (fold majority holds the line)", g["verdict"] == "fail")
    X4 = _arm(340, -1.0, 20.0, [0, -5, 8, -4])
    g = grade_parity({"W": W, "X4": X4}, baseline="W", candidate="X4", delta_r=2.0, fold_majority=3, n_floor=88, control=ctl_ok)
    check("parity: stage-0 fails without -> pass/stage0_fails_without", g["verdict"] == "pass" and g["label"] == "stage0_fails_without")
    g = grade_parity({"W": W, "X": X}, baseline="W", candidate="X", delta_r=2.0, fold_majority=3, n_floor=88, control=ctl_bad)
    check("parity: failed control -> indeterminate, nothing read", g["verdict"] == "indeterminate" and g["label"] == "control_failed")
    g = grade_parity({"W": _arm(50, 13.0, 13.0, [1, 1, 1, 1]), "X": X}, baseline="W", candidate="X", delta_r=2.0,
                     fold_majority=3, n_floor=88, control=None)
    check("parity: n below floor -> indeterminate/underpowered", g["verdict"] == "indeterminate" and g["label"] == "underpowered")

    S = _arm(122, 14.6686, 6.0, [5, 2, 8, -0.3])
    N = _arm(122, 14.6686, 6.0, [5, 2, 8, -0.3])
    R1 = _arm(122, 16.2, 5.5, [5.5, 2.4, 8.5, -0.2])      # +1.53R, no worse DD, 4 fold wins -> passes
    R2 = _arm(122, 15.0, 6.5, [5.2, 2.1, 8.1, -0.4])      # DD worse -> no
    g = grade_sweep({"S": S, "N": N, "R1": R1, "R2": R2}, baseline="N", candidates=["R1", "R2"], delta_r=1.0,
                    fold_majority=3, n_floor=39, control=None, same_as=("S", "N"))
    check("sweep: a reachable arm beats the removed lever -> pass, best named",
          g["verdict"] == "pass" and g["best"] == "R1" and not g["candidates"]["R2"]["passes"])
    g = grade_sweep({"S": S, "N": N, "R2": R2}, baseline="N", candidates=["R2"], delta_r=1.0,
                    fold_majority=3, n_floor=39, control=None, same_as=("S", "N"))
    check("sweep: nothing clears -> fail/no_reachable_lever_beats_removed", g["verdict"] == "fail")
    S2 = _arm(121, 14.0, 6.0, [5, 2, 8, -1])
    g = grade_sweep({"S": S2, "N": N, "R1": R1}, baseline="N", candidates=["R1"], delta_r=1.0,
                    fold_majority=3, n_floor=39, control=None, same_as=("S", "N"))
    check("sweep: 'identical' arms differ -> indeterminate/premise_failed", g["verdict"] == "indeterminate" and g["label"] == "premise_failed")

    # calendar folds: four equal spans, a trade binned by entry_time, the last edge inclusive
    import pandas as pd
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.csv"
        ts = pd.date_range("2025-01-01", "2025-12-31 23:00", freq="h", tz="UTC")
        pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S%z"), "open": 1.0, "high": 1.0, "low": 1.0,
                      "close": 1.0, "volume": 1.0}).to_csv(p, index=False)
        edges = fold_edges(str(p), "2025-01-01", "2025-12-31 23:00")
        tr = [{"entry_time": "2025-02-01 00:00:00+00:00", "net_r": 1.0},
              {"entry_time": "2025-07-15 00:00:00+00:00", "net_r": -2.0},
              {"entry_time": "2025-12-31 23:00:00+00:00", "net_r": 0.5}]
        f = fold_net_r(tr, edges)
        check("folds: 4 calendar spans, trades binned by entry_time incl. the last edge",
              len(f) == 4 and f[0]["net_r"] == 1.0 and f[2]["net_r"] == -2.0 and f[3]["net_r"] == 0.5 and f[1]["trades"] == 0)

    # the argv builder reproduces the evidence builder's argv and applies the arm's edits
    import yaml
    legs = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text(encoding="utf-8"))["strategies"]
    eth = legs.get("trend_donchian_eth_prop")
    if isinstance(eth, dict):
        base_argv, _ = build_arm_argv("trend_donchian_eth_prop", eth, "<d>", "1h", "<e>", "<j>", [], None, None)
        x_cfg = arm_cfg(eth, {}, ["stale_exit_bars", "stale_exit_below_r"])
        x_argv, _ = build_arm_argv("trend_donchian_eth_prop", x_cfg, "<d>", "1h", "<e>", "<j>",
                                   ["--timeout-bars", "100000"], "2024-10-05", "2026-10-03 23:59")
        check("trend arm argv: declared leg carries --stale-exit-bars, the ablated arm does not and pins the window",
              "--stale-exit-bars" in base_argv and "--stale-exit-bars" not in x_argv
              and x_argv[x_argv.index("--timeout-bars") + 1] == "100000" and "--start" in x_argv and "--end" in x_argv
              and "--trail-decay-arm-r" in x_argv)
    xrp = legs.get("xrp_pullback_2h")
    if isinstance(xrp, dict):
        s_argv, _ = build_arm_argv("xrp_pullback_2h", xrp, "<d>", "2h", "<e>", "<j>",
                                   ["--trail-decay-arm-r", "4.49", "--trail-decay-tight-mult", "2.5"], None, None)
        n_argv, _ = build_arm_argv("xrp_pullback_2h", xrp, "<d>", "2h", "<e>", "<j>", [], None, None)
        check("pullback arm argv: the builder omits --trail-decay-* (why the record is approximate); the raw flag adds it",
              "--trail-decay-arm-r" not in n_argv and s_argv[s_argv.index("--trail-decay-arm-r") + 1] == "4.49"
              and "--tp-r" in n_argv and n_argv[n_argv.index("--tp-r") + 1] == "3.0")
    print("self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
