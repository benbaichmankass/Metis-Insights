#!/usr/bin/env python3
# wiring: research/queue unit RQ-20261014-da7a-01 (run.command) via research-script-run.yml -- the token-free runner
"""Intrabar label fidelity: how often is a first-passage label (SL before TP?) UNDECIDABLE at the leg's bar size,
and how often does the conservative SL-first convention get it WRONG once 1-minute bars are read?

WHY THIS EXISTS (Programme 06 Data infrastructure, Q1; row DATA-INFRA-PROGRAMME; operator 2026-10-14: "make sure
we're ingesting enough ... labeling, and learning from that data"). The in-trade exit model (row IN-TRADE-EXIT-MODEL)
learns P(SL before TP | state at bar t). Every harness and every re-walk resolves a bar that touches BOTH the stop and
the target as SL-first (conservative). On a 4h or 2h bar that is a label guess, not a measurement. Whether the repo
needs a persisted 1m (or finer) archive FOR LABELS is therefore a number, and this script measures it. It decides the
priority of the 1m archive build (row DATA-KLINE-ARCHIVE) -- it does not build it.

THE CONSTRUCTION (fixed bracket; say so wherever a number from here is quoted)
  * Population: newest committed ledger per leg (comms/strategy_evidence/runs/*/<leg>__trades.jsonl), resamples
    (__B<k>/__M) excluded, USDT symbols on Binance USD-M futures only, legs whose name ends in a bar size (_5m.._1d).
    Every skipped leg is listed with its reason (the denominator is in the record).
  * R = |entry - sl| (the ledger's INITIAL stop). Bracket: SL at -1 R, TP at +K R for K in --tp-r.
  * Walk: leg-size bars rebuilt from Binance Vision 1m klines (open-time labelled), starting --entry-offset-bars
    (default 1) leg bars after the ledger's entry_time, up to --horizon-bars bars. The ledgers' entry_time /
    exit_time are OPEN-time labels of the entry / exit bar (entry fills at that bar's close). MEASURED 2026-10-14 on
    trend_donchian_xrp_4h (1460 d ledger, 44 trades with a pre-exit window): the initial SL is breached strictly before
    the exit bar on 0/44 trades at offset 1 and on 27/44 at offset 0 -- offset 0 would walk pre-entry price.
    First leg-size bar that touches either level decides. Touches both -> AMBIGUOUS at bar resolution; the
    1m bars inside it are walked: first 1m bar touching one level decides; a 1m bar touching both stays ambiguous.
  * MISLABEL = ambiguous at bar resolution AND the 1m walk says TP first (SL-first convention was wrong).
  * Positive control (RULE ONE), per leg, at BOTH offsets 0 and 1 (both reported): the share of trades whose 1m
    path breaches the initial SL strictly BEFORE the ledger's exit bar (a trade that did that would have stopped
    out earlier, so the alignment or the data is wrong). The leg enters the measurement only if that share is
    <= --control-max-breach at the chosen offset on >= 10 trades; else `control_failed`. The 1m-vs-ledger mfe_r
    match share is reported beside it (mfe_r is a lagged lower bound, so it is informative, not gating).
  * Data: data.binance.vision monthly 1m zips (daily for the current month), cached under --cache. Binance
    Terms of Use apply; nothing is redistributed -- the cache is runner scratch.

VERDICT (registered on the unit): per family with n >= --min-n resolved trades, at any K:
  NEEDED      if mislabel share >= --needed-frac (the SL-first convention is a material label error -> the 1m
              archive is a prerequisite for first-passage labels on that family)
  NOT_NEEDED  if mislabel share < --not-needed-frac at every K on every eligible family
  MIXED       otherwise. INDETERMINATE if no family is eligible or the data could not be fetched.

Writes <out>/verdict.json ({verdict, read_state, population, n, measurement, note}). Tier-1 research code: reads
committed files and a public archive; no VM, no live path, no order. --self-test plants a synthetic path with a known
ambiguous bar and asserts the walk resolves it.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.research.ledger_metalabel import (  # noqa: E402
    DEFAULT_LEDGERS,
    family_of,
    newest_ledgers,
)

TF_RE = re.compile(r"_(\d+)(m|h|d)$")
TF_MIN = {"m": 1, "h": 60, "d": 1440}
VISION = "https://data.binance.vision/data/futures/um"


def leg_minutes(leg: str) -> int | None:
    m = TF_RE.search(leg)
    return int(m.group(1)) * TF_MIN[m.group(2)] if m else None


# ── data ──────────────────────────────────────────────────────────────────────
def _fetch(url: str) -> bytes | None:
    import requests
    try:
        r = requests.get(url, timeout=60)
    except requests.RequestException as exc:  # a missing month is ledgered by the caller (missing_months), never silent
        print(f"intrabar_label_fidelity: fetch failed {url}: {exc}", file=sys.stderr)
        return None
    return r.content if r.status_code == 200 else None


def _parse_zip(blob: bytes) -> np.ndarray:
    """-> array (n, 4): open_time_ms, high, low, close. Handles files with and without a header row."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        txt = z.read(z.namelist()[0]).decode()
    rows = []
    for ln in txt.splitlines():
        p = ln.split(",")
        if not p or not p[0].strip().isdigit():
            continue
        rows.append((float(p[0]), float(p[2]), float(p[3]), float(p[4])))
    return np.array(rows, dtype=float) if rows else np.empty((0, 4))


