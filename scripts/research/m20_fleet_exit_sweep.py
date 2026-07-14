#!/usr/bin/env python3
"""M20 fleet-wide exit-lever sweep — every donchian/pullback-family leg,
CONFIG-EXACT, driven straight from config/strategies.yaml.

The exit-refinement skill's P2 stage industrialized: for each strategy leg it
resolves the leg's harness (donchian family -> scripts/research/backtest_trend.py,
pullback family -> scripts/backtest_pullback.py), its data file, and its OWN
YAML params (donchian/atr/trail/min_conf/long_only/adx_min/pullback_frac...),
then A/Bs the exit-lever cells (stale-stop, giveback-stop, trail +/-1) against
the config-exact base:

  1. IS/OOS split (--split, default 2025-07-01): a cell is a CANDIDATE only if
     it beats base on net_R AND maxDD in BOTH windows.
  2. Candidates go to a yearly walk-forward (2021..2026); PASS needs
     beats-or-ties base on net_R AND maxDD in >= 2/3 of usable folds.

Anything else is an honest negative. Output (one dir per run):
  runtime_logs/m20_fleet/<UTC-date>/results.jsonl   one row per leg x cell x window
  runtime_logs/m20_fleet/<UTC-date>/verdicts.json   per-leg matrix-aligned verdicts
  runtime_logs/m20_fleet/<UTC-date>/SUMMARY.md      human table

Data conventions (trainer): data/{SYMBOL}_{5m,15m,1h,1d}.csv — the finest
available file is used with --resample to the leg's timeframe. PROXY map for
futures without their own file (MGC/XAUUSD -> GC_F); a proxied leg's verdict is
tagged proxy:true. A leg with no data resolves to data_missing (the coverage
matrix's `blocked` reason) rather than being skipped silently.

Tier-1 research tooling — never writes config; Tier-3 ships remain
operator-gated. Run on the trainer (long: hours) detached:
  nohup .venv/bin/python3 scripts/research/m20_fleet_exit_sweep.py \
      --out runtime_logs/m20_fleet >/tmp/fleet_sweep.log 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]

# families with harness exit-lever support; everything else is reported
# no_harness_levers (vwap/ict_scalp/turtle_soup/fade — pending harness levers)
DONCHIAN_HARNESS = "scripts/research/backtest_trend.py"
PULLBACK_HARNESS = "scripts/backtest_pullback.py"
SQUEEZE_HARNESS = "scripts/backtest_squeeze.py"
FVG_HARNESS = "scripts/backtest_fvg_range.py"
FAMILY_HARNESS = {"donchian": DONCHIAN_HARNESS, "pullback": PULLBACK_HARNESS,
                  "squeeze": SQUEEZE_HARNESS, "fvg": FVG_HARNESS}

PROXY_DATA = {"MGC": "GC_F", "XAUUSD": "GC_F", "MES": "ES_F", "MHG": "HG_F"}
DATA_GRAIN = ["5m", "15m", "1h", "1d"]
TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}

FOLDS = [("2021", "2021-01-01", "2022-01-01"), ("2022", "2022-01-01", "2023-01-01"),
         ("2023", "2023-01-01", "2024-01-01"), ("2024", "2024-01-01", "2025-01-01"),
         ("2025", "2025-01-01", "2026-01-01"), ("2026", "2026-01-01", None)]


def classify(name: str) -> str | None:
    if "pullback" in name and "htf_pullback" not in name:
        return "pullback"
    if "htf_pullback" in name:
        return "pullback"
    if "squeeze" in name:
        return "squeeze"
    if "fvg" in name:
        return "fvg"
    if "donchian" in name or "_trend" in name:
        return "donchian"
    return None


def resolve_data(symbol: str, tf: str, data_dir: Path) -> tuple[str | None, bool, str | None]:
    """(path, proxy?, resample) — finest grain <= leg tf; None if nothing.

    Primary convention data/{SYMBOL}_{grain}.csv; fallback is a
    case-insensitive prefix glob (covers legacy names like
    btc_1h_multiyear.csv), matching on the symbol and its USDT-stripped
    base, picking the finest grain token found in the filename.
    """
    sym = PROXY_DATA.get(symbol, symbol)
    proxy = sym != symbol
    leg_min = TF_MINUTES.get(tf, 60)
    # native grain first (a 1d archive usually has YEARS more history than
    # the 1h file it would otherwise be resampled from), then finest
    native = data_dir / f"{sym}_{tf}.csv"
    if native.exists():
        return str(native), proxy, None
    for g in DATA_GRAIN:
        if TF_MINUTES[g] > leg_min:
            break
        p = data_dir / f"{sym}_{g}.csv"
        if p.exists():
            resample = tf if TF_MINUTES[g] < leg_min else None
            return str(p), proxy, resample
    prefixes = {sym.lower()}
    if sym.upper().endswith("USDT"):
        prefixes.add(sym.lower()[:-4])
    best: tuple[int, Path] | None = None
    for p in data_dir.glob("*.csv"):
        low = p.name.lower()
        if not any(low.startswith(pre + "_") or low == pre + ".csv"
                   for pre in prefixes):
            continue
        grain = next((g for g in DATA_GRAIN if f"_{g}" in low), None)
        if grain is None or TF_MINUTES[grain] > leg_min:
            continue
        if best is None or TF_MINUTES[grain] < best[0]:
            best = (TF_MINUTES[grain], p)
    if best is not None:
        resample = tf if best[0] < leg_min else None
        return str(best[1]), proxy, resample
    return None, proxy, None


def base_args(name: str, cfg: dict, fam: str, data: str, resample: str | None) -> list[str]:
    tf = str(cfg.get("timeframe") or "1h")
    sym = (cfg.get("symbols") or ["?"])[0]
    a = ["--data", data, "--symbol", sym, "--timeframe", tf]
    if resample:
        a += ["--resample", resample]
    def opt(flag, key):
        v = cfg.get(key)
        if v is not None:
            a.extend([flag, str(v)])
    def declared_levers():
        # Config-exact means DECLARED EXIT LEVERS too — a shipped stale/giveback
        # cell is part of the leg's baseline, so a new lever cell is measured
        # ON TOP of it (the structural combo A/B the one-lever-per-leg rule
        # wants). Donchian + pullback harnesses carry these flags.
        opt("--stale-exit-bars", "stale_exit_bars")
        opt("--stale-exit-below-r", "stale_exit_below_r")
        opt("--giveback-min-mfe-r", "giveback_min_mfe_r")
        opt("--giveback-r", "giveback_r")
        opt("--trail-decay-arm-r", "trail_decay_arm_r")
        opt("--trail-decay-stall-bars", "trail_decay_stall_bars")
        opt("--trail-decay-tight-mult", "trail_decay_tight_mult")
    if fam == "donchian":
        opt("--donchian", "donchian")
        opt("--atr-period", "atr_period")
        opt("--atr-stop-mult", "atr_stop_mult")
        opt("--trail-mult", "trail_mult")
        opt("--min-confidence", "min_confidence")
        # M21 E-2: a declared confirmation gate is part of the leg's
        # config-exact base, same as the declared exit levers.
        opt("--confirm-bars", "confirm_bars")
        opt("--skip-hours", "skip_hours")
        declared_levers()
        if cfg.get("long_only"):
            a.append("--long-only")
    elif fam == "squeeze":
        for flag, key in (("--bb-period", "bb_period"), ("--bb-std", "bb_std"),
                          ("--kc-mult", "kc_mult"), ("--atr-period", "atr_period"),
                          ("--atr-stop-mult", "atr_stop_mult"),
                          ("--trail-mult", "trail_mult"),
                          ("--timeout-bars", "timeout_bars"),
                          ("--min-confidence", "min_confidence")):
            opt(flag, key)
    elif fam == "fvg":
        for flag, key in (("--range-lookback", "range_lookback"),
                          ("--atr-period", "atr_period"),
                          ("--adx-period", "adx_period"), ("--adx-max", "adx_max"),
                          ("--min-width-pct", "min_width_pct"),
                          ("--max-width-pct", "max_width_pct"),
                          ("--touch-tol-pct", "touch_tol_pct"),
                          ("--min-touches", "min_touches"),
                          ("--third-frac", "third_frac"),
                          ("--fvg-search", "fvg_search"),
                          ("--min-fvg-size-bps", "min_fvg_size_bps"),
                          ("--atr-stop-buffer", "atr_stop_buffer"),
                          ("--exit-style", "exit_style"), ("--tp-r", "tp_r"),
                          ("--timeout-bars", "timeout_bars"),
                          ("--min-confidence", "min_confidence")):
            opt(flag, key)
    else:
        opt("--trend-lookback", "trend_len")
        opt("--pullback-lookback", "pullback_len")
        opt("--pullback-frac", "pullback_frac")
        opt("--atr-period", "atr_period")
        opt("--atr-stop-mult", "atr_stop_mult")
        opt("--trail-mult", "trail_mult")
        opt("--min-confidence", "min_confidence")
        opt("--adx-min", "adx_min")
        # M21 E-2: a declared confirmation gate is config-exact base here too.
        opt("--confirm-bars", "confirm_bars")
        opt("--skip-hours", "skip_hours")
        declared_levers()
    return a


def cells_for(cfg: dict, fam: str | None = None) -> list[tuple[str, str, list[str]]]:
    """(cell_tag, matrix_lever, extra_args). Config-exact base is implied."""
    out = [
        ("stale8_lt0R", "stale_stop", ["--stale-exit-bars", "8"]),
        ("stale12_lt0R", "stale_stop", ["--stale-exit-bars", "12"]),
        ("gb1R_afterMFE1R", "giveback_stop",
         ["--giveback-min-mfe-r", "1.0", "--giveback-r", "1.0"]),
        ("gb1R_afterMFE2R", "giveback_stop",
         ["--giveback-min-mfe-r", "2.0", "--giveback-r", "1.0"]),
    ]
    tm = cfg.get("trail_mult")
    if tm is not None:
        t = float(tm)
        for d in (-1.0, 1.0):
            nt = t + d
            if nt >= 1.5:
                out.append((f"trail{nt:g}", "trail_geometry",
                            ["--trail-mult", str(nt)]))
    # M20 P4.1 trail-decay cells (momentum-exhaustion design § 2): tighten the
    # trail once the move is R-armed and/or stalls. Only for families whose
    # harness carries the lever (trend/pullback); tight mult scales off the
    # leg's own base trail (half, floored at 1.5) so cells stay config-relative.
    if tm is not None and fam in ("donchian", "pullback"):
        tight = max(1.5, round(float(tm) / 2.0, 1))
        decay = [
            (f"decay_arm2R_t{tight:g}",
             ["--trail-decay-arm-r", "2.0"]),
            (f"decay_stall6_t{tight:g}",
             ["--trail-decay-stall-bars", "6"]),
            (f"decay_stall10_t{tight:g}",
             ["--trail-decay-stall-bars", "10"]),
            (f"decay_arm1.5R_stall6_t{tight:g}",
             ["--trail-decay-arm-r", "1.5", "--trail-decay-stall-bars", "6"]),
        ]
        for tag, extra in decay:
            out.append((tag, "trail_decay",
                        extra + ["--trail-decay-tight-mult", str(tight)]))
    return out


def winner_mfe_p80(harness: str, base: list[str], split: str) -> float | None:
    """P80 of the WINNER-trade MFE distribution over the IS window only
    (M20 P4.4 — the percentile arm is baked from train-window trades so the
    OOS verdict never sees test data; the by_year folds inside IS carry the
    one-scalar caveat, recorded in the cell tag). None when < 30 winners."""
    tmp = "/tmp/m20_p80_emit.jsonl"
    cmd = [sys.executable, str(REPO / harness), *base,
           "--emit-trades", tmp, "--json", "/tmp/m20_p80_metrics.json",
           "--end", split]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if p.returncode != 0:
            return None
        mfes = []
        for line in Path(tmp).read_text().splitlines():
            t = json.loads(line)
            if float(t.get("net_r") or 0) > 0 and t.get("mfe_r") is not None:
                mfes.append(float(t["mfe_r"]))
        if len(mfes) < 30:
            return None
        mfes.sort()
        return round(mfes[int(0.8 * (len(mfes) - 1))], 2)
    except Exception:  # noqa: BLE001 — advisory cell, never blocks the sweep
        return None


def run_cell(harness: str, args: list[str], start=None, end=None) -> dict:
    tmp = "/tmp/m20_fleet_cell.json"
    cmd = [sys.executable, str(REPO / harness), *args, "--json", tmp]
    if start:
        cmd += ["--start", start]
    if end:
        cmd += ["--end", end]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    if p.returncode != 0:
        return {"error": (p.stderr or p.stdout)[-250:]}
    try:
        return json.loads(Path(tmp).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"json: {exc}"}


def beats(cell: dict, base: dict) -> bool:
    """net_R AND maxDD both no worse (strict net_R improvement OR dd improvement)."""
    try:
        cn, bn = float(cell["net_total_r"]), float(base["net_total_r"])
        cd, bd = float(cell["max_drawdown_r"]), float(base["max_drawdown_r"])
    except (KeyError, TypeError, ValueError):
        return False
    return cn >= bn and cd <= bd and (cn > bn or cd < bd)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--split", default="2025-07-01")
    ap.add_argument("--out", default=str(REPO / "runtime_logs" / "m20_fleet"))
    ap.add_argument("--only", default=None,
                    help="CSV of leg names to restrict to (debug)")
    ap.add_argument("--levers", default=None,
                    help="CSV of matrix levers to restrict cells to (e.g. "
                         "trail_decay) — skips already-verdicted cells on a re-run")
    ap.add_argument("--list", action="store_true",
                    help="print the run plan (leg -> harness/data/cells) and exit")
    ap.add_argument("--p80-only", action="store_true",
                    help="P4.4 re-run: evaluate ONLY the dynamic p80 decay cell "
                         "per leg (fixed cells already verdicted)")
    a = ap.parse_args(argv[1:])

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml")
                                 .read_text()) or {}).get("strategies") or {}
    only = set(a.only.split(",")) if a.only else None
    levers = set(a.levers.split(",")) if a.levers else None
    data_dir = Path(a.data_dir)
    run_dir = Path(a.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plan, skipped = [], []
    for name, cfg in strategies.items():
        if not isinstance(cfg, dict) or (only and name not in only):
            continue
        fam = classify(name)
        if fam is None:
            skipped.append({"leg": name, "reason": "no_harness_levers"})
            continue
        sym = (cfg.get("symbols") or [None])[0]
        tf = str(cfg.get("timeframe") or "1h")
        data, proxy, resample = resolve_data(str(sym), tf, data_dir)
        if data is None:
            skipped.append({"leg": name, "reason": f"data_missing:{sym}"})
            continue
        harness = FAMILY_HARNESS[fam]
        plan.append({"leg": name, "family": fam, "symbol": sym, "tf": tf,
                     "harness": harness, "data": data, "proxy": proxy,
                     "resample": resample,
                     "base": base_args(name, cfg, fam, data, resample),
                     "cells": [c for c in cells_for(cfg, fam)
                               if not levers or c[1] in levers]})

    print(f"plan: {len(plan)} legs runnable, {len(skipped)} skipped")
    for s in skipped:
        print(f"  SKIP {s['leg']}: {s['reason']}")
    if a.list:
        for p in plan:
            print(f"  RUN  {p['leg']:28s} {p['harness'].split('/')[-1]:22s} "
                  f"{p['data']}{' [PROXY]' if p['proxy'] else ''} "
                  f"cells={[c[0] for c in p['cells']]}")
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    results = (run_dir / "results.jsonl").open("a", encoding="utf-8")

    def log_result(row: dict) -> None:
        results.write(json.dumps(row) + "\n")
        results.flush()

    verdicts: dict = {}
    for p in plan:
        leg = p["leg"]
        print(f"== {leg} ({p['symbol']} {p['tf']}) ==", flush=True)
        base_is = run_cell(p["harness"], p["base"], end=a.split)
        base_oos = run_cell(p["harness"], p["base"], start=a.split)
        log_result({"leg": leg, "cell": "base", "window": "IS", **base_is})
        log_result({"leg": leg, "cell": "base", "window": "OOS", **base_oos})
        if "error" in base_is or "error" in base_oos:
            verdicts[leg] = {"status": "harness_error",
                             "error": base_is.get("error") or base_oos.get("error")}
            continue
        leg_v = {"proxy": p["proxy"], "levers": {}}
        # M20 P4.4 — dynamic MFE-percentile decay cell: arm at the leg's own
        # P80 winner-MFE (IS window only) instead of a fixed R. Only where the
        # family has the decay lever and the fixed decay cells are in scope.
        decay_in_scope = any(lv == "trail_decay" for _, lv, _ in p["cells"])
        if a.p80_only:
            p["cells"] = []  # fixed cells already verdicted; p80 cell only
        if (p["family"] in ("donchian", "pullback") and decay_in_scope):
            tm_val = next((float(x[1]) for x in
                           zip(p["base"], p["base"][1:])
                           if x[0] == "--trail-mult"), None)
            p80 = winner_mfe_p80(p["harness"], p["base"], a.split)
            if p80 is not None and p80 > 0.5 and tm_val:
                tight = max(1.5, round(tm_val / 2.0, 1))
                p["cells"].append(
                    (f"decay_p80arm{p80:g}R_t{tight:g}", "trail_decay",
                     ["--trail-decay-arm-r", str(p80),
                      "--trail-decay-tight-mult", str(tight)]))
                print(f"   p80 winner-MFE arm = {p80}R", flush=True)
            else:
                print(f"   p80 cell skipped (p80={p80}, tm={tm_val})",
                      flush=True)
        for tag, lever, extra in p["cells"]:
            args = p["base"] + extra
            c_is = run_cell(p["harness"], args, end=a.split)
            c_oos = run_cell(p["harness"], args, start=a.split)
            log_result({"leg": leg, "cell": tag, "window": "IS", **c_is})
            log_result({"leg": leg, "cell": tag, "window": "OOS", **c_oos})
            if "error" in c_is or "error" in c_oos:
                leg_v["levers"].setdefault(lever, []).append(
                    {"cell": tag, "verdict": "error"})
                continue
            candidate = beats(c_is, base_is) and beats(c_oos, base_oos)
            entry = {"cell": tag, "is_oos_pass": candidate}
            if candidate:
                wins = usable = 0
                for fname, fs, fe in FOLDS:
                    fb = run_cell(p["harness"], p["base"], start=fs, end=fe)
                    fc = run_cell(p["harness"], args, start=fs, end=fe)
                    log_result({"leg": leg, "cell": f"{tag}@wf{fname}",
                                "window": "fold", "base": fb, "lever": fc})
                    if "error" in fb or "error" in fc:
                        continue
                    usable += 1
                    try:
                        ok = (float(fc["net_total_r"]) >= float(fb["net_total_r"])
                              and float(fc["max_drawdown_r"]) <= float(fb["max_drawdown_r"]))
                    except (KeyError, TypeError, ValueError):
                        ok = False
                    wins += 1 if ok else 0
                entry["walkforward"] = f"{wins}/{usable}"
                entry["verdict"] = ("PASS" if usable >= 4 and wins * 3 >= usable * 2
                                    else "wf_fail")
            else:
                entry["verdict"] = "is_oos_fail"
            leg_v["levers"].setdefault(lever, []).append(entry)
            print(f"   {tag:20s} -> {entry['verdict']}"
                  f"{' wf=' + entry.get('walkforward', '') if 'walkforward' in entry else ''}",
                  flush=True)
        verdicts[leg] = leg_v

    (run_dir / "verdicts.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "split": a.split, "skipped": skipped, "verdicts": verdicts}, indent=1))
    lines = ["# M20 fleet exit-lever sweep", ""]
    for leg, v in verdicts.items():
        if "levers" not in v:
            lines.append(f"- **{leg}**: {v.get('status')} ({v.get('error', '')[:80]})")
            continue
        passes = [e["cell"] for es in v["levers"].values() for e in es
                  if e.get("verdict") == "PASS"]
        lines.append(f"- **{leg}**{' [PROXY]' if v['proxy'] else ''}: "
                     + (f"PASS {passes}" if passes else "all honest negatives"))
    for s in skipped:
        lines.append(f"- **{s['leg']}**: SKIPPED — {s['reason']}")
    (run_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("done ->", run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
