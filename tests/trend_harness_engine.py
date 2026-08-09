"""Test-side loader for THE trend harness engine — `scripts/backtest_trend.py`.

Why this module exists
----------------------
There used to be **two** files named `backtest_trend.py` (`scripts/` and
`scripts/research/`), and they were two ENGINES, not one engine with two flag
sets. A bare ``from backtest_trend import ...`` resolved to whichever directory
happened to be first on ``sys.path`` — so which engine a test exercised was a
property of its import order, not of anything it stated. The research copy was
retired in favour of the **live-faithful** one (frozen entry-bar ATR, SL-first
nested loop, post-exit cooldown, no flip exit — the semantics
``trend_donchian.monitor()`` actually has); see
``BL-20260808-RESEARCH-TREND-ENGINE-RETIREMENT-BLOCKED-BY-TEST-COUPLING``.

Loading by explicit path (the pattern `tests/test_trend_harness_levers.py`
established) makes the choice unambiguous and un-shadowable.

Parameter-name mapping, research engine → this one
--------------------------------------------------
=========================  ==============================================
research ``backtest``      live-faithful ``run_backtest``
=========================  ==============================================
``atr_p``                  ``atr_period``
``atr_stop``               ``atr_stop_mult``
``timeout=0`` (no timeout) ``timeout_bars=RIDE_TO_TAPE_END`` (see below)
``long_only=False``        ``side_filter="both"`` (the default)
(no equivalent)            ``cooldown_bars`` — 0 is the closest analogue
returns ``List[Trade]``    returns a summary dict; pass ``trades_out``
=========================  ==============================================

``timeout`` is the one that bites: the research engine read ``0`` as "no
timeout", while here ``timeout_bars=0`` means *exit on the entry bar itself*
(``exit_idx = entry_i + 0``). :data:`RIDE_TO_TAPE_END` is the honest translation
— large enough that the timeout never binds on a synthetic tape, so the trade
resolves on its stop or at the end of the tape. It is a test-tape convenience,
NOT a claim that the two engines agree on exit timing: they do not, and the
migrated tests assert only contracts that survive that difference (or re-derive
their numbers against this engine — see each file's header).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any, Dict, List, Tuple

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

ENGINE_REL = "scripts/backtest_trend.py"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # dataclass() needs the module registered
    spec.loader.exec_module(mod)
    return mod


ENGINE = _load("_bt_trend_engine_for_tests", ENGINE_REL)

#: ``timeout_bars`` value meaning "the timeout must never bind on this tape".
RIDE_TO_TAPE_END = 10_000

#: The synthetic-tape defaults the migrated lever tests share (a 10-bar channel
#: and a 5-bar ATR resolve fast enough for a ~50-bar tape).
TAPE_DEFAULTS: Dict[str, Any] = dict(
    donchian=10, atr_period=5, atr_stop_mult=2.0, trail_mult=3.0,
    timeout_bars=RIDE_TO_TAPE_END, cooldown_bars=0,
    timeframe="1h", symbol="TEST",
)


def trend_trades(df, **kw) -> List[Any]:
    """Run the live-faithful trend engine on ``df``; return its ``Trade`` list.

    ``trades_out`` is the engine's own additive hook — the summary, the emit
    file and the engine itself are byte-identical whether or not it is passed.
    """
    out: List[Any] = []
    ENGINE.run_backtest(df.copy(), **{**TAPE_DEFAULTS, **kw}, trades_out=out)
    return out


def trend_summary_and_trades(df, **kw) -> Tuple[Dict[str, Any], List[Any]]:
    """Both halves, for a test that needs the summary dict as well."""
    out: List[Any] = []
    summary = ENGINE.run_backtest(df.copy(), **{**TAPE_DEFAULTS, **kw},
                                  trades_out=out)
    return summary, out
