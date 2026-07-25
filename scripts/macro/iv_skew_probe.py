#!/usr/bin/env python3
"""M31 Track B — option-chain IV-skew feature core (credential-free, offline).

This is the **pure feature layer** for the implied-vol *skew* input class: given a
normalized option-chain snapshot for one underlying, compute the classic skew /
smile / term-structure descriptors that a signal would be built from. It is the
reusable heart that the Schwab auth-client (a credential-gated follow-up) feeds —
built and unit-tested FIRST, before the credential lands, exactly as Track A built
its pure IC/feature functions before its network wrapper.

**Features (per underlying snapshot):**
  - ``atm_iv``       — at-the-money implied vol (IV at the strike nearest spot).
  - ``rr25``         — 25-delta RISK REVERSAL: IV(+25Δ call) − IV(−25Δ put). The
    canonical skew measure; **negative** = puts richer than calls (crash-hedging
    demand / fear priced into the downside).
  - ``bf25``         — 25-delta BUTTERFLY: mean(25Δ call, 25Δ put) IV − ATM IV.
    Smile curvature (how much the wings sit above the ATM).
  - ``skew_slope``   — OLS slope of IV vs log-moneyness across the front expiry.
    Negative = downside strikes carry higher IV (the equity "smirk").
  - ``term_ratio``   — far-expiry ATM IV / near-expiry ATM IV (per-underlying IV
    term structure — the direct analogue of Track A's robust VIX3M/VIX ``vix_term``,
    but reconstructible for ANY optioned underlying, not just the ones with a FRED
    3-month sibling — which is the whole reason Track B exists).

**Why this is a separate track, and why it can't be IC-graded here yet.** Option
chains are **point-in-time** — a snapshot carries no history. So unlike Track A
(gradeable off free FRED history *now*), these features can only be turned into an
S2/S3/walk-forward verdict once a **soak** accrues daily snapshots + forward
underlying returns, OR a **historical options dataset** is sourced. That data feed
is the operator-gated Schwab step. This module is deliberately scoped to the pure,
fully-testable feature math so it is correct and ready the moment the feed exists.

Import-pure: NO network, NO credential, NO ``src.*`` import, NO order path. Pure
stdlib math over a plain list of chain rows (the same no-numpy style as Track A).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Optional

# A normalized chain row is a plain dict:
#   {"expiration": "2026-08-15", "dte": 21, "type": "call"|"put",
#    "strike": 5000.0, "iv": 0.183, "delta": 0.31}
# `dte` = days-to-expiration (int); `delta` is signed (puts negative). The Schwab
# `/marketdata/v1/chains` response maps onto this 1:1 (per-contract `volatility`,
# `delta`, `strikePrice`, expiration date) — the credential-gated adapter's only
# job is to flatten that payload into these rows.


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def _finite(x) -> Optional[float]:
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _clean_rows(rows: list) -> list:
    """Keep only rows with a finite strike + iv + delta and a call/put type."""
    out = []
    for r in rows:
        strike, iv, delta = _finite(r.get("strike")), _finite(r.get("iv")), _finite(r.get("delta"))
        typ = str(r.get("type", "")).lower()
        if strike is None or iv is None or delta is None or typ not in ("call", "put"):
            continue
        if iv <= 0 or strike <= 0:
            continue
        out.append({"expiration": r.get("expiration"), "dte": r.get("dte"),
                    "type": typ, "strike": strike, "iv": iv, "delta": delta})
    return out


def group_by_expiration(rows: list) -> dict:
    """``{expiration: [rows...]}`` preserving each expiry's rows."""
    out: dict = {}
    for r in rows:
        out.setdefault(r["expiration"], []).append(r)
    return out


def _nearest(rows: list, key, target: float):
    """Row minimizing ``|key(row) − target|``; ``None`` for an empty list."""
    best, best_d = None, None
    for r in rows:
        d = abs(key(r) - target)
        if best_d is None or d < best_d:
            best, best_d = r, d
    return best


def atm_iv(expiry_rows: list, underlying: float) -> Optional[float]:
    """IV at the strike nearest spot (mean of the call & put leg when both exist)."""
    if underlying is None or underlying <= 0 or not expiry_rows:
        return None
    strikes = sorted({r["strike"] for r in expiry_rows}, key=lambda s: abs(s - underlying))
    if not strikes:
        return None
    k = strikes[0]
    legs = [r["iv"] for r in expiry_rows if r["strike"] == k]
    return sum(legs) / len(legs) if legs else None


def risk_reversal(expiry_rows: list, target_delta: float = 0.25) -> Optional[float]:
    """25Δ risk reversal: IV(+Δ call) − IV(−Δ put). ``None`` if a wing is absent."""
    calls = [r for r in expiry_rows if r["type"] == "call"]
    puts = [r for r in expiry_rows if r["type"] == "put"]
    if not calls or not puts:
        return None
    c = _nearest(calls, lambda r: r["delta"], target_delta)
    p = _nearest(puts, lambda r: r["delta"], -target_delta)
    if c is None or p is None:
        return None
    return c["iv"] - p["iv"]