class Minutes:
    """1m klines for one symbol, loaded month by month on demand."""

    def __init__(self, symbol: str, cache: Path, fetch=_fetch):
        self.symbol, self.cache, self.fetch = symbol, cache, fetch
        self.months: dict[str, np.ndarray] = {}
        self.missing: list[str] = []

    def _month(self, ym: str) -> np.ndarray:
        if ym in self.months:
            return self.months[ym]
        f = self.cache / self.symbol / f"{ym}.npy"
        if f.exists():
            arr = np.load(f)
        else:
            blob = self.fetch(f"{VISION}/monthly/klines/{self.symbol}/1m/{self.symbol}-1m-{ym}.zip")
            if blob is not None:
                arr = _parse_zip(blob)
            else:  # current month: daily files
                y, mth = map(int, ym.split("-"))
                parts = []
                for d in range(1, 32):
                    try:
                        day = datetime(y, mth, d, tzinfo=timezone.utc)
                    except ValueError:
                        break
                    b = self.fetch(f"{VISION}/daily/klines/{self.symbol}/1m/{self.symbol}-1m-{day:%Y-%m-%d}.zip")
                    if b is not None:
                        parts.append(_parse_zip(b))
                arr = np.vstack(parts) if parts else np.empty((0, 4))
            if len(arr):
                f.parent.mkdir(parents=True, exist_ok=True)
                np.save(f, arr)
        if not len(arr):
            self.missing.append(ym)
        self.months[ym] = arr
        return arr

    def window(self, t0_ms: float, t1_ms: float) -> np.ndarray:
        a = datetime.fromtimestamp(t0_ms / 1000, tz=timezone.utc)
        b = datetime.fromtimestamp(t1_ms / 1000, tz=timezone.utc)
        yms, y, m = [], a.year, a.month
        while (y, m) <= (b.year, b.month):
            yms.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        arr = np.vstack([self._month(ym) for ym in yms]) if yms else np.empty((0, 4))
        return arr[(arr[:, 0] >= t0_ms) & (arr[:, 0] < t1_ms)] if len(arr) else arr


# ── walk ──────────────────────────────────────────────────────────────────────
def walk(m1: np.ndarray, t0_ms: float, bar_ms: float, horizon: int, up: float, dn: float) -> tuple[str, str]:
    """m1 rows in favourable-R units already: columns (open_time_ms, hi_r, lo_r). up = +K, dn = -1.
    Returns (bar_label, m1_label) in {tp, sl, timeout, ambiguous}; bar_label 'ambiguous' is resolved by m1_label."""
    for k in range(horizon):
        lo_t, hi_t = t0_ms + k * bar_ms, t0_ms + (k + 1) * bar_ms
        blk = m1[(m1[:, 0] >= lo_t) & (m1[:, 0] < hi_t)]
        if not len(blk):
            continue
        hit_tp, hit_sl = blk[:, 1].max() >= up, blk[:, 2].min() <= dn
        if hit_tp and hit_sl:
            for row in blk:
                t, s = row[1] >= up, row[2] <= dn
                if t and s:
                    return "ambiguous", "ambiguous"
                if t:
                    return "ambiguous", "tp"
                if s:
                    return "ambiguous", "sl"
            return "ambiguous", "ambiguous"
        if hit_tp:
            return "tp", "tp"
        if hit_sl:
            return "sl", "sl"
    return "timeout", "timeout"


def to_r(arr: np.ndarray, entry: float, R: float, long: bool) -> np.ndarray:
    if long:
        return np.column_stack([arr[:, 0], (arr[:, 1] - entry) / R, (arr[:, 2] - entry) / R])
    return np.column_stack([arr[:, 0], (entry - arr[:, 2]) / R, (entry - arr[:, 1]) / R])


def _ms(s: str) -> float:
    import pandas as pd
    return pd.Timestamp(s).tz_convert("UTC").value / 1e6 if pd.Timestamp(s).tzinfo else pd.Timestamp(s, tz="UTC").value / 1e6


