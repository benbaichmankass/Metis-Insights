"""Roll-adjusted continuous futures series builder (offline research tooling).

WHY THIS EXISTS
===============
The IBKR historical pull (`ml/datasets/adapters/ibkr_offvm.py`) pages over
DATED contract months and stitches them into one `market_raw` stream, deduped
by timestamp — a **spliced, NOT back-adjusted** series. At every contract roll
the price level gaps (contango/backwardation), because a September contract and
a December contract trade at genuinely different absolute prices for the same
underlying at the same instant.

For **mean-reversion / pullback** strategies that gap is largely harmless (they
enter on pullbacks into a local range, not on the gap). For **breakout / trend**
strategies it is corrupting: a Donchian breakout reads the roll gap as a
breakout and "rides" it, manufacturing a fake edge. That is exactly why the
2026-07-07 native-MGC-1h `mgc_trend_1h` backtest scored a surprising +57.8R that
did NOT reverse the demote (the demote was validated on continuous GC=F/spot);
see `docs/research/ib-metals-native-backtest-2026-07-07.md`.

To backtest a breakout/trend cell honestly on native futures we need a
**back-adjusted continuous** series: splice the dated contracts AND remove the
roll gaps so the series moves only when the market moves.

WHAT THIS MODULE DOES
=====================
Pure, dependency-free (stdlib-only) functions that take **per-contract** OHLCV
bar series (each tagged with its contract month, WITH the cross-contract
overlaps preserved — the overlap is where the roll offset is measured) and emit
a single continuous series with the roll gaps removed:

  - `panama` / additive back-adjustment (default): shift each older contract's
    segment by the cumulative forward price gap so absolute price levels + ATR
    stay consistent near the front (the un-adjusted, real-price end). Best for
    the repo's price-and-ATR backtest harnesses.
  - `ratio` back-adjustment: multiply older segments by the cumulative price
    ratio so percentage returns are preserved exactly (no negative prices),
    at the cost of the older absolute levels no longer matching the tape.

The output rows are the canonical `market_raw` 9-key shape (see
`adapters/base.py::CANONICAL_COLUMNS`) with an adjusted `symbol` token (e.g.
`MGC.c`) and `source="ibkr_continuous"`, so the existing backtest harnesses
(`scripts/research/backtest_trend.py`, `scripts/backtest_pullback.py`) read the
continuous series with **no change**.

This module never opens a socket and never touches the live order path — it is
Tier-1 offline data tooling. The producer of per-contract input (extending the
IBKR pull to emit per-contract shards) is documented as the next increment in
`docs/research/roll-adjusted-continuous-futures-DESIGN.md`.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

# Reuse the canonical output contract so the continuous shard is a drop-in
# `market_raw` file for the backtest harnesses.
from .adapters.base import CANONICAL_COLUMNS

_OHLC = ("open", "high", "low", "close")

# Methods a caller may request. `panama` == additive.
METHODS = ("panama", "ratio", "none")


class ContinuousBuildError(ValueError):
    """Raised on structurally-invalid per-contract input."""


def _bar_ts(bar: Mapping[str, Any]) -> str:
    ts = bar.get("ts")
    if not isinstance(ts, str) or not ts:
        raise ContinuousBuildError(f"bar missing string 'ts': {bar!r}")
    return ts


def _bars_by_ts(bars: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Index one contract's bars by ts (last write wins on a dup ts)."""
    return {_bar_ts(b): b for b in bars}


def _roll_offset(
    near: Mapping[str, Mapping[str, Any]],
    far: Mapping[str, Mapping[str, Any]],
    method: str,
) -> tuple[str | None, float]:
    """Pick the handover ts + the gap/ratio between a near and a far contract.

    The roll point is the LAST timestamp both contracts share a bar (the
    handover just before the near contract's data runs out). The offset is
    measured there: `far.close - near.close` (additive) or `far.close /
    near.close` (ratio). Returns `(roll_ts, offset)`; `roll_ts` is None when the
    two contracts never overlap (a data gap) — the caller then treats the far
    contract's first bar as the boundary and applies a no-op offset (0.0 add /
    1.0 ratio), degrading honestly rather than fabricating an adjustment.
    """
    common = sorted(set(near) & set(far))
    if not common:
        return None, (1.0 if method == "ratio" else 0.0)
    roll_ts = common[-1]
    if method == "none":
        # Plain splice: keep the handover boundary (for dedup) but remove no gap.
        return roll_ts, 0.0
    near_close = float(near[roll_ts]["close"])
    far_close = float(far[roll_ts]["close"])
    if method == "ratio":
        if near_close == 0.0:
            return roll_ts, 1.0
        return roll_ts, far_close / near_close
    return roll_ts, far_close - near_close


def _apply(bar: Mapping[str, Any], offset: float, method: str) -> dict[str, float]:
    """Return adjusted OHLC for a bar under the cumulative offset."""
    out: dict[str, float] = {}
    for k in _OHLC:
        v = float(bar[k])
        out[k] = v * offset if method == "ratio" else v + offset
    return out


