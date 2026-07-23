"""M28 P4 — the point-in-time thesis replay driver (no-lookahead by construction).

The bridge between the stored history and the P4 scoring core
(:mod:`thesis_backtest`): walk a set of rebalance dates, and at each date form
theses from **only the valuation snapshots known as-of that date**, then look up
the forward price at the calendar exit to produce the scored entries
:func:`~.thesis_backtest.run_thesis_backtest` consumes.

**Point-in-time integrity is the #1 correctness rule** (design §8): every read is
a strict past-only as-of filter (``observed_at <= as_of``) and a revision is a
new snapshot line, never an overwrite — so the replay can never see a future or
revised value. The signature failure mode of macro/value backtesting is
training/testing on revised data; this module makes that impossible by
construction: it forms theses ONLY from :func:`as_of_snapshot_rows` output.

Pure: the snapshot history + a ``price_at(symbol, date)`` lookup are **injected**
(the live path passes the valuation store + a candle reader; tests pass dicts),
so it is fully unit-testable offline with no I/O, no clock, no order path.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from .thesis_tick import form_tick_theses


def as_of_snapshot_rows(
    records: Iterable[Mapping[str, Any]], as_of: str, *, time_key: str = "observed_at"
) -> list[dict]:
    """The latest snapshot row per ``(symbol, metric)`` whose ``observed_at`` is
    ``<= as_of`` — the strict past-only view (no lookahead, no revised data).

    A revision is a new line with a later ``observed_at``; this returns the most
    recent one that was already known at ``as_of``. Rows missing the key or the
    time field are dropped. ISO-8601 timestamps compare lexicographically =
    chronologically (the codebase convention)."""
    latest: dict[tuple, dict] = {}
    for row in records or []:
        symbol, metric = row.get("symbol"), row.get("metric")
        ts = row.get(time_key)
        if symbol is None or metric is None or ts is None:
            continue
        if str(ts) > str(as_of):     # strictly in the future as-of this date → excluded
            continue
        key = (symbol, metric)
        prev = latest.get(key)
        if prev is None or str(ts) > str(prev.get(time_key, "")):
            latest[key] = dict(row)
    return list(latest.values())


def add_days_iso(date_iso: str, days: float) -> str:
    """``date_iso`` + ``days`` → an ISO-8601 ``…Z`` timestamp. Accepts a date
    (``2026-07-23``) or a full timestamp (``2026-07-23T00:00:00Z``)."""
    s = str(date_iso).strip().replace("Z", "+00:00")
    try:
        base = _dt.datetime.fromisoformat(s)
    except ValueError:
        base = _dt.datetime.fromisoformat(s[:10])
    if base.tzinfo is None:
        base = base.replace(tzinfo=_dt.timezone.utc)
    out = base + _dt.timedelta(days=float(days))
    return out.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_replay_entries(
    records: Sequence[Mapping[str, Any]],
    price_at: Callable[[str, str], Optional[float]],
    *,
    rebalance_dates: Sequence[str],
    cfg: Mapping[str, Any],
    horizon_days: float,
) -> list[dict]:
    """Produce the point-in-time scored entries for :func:`run_thesis_backtest`.

    For each rebalance date: form theses from the as-of snapshot rows (the S1
    former via :func:`form_tick_theses`), then for each thesis look up the entry
    price at the date and the exit price ``horizon_days`` later. An entry is
    emitted only when **both** prices resolve (a missing price drops that thesis —
    never a fabricated fill). Each entry:
    ``{thesis_id, symbol, conviction, direction, entry_price, exit_price,
    as_of, exit_at, hold_days}``."""
    entries: list[dict] = []
    for as_of in rebalance_dates or []:
        rows = as_of_snapshot_rows(records, as_of)
        id_prefix = str(as_of).replace("-", "").replace(":", "")[:12]
        theses = form_tick_theses(rows, cfg=cfg, now_iso=str(as_of), id_prefix=id_prefix)
        exit_at = add_days_iso(as_of, horizon_days)
        for t in theses:
            symbol = (t.instrument or {}).get("symbol")
            if not symbol:
                continue
            entry_price = price_at(symbol, str(as_of))
            exit_price = price_at(symbol, exit_at)
            if entry_price is None or exit_price is None:
                continue
            entries.append({
                "thesis_id": t.thesis_id,
                "symbol": symbol,
                "conviction": t.thesis_conviction,
                "direction": t.direction,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "as_of": str(as_of),
                "exit_at": exit_at,
                "hold_days": float(horizon_days),
            })
    return entries
