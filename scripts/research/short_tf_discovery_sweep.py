#!/usr/bin/env python3
"""Short-timeframe strategy DISCOVERY sweep -- the instrument for RQ-20260930-504.

Question it answers (general research mandate, operator 2026-09-30): across a FIXED,
pre-declared grid of (family x symbol x timeframe) cells drawn from the repo's
cost-complete harnesses, run at each family's live-config parameters with NO tuning, does any
cell show a net-of-full-cost edge that survives a family-wise multiple-testing correction on data
it was not screened on?

Design, fixed before any run (the unit's decision_rule is the authority; this file implements it):

  K        the number of cells that are RUNNABLE (harness script exists, candles resolve for
           that exact (symbol, timeframe) and are not a PROXY for another instrument). Printed
           BEFORE any cell is graded, and unrunnable cells are listed with the reason -- never
           dropped silently.
  STAGE A  screen, 2021-01-01 .. 2024-09-30. A cell SURVIVES if n_A >= 88 AND mean net R > 0 AND
           one-sided Student-t p_A < 0.05.  S = number of survivors.
  STAGE B  confirm, 2024-10-01 .. latest complete UTC day, run ONCE, only for survivors, with the
           identical parameters. A cell is CONFIRMED if n_B >= 88 AND mean net R_B > 0 AND
           p_B < 0.05 / S AND at least 3 of 4 equal time-folds have positive net R.
  VERDICT  NOT_APPLICABLE if fewer than half of the K cells produced a Stage A result.
           PASS if >= 1 CONFIRMED.  NULL if S == 0.
           FAIL if S >= 1, none confirmed, and EVERY survivor was adequately powered (n_B >= 88).
           INDETERMINATE if S >= 1, none confirmed, and at least one survivor had n_B < 88 (an
           underpowered rejection is not evidence of absence -- refined from the unit text BEFORE any
           run so a run of thin cells cannot read as a FAIL).
           FAILED CELLS: every cell that raised is recorded on its stage_a/stage_b block, counted in
           n_failed_a / n_failed_b / failed_cells, and makes clean=false. A NULL or FAIL is a claim about
           all K cells, so with any Stage A failure it is downgraded to INDETERMINATE; an all-failed sweep
           can therefore never read as "no edge found". A PASS stands (a confirmed cell is positive
           evidence) but is reported with clean=false.
  REPORTED (never gating): same-UTC-day share, median hold, trades a month, HyroTrader qualifying
           days a month (strict +-1% reading, from |net R| x stop%), and the same cell's mean net R at
           the HyroTrader taker-taker fee (11 bps round trip) beside the 7.5 bps verdict arm.

Costs: each harness resolves the venue-aware fee + slippage + funding stack in its own main(); this
script passes NO cost flags, so the verdict arm is the harness default (7.5 bps fee round trip).
The 11 bps arm is derived from each emitted row as delta_R = 3.5e-4 * entry / risk (entry price
stands in for the entry/exit mid; a report-only approximation, labelled in the output).

CANDLES ON THE RUNNER (manager review of #14586, 2026-09-30): the runner is ubuntu-latest with the candle CSVs
gitignored, so `--fetch` first pulls every crypto (symbol, timeframe) in the grid from Binance-vision futures/um
(keyless, not geoblocked; Bybit 403s GitHub runners) through the ONE existing fetcher
(scripts/ops/fetch_backtest_candles.py) into data/{SYMBOL}_{grain}.csv, the name the resolver expects. A cell
whose fetch fails is not runnable and is listed with the reason. If fewer than HALF of the declared cells are
runnable the sweep is NOT_APPLICABLE (candles missing), so a mostly-unfetched grid can never read as a null.
The Binance USD-M series is a PROXY for Bybit linear (prices track within a few bps); recorded in the output.

Live-config parameters are NOT proof of out-of-sample parameters: ict_scalp and pullback cells run at the params
of the live leg for that symbol/timeframe, and those were probably chosen on history that overlaps Stage B
(2024-10 onward). Such a cell is flagged stage_b_not_fully_oos; a PASS carried ONLY by flagged cells is reported as
pass_caveated, never plain pass. The other families run at harness defaults whose provenance is not established
either; that is stated, not asserted clean.

Universe: WAVES. Wave 1 is the five perps with committed short-timeframe evidence. Adding symbols is a
NEW wave and therefore a NEW, separately registered grid (K changes, so the correction changes) --
append to WAVES and register a unit; never edit wave 1 after a run.

Nothing here touches config/, the live order path or any strategy parameter.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import importlib.util
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# ---- the registered grid (RQ-20260930-504; wave 1) --------------------------------------
WAVES: Dict[int, Tuple[str, ...]] = {
    1: ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "AVAXUSDT"),
}
TIMEFRAMES: Tuple[str, ...] = ("1m", "5m", "15m", "30m")
#: The timeframe sets that are REGISTERED DESIGNS, not overrides: RQ-20260930-504 (5m,15m,30m) and its 1m
#: split RQ-20260930-505 (1m). Anything else is a smoke run. Comparing against the full default made the
#: registered 504 command look like an override (run 36691408944 was mislabelled SMOKE).
REGISTERED_TIMEFRAME_SETS = (frozenset({"5m", "15m", "30m"}), frozenset({"1m"}), frozenset(TIMEFRAMES))

#: family -> harness script, whether it takes --strategy-name (live-config lookup), extra flags.
#: fvg_range / chop_scalp run the FAR target because the live fvg_range unit targets the opposite
#: range boundary (docs/research/fvg-parity-2026-09-18.md: the harness default `mid` is not live
#: parity); chop_scalp follows the same geometry.
FAMILIES: Dict[str, Dict[str, Any]] = {
    "ict_scalp": {"script": "scripts/backtest_ict_scalp.py", "strategy_name": True, "extra": []},
    "fvg_range": {"script": "scripts/backtest_fvg_range.py", "strategy_name": False,
                  "extra": ["--exit-style", "far"]},
    "chop_scalp": {"script": "scripts/backtest_chop_scalp.py", "strategy_name": False,
                   "extra": ["--exit-style", "far"]},
    "pullback": {"script": "scripts/backtest_pullback.py", "strategy_name": True, "extra": []},
    "squeeze": {"script": "scripts/backtest_squeeze.py", "strategy_name": False, "extra": []},
    "fade": {"script": "scripts/backtest_fade.py", "strategy_name": False, "extra": []},
}

STAGE_A = ("2021-01-01", "2024-09-30")
STAGE_B_START = "2024-10-01"
N_FLOOR = 88
ALPHA_A = 0.05
ALPHA_B_FAMILY = 0.05
FOLDS = 4
FOLDS_NEEDED = 3
FEE_VERDICT_BPS = 7.5
FEE_REPORT_BPS = 11.0
HYRO_STRICT_MOVE = 0.01      # |price move| >= 1% of entry (reading A of the +-1% qualifying-day test)


# ---- statistics ------------------------------------------------------------------------
def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (Numerical Recipes 6.4)."""
    tiny, eps = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """P(T > t) for Student's t with `df` degrees of freedom."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    half = 0.5 * _betainc(df / 2.0, 0.5, x)
    return half if t > 0 else 1.0 - half


def mean_test(xs: Sequence[float]) -> Dict[str, Any]:
    """Mean, sd, t and the ONE-SIDED p for H1: mean > 0."""
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": (xs[0] if n else None), "sd": None, "t": None, "p": None}
    m = sum(xs) / n
    sd = statistics.stdev(xs)
    if sd == 0.0:
        return {"n": n, "mean": m, "sd": 0.0, "t": None, "p": (0.0 if m > 0 else 1.0)}
    t = m / (sd / math.sqrt(n))
    return {"n": n, "mean": m, "sd": sd, "t": t, "p": t_sf(t, n - 1)}


# ---- rows ------------------------------------------------------------------------------
def _ts(x: Any) -> Optional[datetime]:
    if x is None:
        return None
    try:
        d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _tf_minutes(tf: str) -> Optional[float]:
    try:
        n, unit = int(tf[:-1]), tf[-1]
        return n * {"m": 1, "h": 60, "d": 1440}[unit]
    except (ValueError, KeyError):
        return None


@dataclass
class Row:
    net_r: float
    entry_time: datetime
    exit_time: Optional[datetime] = None
    hold_hours: Optional[float] = None
    entry: Optional[float] = None
    sl: Optional[float] = None


def parse_rows(lines: Sequence[str], tf: str) -> List[Row]:
    out: List[Row] = []
    tfm = _tf_minutes(tf)
    for ln in lines:
        if not ln.strip():
            continue
        d = json.loads(ln)
        et = _ts(d.get("entry_time"))
        if et is None or d.get("net_r") is None:
            continue
        xt = _ts(d.get("exit_time") or (d.get("meta") or {}).get("exit_time"))
        hold = None
        if xt is not None:
            hold = (xt - et).total_seconds() / 3600.0
        else:
            hb = d.get("hold_bars", (d.get("meta") or {}).get("bars_held"))
            if hb is not None and tfm:
                hold = float(hb) * tfm / 60.0
        out.append(Row(float(d["net_r"]), et, xt, hold,
                       _f(d.get("entry")), _f(d.get("sl"))))
    return out


def _f(x: Any) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def in_window(rows: Sequence[Row], start: str, end: str) -> List[Row]:
    lo = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    hi = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1)
    return [r for r in rows if lo <= r.entry_time < hi]


def fold_sums(rows: Sequence[Row], start: str, end: str, k: int = FOLDS) -> List[float]:
    lo = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    hi = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1)
    step = (hi - lo) / k
    sums = [0.0] * k
    for r in rows:
        i = min(int((r.entry_time - lo) / step), k - 1)
        if 0 <= i < k:
            sums[i] += r.net_r
    return sums


def net_r_at_fee(rows: Sequence[Row], from_bps: float, to_bps: float) -> Optional[float]:
    """Mean net R re-priced from `from_bps` to `to_bps` round-trip fee. REPORT-ONLY approximation:
    delta_R = (to-from)/1e4 * entry / risk, entry standing in for the entry/exit mid."""
    xs = []
    for r in rows:
        if r.entry is None or r.sl is None or r.entry == r.sl:
            return None
        xs.append(r.net_r - (to_bps - from_bps) / 1e4 * r.entry / abs(r.entry - r.sl))
    return (sum(xs) / len(xs)) if xs else None


def venue_fit(rows: Sequence[Row], start: str, end: str) -> Dict[str, Any]:
    """REPORTED attributes; none of these gates a verdict."""
    months = max((datetime.fromisoformat(end) - datetime.fromisoformat(start)).days / 30.4375, 1e-9)
    n = len(rows)
    with_exit = [r for r in rows if r.exit_time is not None]
    same = (sum(r.entry_time.date() == r.exit_time.date() for r in with_exit) / len(with_exit)
            if with_exit else None)
    holds = [r.hold_hours for r in rows if r.hold_hours is not None]
    qual_days = None
    if rows and all(r.entry and r.sl and r.entry != r.sl for r in rows) and with_exit:
        days = set()
        for r in with_exit:
            if r.entry_time.date() != r.exit_time.date():
                continue
            if abs(r.net_r) * abs(r.entry - r.sl) / r.entry >= HYRO_STRICT_MOVE:
                days.add(r.entry_time.date())
        qual_days = len(days) / months
    return {
        "trades_per_month": round(n / months, 2),
        "same_utc_day_share": (round(same, 3) if same is not None else None),
        "median_hold_hours": (round(statistics.median(holds), 2) if holds else None),
        "hyro_qualifying_days_per_month_strict": (round(qual_days, 2) if qual_days is not None else None),
    }


# ---- cells & running -------------------------------------------------------------------
@dataclass
class Cell:
    family: str
    symbol: str
    timeframe: str
    runnable: bool = True
    reason: str = ""
    strategy_name: Optional[str] = None
    stage_a: Dict[str, Any] = field(default_factory=dict)
    stage_b: Dict[str, Any] = field(default_factory=dict)
    survived: bool = False
    confirmed: bool = False
    #: True when the cell runs at a LIVE leg's parameters (chosen on history that probably overlaps Stage B).
    stage_b_not_fully_oos: bool = False

    @property
    def key(self) -> str:
        return f"{self.family}|{self.symbol}|{self.timeframe}"


def _load_data_source():
    spec = importlib.util.spec_from_file_location(
        "_backtest_data_source", str(REPO / "scripts" / "ops" / "backtest_data_source.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: Bybit-style interval code the fetcher speaks, per timeframe label.
INTERVAL_CODE = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60"}
FETCH_START = "2020-12-01"      # a month of warm-up before Stage A opens on 2021-01-01


def fetch_candles(symbols: Sequence[str], timeframes: Sequence[str], *, run=subprocess.run,
                  data_dir: Optional[Path] = None, timeout_s: int = 3600,
                  log: Callable[[str], None] = print) -> Dict[str, Dict[str, Any]]:
    """Fetch each (symbol, timeframe) from Binance-vision into data/{SYMBOL}_{tf}.csv. Never raises:
    a failure is returned per key ({'ok': False, 'detail': ...}) and that cell becomes unrunnable."""
    data_dir = data_dir or (REPO / "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        for tf in timeframes:
            key = f"{sym}|{tf}"
            code = INTERVAL_CODE.get(tf)
            dest = data_dir / f"{sym}_{tf}.csv"
            if code is None:
                out[key] = {"ok": False, "detail": f"no interval code for {tf}"}
                continue
            cmd = [sys.executable, str(REPO / "scripts/ops/fetch_backtest_candles.py"), "--symbol", sym,
                   "--source", "binance_vision", "--interval", code, "--start-date", FETCH_START,
                   "--output", str(dest)]
            try:
                p = run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=str(REPO))
                ok = p.returncode == 0 and dest.exists()
                detail = "" if ok else (p.stderr or p.stdout or "no output")[-300:]
            except subprocess.TimeoutExpired:
                ok, detail = False, f"fetch timed out after {timeout_s}s"
            out[key] = {"ok": bool(ok), "detail": detail, "file": str(dest.relative_to(REPO))
                        if dest.is_relative_to(REPO) else str(dest)}
            log(f"  fetch {key}: {'ok' if ok else 'FAILED ' + detail[:120]}")
    return out


def default_data_check(symbol: str, tf: str) -> Tuple[bool, str]:
    """Runnable only if candles resolve for EXACTLY this (symbol, timeframe) and are not a proxy."""
    ds = _load_data_source()
    res = ds.resolve_or_refuse(symbol, tf, None, legacy_default=None, env={})
    if not res.ok:
        return False, f"no candle file for {symbol} {tf} ({res.state})"
    if getattr(res, "proxy", False):
        return False, f"{symbol} {tf} resolves to a PROXY file (would test a different instrument)"
    return True, res.state


def live_leg_for(family: str, symbol: str, tf: str,
                 strategies: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The live config leg of `family` on (symbol, tf), so the cell runs at LIVE parameters."""
    if strategies is None:
        import yaml
        strategies = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())["strategies"]
    for name, cfg in strategies.items():
        if not isinstance(cfg, dict) or str(cfg.get("timeframe")) != tf:
            continue
        if symbol not in (cfg.get("symbols") or []):
            continue
        if family == "ict_scalp" and name.startswith("ict_scalp"):
            return name
        if family == "pullback" and "pullback" in name:
            return name
    return None