def measure(a, fetch=_fetch) -> dict[str, Any]:
    ks = [float(x) for x in a.tp_r.split(",")]
    skipped: dict[str, str] = {}
    legs_out: dict[str, Any] = {}
    feeds: dict[str, Minutes] = {}
    for leg, path in newest_ledgers(a.ledgers).items():
        if a.legs and not re.search(a.legs, leg):
            continue
        tf = leg_minutes(leg)
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        sym = str(rows[0].get("symbol", "")) if rows else ""
        if tf is None:
            skipped[leg] = "no bar size in leg name"
            continue
        if not sym.endswith("USDT"):
            skipped[leg] = f"symbol {sym or '?'} not a Binance USD-M perp"
            continue
        feed = feeds.setdefault(sym, Minutes(sym, Path(a.cache), fetch))
        bar_ms, per_k = tf * 60_000.0, {k: {"n": 0, "amb": 0, "mis": 0, "amb_1m": 0, "tp": 0, "sl": 0, "timeout": 0} for k in ks}
        ctl = {0: [0, 0], 1: [0, 0]}  # offset -> [n, breaches before exit bar]
        mfe_n = mfe_ok = bad_rows = 0
        for r in rows[: a.max_trades_per_leg]:
            try:
                entry, sl = float(r["entry"]), float(r["sl"])
                R = abs(entry - sl)
                long = str(r.get("direction", "long")).lower() == "long"
                t0, t_exit = _ms(r["entry_time"]), _ms(r["exit_time"])
            except (KeyError, TypeError, ValueError) as exc:
                bad_rows += 1
                print(f"intrabar_label_fidelity: {leg} unreadable row skipped ({exc})", file=sys.stderr)
                continue
            if R <= 0:
                continue
            for off in (0, 1):
                w0, w1 = t0 + off * bar_ms, t_exit + (off - 1) * bar_ms
                if w1 > w0:
                    pre = feed.window(w0, w1)
                    if len(pre):
                        ctl[off][0] += 1
                        ctl[off][1] += int(to_r(pre, entry, R, long)[:, 2].min() < -1.0)
            start = t0 + a.entry_offset_bars * bar_ms
            raw = feed.window(start, start + a.horizon_bars * bar_ms)
            if not len(raw):
                continue
            m1 = to_r(raw, entry, R, long)
            held = m1[m1[:, 0] < t_exit + (a.entry_offset_bars) * bar_ms]
            if r.get("mfe_r") is not None and len(held):
                mfe_n += 1
                mfe_ok += int(abs(held[:, 1].max() - float(r["mfe_r"])) <= a.control_tol)
            for k in ks:
                bl, ml = walk(m1, start, bar_ms, a.horizon_bars, k, -1.0)
                c = per_k[k]
                c["n"] += 1
                c[ml if ml in ("tp", "sl", "timeout") else "timeout"] += 0 if ml == "ambiguous" else 1
                if bl == "ambiguous":
                    c["amb"] += 1
                    c["mis"] += int(ml == "tp")
                    c["amb_1m"] += int(ml == "ambiguous")
        cn, cb = ctl[a.entry_offset_bars]
        breach = (cb / cn) if cn else None
        status = "measured" if cn >= 10 and breach is not None and breach <= a.control_max_breach else "control_failed"
        legs_out[leg] = {"family": family_of(leg), "symbol": sym, "bar_minutes": tf, "status": status, "unreadable_rows": bad_rows,
                         "control": {"entry_offset_bars": a.entry_offset_bars,
                                     "sl_breach_before_exit": {str(o): {"n": v[0], "breaches": v[1]} for o, v in ctl.items()},
                                     "mfe_match_within_tol": {"n": mfe_n, "match_frac": (mfe_ok / mfe_n) if mfe_n else None}},
                         "per_k": {str(k): {**v, "amb_frac": v["amb"] / v["n"] if v["n"] else None,
                                            "mislabel_frac": v["mis"] / v["n"] if v["n"] else None} for k, v in per_k.items()}}
    fams: dict[str, Any] = {}
    for leg, L in legs_out.items():
        if L["status"] != "measured":
            continue
        F = fams.setdefault(L["family"], {"legs": [], **{str(k): {"n": 0, "amb": 0, "mis": 0, "amb_1m": 0} for k in ks}})
        F["legs"].append(leg)
        for k in ks:
            for f in ("n", "amb", "mis", "amb_1m"):
                F[str(k)][f] += L["per_k"][str(k)][f]
    for F in fams.values():
        for k in ks:
            c = F[str(k)]
            c["amb_frac"] = c["amb"] / c["n"] if c["n"] else None
            c["mislabel_frac"] = c["mis"] / c["n"] if c["n"] else None
    eligible = {f: F for f, F in fams.items() if min(F[str(k)]["n"] for k in ks) >= a.min_n}
    if not eligible:
        verdict = "indeterminate"
    else:
        worst = max(F[str(k)]["mislabel_frac"] for F in eligible.values() for k in ks)
        verdict = "NEEDED" if worst >= a.needed_frac else ("NOT_NEEDED" if worst < a.not_needed_frac else "MIXED")
    return {"verdict": verdict, "families": fams, "eligible_families": sorted(eligible), "legs": legs_out,
            "skipped": skipped, "missing_months": {s: f.missing for s, f in feeds.items() if f.missing}}


