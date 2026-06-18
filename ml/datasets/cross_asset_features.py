"""Cross-asset (peer-asset) conditioning features (S-CROSS-ASSET-PROBE, 2026-06-18).

The operator's framing: *"predicting what one asset will do based on how other
assets are performing … expanding the pool of indicators a strategy can look at
even for trading a specific asset."* This module is the **cheap probe** of that
idea — it derives, for a TARGET symbol, a fixed block of conditioning features
from one or two PEER symbols' OHLCV, so a regime/outcome head can be A/B'd with
vs without the block (does cross-asset information add edge before any live
wiring?).

It is the crypto-peer sibling of :mod:`ml.datasets.macro_features` (the MES macro
side-stream). Same architecture: **pure functions** compute per-bar feature rows
from already-sorted observations; the rows are written to a side-stream
``data.jsonl`` by ``scripts/ml/build_cross_asset.py`` and as-of joined into
``market_features`` via its optional ``cross_asset_path`` kwarg (``0.0`` when
omitted — every existing build is unchanged).

### Positional peer slots (fixed schema)

To keep the ``market_features`` schema fixed while staying reusable for any
target/peer combination, peers occupy **positional slots** ``peer1`` / ``peer2``
rather than hard-coded symbol names. The producer records the slot→symbol map in
its ``metadata.json`` (e.g. ``{peer1: BTCUSDT, peer2: SOLUSDT}`` for an ETH
target). A missing slot emits ``0.0`` for its columns (neutral), exactly like an
omitted macro side-stream.

### Per-peer feature block

For each peer slot, over a trailing (past-only) window:

- ``xa_<slot>_ret`` — peer's **contemporaneous** bar log-return (co-movement).
- ``xa_<slot>_ret_lag1`` — peer's **previous** bar log-return (the lead signal —
  "what BTC just did" as a predictor of the target's next move).
- ``xa_<slot>_vol`` — peer's rolling log-return vol over ``vol_window_n``.
- ``xa_<slot>_rel_strength`` — target cumulative return minus peer cumulative
  return over ``vol_window_n`` (relative momentum; >0 ⇒ target outperforming).
- ``xa_<slot>_beta`` — rolling OLS beta of target returns on peer returns over
  ``beta_window_n`` (how much of the target's move is "the market").
- ``xa_<slot>_beta_residual`` — ``ret_target_t − beta·ret_peer_t`` (the
  idiosyncratic, non-peer-explained move on the bar).

Plus one cross-sectional column:

- ``xa_breadth_up`` — fraction of present peers with a positive contemporaneous
  return (a crude "is the complex risk-on" breadth read).

### Cadence + leakage discipline

Unlike macro (daily series joined onto intraday bars, so the producer lags one
day), crypto peers are **same-cadence** as the target — a peer 1h bar closes at
the same wall-clock instant as the target 1h bar, so at the target's decision
time ``t`` the peer's bar-``t`` close is genuinely available. Contemporaneous
(lag-0) reads are therefore realistic, **not** leakage. Every feature reads only
peer/target bars at or before ``t``; the ``market_features`` forward label spans
``[t+1 .. t+forward_window_m]`` (strictly after ``t``), so the two windows never
overlap. ``None`` propagates and is converted to ``0.0`` (neutral) at emit time,
matching the funding/OI, microstructure, and macro families.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

# Number of positional peer slots the fixed schema reserves.
N_PEER_SLOTS: int = 2

_PER_PEER_SUFFIXES: tuple[str, ...] = (
    "ret",
    "ret_lag1",
    "vol",
    "rel_strength",
    "beta",
    "beta_residual",
)


def _peer_columns(slot: int) -> tuple[str, ...]:
    return tuple(f"xa_peer{slot}_{suf}" for suf in _PER_PEER_SUFFIXES)


# The fixed cross-asset feature columns this family contributes to
# `market_features`. Single source of truth shared by the builder schema, the
# side-stream producer, and the tests.
CROSS_ASSET_FEATURE_COLUMNS: tuple[str, ...] = tuple(
    col for slot in range(1, N_PEER_SLOTS + 1) for col in _peer_columns(slot)
) + ("xa_breadth_up",)


def _finite_or_zero(value: float | None) -> float:
    """``None`` / non-finite → ``0.0`` (neutral) — the feature-emit shape."""
    if value is None or not math.isfinite(value):
        return 0.0
    return float(value)


def log_returns(closes: Sequence[float | None]) -> list[float | None]:
    """Bar-to-bar log returns; ``None`` at index 0 and across non-positive closes."""
    out: list[float | None] = [None] * len(closes)
    for i in range(1, len(closes)):
        prev, curr = closes[i - 1], closes[i]
        if prev is None or curr is None or prev <= 0 or curr <= 0:
            continue
        out[i] = math.log(curr / prev)
    return out


def rolling_vol(window: Sequence[float | None], *, min_n: int = 2) -> float | None:
    """Population stdev of the clean returns in ``window`` (past-only)."""
    vals = [v for v in window if v is not None]
    if len(vals) < min_n:
        return None
    return statistics.pstdev(vals)


def rel_strength(
    target_w: Sequence[float | None], peer_w: Sequence[float | None]
) -> float | None:
    """Target cumulative return minus peer cumulative return over the window.

    ``sum(target rets) − sum(peer rets)`` — relative momentum; ``>0`` means the
    target out-ran the peer over the window. ``None`` when either side has no
    usable returns.
    """
    t = [v for v in target_w if v is not None]
    p = [v for v in peer_w if v is not None]
    if not t or not p:
        return None
    return math.fsum(t) - math.fsum(p)


def rolling_beta(
    target_w: Sequence[float | None], peer_w: Sequence[float | None], *, min_n: int = 5
) -> float | None:
    """OLS beta of target on peer over the window: ``cov(t,p) / var(p)``.

    Pairs are formed only where BOTH series are non-``None`` at the same index.
    ``None`` when too few pairs or the peer variance is ~zero.
    """
    pairs = [
        (t, p)
        for t, p in zip(target_w, peer_w)
        if t is not None and p is not None
    ]
    if len(pairs) < min_n:
        return None
    ts = [t for t, _ in pairs]
    ps = [p for _, p in pairs]
    pmean = statistics.fmean(ps)
    tmean = statistics.fmean(ts)
    var_p = math.fsum((p - pmean) ** 2 for p in ps)
    if var_p <= 1e-18:
        return None
    cov = math.fsum((t - tmean) * (p - pmean) for t, p in pairs)
    return cov / var_p


def _aligned_return_series(
    bar_ts: Sequence[str], peer_rows: Sequence[Mapping[str, Any]]
) -> list[float | None]:
    """Peer log-returns aligned exactly onto ``bar_ts`` by timestamp.

    Same-cadence crypto bars share a timestamp grid, so this is an exact ts→ts
    map (a missing peer bar at a given ts → ``None`` there). Both inputs are
    assumed ascending; the peer return at ts ``T`` is computed from the peer's
    own previous bar (so it is the close-to-close return ending at ``T``).
    """
    peer_sorted = sorted(peer_rows, key=lambda r: str(r.get("ts", "")))
    peer_ts = [str(r.get("ts", "")) for r in peer_sorted]
    peer_close = [
        float(r["close"]) if r.get("close") is not None else None
        for r in peer_sorted
    ]
    peer_ret = log_returns(peer_close)
    ret_by_ts = dict(zip(peer_ts, peer_ret))
    return [ret_by_ts.get(t) for t in bar_ts]


def compute_cross_asset_feature_rows(
    target_rows: Sequence[Mapping[str, Any]],
    peer_rows_by_slot: Sequence[Sequence[Mapping[str, Any]]],
    *,
    vol_window_n: int = 20,
    beta_window_n: int = 50,
) -> list[dict[str, Any]]:
    """Per-bar cross-asset feature rows for a target, keyed at the target's ts.

    ``target_rows`` / each ``peer_rows_by_slot`` entry are ``market_raw``-shaped
    (``{ts, close, ...}``). Up to :data:`N_PEER_SLOTS` peer series are read in
    order; extra peers are ignored, absent slots emit ``0.0``. Every column is
    past-only (see module docstring) so the rows are leakage-safe to as-of join.
    """
    if vol_window_n < 2:
        raise ValueError(f"vol_window_n must be >= 2; got {vol_window_n}")
    if beta_window_n < 2:
        raise ValueError(f"beta_window_n must be >= 2; got {beta_window_n}")

    tgt = sorted(target_rows, key=lambda r: str(r.get("ts", "")))
    n = len(tgt)
    if n == 0:
        return []
    bar_ts = [str(r.get("ts", "")) for r in tgt]
    tgt_close = [
        float(r["close"]) if r.get("close") is not None else None for r in tgt
    ]
    tgt_ret = log_returns(tgt_close)

    # Peer returns aligned onto the target grid, one list per slot (padded with
    # all-None for absent slots so the column block always emits 0.0 there).
    peer_ret_by_slot: list[list[float | None]] = []
    for slot in range(N_PEER_SLOTS):
        if slot < len(peer_rows_by_slot) and peer_rows_by_slot[slot]:
            peer_ret_by_slot.append(
                _aligned_return_series(bar_ts, peer_rows_by_slot[slot])
            )
        else:
            peer_ret_by_slot.append([None] * n)

    out_rows: list[dict[str, Any]] = []
    for i in range(n):
        row: dict[str, Any] = {"ts": bar_ts[i]}
        breadth_present = 0
        breadth_up = 0
        vs = max(0, i - vol_window_n + 1)
        bs = max(0, i - beta_window_n + 1)
        tgt_vol_w = tgt_ret[vs : i + 1]
        tgt_beta_w = tgt_ret[bs : i + 1]
        for slot in range(N_PEER_SLOTS):
            pret = peer_ret_by_slot[slot]
            cols = _peer_columns(slot + 1)
            ret_t = pret[i]
            ret_lag1 = pret[i - 1] if i - 1 >= 0 else None
            vol = rolling_vol(pret[vs : i + 1])
            rs = rel_strength(tgt_vol_w, pret[vs : i + 1])
            beta = rolling_beta(tgt_beta_w, pret[bs : i + 1])
            beta_resid = (
                tgt_ret[i] - beta * ret_t
                if (beta is not None and ret_t is not None and tgt_ret[i] is not None)
                else None
            )
            row[cols[0]] = _finite_or_zero(ret_t)
            row[cols[1]] = _finite_or_zero(ret_lag1)
            row[cols[2]] = _finite_or_zero(vol)
            row[cols[3]] = _finite_or_zero(rs)
            row[cols[4]] = _finite_or_zero(beta)
            row[cols[5]] = _finite_or_zero(beta_resid)
            if ret_t is not None:
                breadth_present += 1
                if ret_t > 0:
                    breadth_up += 1
        row["xa_breadth_up"] = (
            breadth_up / breadth_present if breadth_present else 0.0
        )
        out_rows.append(row)
    return out_rows