def build_cells(wave: int, families: Sequence[str], timeframes: Sequence[str],
                data_check: Callable[[str, str], Tuple[bool, str]],
                strategies: Optional[Dict[str, Any]] = None) -> List[Cell]:
    cells: List[Cell] = []
    for fam in families:
        script = REPO / FAMILIES[fam]["script"]
        for sym in WAVES[wave]:
            for tf in timeframes:
                c = Cell(fam, sym, tf)
                if not script.exists():
                    c.runnable, c.reason = False, f"harness script missing: {FAMILIES[fam]['script']}"
                else:
                    ok, why = data_check(sym, tf)
                    c.runnable, c.reason = ok, ("" if ok else why)
                if c.runnable and FAMILIES[fam]["strategy_name"]:
                    c.strategy_name = live_leg_for(fam, sym, tf, strategies)
                    c.stage_b_not_fully_oos = c.strategy_name is not None
                cells.append(c)
    return cells


Runner = Callable[[Cell, str, str], List[str]]


def subprocess_runner(timeout_s: int) -> Runner:
    """Run one harness cell over [start, end]; return the emitted JSONL lines. Raises on failure."""
    def run(cell: Cell, start: str, end: str) -> List[str]:
        fam = FAMILIES[cell.family]
        with tempfile.TemporaryDirectory() as td:
            emit = os.path.join(td, "trades.jsonl")
            cmd = [sys.executable, str(REPO / fam["script"]), "--symbol", cell.symbol,
                   "--timeframe", cell.timeframe, "--start", start, "--end", end,
                   "--emit-trades", emit, *fam["extra"]]
            if cell.strategy_name:
                cmd += ["--strategy-name", cell.strategy_name]
            env = {k: v for k, v in os.environ.items() if k != "BACKTEST_DATA_PATH"}
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                               cwd=str(REPO), env=env)
            if p.returncode != 0:
                raise RuntimeError(f"rc={p.returncode}: {(p.stderr or p.stdout)[-400:]}")
            if not os.path.exists(emit):
                return []
            return Path(emit).read_text().splitlines()
    return run