def build_continuous(
    contracts: Iterable[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    method: str = "panama",
    out_symbol: str | None = None,
    source: str = "ibkr_continuous",
) -> list[dict[str, Any]]:
    """Back-adjust per-contract OHLCV into one continuous `market_raw` series.

    `contracts` is an iterable of `{"month": "YYYYMM"|"YYYYMMDD", "bars": [...]}`
    where each bar carries at least `ts`/`open`/`high`/`low`/`close`/`volume`.
    Contracts are ordered by `month` ascending (oldest → front); the FRONT
    (newest) contract is the anchor and keeps its real prices (offset 0/×1),
    each older segment shifted by the cumulative forward roll gap.

    Returns canonical `market_raw` rows (newest contract un-adjusted), ascending
    by ts, one row per continuous timestamp:
      - segment boundaries are the per-pair roll timestamps (last common bar);
      - the near contract supplies bars up to & including its roll_ts, the far
        contract everything after — so there are no duplicate timestamps.

    `method`: `panama` (additive, default) / `ratio` / `none` (splice only, no
    gap removal — equivalent to today's adapter output, useful as an A/B arm).
    """
    if method not in METHODS:
        raise ContinuousBuildError(f"unknown method {method!r}; known: {METHODS}")

    cs = [c for c in contracts if c.get("bars")]
    # Order oldest → newest by the contract month/expiry token.
    cs.sort(key=lambda c: str(c.get("month", "")))
    if not cs:
        return []

    indexed = [_bars_by_ts(c["bars"]) for c in cs]

    # roll_ts[i] is the handover from contract i to i+1 (len == n-1). A None
    # entry means the pair never overlapped; the boundary falls back to the far
    # contract's first ts.
    roll_ts: list[str | None] = []
    # pair_offset[i] is the raw near→far gap/ratio at roll_ts[i].
    pair_offset: list[float] = []
    for i in range(len(cs) - 1):
        rt, off = _roll_offset(indexed[i], indexed[i + 1], method)
        roll_ts.append(rt)
        pair_offset.append(off)

    # Cumulative adjustment per contract segment, anchored so the FRONT
    # (newest) contract is un-adjusted. Walk back from the front:
    #   adj[n-1] = identity; adj[i] = combine(adj[i+1], pair_offset[i]).
    n = len(cs)
    cum: list[float] = [1.0 if method == "ratio" else 0.0] * n
    for i in range(n - 2, -1, -1):
        if method == "ratio":
            cum[i] = cum[i + 1] * pair_offset[i]
        else:
            cum[i] = cum[i + 1] + pair_offset[i]

    # Assemble segments. Segment i uses contract i's bars with ts in
    # (prev_boundary, this_boundary]; the front contract takes everything after
    # its predecessor's roll.
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    prev_boundary: str | None = None  # exclusive lower bound (a ts string)
    for i, c in enumerate(cs):
        # Upper bound (inclusive) for this segment.
        if i < n - 1:
            upper = roll_ts[i]
            if upper is None:
                # No overlap with the next contract: hand over at the next
                # contract's first bar (exclusive), i.e. keep this contract's
                # bars strictly before that first ts.
                nxt_first = min(indexed[i + 1]) if indexed[i + 1] else None
                upper = None
                hard_upper = nxt_first
            else:
                hard_upper = None
        else:
            upper = None       # front contract: no upper bound
            hard_upper = None

        for ts in sorted(indexed[i]):
            if prev_boundary is not None and ts <= prev_boundary:
                continue
            if upper is not None and ts > upper:
                continue
            if hard_upper is not None and ts >= hard_upper:
                continue
            if ts in seen:
                continue
            bar = indexed[i][ts]
            adj = _apply(bar, cum[i], method)
            rows.append({
                "ts": ts,
                "symbol": out_symbol or f"{symbol}.c",
                "timeframe": timeframe,
                "open": adj["open"],
                "high": adj["high"],
                "low": adj["low"],
                "close": adj["close"],
                "volume": float(bar.get("volume") or 0.0),
                "source": source,
            })
            seen.add(ts)
        # Advance the lower bound to this segment's handover.
        prev_boundary = roll_ts[i] if (i < n - 1 and roll_ts[i] is not None) else prev_boundary

    rows.sort(key=lambda r: r["ts"])
    # Sanity: every row must carry exactly the canonical columns.
    for r in rows:
        if set(r) != set(CANONICAL_COLUMNS):
            raise ContinuousBuildError(
                f"continuous row has non-canonical columns: {sorted(r)}"
            )
    return rows


def group_bars_by_contract(
    tagged_bars: Iterable[Mapping[str, Any]],
    *,
    contract_key: str = "contract",
) -> list[dict[str, Any]]:
    """Group a flat stream of contract-tagged bars into per-contract series.

    The per-contract IBKR pull (increment 2) emits one flat jsonl where each bar
    carries its `contract` month; this reshapes it into the
    `[{"month","bars":[...]}]` structure `build_continuous` expects, each
    contract's bars sorted ascending by ts. A bar with no contract tag is
    dropped (it cannot be roll-attributed) — the count is the caller's to log.
    """
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for b in tagged_bars:
        month = b.get(contract_key)
        if not month:
            continue
        groups.setdefault(str(month), []).append(b)
    out: list[dict[str, Any]] = []
    for month, bars in groups.items():
        out.append({"month": month, "bars": sorted(bars, key=_bar_ts)})
    out.sort(key=lambda c: c["month"])
    return out
