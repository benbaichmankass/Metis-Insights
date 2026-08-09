#!/usr/bin/env python3
"""RETIRED 2026-08-09 — this was the second, non-live-faithful trend engine.

THE ONE ENGINE IS ``scripts/backtest_trend.py``. Import that.

This module is a deliberate **hard-fail shim**, not a deletion: a missed caller
gets this explanation instead of a ``FileNotFoundError`` or — far worse — a
silent resolution to a different file that happens to share the basename. It
also occupies the name, so a third copy cannot quietly reappear here.

WHY IT WAS RETIRED
------------------
There were two files called ``backtest_trend.py`` and they were two ENGINES, not
one engine with two flag sets. Run over identical candles with every optional
lever OFF they still disagreed about *which trades exist*, because they differ
on:

* **trail ATR basis** — ``scripts/backtest_trend.py`` freezes the ENTRY bar's
  ATR for the whole trade; this copy multiplied the CURRENT bar's rolling ATR
  on every managed bar;
* **opposite-signal (flip) exit** — only this copy had one;
* **post-exit cooldown bars** — only ``scripts/backtest_trend.py`` has one;
* **fee basis** (average of entry/exit price vs entry price only), warm-up
  length, ``timeout`` semantics (``0`` here meant *no timeout*; there it means
  *exit on the entry bar*), and the win-rate denominator (gross R > 0 vs
  net-of-fee R > 0).

``src/units/strategies/trend_donchian.py`` freezes the entry ATR into the order
package's ``meta["atr"]`` and its ``monitor()`` trails off that frozen value, so
on the load-bearing exit semantic — the trail, which is this strategy's only
profit exit — **live matches ``scripts/backtest_trend.py``**, and this copy was
the one that drifted. That fact, not a flag count, decided the direction.

PR #8633 ported all 15 research-only lever flags into the live-faithful engine
(byte-identical at default, research-only flag count 15 → 0). This retirement is
the follow-up that removes the losing engine now that nothing needs it.

WHERE EVERYTHING WENT
---------------------
================================  ==========================================
was                               now
================================  ==========================================
``backtest(df, donchian, ...)``   ``scripts/backtest_trend.py::run_backtest``
                                  (keyword-only; pass ``trades_out=[]`` for
                                  the ``Trade`` list — it returns a summary
                                  dict)
``_load`` / ``_resample``         ``scripts/candle_io.py::load_candles`` /
                                  ``resample_ohlcv`` (lifted verbatim)
``_atr`` / ``_signal``            ``scripts/backtest_trend.py`` internals
the five lever test files         migrated onto the live-faithful engine via
                                  ``tests/trend_harness_engine.py``
================================  ==========================================

Tracking: ``BL-20260808-RESEARCH-TREND-ENGINE-RETIREMENT-BLOCKED-BY-TEST-COUPLING``.
Convergence is enforced by ``scripts/research/trend_harness_divergence.py``
(CI guard ``trend-engine-convergence-guard``), which detects this shim by the
**absence of an engine entry point** — so restoring one here fails CI rather
than silently re-opening the fork.
"""
from __future__ import annotations

_MESSAGE = (
    "scripts/research/backtest_trend.py was RETIRED on 2026-08-09 — it was the "
    "second, non-live-faithful trend engine (rolling-ATR trail, opposite-signal "
    "flip exit, no post-exit cooldown).\n\n"
    "Use the ONE engine instead:\n"
    "    from backtest_trend import run_backtest   # scripts/backtest_trend.py\n"
    "    trades = []\n"
    "    run_backtest(df, donchian=..., atr_period=..., atr_stop_mult=...,\n"
    "                 trail_mult=..., timeout_bars=..., cooldown_bars=...,\n"
    "                 timeframe=..., symbol=..., trades_out=trades)\n\n"
    "Note run_backtest is KEYWORD-ONLY, returns a summary dict (pass "
    "trades_out=[] for the Trade list), and reads `timeout_bars=0` as 'exit on "
    "the entry bar' where this engine read `timeout=0` as 'no timeout'.\n\n"
    "IO helpers moved: scripts/candle_io.py::load_candles / resample_ohlcv "
    "(lifted verbatim, so behaviour is unchanged).\n\n"
    "Rationale + full mapping: the module docstring of this file.\n"
    "Tracking: BL-20260808-RESEARCH-TREND-ENGINE-RETIREMENT-BLOCKED-BY-TEST-COUPLING"
)


class RetiredEngineError(ImportError):
    """Raised on any attempt to use the retired research trend engine."""


def __getattr__(name: str):
    """Fail loudly on ANY attribute access, including `from … import backtest`.

    A module-level ``__getattr__`` (PEP 562) is what makes the shim work for the
    `from backtest_trend import backtest` form every retired caller used: the
    import machinery falls back to it when the name is absent, so the caller
    gets :class:`RetiredEngineError` with the migration note rather than a bare
    ``ImportError: cannot import name 'backtest'``.

    Deliberately NOT raised at import time: the convergence guard must be able
    to import this module to confirm it exposes no engine entry point. A shim
    that explodes on import would force the guard to fall back to reading the
    source as prose, which is exactly the check that must not exist.
    """
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    raise RetiredEngineError(f"{_MESSAGE}\n\n(attempted to access: {name!r})")


if __name__ == "__main__":
    raise SystemExit(_MESSAGE)