def _run_cells(cells: Sequence[Cell], runner: Runner, start: str, end: str,
               jobs: int) -> Dict[str, Any]:
    results: Dict[str, Any] = {}

    def one(c: Cell):
        try:
            return c.key, ("ok", parse_rows(runner(c, start, end), c.timeframe))
        except Exception as exc:  # allow-silent: NOT swallowed -- the failure is returned as ("producer_failed", detail), stored on the cell as stage_a/stage_b status, counted in the output as n_failed_a / n_failed_b / failed_cells, and any failure makes clean=false and downgrades NULL/FAIL to INDETERMINATE  # noqa: BLE001
            return c.key, ("producer_failed", str(exc)[:300])

    with cf.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        for k, v in ex.map(one, list(cells)):
            results[k] = v
    return results


# ---- the sweep -------------------------------------------------------------------------
def latest_complete_day(today: Optional[date] = None) -> str:
    d = (today or datetime.now(timezone.utc).date()) - timedelta(days=1)
    return d.isoformat()


def sweep(*, wave: int, families: Sequence[str], timeframes: Sequence[str], runner: Runner,
          data_check: Callable[[str, str], Tuple[bool, str]], jobs: int = 2,
          stage_a: Tuple[str, str] = STAGE_A, stage_b_start: str = STAGE_B_START,
          stage_b_end: Optional[str] = None, strategies: Optional[Dict[str, Any]] = None,
          smoke: bool = False, fetch: Optional[Callable[[], Dict[str, Dict[str, Any]]]] = None,
          log: Callable[[str], None] = print) -> Dict[str, Any]:
    stage_b_end = stage_b_end or latest_complete_day()
    fetched = fetch() if fetch is not None else None
    if fetched is not None:
        _dc = data_check

        def data_check(sym: str, tf: str, _dc=_dc):      # noqa: F811 -- a fetch failure makes the cell unrunnable
            f = fetched.get(f"{sym}|{tf}")
            if f is not None and not f["ok"]:
                return False, f"candle fetch failed: {f['detail']}"
            return _dc(sym, tf)
    cells = build_cells(wave, families, timeframes, data_check, strategies)
    runnable = [c for c in cells if c.runnable]
    K = len(runnable)
    log(f"GRID wave={wave}: {len(cells)} cells declared, K={K} runnable")
    for c in cells:
        if not c.runnable:
            log(f"  not runnable: {c.key}  -- {c.reason}")
    out: Dict[str, Any] = {
        "wave": wave, "K": K, "declared": len(cells), "n_floor": N_FLOOR,
        "windows": {"stage_a": list(stage_a), "stage_b": [stage_b_start, stage_b_end]},
        "verdict_fee_bps_roundtrip": FEE_VERDICT_BPS, "report_fee_bps_roundtrip": FEE_REPORT_BPS,
        "report_arm_note": "11 bps arm is re-priced per row as 3.5e-4*entry/risk (approximation, "
                           "report-only, never gates)",
        "cost_policy": "harness default, venue-aware fee+slippage+funding resolved in each harness main()",
        "read_state": "smoke" if smoke else "graded",
        "candle_source": ("binance_vision futures/um via fetch_backtest_candles.py (PROXY for Bybit linear)"
                          if fetched is not None else "pre-staged data/ files (trainer)"),
        "fetch": fetched,
        "report_only_note": "chop_scalp rows carry no entry/sl/exit_time, so its 11 bps arm and venue-fit "
                            "fields read null; they are report-only and gate nothing",
    }

    # STAGE A
    a_res = _run_cells(runnable, runner, *stage_a, jobs)
    ok_a = 0
    for c in runnable:
        status, payload = a_res[c.key]
        if status != "ok":
            c.stage_a = {"status": "producer_failed", "detail": payload}
            continue
        ok_a += 1
        rows = in_window(payload, *stage_a)
        t = mean_test([r.net_r for r in rows])
        c.stage_a = {"status": "ok", "n": t["n"], "mean_net_r": _r(t["mean"]), "p": _r(t["p"], 6)}
        c.survived = bool(t["n"] >= N_FLOOR and t["mean"] is not None and t["mean"] > 0
                          and t["p"] is not None and t["p"] < ALPHA_A)
    S = sum(c.survived for c in runnable)
    failed_a = [c.key for c in runnable if c.stage_a.get("status") == "producer_failed"]
    out.update(S=S, stage_a_ok=ok_a, n_failed_a=len(failed_a), failed_cells=list(failed_a))
    log(f"STAGE A: {ok_a}/{K} cells produced a result; S={S} survivor(s) at p<{ALPHA_A}, n>={N_FLOOR}")

    if K == 0 or K < len(cells) / 2 or ok_a < K / 2:
        out.update(verdict="not_applicable", clean=False,
                   read_state="producer_failed" if not smoke else "smoke",
                   population=f"{ok_a} of {K} runnable cells produced a Stage A result; "
                              f"{K} of {len(cells)} declared cells were runnable (candles missing or fetch failed "
                              f"for the rest)",
                   cells=[_cell_dict(c) for c in cells])
        return out
    if S == 0:
        # A NULL is a statement about ALL K cells. If any cell failed to run it is not one.
        out.update(verdict=("null" if not failed_a else "indeterminate"), clean=(not failed_a),
                   population=f"K={K} cells, S=0 survivors of the Stage A screen"
                              + (f"; {len(failed_a)} cell(s) failed to run, so this is NOT a null over K"
                                 if failed_a else ""),
                   cells=[_cell_dict(c) for c in cells])
        return out

    # STAGE B (survivors only, once)
    alpha_b = ALPHA_B_FAMILY / S
    out["alpha_b"] = alpha_b
    survivors = [c for c in runnable if c.survived]
    b_res = _run_cells(survivors, runner, stage_b_start, stage_b_end, jobs)
    underpowered = 0
    for c in survivors:
        status, payload = b_res[c.key]
        if status != "ok":
            c.stage_b = {"status": "producer_failed", "detail": payload}
            underpowered += 1        # no answer is not a rejection
            continue
        rows = in_window(payload, stage_b_start, stage_b_end)
        t = mean_test([r.net_r for r in rows])
        folds = fold_sums(rows, stage_b_start, stage_b_end)
        pos = sum(1 for f in folds if f > 0)
        powered = t["n"] >= N_FLOOR
        if not powered:
            underpowered += 1
        c.confirmed = bool(powered and t["mean"] is not None and t["mean"] > 0
                           and t["p"] is not None and t["p"] < alpha_b and pos >= FOLDS_NEEDED)
        c.stage_b = {
            "status": "ok", "n": t["n"], "mean_net_r": _r(t["mean"]), "p": _r(t["p"], 6),
            "alpha_b": alpha_b, "folds_net_r": [round(f, 3) for f in folds], "folds_positive": pos,
            "indeterminate_underpowered": (not powered),
            "mean_net_r_at_report_fee": _r(net_r_at_fee(rows, FEE_VERDICT_BPS, FEE_REPORT_BPS)),
            "venue_fit_reported_not_gating": venue_fit(rows, stage_b_start, stage_b_end),
        }
    confirmed = [c for c in survivors if c.confirmed]
    failed_b = [c.key for c in survivors if c.stage_b.get("status") == "producer_failed"]
    out.update(n_failed_b=len(failed_b), failed_cells=list(failed_a) + failed_b,
               clean=not (failed_a or failed_b))
    if confirmed:
        # a confirmed cell is a positive finding even if others failed to run, but a PASS carried only by
        # cells at LIVE-config parameters is not fully out-of-sample (see the module docstring)
        verdict = "pass" if any(not c.stage_b_not_fully_oos for c in confirmed) else "pass_caveated"
    elif underpowered or failed_a:
        # ...but "nothing confirmed" is only a FAIL if every one of the K cells actually ran.
        verdict = "indeterminate"
    else:
        verdict = "fail"
    out.update(verdict=verdict, confirmed=[c.key for c in confirmed],
               population=f"K={K} cells, S={S} survivors, {len(confirmed)} confirmed, "
                          f"{underpowered} survivor(s) underpowered or unrun in Stage B",
               cells=[_cell_dict(c) for c in cells])
    return out


