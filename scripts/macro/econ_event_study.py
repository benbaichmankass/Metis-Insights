#!/usr/bin/env python3
"""ROADMAP_MACRO M1 — economic-surprise → forward-price event study (observe-only).

The M1 gate wants a **clean joined dataset**: point-in-time release history
(consensus + realized) joined to the traded price series, so M2 can calibrate
surprise-vs-consensus → forward returns. The calendar/consensus/surprise HALF is
built (`econ_calendar_produce.py` → `comms/macro/econ_calendar_snapshots.jsonl`,
PIT, append-only). This harness is the **join + measurement instrument**: it reads
the resolved release rows for one event `kind` (e.g. `eia_natgas_storage`), pairs
each release with a daily-close price panel (NG=F for gas), and reports the
information coefficient IC(H) = Spearman(surprise, forward return) at a range of
trading-day horizons, plus a Pearson and a directional sign-hit-rate.

**Reading the sign.** For a supply/inventory print like EIA natural-gas storage a
BUILD bigger than consensus (positive surprise) is bearish → the hypothesis is a
NEGATIVE surprise→return IC. The harness only *measures*; it takes no position and
asserts no sign — it reports the number and lets the evidence speak.

**Honesty about n (stated, not hidden).** The free calendar feeds only carry a
recent window of point-in-time consensus, so today the resolved history for a
weekly print is a HANDFUL of releases. At n≈6 an IC is a lead, not a result — the
t-stat is reported but the verdict caps at `insufficient_history` until enough
releases accrue (consensus depth grows going forward as the daily producer runs).
Right-censored releases (no forward bar H days out yet) are honestly excluded from
that horizon's n, never zero-filled.

Off-VM-guarded price fetch (via `fetch_macro_candles.symbol_close_pairs`) so the
live trading VM never opens a market-data socket; the panel is injectable for
tests. Reads snapshots + prices, writes a scorecard JSON. No order path, no DB
write. Pairs with `econ_calendar_produce.py` (the PIT producer) + the
`horizon_ic_scan.py` value-sleeve scan (the same IC-by-horizon shape).
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from horizon_ic_scan import ic_t_stat  # noqa: E402  (rank-correlation t, shared)
from thesis_backtest_run import load_close_panels  # noqa: E402  (per-symbol CSV reader, shared)

DEFAULT_HORIZONS = [1, 3, 5, 10, 21]  # trading days: next-day → ~1 month
# Legacy BARE default (kept only for the `--json` help string + back-compat awareness).
# The default OUT path is now per-kind suffixed — see `default_scorecard_path`. A bare
# `econ_event_study_scorecard.json` is the eia_natgas_storage kind's legacy landed name
# (the workflow still writes it via an explicit --json for registry back-compat), but a
# default invocation NEVER writes the bare name again: an unsuffixed default is an
# implicit-primary that lets `econ_event_study_*_scorecard.json` enumerate 4-of-5 and
# read complete (BL-20260730-ECON-SCORECARD-NAMING-TRAP).
DEFAULT_SCORECARD_PATH = os.path.join("comms", "macro", "econ_event_study_scorecard.json")


def default_scorecard_path(kind: str) -> str:
    """Per-kind scorecard path — ALWAYS suffixed with the kind, so a default
    invocation can never write the ambiguous bare `econ_event_study_scorecard.json`
    (the naming trap: a kind-less filename that a family glob misses)."""
    return os.path.join("comms", "macro", f"econ_event_study_{kind}_scorecard.json")

# Event kind → the daily-close price series whose forward return the surprise is
# hypothesised to move. Front-month futures (yfinance `=F` tickers); NOT hardcoded
# into the order path — a reporting map only, overridable via --symbol.
KIND_DEFAULT_SYMBOL = {
    "eia_natgas_storage": "NG=F",
    "eia_crude_stocks": "CL=F",
    "api_crude_stocks": "CL=F",
    "eia_gasoline_stocks": "RB=F",
    "baker_hughes_us_oil_rig_count": "CL=F",
    "cpi_yoy": "ES=F",
    "cpi_mom": "ES=F",
    # Added 2026-07-30: the two weekly labour series are the HIGHEST-n kinds the
    # keyless FRED backfill actually resolves (ICSA 3031 rows / CCSA 3030 vs the two
    # energy kinds, whose ids 404 — BL-20260730-EIA-SERIES-IDS-NOT-FRED). Without a
    # mapping here the study refuses them for want of a symbol, so the only kinds it
    # could ever run were the two that are blocked. Mirrors config/macro_econ_series.yaml.
    "initial_jobless_claims": "ES=F",
    "continuing_jobless_claims": "ES=F",
    "nfp": "ES=F",
    "fomc": "ES=F",
}
# n at/below which an IC is a lead, not a result (the honest-small-n cap).
MIN_HONEST_N = 12


def _norm_date(s: object) -> str:
    return str(s or "")[:10]


def load_resolved_events(snapshots_path: str, kind: str) -> list[dict]:
    """Resolved releases for one event ``kind`` from the PIT snapshots JSONL.

    Dedupes by ``scheduled_for`` (a release date can be observed by more than one
    daily capture — same values, one row), preferring the row whose
    ``realized_outcome`` carries a real ``consensus`` (so ``surprise`` is defined)
    and, among those, the earliest ``observed_at`` (the first point-in-time read —
    never a later revised consensus). Returns ``[{date, surprise, surprise_pct,
    actual, consensus, change}, ...]`` ascending by date. Best-effort: an
    unreadable line is skipped, never fatal."""
    best: dict[str, dict] = {}
    try:
        fh = open(snapshots_path, encoding="utf-8")
    except OSError:
        return []
    with fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except (ValueError, TypeError):
                continue
            if r.get("kind") != kind or r.get("status") != "resolved":
                continue
            date = _norm_date(r.get("scheduled_for") or r.get("scheduled_at"))
            if len(date) != 10:
                continue
            ro = r.get("realized_outcome") or {}
            cand = {
                "date": date,
                "surprise": _num(ro.get("surprise")),
                "surprise_pct": _num(ro.get("surprise_pct")),
                "actual": _num(ro.get("actual")),
                "consensus": _num(ro.get("consensus")),
                "change": _num(ro.get("change")),
                "observed_at": str(r.get("observed_at") or ""),
            }
            prev = best.get(date)
            if prev is None or _better(cand, prev):
                best[date] = cand
    return [best[d] for d in sorted(best)]


def _better(cand: dict, prev: dict) -> bool:
    """Prefer a defined surprise; tie-break to the EARLIEST observed_at (the
    first point-in-time read — the never-revised consensus)."""
    c_has, p_has = cand["surprise"] is not None, prev["surprise"] is not None
    if c_has != p_has:
        return c_has
    return (cand.get("observed_at") or "~") < (prev.get("observed_at") or "~")


def _num(v: object) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def make_forward_return(panel: list[tuple[str, float]]) -> Callable[[str, int], Optional[float]]:
    """Build ``forward_return(date_iso, horizon_trading_days)`` over one ascending
    daily-close panel ``[(YYYY-MM-DD, close), ...]``.

    Base = the close ON the release date if the symbol traded that day, else the
    last close STRICTLY BEFORE it (never a future bar); forward = the close
    ``horizon`` trading rows later. ``None`` when the release precedes the panel
    OR the forward bar isn't in the panel yet (right-censored — honestly excluded,
    never zero-filled)."""
    dates = [d for d, _ in panel]
    closes = [c for _, c in panel]

    def forward_return(date_iso: str, horizon: int) -> Optional[float]:
        target = _norm_date(date_iso)
        pos = bisect.bisect_right(dates, target) - 1  # rightmost bar with date <= target
        if pos < 0:
            return None
        fwd = pos + int(horizon)
        if fwd >= len(closes):
            return None  # forward bar not yet available (right-censored)
        base = closes[pos]
        if base == 0:
            return None
        return (closes[fwd] - base) / base

    return forward_return


def _panel_position(panel_dates: list[str], date_iso: str) -> int:
    """Trading-day index of the rightmost bar with date <= *date_iso* (−1 if the
    release precedes the panel). Same base-bar rule as ``make_forward_return``."""
    return bisect.bisect_right(panel_dates, _norm_date(date_iso)) - 1


def _median(xs: list[float]) -> Optional[float]:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def release_spacing_td(events: list[dict], panel: list[tuple[str, float]]) -> Optional[float]:
    """Median **trading-day** gap between consecutive resolved releases (mapped to
    their base bar in the price panel). This is the sampling interval `s` the
    forward-window overlap is measured against — horizon-independent, so it's
    computed once. `None` when fewer than two releases fall inside the panel."""
    dates = [d for d, _ in panel]
    pos = [p for p in (_panel_position(dates, e["date"]) for e in events) if p >= 0]
    pos.sort()
    gaps = [b - a for a, b in zip(pos, pos[1:])]
    return _median([float(g) for g in gaps]) if gaps else None


def effective_n(n: int, horizon: int, spacing_td: Optional[float]) -> tuple[float, Optional[float]]:
    """Overlap-corrected effective sample size for an `n`-release IC measured over
    a `horizon`-trading-day forward window sampled every `spacing_td` trading days.

    When the forward window is LONGER than the release spacing the windows overlap,
    so consecutive forward returns are autocorrelated and the raw t (which assumes
    `n` independent draws) is OPTIMISTIC. The standard rule-of-thumb deflation is
    `n_eff = n / overlap_factor`, `overlap_factor = max(1, horizon / spacing)` —
    roughly the number of overlapping windows per independent block. Returns
    `(overlap_factor, n_eff)`; `n_eff` is `None` when the spacing is unknown (then
    the caller keeps the raw t and says so). NOT a rigorous Newey–West HAC on the
    rank statistic — an effective-sample rule-of-thumb, the honest sibling of the
    raw t (a full HAC is a documented further refinement)."""
    if spacing_td is None or spacing_td <= 0 or not n:
        return 1.0, None
    overlap_factor = max(1.0, float(horizon) / float(spacing_td))
    return overlap_factor, float(n) / overlap_factor


def _rank(values: list[float]) -> list[float]:
    """Fractional (tie-averaged) ranks — the Spearman building block."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank across the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    """Tie-averaged Spearman rank correlation in [-1, 1]; None on < 3 points or no
    variance (honest-null — never a fabricated correlation on a degenerate sample)."""
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _rank(xs), _rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _sign_hit_rate(xs: list[float], ys: list[float]) -> Optional[float]:
    """Fraction of releases where sign(x) == sign(y), over points with both signs
    non-zero. A directional, outlier-robust read at tiny n. None when no signed
    pairs. (For an inventory print the *hypothesis* is anti-correlation, so a
    hit-rate < 0.5 here is the expected bearish-on-build direction — the harness
    reports the raw agreement and leaves the sign to the reader.)"""
    pairs = [(x, y) for x, y in zip(xs, ys) if x and y]
    if not pairs:
        return None
    hits = sum(1 for x, y in pairs if (x > 0) == (y > 0))
    return hits / len(pairs)


def event_study(
    events: list[dict], panel: list[tuple[str, float]], *,
    horizons: list[int], value_key: str = "surprise",
) -> list[dict]:
    """Per-horizon surprise→forward-return statistics.

    For each horizon: pair each release's ``value_key`` (default ``surprise``;
    releases missing it are skipped for that stat) with its forward return, then
    report n, Spearman IC + its rule-of-thumb t, Pearson, sign-hit-rate, and the
    mean forward return. Right-censored releases drop out of that horizon's n."""
    fwd = make_forward_return(panel)
    spacing_td = release_spacing_td(events, panel)
    rows: list[dict] = []
    for h in horizons:
        xs: list[float] = []
        ys: list[float] = []
        for e in events:
            v = e.get(value_key)
            r = fwd(e["date"], h)
            if v is None or r is None:
                continue
            xs.append(float(v))
            ys.append(float(r))
        n = len(xs)
        ic = _spearman(xs, ys)
        # Overlap correction: the raw ic_t assumes n independent draws, but a
        # forward window LONGER than the release spacing overlaps → autocorrelated
        # returns → optimistic t. ic_t_eff deflates n by the overlap factor (the
        # honest t, a rule-of-thumb; see effective_n). None-spacing → no correction.
        overlap_factor, n_eff = effective_n(n, h, spacing_td)
        ic_t_eff = ic_t_stat(ic, n_eff) if n_eff is not None else None
        rows.append({
            "horizon_days": h,
            "n": n,
            "ic": _r(ic),
            "ic_t": (lambda t: None if t is None else round(t, 3))(ic_t_stat(ic, n)),
            # Overlap-corrected (honest) t + the effective sample it's computed on.
            "overlap_factor": _r(overlap_factor),
            "n_eff": _r(n_eff),
            "ic_t_eff": (lambda t: None if t is None else round(t, 3))(ic_t_eff),
            "pearson": _r(_pearson(xs, ys)),
            "sign_hit_rate": _r(_sign_hit_rate(xs, ys)),
            "mean_fwd_return": _r(sum(ys) / n) if n else None,
        })
    return rows


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = sum((a - mx) ** 2 for a in xs) ** 0.5
    dy = sum((b - my) ** 2 for b in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _r(v: object) -> Optional[float]:
    return None if v is None else round(float(v), 6)


def _honest_t(r: dict) -> Optional[float]:
    """The overlap-corrected t when present, else the raw t (back-compat for
    callers/rows that predate the correction). This is the t the VERDICT trusts —
    an overlapping window's raw t is only a lead."""
    te = r.get("ic_t_eff")
    return te if te is not None else r.get("ic_t")


def summarize(rows: list[dict], *, t_flag: float = 2.0, min_honest_n: int = MIN_HONEST_N) -> dict:
    """Honest verdict. A horizon is *flagged* when its OVERLAP-CORRECTED |IC_t_eff|
    >= t_flag (the raw |IC_t| is kept for reference but is optimistic when forward
    windows overlap — see effective_n). The top-line verdict caps at
    ``insufficient_history`` while the max n across horizons is <= ``min_honest_n``.

    Added 2026-08-01: the flag now trusts the overlap-corrected t, so a signal that
    is significant ONLY on the autocorrelation-inflated raw t (the natgas-21d shape:
    weekly releases, 21-td forward window overlapping ~4×) reports the distinct
    ``flagged_overlap_uncorrected_only`` verdict instead of a false
    ``surprise_predicts_forward_return``."""
    scored = [r for r in rows if r["n"] and r["ic"] is not None]
    max_n = max((r["n"] for r in rows), default=0)
    flagged_raw = [r for r in scored if r["ic_t"] is not None and abs(r["ic_t"]) >= t_flag]
    flagged_honest = [r for r in scored if _honest_t(r) is not None and abs(_honest_t(r)) >= t_flag]
    strongest = max(scored, key=lambda r: abs(r["ic"]), default=None)
    enough = max_n > min_honest_n
    return {
        "max_n": max_n,
        "min_honest_n": min_honest_n,
        "sufficient_history": enough,
        # Optimistic (raw-t) flag kept for reference; the corrected flag is the one
        # the verdict trusts.
        "any_flagged_horizon": bool(flagged_raw),
        "any_flagged_horizon_overlap_corrected": bool(flagged_honest),
        "strongest_ic_horizon_days": strongest["horizon_days"] if strongest else None,
        "strongest_ic": strongest["ic"] if strongest else None,
        "strongest_ic_t": strongest["ic_t"] if strongest else None,
        "strongest_ic_t_eff": strongest.get("ic_t_eff") if strongest else None,
        "t_flag": t_flag,
        "verdict": (
            "no_data" if not scored
            else "insufficient_history" if not enough
            else "surprise_predicts_forward_return" if flagged_honest
            else "flagged_overlap_uncorrected_only" if flagged_raw
            else "no_edge_at_tested_horizons"
        ),
    }


def render(rows: list[dict], summary: dict, *, meta: dict) -> str:
    lines = [
        "Econ-surprise → forward-return event study (observe-only)",
        "=" * 58,
        f"kind={meta['kind']}  symbol={meta['symbol']}  value={meta['value_key']}  "
        f"releases={meta['releases']}  price_bars={meta['price_bars']}",
        "",
        f"{'H(td)':>6} {'n':>5} {'IC':>9} {'IC_t':>8} {'ovlp':>6} {'n_eff':>7} "
        f"{'IC_t_eff':>9} {'pearson':>9} {'sign_hit':>9} {'mean_fwd':>10}",
    ]
    for r in rows:
        lines.append(
            f"{r['horizon_days']:>6} {r['n']:>5} {_f(r['ic']):>9} {_f(r['ic_t']):>8} "
            f"{_f(r.get('overlap_factor')):>6} {_f(r.get('n_eff')):>7} {_f(r.get('ic_t_eff')):>9} "
            f"{_f(r['pearson']):>9} {_f(r['sign_hit_rate']):>9} {_f(r['mean_fwd_return']):>10}"
        )
    lines += [
        "",
        f"verdict: {summary['verdict']}  (max_n={summary['max_n']}  "
        f"strongest_IC={_f(summary['strongest_ic'])} @ {summary['strongest_ic_horizon_days']}td, "
        f"raw_t={_f(summary['strongest_ic_t'])}  overlap_corrected_t={_f(summary.get('strongest_ic_t_eff'))})",
        "note: IC = Spearman(surprise, forward return). For an inventory BUILD print a "
        "bigger-than-consensus surprise is bearish, so the hypothesis is a NEGATIVE IC.",
        "note: IC_t is OPTIMISTIC when the forward window overlaps the release spacing "
        "(ovlp>1); IC_t_eff deflates n by the overlap factor and is the t the verdict trusts. "
        "flagged_overlap_uncorrected_only = significant on the raw t only, not after overlap correction.",
        "note: verdict caps at insufficient_history until enough releases accrue "
        f"(max_n must exceed {summary['min_honest_n']}); free feeds start with a small PIT window.",
    ]
    return "\n".join(lines)


def _f(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _resolve_panel(symbol: str, candles_dir: Optional[str]) -> list[tuple[str, float]]:
    """Price panel for ``symbol``: from ``<candles-dir>/<SYMBOL>.csv`` when given,
    else an off-VM live fetch via the shared candle fetcher. Empty on failure."""
    if candles_dir:
        panels = load_close_panels(candles_dir)
        return panels.get(str(symbol).upper(), [])
    from fetch_macro_candles import symbol_close_pairs, _resolve_fetchers  # noqa: E402
    download, stooq_urlopen = _resolve_fetchers(None, None, "2005-01-01")
    return symbol_close_pairs(symbol, download=download, stooq_urlopen=stooq_urlopen)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Economic-surprise → forward-price event study (observe-only)")
    ap.add_argument("--snapshots", default=os.path.join("comms", "macro", "econ_calendar_snapshots.jsonl"),
                    help="PIT econ-calendar snapshots JSONL")
    ap.add_argument("--kind", default="eia_natgas_storage", help="event kind to study (default eia_natgas_storage)")
    ap.add_argument("--symbol", default=None, help="price symbol (default: the kind's mapped futures symbol)")
    ap.add_argument("--candles-dir", default=None,
                    help="dir of per-symbol daily-close CSVs (default: off-VM live fetch)")
    ap.add_argument("--horizons", default=None, help="CSV of forward trading-day horizons (default 1,3,5,10,21)")
    ap.add_argument("--value-key", default="surprise", choices=["surprise", "surprise_pct", "change"],
                    help="release field correlated with forward return (default surprise)")
    ap.add_argument("--t-flag", type=float, default=2.0, help="|IC_t| threshold to flag a horizon (default 2.0)")
    ap.add_argument("--min-honest-n", type=int, default=MIN_HONEST_N,
                    help=f"max_n must EXCEED this for a non-provisional verdict (default {MIN_HONEST_N})")
    ap.add_argument("--json", default=None,
                    help="scorecard JSON out (default: comms/macro/econ_event_study_<kind>_scorecard.json "
                         "— ALWAYS per-kind suffixed; never the bare econ_event_study_scorecard.json)")
    ap.add_argument("--generated-at", default=None)
    ap.add_argument("--dry-run", action="store_true", help="compute + print; write nothing")
    ap.add_argument("--allow-empty-panel", action="store_true",
                    help="exit 0 even when the price panel is empty (deliberate dry probe "
                         "only; the default is to FAIL so a vacuous scorecard can't pass as "
                         "a thin one — BL-20260730-M1-PRICE-JOIN-DEAD)")
    args = ap.parse_args(argv)

    horizons = (
        [int(x) for x in args.horizons.split(",") if x.strip()]
        if args.horizons else list(DEFAULT_HORIZONS)
    )
    symbol = args.symbol or KIND_DEFAULT_SYMBOL.get(args.kind)
    if not symbol:
        print(f"no price symbol for kind={args.kind}; pass --symbol")
        return 1

    events = load_resolved_events(args.snapshots, args.kind)
    panel = _resolve_panel(symbol, args.candles_dir)
    rows = event_study(events, panel, horizons=horizons, value_key=args.value_key)
    summary = summarize(rows, t_flag=args.t_flag, min_honest_n=args.min_honest_n)
    meta = {
        "kind": args.kind,
        "symbol": symbol,
        "value_key": args.value_key,
        "releases": len(events),
        "releases_with_value": sum(1 for e in events if e.get(args.value_key) is not None),
        "price_bars": len(panel),
        # Median trading-day gap between releases — the sampling interval the
        # forward-window overlap correction is measured against (see effective_n).
        "release_spacing_td": _r(release_spacing_td(events, panel)),
        "horizons": horizons,
        "snapshots_path": args.snapshots,
        "generated_at": args.generated_at,
    }
    print(render(rows, summary, meta=meta))

    if not args.dry_run:
        out = {"rows": rows, "summary": summary, "meta": meta}
        # Default OUT is per-kind suffixed — never the bare name (naming trap).
        out_path = args.json or default_scorecard_path(args.kind)
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {out_path}")

    # VACUITY GUARD (BL-20260730-M1-PRICE-JOIN-DEAD). A study asked to measure a
    # kind and handed ZERO price bars has not produced a thin result — it has
    # produced NO result, and must not exit 0. This study reported
    # `price_bars: 0` / `verdict: no_data` on every run of its life while its
    # workflow stayed green and the roadmap recorded the verdict as one that would
    # "self-graduate as history accrues" — it never could. A fresh, well-formed,
    # entirely vacuous artifact is the failure mode; exiting non-zero is what makes
    # it visible. `--allow-empty-panel` is the explicit opt-out for a deliberate
    # dry probe.
    if not args.allow_empty_panel and len(panel) == 0:
        print(
            f"\nFAIL: no price bars for symbol={symbol} (kind={args.kind}). The "
            "surprise->forward-return join measured NOTHING, so the verdict above is "
            "vacuous, not thin. Check the candle fetch (yfinance installed? Stooq "
            "ticker resolvable?) — do not read this scorecard as evidence.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