#: domain class -> script_run contract verdict (NEEDED clears the registered 2% bar; MIXED is between the bars)
VERDICT_MAP = {"NEEDED": "pass", "NOT_NEEDED": "no_action_warranted", "MIXED": "indeterminate"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out")
    ap.add_argument("--ledgers", default=DEFAULT_LEDGERS)
    ap.add_argument("--legs", default=None)
    ap.add_argument("--tp-r", default="1,2,3")
    ap.add_argument("--horizon-bars", type=int, default=60)
    ap.add_argument("--max-trades-per-leg", type=int, default=400)
    ap.add_argument("--cache", default="/tmp/intrabar_1m_cache")
    ap.add_argument("--entry-offset-bars", type=int, default=1)
    ap.add_argument("--control-tol", type=float, default=0.05)
    ap.add_argument("--control-max-breach", type=float, default=0.05)
    ap.add_argument("--min-n", type=int, default=100)
    ap.add_argument("--needed-frac", type=float, default=0.02)
    ap.add_argument("--not-needed-frac", type=float, default=0.005)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.out:
        ap.error("--out is required")
    m = measure(a)
    n = sum(F[str(float(a.tp_r.split(",")[0]))]["n"] for F in m["families"].values()) if m["families"] else 0
    # script_run's contract allows only pass|fail|no_action_warranted|indeterminate|not_applicable: the domain class
    # (NEEDED / NOT_NEEDED / MIXED) rides in measurement.fidelity_class, never in `verdict` (RQ-20261014-da7a-01 landed
    # producer_failed on 'MIXED').
    rec = {"verdict": VERDICT_MAP.get(m["verdict"], m["verdict"]), "read_state": "measured" if m["eligible_families"] else "no_data", "n": n,
           "population": (f"newest committed ledger per leg under {a.ledgers}, resamples excluded, Binance USD-M USDT legs only, "
                          f"<= {a.max_trades_per_leg} trades per leg, legs passing the initial-SL-before-exit alignment control; "
                          f"{len(m['legs'])} legs walked, {len(m['skipped'])} skipped (listed)"),
           "measurement": {"fidelity_class": m["verdict"], "bars": {"tp_r": a.tp_r, "horizon_bars": a.horizon_bars, "min_n": a.min_n,
                                    "needed_frac": a.needed_frac, "not_needed_frac": a.not_needed_frac}, **m},
           "note": ("Fixed bracket (initial SL at -1R, TP at +K R) re-walked on Binance Vision 1m klines; MISLABEL = the bar "
                    "touched both levels and 1m says TP came first, i.e. the SL-first convention mislabelled it. Label-fidelity "
                    "measurement for the 1m archive decision, NOT an edge or promotion case.")}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "verdict.json").write_text(json.dumps(rec, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"intrabar_label_fidelity: class={m['verdict']} verdict={rec['verdict']} read_state={rec['read_state']} n={n}")
    return 0


def _self_test() -> int:
    t0, bar = 0.0, 4 * 3_600_000.0
    # entry 100, R 1 (long, sl 99). Bar 0 quiet; bar 1 touches 102 (K=2) at minute 10 then 99 at minute 30 -> TP first.
    rows = []
    for i in range(480):
        t = t0 + i * 60_000.0
        hi, lo = 100.2, 99.8
        if i == 240 + 10:
            hi = 102.1
        if i == 240 + 30:
            lo = 98.9
        rows.append((t, hi, lo, 100.0))
    m1 = to_r(np.array(rows), 100.0, 1.0, True)
    bl, ml = walk(m1, t0, bar, 5, 2.0, -1.0)
    assert (bl, ml) == ("ambiguous", "tp"), (bl, ml)
    # same minute touches both -> stays ambiguous
    rows[250] = (rows[250][0], 102.1, 98.9, 100.0)
    bl, ml = walk(to_r(np.array(rows), 100.0, 1.0, True), t0, bar, 5, 2.0, -1.0)
    assert (bl, ml) == ("ambiguous", "ambiguous"), (bl, ml)
    # short side mirrors
    m1s = to_r(np.array([(0.0, 100.2, 97.9, 100.0)]), 100.0, 1.0, False)
    assert walk(m1s, 0.0, 60_000.0, 1, 2.0, -1.0) == ("tp", "tp")
    assert leg_minutes("trend_donchian_btc_4h") == 240 and leg_minutes("x_15m") == 15 and leg_minutes("foo") is None
    print("intrabar_label_fidelity self-test: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