def butterfly(expiry_rows: list, atm: Optional[float], target_delta: float = 0.25) -> Optional[float]:
    """25Δ butterfly: mean(25Δ call, 25Δ put) IV − ATM IV (smile curvature)."""
    if atm is None:
        return None
    calls = [r for r in expiry_rows if r["type"] == "call"]
    puts = [r for r in expiry_rows if r["type"] == "put"]
    if not calls or not puts:
        return None
    c = _nearest(calls, lambda r: r["delta"], target_delta)
    p = _nearest(puts, lambda r: r["delta"], -target_delta)
    if c is None or p is None:
        return None
    return (c["iv"] + p["iv"]) / 2.0 - atm


def _ols_slope(xs: list, ys: list) -> Optional[float]:
    """Least-squares slope of ys on xs; ``None`` for < 3 points or zero x-variance."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx


def skew_slope(expiry_rows: list, underlying: float) -> Optional[float]:
    """OLS slope of IV vs log-moneyness ``log(strike/underlying)`` across the expiry.

    Negative = lower strikes carry higher IV (the equity downside smirk)."""
    if underlying is None or underlying <= 0:
        return None
    xs, ys = [], []
    for r in expiry_rows:
        m = math.log(r["strike"] / underlying)
        xs.append(m)
        ys.append(r["iv"])
    return _ols_slope(xs, ys)


def _front_and_back(by_exp: dict):
    """Return (front_expiry_rows, back_expiry_rows) by ascending ``dte``.

    Falls back to sorting on the expiration string when ``dte`` is absent."""
    def _key(item):
        rows = item[1]
        dtes = [r["dte"] for r in rows if isinstance(r.get("dte"), (int, float))]
        return (0, min(dtes)) if dtes else (1, str(item[0]))

    items = sorted(by_exp.items(), key=_key)
    if not items:
        return None, None
    return items[0][1], items[-1][1]


def iv_term_ratio(by_exp: dict, underlying: float) -> Optional[float]:
    """far-ATM-IV / near-ATM-IV across expiries (>1 = contango). Needs ≥ 2 expiries."""
    if len(by_exp) < 2:
        return None
    front, back = _front_and_back(by_exp)
    near, far = atm_iv(front, underlying), atm_iv(back, underlying)
    if near is None or far is None or near <= 0:
        return None
    return far / near


def skew_features(rows: list, underlying: float, *, target_delta: float = 0.25) -> dict:
    """Full skew-feature dict for one underlying's chain snapshot.

    All values are ``None`` when uncomputable (too few strikes / one-sided wing /
    single expiry) — a consumer renders null as "—", never 0."""
    clean = _clean_rows(rows)
    by_exp = group_by_expiration(clean)
    front, _ = _front_and_back(by_exp)
    front = front or []
    atm = atm_iv(front, underlying)
    return {
        "underlying": _finite(underlying),
        "n_rows": len(clean),
        "n_expirations": len(by_exp),
        "atm_iv": atm,
        "rr25": risk_reversal(front, target_delta),
        "bf25": butterfly(front, atm, target_delta),
        "skew_slope": skew_slope(front, underlying),
        "term_ratio": iv_term_ratio(by_exp, underlying),
    }


# ---------------------------------------------------------------------------
# offline CLI (reads a normalized snapshot JSON — NO live fetch)
# ---------------------------------------------------------------------------

def _fmt(v) -> str:
    return "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="M31 Track B — option-chain IV-skew feature core (offline; no credential)")
    ap.add_argument("--from-json", help="path to a normalized snapshot JSON "
                    '{"underlying": <price>, "rows": [{expiration,dte,type,strike,iv,delta}, ...]}; '
                    "omit to read from stdin")
    ap.add_argument("--target-delta", type=float, default=0.25)
    args = ap.parse_args()
    raw = open(args.from_json).read() if args.from_json else sys.stdin.read()
    if not raw.strip():
        print("M31 Track B — no snapshot provided.\n"
              "This is the credential-free feature core; live chains come from the\n"
              "operator-gated Schwab adapter. Pipe a normalized snapshot JSON to grade one.")
        return 0
    snap = json.loads(raw)
    feats = skew_features(snap.get("rows", []), snap.get("underlying"),
                          target_delta=args.target_delta)
    print("M31 Track B — IV-skew features (single point-in-time snapshot)")
    print("=" * 64)
    for k, v in feats.items():
        print(f"  {k:>16} = {_fmt(v)}")
    print("\nrr25 < 0 ⇒ puts richer (downside fear); term_ratio > 1 ⇒ IV contango.")
    print("NOTE: one snapshot is not gradeable — an IC verdict needs a soak of daily\n"
          "snapshots + forward underlying returns (the Schwab-fed follow-up).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
