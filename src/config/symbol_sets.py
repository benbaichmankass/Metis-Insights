"""ONE definition of "which symbols does this account concern", for all readers.

WHY THIS MODULE EXISTS
----------------------
``config/accounts.yaml`` carries two lists per account and they answer two
different questions (operator, 2026-09-22):

* ``strategies:`` — the ROSTER. The single source of truth for what the account
  TRADES. Routing tests this and nothing else.
* ``symbols:`` — the PULL LIST. A purely ADDITIVE set of instruments to fetch
  DATA for, for reasons unrelated to the strategies. It is legitimate for it to
  name a symbol nothing trades; 21 entries did on 2026-09-22.

Four places in the codebase needed "this account's symbols" and each computed
it privately from the declared list alone:

===================================================  =====================
site                                                 what it does
===================================================  =====================
``src/main.py::_resolve_tick_symbols``               the tick's candle fetch set
``clients.py::account_bybit_open_orders``            per-symbol POSITION cross-check
``clients.py::_bybit_configured_symbols``            the raw position sweep
``runtime/exchange_accounts.py::bybit_fill_accounts``  the fill / funding / cost sweep
===================================================  =====================

Four private copies of one question is the defect. E42 is about to make the
tick fetch ``UNION(roster-implied, declared)``; doing that while the other
three still read the declared list alone would open a window in which a
rostered-but-undeclared symbol **TRADES while staying invisible to the
naked-position cross-check**. The operator declined that window even
transiently, which is why this module lands FIRST and the union second.

THE MODE IS EXPLICIT AND HAS NO DEFAULT, DELIBERATELY
------------------------------------------------------
:func:`resolve_symbols` requires ``mode=``. Every current caller wants
:data:`UNION` except the tick, which stays :data:`DECLARED` until the held
PR #12736 flips it — so a default would be wrong for somebody, and a caller
that gets its mode by accident is the four-private-copies bug wearing a
central import. Each call site states which it wants and why, in one line, at
the call.

WHICH MODE EACH SITE WANTS — ESTABLISHED PER SITE, NOT ASSUMED
---------------------------------------------------------------
* **tick fetch set** — wants ``UNION``, and is pinned to ``DECLARED`` here
  ONLY because that flip is PR #12736's change and it is held for a Tier-2
  approval. This PR is behaviour-preserving on the tick on purpose.
* **Bybit position cross-check** (both readers) — ``UNION``. Their whole reason
  to exist is that a ``settleCoin`` page can silently omit a live symbol
  (``BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND``); a position can exist on any
  symbol the account trades, so grading it against the pull list rather than
  the roster reintroduces the blindness one level up. The cost is one REST call
  per symbol the page did not return, which is the cheaper side of the trade
  against reporting a live position as absent.
* **fill / funding / cost sweep** — ``UNION``. Bybit serves funding per
  contract, so a rostered symbol missing from the pull list accrues funding the
  sweep never pulls, and the cost stack is then wrong by an unmeasured amount
  on a leg that is really trading.

A NO-OP ON TODAY'S CONFIG, WHICH IS WHAT MAKES IT SAFE TO LAND
---------------------------------------------------------------
MEASURED over ``config/accounts.yaml`` × ``config/strategies.yaml``: **zero**
rostered symbols are missing from any account's pull list, so ``UNION`` and
``DECLARED`` return the same set for every account today.
``--self-test`` asserts that over the real config, so the day they diverge is a
CI failure rather than a surprise in production.

FAIL-SAFE IN ONE DIRECTION ONLY
--------------------------------
An unreadable ``strategies.yaml`` makes the roster half contribute nothing, so
``UNION`` degrades to ``DECLARED`` — never to empty, and never narrower than
the pre-2026-09-22 behaviour. Widening a symbol set can cost a wasted fetch;
narrowing one can strand a leg or blind a position check.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

#: Declared ``symbols:`` only — the pre-E42 behaviour.
DECLARED = "declared"
#: ``symbols:`` ∪ every symbol the account's rostered strategies declare.
UNION = "union"
MODES = (DECLARED, UNION)

# Per-exchange default instrument for an account that omits ``symbols:``
# entirely. Keeps such an account fetching its natural instrument rather than
# nothing. Applied ONLY when the declared list is empty, so it can never
# displace an explicit declaration.
EXCHANGE_DEFAULT_SYMBOL = {
    "bybit": "BTCUSDT",
    "interactive_brokers": "MES",
}


def _clean(symbols: Optional[Iterable[Any]]) -> list[str]:
    """Trim, drop blanks, preserve order, de-duplicate."""
    out: list[str] = []
    for raw in (symbols or []):
        sym = str(raw).strip()
        if sym and sym not in out:
            out.append(sym)
    return out


def roster_symbols(roster: Optional[Sequence[Any]],
                   strategies_cfg: Optional[Mapping[str, Any]]) -> list[str]:
    """Symbols the account's rostered strategies declare, in roster order.

    A strategy absent from ``strategies_cfg``, or declaring no ``symbols:``,
    contributes nothing rather than raising — the tick loop must not be able to
    die on a roster typo. ``roster-symbol-reachability`` reports that case as
    ``strategy_unknown`` in CI, which is where it belongs.
    """
    out: list[str] = []
    cfgs = strategies_cfg or {}
    for name in (roster or []):
        scfg = cfgs.get(name) or {}
        if not isinstance(scfg, Mapping):
            continue
        for sym in _clean(scfg.get("symbols")):
            if sym not in out:
                out.append(sym)
    return out


def resolve_symbols(
    *,
    declared: Optional[Iterable[Any]],
    roster: Optional[Sequence[Any]],
    mode: str,
    strategies_cfg: Optional[Mapping[str, Any]] = None,
    exchange: Optional[str] = None,
) -> list[str]:
    """The symbols this account concerns, under an EXPLICIT ``mode``.

    ``declared`` is the account's ``symbols:`` list, ``roster`` its
    ``strategies:`` list. The declared entries come first and keep their
    order, so ``UNION`` can only ever APPEND — a caller switching from
    ``DECLARED`` to ``UNION`` cannot lose a symbol it was already getting.

    ``exchange`` opts into the per-exchange default for an account that
    declares no symbols at all; callers that do not want a default (the data
    sweeps, which would rather see an empty list than invent an instrument)
    simply omit it.
    """
    if mode not in MODES:
        raise ValueError(
            f"resolve_symbols(mode=) must be one of {MODES}; got {mode!r}. "
            "The mode has no default on purpose: the tick wants DECLARED until "
            "PR #12736 lands and every data reader wants UNION, so there is no "
            "value that is right for every caller."
        )
    out = _clean(declared)
    if not out and exchange:
        default = EXCHANGE_DEFAULT_SYMBOL.get(str(exchange or "").lower())
        if default:
            out = [default]
    if mode == UNION:
        for sym in roster_symbols(roster, strategies_cfg):
            if sym not in out:
                out.append(sym)
    return out


def load_strategies_cfg() -> dict:
    """``strategies.yaml``, or ``{}`` when it cannot be read.

    ``{}`` is the fail-safe value: it makes the roster half contribute nothing,
    so ``UNION`` degrades to ``DECLARED`` rather than to empty. A config-read
    failure must never be able to NARROW a symbol set.
    """
    try:
        from src.units.strategies import load_strategy_config
        return dict(load_strategy_config() or {})
    except Exception:  # noqa: BLE001
        return {}


def symbols_for_account_id(account_id: str, *, mode: str,
                           cfg: Optional[Mapping[str, Any]] = None,
                           exchange: Optional[str] = None) -> list[str]:
    """:func:`resolve_symbols` for a named account, loading what it needs.

    ⚠️ **A CALLER-SUPPLIED ``cfg`` THAT CARRIES ``symbols`` IS AUTHORITATIVE
    FOR BOTH LISTS**, and that rule was learned the hard way rather than
    designed. The first version loaded the roster from ``accounts.yaml`` by id
    whenever the cfg lacked one — so a caller that passed an explicit
    ``symbols=[...]`` had its own input silently widened by facts from a file
    it never mentioned. Four existing tests caught it, and they were right to:
    the pre-existing contract on ``_bybit_configured_symbols`` is *"prefers the
    ``symbols`` key already on the passed cfg"*, and quietly merging another
    source into a caller's explicit view is a worse defect than the one this
    module fixes.

    So: cfg carries a NON-EMPTY ``symbols`` → both lists come from cfg (no
    roster key means no roster contribution, i.e. exactly the declared list).
    cfg carries an EMPTY or absent ``symbols`` → the full config is loaded by
    id, which is where the union does its work.

    ⚠️ **THE EMPTY CASE IS LOAD-BEARING AND IS NOT AN OVERSIGHT.**
    ``tests/test_accounts_clients_position_read_state_collapse.py::
    TestRosterCrossCheckAlreadyExists`` pins it as MI-222's structural
    correction: *"the roster sweep cannot be switched off from the cfg dict …
    a caller cannot opt out by handing in an empty roster"*, so for any
    ``account_id`` present in accounts.yaml the per-symbol position
    cross-check is UNCONDITIONAL. That test's own docstring records the last
    session that got this backwards. Preserving `[] → fall through` is why the
    truthiness check here is deliberate rather than sloppy.

    ⚠️ **The gap this leaves, stated rather than hidden:** a caller holding a
    cfg with ``symbols`` but no ``strategies`` gets DECLARED-ONLY even under
    ``mode=UNION``. MEASURED 2026-09-22 — the production path
    (``src.units.ui.data_loaders.list_accounts``, which feeds every
    ``/api/diag`` account read) returns **both** keys on every account, so no
    live caller is in that shape today. A future one would need to pass its
    roster too.
    """
    supplied = cfg or {}
    declared_in_cfg = supplied.get("symbols")
    has_declared = isinstance(declared_in_cfg, (list, tuple)) and bool(declared_in_cfg)
    if has_declared:
        resolved: Mapping[str, Any] = supplied
    else:
        try:
            from src.config.accounts_loader import load_accounts_dict
            resolved = (load_accounts_dict() or {}).get(account_id) or {}
        except Exception:  # noqa: BLE001
            resolved = supplied
    return resolve_symbols(
        declared=resolved.get("symbols"),
        roster=resolved.get("strategies"),
        mode=mode,
        strategies_cfg=load_strategies_cfg(),
        # ⚠️ NEVER defaulted from the cfg's `exchange`. A data reader must not
        # INVENT an instrument: for an account that declares nothing, "we have
        # no symbols for this account" and "assume it trades BTCUSDT" are
        # opposite statements, and the second one manufactures a per-symbol
        # venue read for a symbol nobody declared. The tick loop wants that
        # default and passes `exchange=` explicitly; the sweeps do not.
        exchange=exchange,
    )
