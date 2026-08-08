#!/usr/bin/env python3
"""RETIRED — this harness was the NON-live-faithful trend engine. Use
``scripts/backtest_trend.py``.

This file used to hold a second, independent Donchian trend engine. It was not a
variant of ``scripts/backtest_trend.py`` with extra flags: measured on 2026-08-08
with identical inputs and **every lever off**, the two disagreed about *which
trades exist* — 29 vs 35 trades at donchian 20, 22 vs 28 at donchian 30
(*population: BTCUSDT 2022-07-23→27, the committed `data/backtest_candles.csv` at
5m, 1001 bars*). They differed on the trail's ATR basis, an opposite-signal flip
exit, a post-exit cooldown, the fee basis, warm-up and `timeout_bars` semantics.

**Why THIS one lost.** ``src/units/strategies/trend_donchian.py`` freezes the
entry ATR into ``meta["atr"]`` and ``monitor()`` trails off that frozen value,
saying so at the write site: *"Without this the live trail would drift with a
rolling ATR and diverge from what was validated."* This engine multiplied the
CURRENT bar's rolling ATR — precisely the drift the live code documents itself as
guarding against. For a strategy whose only profit exit IS the trail, that is the
load-bearing exit semantic, so ``scripts/backtest_trend.py`` is the copy that
reproduces live and the one the fidelity pipeline runs.

All 15 lever flags that were unique to this file
(``--trail-decay-*``, ``--giveback-*``, ``--bank-*``, ``--confirm-bars``,
``--skip-hours``, ``--vol-skip-*-pctl``, ``--vol-pctl-window``,
``--trail-vol-*``) were **ported** into ``scripts/backtest_trend.py`` with the
same names and defaults, re-implemented in that engine's semantics. It is now a
strict superset: 43 flags vs this file's 36, 0 research-only.

This shim exists instead of a deletion so a missed caller **fails loudly** rather
than silently resolving to a stale copy or a confusing `FileNotFoundError`. A
third copy reappearing is the failure mode being prevented.

Record: ``docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md`` §5f ·
``BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE`` ·
``scripts/research/trend_harness_divergence.py`` (the instrument that measured it).

**Numbers produced by this engine are not comparable to the live-faithful one.**
The M20 lever sweeps (`m20_exit_sweep.py`, `m20_fleet_exit_sweep.py`) ran here and
have been repointed; any lever parameter they produced — including the LIVE
`trend_donchian` `trail_decay_arm_r: 6.49` / `trail_decay_tight_mult: 2.5` — was
fitted against this engine's trail and needs re-sweeping before it is trusted
(`BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL`).
"""
from __future__ import annotations

import sys

_MESSAGE = """\
scripts/research/backtest_trend.py is RETIRED.

Use scripts/backtest_trend.py — the live-faithful trend engine (it freezes the
entry bar's ATR for the trail, matching trend_donchian.monitor(); this file
trailed off a rolling ATR and produced a different trade set).

Every flag this file declared now exists there with the same name and default.
Same command line, different path:

    python scripts/backtest_trend.py --data <csv> [...]

Why, and the measurement:
  docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md  §5f
  scripts/research/trend_harness_divergence.py
  BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE
"""


def main(argv=None) -> int:  # inert: a retired shim reads no argv by design — the parameter exists ONLY so an existing `main(sys.argv)` caller reaches the explanation instead of a TypeError
    sys.stderr.write(_MESSAGE)
    return 2


def backtest(*_args, **_kwargs):  # inert: a retired shim reads no argument by design — it accepts the old signature ONLY so an existing caller reaches the explanation below instead of a bare TypeError
    """Import-time callers get the same explanation as CLI callers."""
    raise RuntimeError(_MESSAGE)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
