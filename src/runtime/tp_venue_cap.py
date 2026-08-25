"""The ONE owner of the venue take-profit clamp (``0.099``).

Import this; do not re-declare the literal. Before 2026-08-25 the value was
declared **thirteen** times under **three** different names
(``_TP_SENTINEL_CAP_PCT`` / ``TP_VENUE_CAP_PCT`` / ``LIVE_TP_CAP_PCT``) across
``src/units/strategies/`` (4), ``src/runtime/`` (2) and ``scripts/`` (7), with
**nothing** binding them together -- no import, no test, no guard.
``m20_fleet_exit_sweep.py`` stated the consequence plainly and correctly:

    "If the live constant moves, this silently keeps measuring the OLD book,
    and the sweep will look correct while doing it."

That module also recorded why it had not simply imported one of the others --
*"there is no single owner to import FROM -- importing one would just pick a
winner arbitrarily"*. This module is that owner, which is the objection's
answer rather than a thirteenth opinion.

WHAT THE VALUE IS
-----------------
Bybit (and most exchanges) reject a take-profit further than ~10% from the
reference base price (**ErrCode 10001** -- hit every ``trend_donchian`` short
at BTC ~$75k on 2026-05-27). PR #2141 first clamped the 50R sentinel to
``entry*0.01``, which satisfied the in-process ``tp > 0`` pre-flight but sits
~99% below entry and the exchange refuses it. ``0.099`` is ~9.9% from entry:
exchange-valid, and still far enough out that the monitor's trail remains the
real profit-exit rather than this clamp.

.. warning::
   **NO ``tp_r`` REPRODUCES THIS CLAMP.** The effective target is
   ``min(cap_r, tp_r)`` where ``cap_r = TP_VENUE_CAP_PCT * entry / risk``
   (``position_telemetry.cap_r``) -- a **percent-of-entry** against a
   **multiple-of-risk**. They are different functions of different variables,
   so lowering a leg's ``tp_r`` to some "equivalent" figure is not equivalent
   and tightens the real target on trades the clamp was never binding for.

.. warning::
   **THE VALUE IS NAMED FOR A BYBIT BOUNDARY AND IS APPLIED TO EVERY SYMBOL,
   INCLUDING LEGS THAT TOUCH NO BYBIT ACCOUNT.** Measured 2026-08-25 against
   ``config/accounts.yaml``: the three Bybit accounts carry only
   ``BTCUSDT ETHUSDT SOLUSDT XRPUSDT ADAUSDT AVAXUSDT``, so equity/futures legs
   (GLD, QQQ, SCHA, IWM, MES, MGC, ...) are clamped by a limit imported from a
   venue they do not trade on. This was raised on the coordination board
   2026-08-16, **together with its own correction**: Bybit's absence is
   necessary but not sufficient, because nobody has checked what those venues'
   actual caps are. So whether ``0.099`` is right, too tight, or too loose for
   a non-Bybit leg is an **OPEN QUESTION**. Consolidating the declaration does
   not answer it and must not be read as having answered it -- this module
   deliberately preserves the single value the fleet has always used, and any
   per-venue split is a separate, Tier-3, evidence-gated change.

WHY IT IMPORTS ONLY ``typing``
------------------------------
``target_expectation.py`` and ``m20_fleet_exit_sweep.py`` each recorded that
they were kept dependency-free on purpose. That property is preserved in
substance: this module imports nothing but ``typing``, and both
``src/__init__.py`` and ``src/runtime/__init__.py`` are empty, so importing it
costs no heavy dependency (measured: 0.012 s, zero of pandas/numpy/ccxt/
ib_insync/yaml newly loaded).
"""

from typing import FrozenSet

#: The venue take-profit clamp, as a fraction of entry price.
TP_VENUE_CAP_PCT = 0.099

#: Strategy FAMILIES whose live unit applies the clamp inside ``order_package``.
#:
#: .. warning::
#:    ``fade`` is present and is **not live**. ``fade_breakout_4h`` is
#:    ``execution: shadow`` (re-verified against ``config/strategies.yaml``
#:    2026-08-25) -- it carries the clamp in its unit file but places no live
#:    order, so the clamp reaches money through THREE families, not four. The
#:    entry is deliberate and forward-looking, so that a promotion of the day
#:    fade to live is covered rather than something to remember. It must not be
#:    read as "fade is being measured today".
CLAMPING_FAMILIES: FrozenSet[str] = frozenset({
    "donchian", "pullback", "fade", "squeeze",
})

#: Unit MODULES under ``src/units/strategies/`` that apply the clamp.
#:
#: Needed beside :data:`CLAMPING_FAMILIES` because the equity legs are named
#: ``qqq_trend_long_1d`` / ``scha_trend_long_1d`` / ... -- their family string
#: does not resolve, yet their signal builder imports ``order_package`` from
#: ``trend_donchian``, which clamps. A family-only test under-claims on all of
#: them (established 2026-08-16 by ``lever_reachability_audit``).
CLAMPING_UNIT_MODULES: FrozenSet[str] = frozenset({
    "trend_donchian",
    "htf_pullback_trend_2h",
    "fade_breakout_4h",
    "squeeze_breakout_4h",
})


def family_clamps(family: str) -> bool:
    """True when ``family``'s live unit applies the venue TP clamp."""
    return str(family) in CLAMPING_FAMILIES


def unit_module_clamps(module_name: str) -> bool:
    """True when a unit module applies the venue TP clamp.

    Accepts either a bare module name (``"trend_donchian"``) or a dotted path
    (``"src.units.strategies.trend_donchian"``); only the final segment is
    significant, so a caller that resolved an import path need not strip it.
    """
    return str(module_name).rsplit(".", 1)[-1] in CLAMPING_UNIT_MODULES