def _r(x: Any, nd: int = 4) -> Any:
    return None if x is None else round(float(x), nd)


def _cell_dict(c: Cell) -> Dict[str, Any]:
    return {"family": c.family, "symbol": c.symbol, "timeframe": c.timeframe,
            "runnable": c.runnable, "reason": c.reason, "strategy_name": c.strategy_name,
            "survived_stage_a": c.survived, "confirmed": c.confirmed,
            "stage_b_not_fully_oos": c.stage_b_not_fully_oos,
            "stage_a": c.stage_a, "stage_b": c.stage_b}


#: The sweep's own verdict names -> the research-result contract's CLOSED verdict set
#: (pass / fail / no_action_warranted / indeterminate / not_applicable). A verdict.json outside it, or a
#: read_state outside (measured / no_data / producer_failed / not_attempted), makes the collector land the whole
#: run as producer_failed -- which is exactly what happened to RQ-20260930-501 on 2026-09-30.
_CONTRACT_VERDICT = {"pass": "pass", "pass_caveated": "pass", "fail": "fail", "null": "no_action_warranted",
                     "indeterminate": "indeterminate", "not_applicable": "not_applicable"}


def to_result_verdict(res: Dict[str, Any]) -> Dict[str, Any]:
    """Project the sweep result onto the collector's verdict.json contract. The sweep's own detail rides along
    under `sweep`; nothing is dropped, nothing is renamed inside it."""
    internal = str(res.get("verdict"))
    verdict = _CONTRACT_VERDICT[internal]
    smoke = res.get("read_state") == "smoke"
    if verdict == "not_applicable":
        return {"verdict": verdict, "read_state": "producer_failed", "n": None,
                "population": str(res.get("population") or "producer failed"),
                "note": "producer_failed: " + str(res.get("population") or ""), "sweep": res}
    notes = []
    if internal == "pass_caveated":
        notes.append("CAVEATED PASS: every confirmed cell runs at a live leg's parameters, so Stage B is not fully "
                     "out-of-sample for it")
    if internal == "null":
        notes.append("clean NULL: no cell survived the Stage A screen (a null over all K cells; detects only "
                     "large edges, ~0.2 sd)")
    if res.get("clean") is False:
        notes.append(f"UNCLEAN: {len(res.get('failed_cells') or [])} cell(s) failed to run")
    if smoke:
        notes.append("SMOKE RUN: grid or windows were overridden; not the registered design")
    return {"verdict": verdict, "read_state": "measured", "n": int(res["K"]),
            "population": ("SMOKE RUN: " if smoke else "") + str(res.get("population") or ""),
            "note": "; ".join(notes), "sweep": res}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="directory for verdict.json")
    ap.add_argument("--wave", type=int, default=1)
    ap.add_argument("--families", default=",".join(FAMILIES))
    ap.add_argument("--timeframes", default=",".join(TIMEFRAMES))
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--cell-timeout-s", type=int, default=3600)
    ap.add_argument("--fetch", action="store_true",
                    help="first fetch the crypto candles for the grid from Binance-vision (GitHub runner has none)")
    ap.add_argument("--list-grid", action="store_true",
                    help="print K and the unrunnable cells, run nothing")
    ap.add_argument("--stage-a-start", default=STAGE_A[0])
    ap.add_argument("--stage-a-end", default=STAGE_A[1])
    ap.add_argument("--stage-b-start", default=STAGE_B_START)
    ap.add_argument("--stage-b-end", default=None)
    a = ap.parse_args(argv)
    if a.wave not in WAVES:
        print(f"unknown wave {a.wave}; registered waves: {sorted(WAVES)}", file=sys.stderr)
        return 2
    fams = [f for f in a.families.split(",") if f]
    unknown = [f for f in fams if f not in FAMILIES]
    if unknown:
        print(f"unknown families {unknown}", file=sys.stderr)
        return 2
    tfs = [t for t in a.timeframes.split(",") if t]
    overridden = (a.families != ",".join(FAMILIES) or frozenset(tfs) not in REGISTERED_TIMEFRAME_SETS
                  or (a.stage_a_start, a.stage_a_end) != STAGE_A or a.stage_b_start != STAGE_B_START
                  or a.stage_b_end is not None or a.wave != 1)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.list_grid:
        cells = build_cells(a.wave, fams, tfs, default_data_check)
        print(f"K={sum(c.runnable for c in cells)} runnable of {len(cells)} declared")
        for c in cells:
            print(f"  {'OK ' if c.runnable else 'NO '} {c.key}  {c.reason}")
        return 0
    fetch = (lambda: fetch_candles(WAVES[a.wave], tfs)) if a.fetch else None
    res = sweep(wave=a.wave, families=fams, timeframes=tfs, fetch=fetch,
                runner=subprocess_runner(a.cell_timeout_s), data_check=default_data_check,
                jobs=a.jobs, stage_a=(a.stage_a_start, a.stage_a_end),
                stage_b_start=a.stage_b_start, stage_b_end=a.stage_b_end, smoke=overridden)
    contract = to_result_verdict(res)
    (out / "verdict.json").write_text(json.dumps(contract, indent=1, default=str) + "\n")
    print(json.dumps({k: contract.get(k) for k in ("verdict", "read_state", "population", "n", "note")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
