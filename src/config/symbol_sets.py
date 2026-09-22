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


# ---------------------------------------------------------------------------
# Self-test — runs in CI as `symbol-resolver-guard`.
# ---------------------------------------------------------------------------

#: The four sites this module exists to unify, and the mode each one asked for.
#: The BYPASS control below reads these files and fails if any of them has gone
#: back to computing a symbol set privately. Centralising the definition means
#: nothing if a fifth caller — or a future edit to one of these four — quietly
#: re-derives it, which is exactly how there came to be four.
CALL_SITES = {
    "src/main.py": (
        DECLARED,
        "the tick's candle fetch set; wants UNION, pinned to DECLARED until "
        "PR #12736 lands",
    ),
    "src/units/accounts/clients.py": (
        UNION,
        "the Bybit per-symbol POSITION cross-check, both readers",
    ),
    "src/runtime/exchange_accounts.py": (
        UNION,
        "the fill / funding / cost sweep",
    ),
}

#: A private re-derivation looks like one of these in a converted file.
#: Deliberately NOT a bare `symbols` match: the word appears in prose, in
#: `resolve_symbols(` itself and in `declared=cfg.get("symbols")`, which IS the
#: routed form. These patterns are the *unrouted* reads only.
_BYPASS_PATTERNS = (
    'account.get("symbols")',
    'acct.symbols',
    'getattr(acct, "symbols"',
)


