#!/usr/bin/env python3
"""backtest↔live fidelity calibrator — the earned-trust linchpin (P0).

Design of record: docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md § 2.4.

The problem it solves: we have never MEASURED whether our backtests are right, so we
fell back to "only trust real live trades" — which caps every decision at reality's
clock. This turns the qualitative `faithful`/`approximate` label into a measured
**agreement score** per strategy×symbol: does the backtest trade distribution
reproduce the LIVE trade distribution (win-rate + realized-R shape) on the legs where
we have both? A leg that clears the gate → its backtest is TRUSTED OOS evidence now;
a leg that drifts → its backtest is a lead, not a result; a leg with too few live
trades → `insufficient-live` (honest, never a silent pass).

TRUST DISCIPLINE (the scars):
- Live population is **measured-provenance only** (`provenance.pnl_is_trustworthy`) —
  fabricated/paper pnl never enters the calibration set (the same filter the P0
  label-augment eval used to exclude the poisoned paper book).
- Win-rate agreement + a two-sample KS on the realized-R distribution are the two
  axes; the verdict abstains below a live-n floor rather than certifying on noise.
- Pure functions (`agreement`) are network/DB-free and unit-tested; only `_load_*`
  touch SQLite (read-only).

Usage (on the trainer, where both DBs live):
    python scripts/research/backtest_fidelity_calibrate.py \
      --backtest-db datasets-out/backtest_trades.db \
      --live-db data/trade_journal.db \
      --strategy trend_donchian --symbol BTCUSDT \
      --out comms/research/backtest_fidelity_trend_donchian_BTCUSDT.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# Gate thresholds (documented; a leg clears only if it meets ALL).
MIN_LIVE_N = 30            # below this the live sample is too thin to calibrate
MAX_WINRATE_DIFF = 0.15    # |backtest win-rate − live win-rate| must be ≤ this
MAX_KS = 0.30              # KS(realized-R) must be ≤ this (distribution agreement)
MAX_MEAN_R_GAP = 0.50      # |backtest mean-R − live mean-R| must be ≤ this

#: A leg whose mean is this concentrated in ONE row is reported as
#: outlier-dominated. Not a gate — a legibility flag, so a magnitude failure is
#: never mysterious (see `agreement`).
#:
#: **0.50 is the mathematical CEILING of the metric, not a midpoint** — the
#: deviations above and below a mean sum to the same total, so a lone value on
#: its side of the mean contributes exactly half and can never contribute more.
#: The threshold therefore has to sit BELOW 0.5 to be reachable; 0.40 means "one
#: row is most of the deviation on its side". Do not "fix" this to 0.8 — that
#: would make the flag unreachable and silently retire it.
OUTLIER_DOMINANCE_FLAG = 0.40


def _mean_outlier_share(xs: Sequence[float]) -> float | None:
    """Share of the total absolute deviation-from-mean contributed by the single
    most extreme value, in ``[0, 0.5]``. ``None`` for n < 3.

    Answers "is this mean a property of the distribution, or of one row?" —
    the question that made the 2026-08-06 `calibrated`-with-a-92x-mean-gap
    verdict unreadable until someone dumped the rows by hand.
    """
    vals = [x for x in xs if x is not None and not math.isnan(x)]
    if len(vals) < 3:
        return None
    mu = sum(vals) / len(vals)
    devs = sorted((abs(x - mu) for x in vals), reverse=True)
    total = sum(devs)
    if total <= 0:
        return 0.0
    return devs[0] / total


def _ks_2samp(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Two-sample Kolmogorov–Smirnov statistic (max CDF gap). Stdlib-only."""
    a = sorted(x for x in a if x is not None and not math.isnan(x))
    b = sorted(x for x in b if x is not None and not math.isnan(x))
    if not a or not b:
        return None
    grid = sorted(set(a) | set(b))

    def cdf(xs: list[float], v: float) -> float:
        # fraction of xs ≤ v
        lo, hi = 0, len(xs)
        while lo < hi:
            mid = (lo + hi) // 2
            if xs[mid] <= v:
                lo = mid + 1
            else:
                hi = mid
        return lo / len(xs)

    return max(abs(cdf(a, v) - cdf(b, v)) for v in grid)


