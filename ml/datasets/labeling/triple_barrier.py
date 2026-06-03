"""Triple-barrier labeling + CUSUM event sampling (de Prado, AFML Ch. 2–3).

Pure stdlib — no numpy/pandas — so it unit-tests in the sandbox and composes
into the ``setup_candidates`` dataset family (S-MLOPT-S5) and the meta-labeling
model (S-MLOPT-S6).

**Triple-barrier** turns a price path into a label by racing three barriers
from an entry bar:

  - an **upper** barrier (take-profit) at ``entry * (1 + pt_mult * vol)`` for a
    long (mirrored for a short),
  - a **lower** barrier (stop-loss) at ``entry * (1 - sl_mult * vol)``,
  - a **vertical** barrier (timeout) ``max_holding`` bars forward.

Whichever is hit first sets the label: ``+1`` (take-profit), ``-1`` (stop-loss),
or — at the timeout — the sign of the realised return (``0`` only on an exact
flat close). Barriers are sized by **local volatility** at the signal bar so a
quiet market and a fast market get comparably-reachable barriers.

**Realistic-fill discipline** (the domain-shift caveat in the roadmap): a fill
is never assumed more favourable than the bar allows. Touches are detected on
the bar **high/low**, not the close; when a single bar straddles *both*
barriers we resolve to the **stop-loss** (the conservative, adverse-first
assumption — never claim the profit when the bar could have stopped you out
first); an optional ``slippage`` fraction is charged against every fill. These
keep synthetic-barrier labels from being systematically optimistic vs live
fills — but they are still synthetic, so a model trained on them must be
**evaluated on real live trades** (enforced by the ``setup_candidates`` family's
``is_live_trade`` split column).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class BarrierConfig:
    """Barrier geometry for one labeling pass.

    ``pt_mult`` / ``sl_mult`` multiply the signal-bar volatility to size the
    take-profit / stop-loss distances (in return space). ``max_holding`` is the
    vertical-barrier horizon in bars. ``slippage`` is a fractional cost charged
    against every fill (entry + exit), so a round trip pays ``2 * slippage``.
    """

    pt_mult: float = 1.0
    sl_mult: float = 1.0
    max_holding: int = 10
    slippage: float = 0.0

    def __post_init__(self) -> None:
        if self.pt_mult <= 0:
            raise ValueError(f"pt_mult must be > 0; got {self.pt_mult}")
        if self.sl_mult <= 0:
            raise ValueError(f"sl_mult must be > 0; got {self.sl_mult}")
        if self.max_holding < 1:
            raise ValueError(f"max_holding must be >= 1; got {self.max_holding}")
        if self.slippage < 0:
            raise ValueError(f"slippage must be >= 0; got {self.slippage}")


@dataclass(frozen=True)
class BarrierOutcome:
    """Resolved label for one event.

    ``barrier`` ∈ {``"tp"``, ``"sl"``, ``"timeout"``}. ``label`` is ``+1`` on a
    take-profit, ``-1`` on a stop-loss, and at the timeout the sign of the net
    return (``0`` only on an exact flat). ``ret`` is the **direction-signed**
    return net of slippage (positive = the trade made money). ``r_multiple`` is
    ``ret`` normalised by the stop distance (``sl_mult * vol``) — risk units,
    so ``+1`` ≈ "made one R". ``holding_bars`` is how many bars the trade was
    open (entry bar inclusive → exit bar).
    """

    barrier: str
    label: int
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    ret: float
    r_multiple: float
    holding_bars: int

    def to_dict(self) -> dict:
        return {
            "barrier": self.barrier,
            "label": self.label,
            "entry_idx": self.entry_idx,
            "exit_idx": self.exit_idx,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "ret": self.ret,
            "r_multiple": self.r_multiple,
            "holding_bars": self.holding_bars,
        }


def cusum_events(values: Sequence[float], threshold: float) -> list[tuple[int, int]]:
    """de Prado symmetric CUSUM filter — sample events from a series.

    Accumulates positive and negative run-sums of first differences of
    ``values`` (use ``log(close)`` for a returns-driven filter); when either run
    crosses ``threshold`` it records an event at that index and resets that run.
    This samples bars where something *happened* (a sustained move) instead of
    labeling every bar, which de-clusters the dataset and is the canonical event
    sampler that pairs with triple-barrier labeling.

    Returns a list of ``(index, side)`` where ``side`` is ``+1`` for an
    up-breach (a long-momentum candidate) and ``-1`` for a down-breach (short).
    """
    if threshold <= 0:
        raise ValueError(f"threshold must be > 0; got {threshold}")
    events: list[tuple[int, int]] = []
    s_pos = 0.0
    s_neg = 0.0
    for i in range(1, len(values)):
        diff = float(values[i]) - float(values[i - 1])
        s_pos = max(0.0, s_pos + diff)
        s_neg = min(0.0, s_neg + diff)
        if s_pos >= threshold:
            s_pos = 0.0
            s_neg = 0.0
            events.append((i, 1))
        elif s_neg <= -threshold:
            s_pos = 0.0
            s_neg = 0.0
            events.append((i, -1))
    return events


def label_event(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    entry_idx: int,
    entry_price: float,
    direction: int,
    vol: float,
    config: BarrierConfig,
) -> BarrierOutcome | None:
    """Race the three barriers from ``entry_idx`` and return the outcome.

    ``entry_idx`` is the bar the position is **already open on** (the caller
    enters at the post-signal bar's open, so no signal-bar look-ahead). The scan
    runs over bars ``[entry_idx, entry_idx + max_holding]`` (clamped to the
    series), checking the **low** for a long's stop / **high** for its target
    (mirrored for a short). On a bar that straddles both barriers we resolve to
    the stop-loss (adverse-first). If neither barrier is touched by the horizon,
    the trade exits at the horizon bar's close (the vertical barrier).

    Returns ``None`` when the event can't be labeled (non-positive ``vol`` or
    price, ``direction`` not ±1, or no forward bar exists).
    """
    if direction not in (1, -1):
        raise ValueError(f"direction must be +1 or -1; got {direction}")
    if vol <= 0 or entry_price <= 0:
        return None
    n = len(closes)
    if not (len(highs) == len(lows) == n):
        raise ValueError("highs, lows, closes must be the same length")
    if entry_idx < 0 or entry_idx >= n:
        return None

    tp_ret = config.pt_mult * vol
    sl_ret = config.sl_mult * vol
    if direction == 1:
        tp_price = entry_price * (1.0 + tp_ret)
        sl_price = entry_price * (1.0 - sl_ret)
    else:
        tp_price = entry_price * (1.0 - tp_ret)
        sl_price = entry_price * (1.0 + sl_ret)

    horizon = min(entry_idx + config.max_holding, n - 1)
    for j in range(entry_idx, horizon + 1):
        hi = float(highs[j])
        lo = float(lows[j])
        if direction == 1:
            hit_sl = lo <= sl_price
            hit_tp = hi >= tp_price
        else:
            hit_sl = hi >= sl_price
            hit_tp = lo <= tp_price
        # Adverse-first: a bar that straddles both resolves to the stop.
        if hit_sl:
            return _resolve(entry_idx, j, entry_price, sl_price, direction,
                            "sl", -1, sl_ret, config)
        if hit_tp:
            return _resolve(entry_idx, j, entry_price, tp_price, direction,
                            "tp", 1, sl_ret, config)

    # Vertical barrier: exit at the horizon close.
    exit_price = float(closes[horizon])
    gross = direction * (exit_price / entry_price - 1.0)
    net = gross - 2.0 * config.slippage
    label = 1 if net > 0 else (-1 if net < 0 else 0)
    return BarrierOutcome(
        barrier="timeout",
        label=label,
        entry_idx=entry_idx,
        exit_idx=horizon,
        entry_price=entry_price,
        exit_price=exit_price,
        ret=net,
        r_multiple=net / sl_ret if sl_ret > 0 else 0.0,
        holding_bars=horizon - entry_idx,
    )


def _resolve(
    entry_idx: int,
    exit_idx: int,
    entry_price: float,
    barrier_price: float,
    direction: int,
    barrier: str,
    label: int,
    sl_ret: float,
    config: BarrierConfig,
) -> BarrierOutcome:
    """Build a BarrierOutcome for a touched (tp/sl) barrier, charging slippage."""
    gross = direction * (barrier_price / entry_price - 1.0)
    net = gross - 2.0 * config.slippage
    return BarrierOutcome(
        barrier=barrier,
        label=label,
        entry_idx=entry_idx,
        exit_idx=exit_idx,
        entry_price=entry_price,
        exit_price=barrier_price,
        ret=net,
        r_multiple=net / sl_ret if sl_ret > 0 else 0.0,
        holding_bars=exit_idx - entry_idx,
    )


def log_prices(closes: Sequence[float]) -> list[float]:
    """``log(close)`` series for the CUSUM filter; non-positive closes carry
    the previous valid value forward so a bad tick doesn't create a fake jump."""
    out: list[float] = []
    last = 0.0
    for c in closes:
        c = float(c)
        if c > 0:
            last = math.log(c)
        out.append(last)
    return out