def bypass_offenders(read=None) -> list[str]:
    """Converted files that still compute a symbol set privately.

    ``read`` is injectable so the self-test can plant a violation without
    touching the tree — a control that can only be exercised by breaking the
    repo is a control nobody runs.
    """
    import re  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    def _default_read(rel: str) -> str:
        return (Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")

    reader = read or _default_read
    out: list[str] = []
    for rel in CALL_SITES:
        try:
            text = reader(rel)
        except Exception:  # noqa: BLE001
            out.append(f"{rel}: COULD NOT READ — not a pass, a failure to check")
            continue
        # The exemption is VERIFIED, not presence-only. A marker counts only in
        # a file that actually imports this module, so the cheapest way to
        # silence the control is still to route the call — the
        # `new-table-wiring-guard` lesson (a guard cheaper to lie to than to
        # satisfy is worse than no guard). ⚠️ ITS HONEST LIMIT, stated rather
        # than hidden: this proves the file is routed SOMEWHERE, not that this
        # particular line is the fallback of a routed call. That residue is why
        # the marker must carry a reason a reviewer reads in the diff.
        routed_file = "symbol_sets" in text
        for pat in _BYPASS_PATTERNS:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or pat not in line:
                    continue
                # `declared=...` is the ROUTED form: it is how a caller hands
                # the declared list TO the resolver, not a private derivation.
                if re.search(r"declared\s*=", line):
                    continue
                marker = re.search(r"#\s*symbol-set:\s*(.+)$", line)
                if marker and routed_file and len(marker.group(1).strip()) >= 20:
                    continue
                if marker and not routed_file:
                    out.append(f"{rel}: marker claims an exemption in a file "
                               f"that never imports the resolver: {stripped[:80]}")
                    continue
                out.append(f"{rel}: {stripped[:100]}")
    return out


def _self_test() -> int:  # noqa: C901
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {label}: {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    print("symbol-resolver self-test")
    scfg = {"leg_x": {"symbols": ["AVAXUSDT"]}, "leg_y": {"symbols": ["XRPUSDT"]},
            "leg_none": {"timeframe": "1h"}}

    # --- the core contract
    check("1 (DECLARED ignores the roster entirely)",
          resolve_symbols(declared=["BTCUSDT"], roster=["leg_x"],
                          mode=DECLARED, strategies_cfg=scfg) == ["BTCUSDT"])
    check("2 (UNION appends the roster's symbols AFTER the declared ones, so "
          "it can only ever lengthen the list)",
          resolve_symbols(declared=["BTCUSDT"], roster=["leg_x"],
                          mode=UNION, strategies_cfg=scfg)
          == ["BTCUSDT", "AVAXUSDT"])
    check("3 (a declared symbol no leg trades SURVIVES the union — the pull "
          "list is additive, and 21 such entries are live)",
          resolve_symbols(declared=["BTCUSDT", "ADAUSDT"], roster=["leg_x"],
                          mode=UNION, strategies_cfg=scfg)
          == ["BTCUSDT", "ADAUSDT", "AVAXUSDT"])
    check("4 (no duplicate when the roster's symbol is already declared)",
          resolve_symbols(declared=["AVAXUSDT"], roster=["leg_x"],
                          mode=UNION, strategies_cfg=scfg) == ["AVAXUSDT"])

    # --- the mode is explicit. A caller that forgets it must not get a
    #     plausible default; it must fail loudly.
    try:
        resolve_symbols(declared=["BTCUSDT"], roster=[], mode="")  # type: ignore[arg-type]
        check("5 (an absent/invalid mode RAISES rather than defaulting)", False)
    except ValueError as exc:
        check("5 (an absent/invalid mode RAISES rather than defaulting)",
              "no default" in str(exc))

    # --- fail-safe: never narrower.
    check("6 (unreadable strategies.yaml -> UNION degrades to DECLARED, "
          "never to empty)",
          resolve_symbols(declared=["BTCUSDT"], roster=["leg_x"],
                          mode=UNION, strategies_cfg=None) == ["BTCUSDT"])
    check("7 (a rostered strategy with no `symbols:` contributes nothing and "
          "does not raise)",
          resolve_symbols(declared=["BTCUSDT"], roster=["leg_none", "ghost"],
                          mode=UNION, strategies_cfg=scfg) == ["BTCUSDT"])
    check("8 (the exchange default applies ONLY to an empty declared list, "
          "and never displaces a declaration)",
          resolve_symbols(declared=[], roster=[], mode=DECLARED,
                          exchange="interactive_brokers") == ["MES"]
          and resolve_symbols(declared=["SPY"], roster=[], mode=DECLARED,
                              exchange="bybit") == ["SPY"])
    check("9 (no exchange passed -> no invented instrument; a data sweep would "
          "rather see [] than a symbol nobody declared)",
          resolve_symbols(declared=[], roster=[], mode=UNION) == [])

    # --- one planted case PER CALL SITE, in that site's own mode.
    check("10 (tick site, DECLARED: a rostered-but-undeclared symbol is NOT "
          "fetched — today's behaviour, held until #12736)",
          "AVAXUSDT" not in resolve_symbols(
              declared=["BTCUSDT"], roster=["leg_x"], mode=DECLARED,
              strategies_cfg=scfg, exchange="bybit"))
    check("11 (position cross-check site, UNION: the same symbol IS in the "
          "denominator, so a live position on it cannot hide)",
          "AVAXUSDT" in resolve_symbols(
              declared=["BTCUSDT"], roster=["leg_x"], mode=UNION,
              strategies_cfg=scfg))
    check("12 (fill/funding sweep site, UNION: the same symbol IS swept, so "
          "its funding reaches the cost stack)",
          "AVAXUSDT" in resolve_symbols(
              declared=["BTCUSDT"], roster=["leg_x"], mode=UNION,
              strategies_cfg=scfg))
    check("13 (and the two UNION sites agree exactly — one definition means "
          "they cannot drift)",
          resolve_symbols(declared=["BTCUSDT"], roster=["leg_x", "leg_y"],
                          mode=UNION, strategies_cfg=scfg)
          == resolve_symbols(declared=["BTCUSDT"], roster=["leg_x", "leg_y"],
                             mode=UNION, strategies_cfg=scfg))

    # --- THE BYPASS CONTROL, both directions.
    planted = {
        "src/main.py": 'syms = list(getattr(acct, "symbols", None) or [])\n',
        "src/units/accounts/clients.py": "x = 1\n",
        "src/runtime/exchange_accounts.py": "y = 2\n",
    }
    check("14 (a call site that goes back to deriving the set privately is "
          "CAUGHT — the control is planted, not hoped for)",
          len(bypass_offenders(read=planted.get)) == 1
          and "src/main.py" in bypass_offenders(read=planted.get)[0])
    clean = {k: "declared=cfg.get(\"symbols\"), roster=cfg.get(\"strategies\")\n"
             for k in CALL_SITES}
    check("15 (…and the ROUTED form — `declared=cfg.get(\"symbols\")` — is not "
          "mistaken for a bypass)",
          bypass_offenders(read=clean.get) == [])
    marked_routed = {k: "from src.config import symbol_sets\n"
                        "    syms = account.get(\"symbols\")  # symbol-set: degraded fallback of the routed call above\n"
                     for k in CALL_SITES}
    check("15b (a marked line in a file that DOES route is exempt)",
          bypass_offenders(read=marked_routed.get) == [])
    marked_unrouted = {k: "syms = account.get(\"symbols\")  # symbol-set: degraded fallback of the routed call above\n"
                       for k in CALL_SITES}
    check("15c (…the SAME marker in a file that never imports the resolver is "
          "REFUSED — the exemption is verified, not presence-only)",
          len(bypass_offenders(read=marked_unrouted.get)) == 3
          and "never imports the resolver" in bypass_offenders(read=marked_unrouted.get)[0])
    short = {k: "from src.config import symbol_sets\n"
                "    syms = account.get(\"symbols\")  # symbol-set: nope\n"
             for k in CALL_SITES}
    check("15d (…and a marker with no real reason does not buy an exemption)",
          len(bypass_offenders(read=short.get)) == 3)

    # --- cfg precedence. The four tests that caught the first version of this
    #     are the reason these exist: a caller's explicit view must not be
    #     silently widened from a file it never named.
    check("15g (an EMPTY cfg `symbols` falls through to accounts.yaml — MI-222: "
          "the cross-check cannot be switched off from the cfg dict)",
          symbols_for_account_id("bybit_2", mode=UNION, cfg={"symbols": []})
          == ["BTCUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT"])
    check("15h (an UNKNOWN account id resolves to [] and never to an invented "
          "exchange default — a data sweep must not manufacture a venue read "
          "for a symbol nobody declared)",
          symbols_for_account_id("no-such-account", mode=UNION,
                                 cfg={"symbols": [], "exchange": "bybit"}) == [])
    check("15e (a cfg carrying `symbols` is authoritative — its OWN roster "
          "key, or the absence of one, decides; accounts.yaml is not consulted)",
          symbols_for_account_id("bybit_2", mode=UNION,
                                 cfg={"symbols": ["XRPUSDT", "BTCUSDT"]})
          == ["XRPUSDT", "BTCUSDT"])
    check("15f (…and that same cfg WITH a roster does get the union)",
          symbols_for_account_id(
              "bybit_2", mode=UNION,
              cfg={"symbols": ["BTCUSDT"], "strategies": ["xrp_pullback_2h"]})
          == ["BTCUSDT", "XRPUSDT"])

    check("16 (a file we could not READ is reported, not silently passed)",
          len(bypass_offenders(read=lambda rel: (_ for _ in ()).throw(OSError()))) == 3)
    live = bypass_offenders()
    check("17 (the REAL tree: all three converted files route through this "
          f"module) -> {live if live else 'clean'}", live == [])

    # --- the claim the whole change rests on, asserted against the real config.
    try:
        # The canonical readers, not a hand-rolled parse: `accounts.yaml` has
        # exactly one sanctioned dict reader (`canonical-config-loaders` fails
        # the build on a second one, and it caught the first draft of this very
        # block), and strategies.yaml goes through this module's own loader.
        from src.config.accounts_loader import load_accounts_dict  # noqa: PLC0415
        acc = load_accounts_dict() or {}
        strat = load_strategies_cfg()
        diverged = []
        for name, cfg in acc.items():
            cfg = cfg or {}
            d = resolve_symbols(declared=cfg.get("symbols"),
                                roster=cfg.get("strategies"), mode=DECLARED,
                                strategies_cfg=strat, exchange=cfg.get("exchange"))
            u = resolve_symbols(declared=cfg.get("symbols"),
                                roster=cfg.get("strategies"), mode=UNION,
                                strategies_cfg=strat, exchange=cfg.get("exchange"))
            if d != u:
                diverged.append(f"{name}: UNION adds {[s for s in u if s not in d]}")
        check(f"18 (DECLARED == UNION on all {len(acc)} live accounts, so "
              "switching a reader's mode is a NO-OP on today's config) -> "
              f"{diverged if diverged else 'no divergence'}",
              diverged == [])
    except Exception as exc:  # noqa: BLE001
        check(f"18 (live-config equivalence) — COULD NOT READ: {exc}", False)

    print("self-test OK — one definition, an explicit mode per caller, "
          "fail-safe in the widening direction only, and a planted bypass is "
          "caught while the routed form is not."
          if ok else "self-test FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    if ap.parse_args().self_test:
        sys.exit(_self_test())
    for _mode in MODES:
        print(f"--- mode={_mode}")
        from src.config.accounts_loader import load_accounts_dict
        _scfg = load_strategies_cfg()
        for _aid, _cfg in (load_accounts_dict() or {}).items():
            print(f"  {_aid}: {resolve_symbols(declared=(_cfg or {}).get('symbols'), roster=(_cfg or {}).get('strategies'), mode=_mode, strategies_cfg=_scfg, exchange=(_cfg or {}).get('exchange'))}")