def _win_rate(outcomes: Sequence[float]) -> float | None:
    vals = [x for x in outcomes if x is not None]
    if not vals:
        return None
    return sum(1 for x in vals if x > 0) / len(vals)


def agreement(
    live_r: Sequence[float],
    backtest_r: Sequence[float],
    *,
    min_live_n: int = MIN_LIVE_N,
    max_winrate_diff: float = MAX_WINRATE_DIFF,
    max_ks: float = MAX_KS,
    max_mean_r_gap: float = MAX_MEAN_R_GAP,
    harness_faithful: bool | None = None,
    omitted_levers: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Pure: score backtest↔live agreement from two realized-R samples.

    Returns the metrics + a verdict ∈ {calibrated, drifts, insufficient-live}.

    Both samples must be on the SAME axis — this function cannot tell them apart,
    so the caller owns that (see ``_live_rows``' ``r_basis``). Scoring a ±1
    sign-proxy live sample against a continuous backtest R forces
    ``KS ≥ 1 − max(win_rate)`` no matter how faithful the backtest is; the
    resulting ``drifts`` is an artifact of the axis, not a finding.

    THREE axes, and the third exists because two were not enough (2026-08-06).
    ``htf_pullback_trend_2h``/BTCUSDT cleared BOTH the win-rate gap (0.123) and
    KS(R) (0.213) and was declared ``calibrated`` while its live mean-R was
    **−3.41 against a backtest −0.04** — a ~92× magnitude gap. Neither existing
    axis can see that: win-rate measures how OFTEN, KS is a max-CDF-gap that is
    insensitive to tail magnitude, and *nothing* measured how MUCH. A backtest
    that reproduces the shape and frequency of a book while missing its loss
    magnitude by two orders of magnitude is not trustworthy OOS evidence, so
    ``mean_r_gap`` is now a gate condition.

    ``live_mean_r_outlier_share`` is reported but deliberately **not** gated: it
    says whether a mean is a property of the distribution or of one row (that
    leg's mean was one row at −99.5R; excluding the worst three it was +0.002).
    Gating on it would silently forgive a poisoned sample; reporting it makes a
    magnitude failure legible instead of mysterious.
    """
    n_live, n_bt = len(list(live_r)), len(list(backtest_r))
    wr_live, wr_bt = _win_rate(live_r), _win_rate(backtest_r)
    wr_diff = None if (wr_live is None or wr_bt is None) else abs(wr_bt - wr_live)
    ks = _ks_2samp(live_r, backtest_r)
    mean_live = (sum(live_r) / n_live) if n_live else None
    mean_bt = (sum(backtest_r) / n_bt) if n_bt else None
    mean_gap = (None if (mean_live is None or mean_bt is None)
                else abs(mean_bt - mean_live))
    outlier_share = _mean_outlier_share(live_r)

    if n_live < min_live_n:
        verdict = "insufficient-live"
        reason = f"live n={n_live} < floor {min_live_n} — cannot calibrate; backtest is a lead, not a result"
    else:
        wr_ok = wr_diff is not None and wr_diff <= max_winrate_diff
        ks_ok = ks is None or ks <= max_ks
        mean_ok = mean_gap is not None and mean_gap <= max_mean_r_gap
        if wr_ok and ks_ok and mean_ok:
            if harness_faithful is False:
                # The producing harness DECLARED itself incomplete. Agreement on
                # a distribution the harness admits it cannot fully model is not
                # trust — it is a coincidence we have no basis to rely on, and
                # certifying it would hand the P3 promotion gate evidence its own
                # producer disowned. Distinct verdict, never `calibrated`.
                verdict = "approximate-harness"
                missing = ", ".join(omitted_levers or []) or "unspecified levers"
                reason = (
                    "metrics agree, but the harness that produced these backtest "
                    f"rows reports faithful=False (omitted: {missing}) — agreement "
                    "on an admittedly-incomplete model is not earned trust. Treat "
                    "as a lead, not a result, until the levers are modelled."
                )
            else:
                verdict = "calibrated"
                reason = "backtest reproduces the live distribution within tolerance — TRUSTED OOS evidence"
        else:
            verdict = "drifts"
            bits = []
            if not wr_ok:
                if wr_diff is None:
                    bits.append("win-rate unavailable (empty backtest sample)")
                else:
                    bits.append(f"win-rate gap {wr_diff:.3f} > {max_winrate_diff}")
            if not ks_ok and ks is not None:
                bits.append(f"KS(R) {ks:.3f} > {max_ks}")
            if not mean_ok:
                if mean_gap is None:
                    bits.append("mean-R unavailable (empty sample)")
                else:
                    bit = f"mean-R gap {mean_gap:.3f} > {max_mean_r_gap}"
                    # Name the outlier domination in the reason itself. A bare
                    # magnitude failure sends the reader to dump rows by hand;
                    # this says up front whether to suspect the book or one row.
                    if (outlier_share is not None
                            and outlier_share >= OUTLIER_DOMINANCE_FLAG):
                        bit += (f" — but {outlier_share:.0%} of the live mean's"
                                " deviation is ONE row, so suspect a poisoned"
                                " row before a real magnitude drift")
                    bits.append(bit)
            reason = "backtest drifts from live (" + "; ".join(bits) + ") — backtest is a lead, not a result"

    return {
        "verdict": verdict,
        "reason": reason,
        # State the producing harness's own fidelity claim on every result, not
        # only when it bites. `None` means the rows carried no label — which is
        # NOT the same as faithful, and is reported as its own value so a reader
        # can tell "the harness said it was complete" from "nobody recorded it".
        "harness_faithful": harness_faithful,
        "omitted_levers": list(omitted_levers or []),
        "n_live": n_live,
        "n_backtest": n_bt,
        "live_win_rate": wr_live,
        "backtest_win_rate": wr_bt,
        "win_rate_diff": wr_diff,
        "ks_realized_r": ks,
        "live_mean_r": mean_live,
        "backtest_mean_r": mean_bt,
        "mean_r_gap": mean_gap,
        "live_mean_r_outlier_share": outlier_share,
        "thresholds": {"min_live_n": min_live_n, "max_winrate_diff": max_winrate_diff,
                       "max_ks": max_ks, "max_mean_r_gap": max_mean_r_gap},
    }


# ---- stratification (regime/small-sample separation) ------------------------

def _year_of(ts: Any) -> str | None:
    """UTC year bucket from an entry timestamp (epoch-ms/epoch-s/ISO). None if
    un-parseable — the row then only counts toward the un-stratified overall."""
    if ts is None:
        return None
    s = str(ts).strip()
    if not s:
        return None
    try:  # epoch (s or ms)
        v = float(s)
        if v > 1e12:  # epoch-ms
            v /= 1000.0
        from datetime import datetime, timezone
        return str(datetime.fromtimestamp(v, tz=timezone.utc).year)
    except ValueError:
        pass
    m = re.match(r"(\d{4})-\d{2}", s)  # ISO
    return m.group(1) if m else None


def stratified_agreement(
    live_rows: Sequence[dict[str, Any]],
    backtest_rows: Sequence[dict[str, Any]],
    *,
    key: str,
    **thresholds: Any,
) -> dict[str, Any]:
    """Pure: agreement() overall PLUS one agreement() per stratum, grouped by
    `key` ∈ {direction, year}. Separates a UNIFORM cost-model gap (drift roughly
    equal across strata) from a CONCENTRATED regime/small-sample bias (drift in one
    stratum, others fine) — the § 5a caveat, operationalized. Strata below the
    live-n floor are reported honestly as `insufficient-live`, never hidden."""
    def _bucket(row: dict[str, Any]) -> str | None:
        if key == "direction":
            d = str(row.get("direction") or "").lower()
            return d if d in ("long", "short") else None
        if key == "year":
            return _year_of(row.get("ts"))
        return None

    overall = agreement([r["r"] for r in live_rows],
                        [r["r"] for r in backtest_rows], **thresholds)
    strata_keys = sorted({_bucket(r) for r in list(live_rows) + list(backtest_rows)}
                         - {None})
    strata: dict[str, Any] = {}
    for sk in strata_keys:
        lr = [r["r"] for r in live_rows if _bucket(r) == sk]
        br = [r["r"] for r in backtest_rows if _bucket(r) == sk]
        strata[sk] = agreement(lr, br, **thresholds)
    return {"key": key, "overall": overall, "strata": strata}


# ---- DB readers (read-only) -------------------------------------------------

#: The two live-R axes. ``stop_distance`` is the real R (``pnl / risk_usd``);
#: ``sign_proxy`` is the ±1 win/loss proxy the P0 calibrator shipped with. They are
#: NOT interchangeable and a run must never mix them — see ``_live_rows``.
R_BASES = ("stop_distance", "sign_proxy")
DEFAULT_R_BASIS = "stop_distance"


def _live_rows(
    live_db: str, strategy: str, symbol: str, *, r_basis: str = DEFAULT_R_BASIS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Measured-provenance-only live rows + the coverage diagnostic that says
    WHICH R axis was computed and over how much of the sample.

    Returns ``(rows, diag)`` where each row is ``{r, direction, ts, won}`` and
    ``diag`` carries ``r_basis`` / ``rows_scanned`` / ``rows_trusted`` /
    ``rows_r_measured`` / ``r_coverage``.

    **Two axes, never blended (P1.x).**

    - ``stop_distance`` (default) — the real R multiple, ``pnl / risk_usd`` with
      ``risk_usd = |entry − stop| · |qty| · contract_value_usd``, via the canonical
      :func:`src.web.api._clean_trades.r_multiple`. That helper is imported, not
      re-derived: a second copy of the risk formula is exactly how the live and
      API R axes would drift apart.
    - ``sign_proxy`` — the legacy ±1 win/loss stand-in. It is kept ONLY as an
      explicit opt-in for reproducing the P0 numbers, because a ±1 point-mass
      against a continuous backtest R makes ``KS(R) ≥ 1 − max(win_rate)`` **by
      construction, regardless of cost** — the artifact that drove every
      ``drifts`` verdict in § 5b of the design doc.

    A row whose risk is not derivable (no stop, flat stop, missing size) yields
    ``None`` from ``r_multiple`` and is **excluded from the R sample, never
    back-filled with the sign proxy**. Silently substituting the proxy for the
    unmeasurable rows would rebuild the very artifact this change removes, in a
    sample that *claims* to be stop-distance R — the labelled-quantity-is-not-
    what-was-computed defect (``diagnostic-provenance-guard`` sub-class A). The
    exclusion is reported as ``r_coverage`` instead, mirroring ``/performance``'s
    ``rCoverage`` discipline: transparency, never a raw-pnl fallback.
    """
    if r_basis not in R_BASES:
        raise ValueError(f"r_basis must be one of {R_BASES}, got {r_basis!r}")
    try:
        from src.runtime import provenance  # trust filter
        trust = provenance.pnl_is_trustworthy
    except Exception:  # allow-silent: provenance import is optional here — absent ⇒ unfiltered live sample; research calibrator, not a live read-path
        trust = None
    from src.runtime.local_pnl import contract_value_usd_for
    from src.web.api._clean_trades import r_multiple

    con = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    # The R inputs are OPTIONAL columns: select them only when the schema has
    # them, so a minimal/legacy trades table degrades to r_coverage 0.0 (the
    # honest "no row was R-measurable") instead of raising `no such column`,
    # which the caller would surface as an empty — and so falsely clean — leg.
    avail = {row[1] for row in con.execute("PRAGMA table_info(trades)")}
    r_cols = [c for c in ("entry_price", "stop_loss", "position_size") if c in avail]
    extra = "".join(f", {c}" for c in r_cols)
    rows = con.execute(
        f"SELECT pnl, notes, direction, timestamp{extra} FROM trades "
        "WHERE status='closed' "
        "AND COALESCE(is_backtest,0)=0 AND strategy_name=? AND symbol=? "
        "AND pnl IS NOT NULL",
        (strategy, symbol),
    ).fetchall()
    con.close()

    cvu = contract_value_usd_for(symbol)
    out: list[dict[str, Any]] = []
    scanned = len(rows)
    trusted = 0
    for r in rows:
        if trust is not None:
            try:
                if not trust(dict(r)):
                    continue
            except Exception:  # allow-silent: fail-open on an un-scoreable row (keep it) — research calibrator, not a live read-path
                pass
        trusted += 1
        pnl = r["pnl"] or 0
        if r_basis == "sign_proxy":
            rv: float | None = 1.0 if pnl > 0 else -1.0
        else:
            rv = r_multiple(
                pnl,
                r["entry_price"] if "entry_price" in r.keys() else None,
                r["stop_loss"] if "stop_loss" in r.keys() else None,
                r["position_size"] if "position_size" in r.keys() else None,
                cvu,
            )
        if rv is None:
            continue  # not R-measurable — counted in r_coverage, never proxied
        out.append({"r": rv, "direction": r["direction"], "ts": r["timestamp"],
                    "won": pnl > 0})
    diag = {
        "r_basis": r_basis,
        # THREE states, never collapsed (CLAUDE.md § "Collapsed states"):
        #   applied     — the provenance filter ran; `rows_trusted` is a real filter result
        #   unavailable — `src.runtime.provenance` would not import, so NOTHING was
        #                 filtered and `rows_trusted` equals `rows_scanned` by default
        #
        # Without this field those two are indistinguishable in the output: an
        # import failure silently WIDENS the population to include fabricated pnl
        # and then reports a 100% trusted rate, which reads as an exceptionally
        # clean leg rather than as an unfiltered one. That inverts the meaning of
        # the number this whole module exists to produce — the docstring's first
        # trust-discipline bullet is "live population is measured-provenance only".
        #
        # The sibling loader already got this right: `setup_candidates._load_live_trades`
        # logs a WARNING naming the skipped filter rather than returning a silently
        # unfiltered population. This is that same disclosure, as a field.
        "trust_filter": "applied" if trust is not None else "unavailable",
        "rows_scanned": scanned,
        "rows_trusted": trusted,
        "rows_r_measured": len(out),
        # None (not 0.0) when nothing was trusted, so "no trusted live trade"
        # stays distinguishable from "trusted trades exist but none carried a
        # usable stop" — two very different findings that a bare 0.0 conflates.
        "r_coverage": (round(len(out) / trusted, 4) if trusted else None),
    }
    return out, diag


def _backtest_rows(bt_db: str, strategy: str, symbol: str) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{bt_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    # The recorder (ml.datasets.backtest_recorder) stores the harness ENTRY time in
    # `timestamp` (not a separate entry_ts column) and the R-multiple in `pnl`.
    rows = con.execute(
        "SELECT pnl, direction, timestamp, notes FROM trades "
        "WHERE COALESCE(is_backtest,0)=1 AND strategy_name=? AND symbol=? "
        "AND pnl IS NOT NULL",
        (strategy, symbol),
    ).fetchall()
    con.close()
    return [{"r": float(r["pnl"]), "direction": r["direction"],
             "ts": r["timestamp"], "won": float(r["pnl"]) > 0,
             "notes": r["notes"]}
            for r in rows if r["pnl"] is not None]


def backtest_fidelity(rows: Sequence[Mapping[str, Any]]) -> tuple[bool | None, list[str]]:
    """The producing harness's fidelity claim, read off the rows themselves.

    Conservative by construction: if ANY row was produced by a leg that
    declared ``faithful=False``, the whole sample is approximate — a set mixing
    complete and incomplete rows cannot be graded as complete. Returns
    ``(None, [])`` when no row carries a label, which is *unknown*, NOT
    *faithful*: legacy rows predate the label and must not be silently
    promoted to trusted (the `UNVERIFIED` ≠ `MEASURED` rule, one level up).
    """
    from ml.datasets.backtest_recorder import (
        R_COST_BASIS_AMBIGUOUS,
        R_COST_BASIS_GROSS,
        parse_backtest_notes,
    )

    seen_label = False
    approximate = False
    levers: list[str] = []
    for row in rows:
        parsed = parse_backtest_notes(row.get("notes"))
        # R-COST-BASIS (B6, 2026-08-20). A gross-R row has NOT had fees or
        # slippage deducted, so it is systematically optimistic — which is the
        # same kind of claim `fidelity=False` makes (this sample is not what
        # live would have produced) and belongs in the same verdict rather than
        # a parallel one nobody reads. `r_multiple` is folded in too: the
        # producer used a key that says neither net nor gross, so the sample
        # CANNOT be shown to be net, and "cannot be shown" is not "is".
        #
        # It sets `approximate` but is deliberately NOT appended to `levers`:
        # that list names EXIT LEVERS the harness omitted, and a cost basis is
        # not a lever. Putting it there would make the omitted-lever list
        # describe something it is not — the semantic substitution
        # (diagnostic-provenance sub-class A) this repo names by that number.
        basis = parsed.get("r_cost_basis")
        if basis in (R_COST_BASIS_GROSS, R_COST_BASIS_AMBIGUOUS):
            seen_label = True
            approximate = True
        fid = parsed.get("fidelity")
        if fid is None:
            continue
        seen_label = True
        if str(fid).lower() != "faithful":
            approximate = True
            for lever in parsed.get("omitted_levers") or []:
                if lever not in levers:
                    levers.append(lever)
    if not seen_label:
        return None, []
    return (not approximate), sorted(levers)


def _live_realized_r(live_db: str, strategy: str, symbol: str,
                     *, r_basis: str = DEFAULT_R_BASIS) -> list[float]:
    return [r["r"] for r in _live_rows(live_db, strategy, symbol, r_basis=r_basis)[0]]


def _backtest_realized_r(bt_db: str, strategy: str, symbol: str) -> list[float]:
    return [r["r"] for r in _backtest_rows(bt_db, strategy, symbol)]


def _legs_in(db: str, is_backtest: int) -> set[tuple[str, str]]:
    """Distinct (strategy_name, symbol) with resolved pnl in one DB. A DB error
    RAISES (never a silent empty set) — an empty trust map from a swallowed read
    error would read as a clean 'no overlapping legs', the false-negative the
    silent-empty guard exists to stop."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT strategy_name, symbol FROM trades "
            "WHERE COALESCE(is_backtest,0)=? AND strategy_name IS NOT NULL "
            "AND symbol IS NOT NULL AND pnl IS NOT NULL",
            (is_backtest,),
        ).fetchall()
    finally:
        con.close()
    return {(r[0], r[1]) for r in rows}


def _calibrate_leg(live_db: str, bt_db: str, strategy: str, symbol: str,
                   *, stratify: str = "none",
                   r_basis: str = DEFAULT_R_BASIS) -> dict[str, Any]:
    live, live_diag = _live_rows(live_db, strategy, symbol, r_basis=r_basis)
    bt = _backtest_rows(bt_db, strategy, symbol)
    # The producing harness's OWN fidelity claim, read off the rows. Passed
    # INTO the pure gate so a leg whose harness declared faithful=False can
    # never come back `calibrated` — the trust verdict and the fidelity label
    # are decided in one place, not reconciled by a reader afterwards.
    bt_faithful, bt_omitted = backtest_fidelity(bt)
    result = agreement([r["r"] for r in live], [r["r"] for r in bt],
                       harness_faithful=bt_faithful, omitted_levers=bt_omitted)
    result.update({"strategy": strategy, "symbol": symbol})
    # The output declares WHICH axis produced the numbers above it. A KS(R) with
    # no r_basis beside it is unreadable — the sign-proxy KS and the real-R KS
    # are different quantities under one label, which is the whole reason § 5b's
    # `drifts` verdicts were misread as a cost finding.
    result["live_r"] = live_diag
    # A leg where trusted rows EXIST but none carried a usable stop is not the
    # same finding as a leg with no live trades, and `agreement` cannot tell them
    # apart (both arrive as an empty sample → "live n=0 < floor"). Say so, so an
    # unmeasurable leg is never read as an untraded one.
    if (r_basis == "stop_distance" and live_diag["rows_trusted"] > 0
            and live_diag["rows_r_measured"] == 0):
        result["verdict"] = "insufficient-live"
        result["reason"] = (
            f"{live_diag['rows_trusted']} trusted live trade(s) exist but NONE was "
            "R-measurable (no stop / flat stop / missing size) — the leg is "
            "unmeasurable on the stop-distance axis, not untraded"
        )
    if stratify and stratify != "none":
        result["stratified"] = stratified_agreement(live, bt, key=stratify)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backtest-db", required=True)
    p.add_argument("--live-db", required=True)
    p.add_argument("--strategy", default=None, help="single-leg mode (with --symbol)")
    p.add_argument("--symbol", default=None)
    p.add_argument("--stratify", choices=["none", "direction", "year"], default="none",
                   help="also compute per-stratum agreement to separate a uniform "
                        "cost-model gap from a concentrated regime/small-sample bias.")
    p.add_argument("--r-basis", choices=list(R_BASES), default=DEFAULT_R_BASIS,
                   help="live-R axis. 'stop_distance' (default) is the real R "
                        "(pnl / |entry-stop|·qty·contract_value); 'sign_proxy' is the "
                        "legacy ±1 win/loss stand-in, kept only to reproduce the P0 "
                        "numbers — its KS(R) is an artifact of the ±1 point-mass, not "
                        "a fidelity signal.")
    p.add_argument("--trust-map", action="store_true",
                   help="run every (strategy,symbol) leg present in BOTH DBs and emit "
                        "a table — the full trust map (§ 5a).")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)

    if a.trust_map:
        legs = sorted(_legs_in(a.live_db, 0) & _legs_in(a.backtest_db, 1))
        rows = [_calibrate_leg(a.live_db, a.backtest_db, s, sym, stratify=a.stratify,
                               r_basis=a.r_basis)
                for (s, sym) in legs]
        result: dict[str, Any] = {"trust_map": rows, "n_legs": len(rows),
                                  "r_basis": a.r_basis, "verdict_counts": {}}
        for r in rows:
            v = r["verdict"]
            result["verdict_counts"][v] = result["verdict_counts"].get(v, 0) + 1
    else:
        if not a.strategy or not a.symbol:
            p.error("single-leg mode needs --strategy and --symbol (or use --trust-map)")
        result = _calibrate_leg(a.live_db, a.backtest_db, a.strategy, a.symbol,
                                stratify=a.stratify, r_basis=a.r_basis)

    out = json.dumps(result, indent=2)
    print(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(out)
        print(f"wrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
