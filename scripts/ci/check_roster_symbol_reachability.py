#!/usr/bin/env python3
"""roster-symbol-reachability — a rostered leg's symbol must be reachable.

THE TWO LISTS, AND WHY THEY ARE NOT ONE (operator, 2026-09-22)
---------------------------------------------------------------
    "There are two separate issues here. There's the issue of what trades on
     what account and what data gets pulled from the account, and those are
     two separate things that aren't dependent... that shouldn't be formatted
     as a gate. That should just be formatted as additional data pulls
     unrelated to the strategies themselves... And even if something is for
     some reason missing from the pull list, it doesn't mean that it shouldn't
     be traded — it means that the pull list is not updated correctly."

* ``config/accounts.yaml::strategies`` — the ROSTER. It is the single source
  of truth for what trades. Nothing in this guard ever asks it to change.
* ``config/accounts.yaml::symbols`` — the PULL LIST. Purely additive: symbols
  we fetch for reasons unrelated to the strategies. It is legitimate for it to
  name a symbol nothing trades (19 such entries on 2026-09-22), so this guard
  NEVER reports one.

THE DEFECT THIS EXISTS TO CATCH
-------------------------------
Until 2026-09-22 ``src/main.py::_resolve_tick_symbols`` built the trader's
per-tick fetch set from the PULL LISTS ALONE. A leg on the roster whose symbol
nobody had added to its account's ``symbols:`` therefore got no candles, built
no signal and placed no order — while reading, everywhere an operator looks, as
wired and trading. A third execution gate in everything but name, which Prime
Directive rule 6 forbids, and the same shape as the ``MULTI_SYMBOL_ENABLED``
flag that stranded MES in 2026-05 — only spelled as an omission rather than as
a flag, which is why no guard saw it.

``_resolve_tick_symbols`` now fetches the UNION of roster-implied and declared
symbols, so the omission no longer stops a leg trading. It is still a BUG,
because the declared list independently drives real data pulls that are NOT the
tick: the Bybit per-symbol position cross-check that catches a settleCoin page
omitting a live symbol (``clients.py::account_bybit_open_orders``), the Bybit
fill/cost sweep (``runtime/exchange_accounts.py``), and the SPA's symbol
selectors via ``/api/bot/config``. A rostered symbol absent from the pull list
is invisible to all three.

MEASURED 2026-09-22 over all 11 accounts in ``config/accounts.yaml`` against
``config/strategies.yaml`` at ``06914ce2f``: **0 unreachable**, and **21 orphan
data-pulls** (see the count note below). The zero is not reassurance — it reads
zero because a human checked symbol coverage BY HAND. The near-miss is on
record and re-readable: commit ``d257fe576`` on
``claude/r8-wire-fold-consistent-legs`` (STAGED, NOT merged to ``main`` as of
this writing) adds ``avax_pullback_2h`` to ``bybit_2``'s roster — REAL MONEY —
and adds ``AVAXUSDT`` to its ``symbols:`` in the same diff, with an inline
comment saying the symbol "was absent here" and would have had to be added.
Nothing mechanical has ever asserted that relationship. That is what this is.

⚠️ **THE ORPHAN COUNT DEPENDS ON ITS POPULATION AND IS REPORTED, NEVER
ASSERTED.** This script counts **21** because its population is every account
not ``enabled: false``, including the two whose roster is explicitly empty
(``ib_live``/MES, ``oanda_practice``/XAUUSD). The E42 checklist row records
**19** over a population that also drops ``BTCUSDT`` on ``bybit_2`` and
``bybit_portfolio`` — the primary symbol, which the tick fetches regardless.
Both are correct about different sets; neither number means anything without
the set, so the run line prints the denominators beside it.

WHAT IT REFUSES TO SAY
----------------------
The failure message names the PULL LIST as the thing that is out of date, never
the roster and never the leg. There is no reading of a finding here under which
the remedy is to stop trading something: the operator was explicit, and a guard
whose message invites the wrong fix is worse than no guard.

THREE FINDING KINDS, NEVER COLLAPSED — they have different remedies:

  ``pull_list_stale``   the account rosters a leg whose symbol its ``symbols:``
                        does not declare. Remedy: add the symbol to the pull
                        list. The leg trades either way.
  ``strategy_unknown``  the roster names a strategy absent from
                        strategies.yaml, or one declaring no ``symbols:`` — so
                        what to fetch for it is UNKNOWABLE, not merely
                        undeclared. Remedy: fix the roster entry or the
                        strategy's ``symbols:``.
  ``no_instrument_profile``
                        a rostered symbol has no entry in
                        ``config/instruments.yaml``, so
                        ``_instrument_exchange_for`` returns None and the tick
                        builds the PRIMARY account's connector for it. It would
                        be fetched from the wrong venue. Remedy: add the
                        profile.

POPULATION. Accounts with ``enabled: false`` or an explicit ``strategies: []``
are SKIPPED and COUNTED — they trade nothing, so they have no reachability
question, and dropping them silently would understate the denominator. The
``configured`` flag (credentials present in the VM's env) is deliberately NOT
consulted: it is not knowable from the repo, and a guard that graded it would
be measuring the runner's environment rather than the config.

Exit 0 clean, 1 with findings, 2 on a structural problem. ``--self-test`` runs
planted positive AND negative controls and touches no repo file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

ACCOUNTS = REPO / "config" / "accounts.yaml"
STRATEGIES = REPO / "config" / "strategies.yaml"


def _norm(sym: Any) -> str:
    """Match ``StrategyIntent`` normalisation (upper, ``/`` stripped)."""
    return str(sym).strip().upper().replace("/", "")


def skipped_accounts(accounts: Dict[str, Any]) -> List[str]:
    """Accounts that trade nothing — no reachability question to ask."""
    out = []
    for name, cfg in (accounts or {}).items():
        cfg = cfg or {}
        roster = cfg.get("strategies")
        if cfg.get("enabled") is False:
            out.append(f"{name} (enabled: false)")
        elif isinstance(roster, list) and not roster:
            out.append(f"{name} (strategies: [])")
    return out


def findings(accounts: Dict[str, Any], strategies: Dict[str, Any],
             profiled: frozenset) -> List[Dict[str, str]]:
    """Every rostered (account, strategy, symbol) that is not reachable.

    ``profiled`` is the set of symbols ``config/instruments.yaml`` knows, as
    ``_instrument_exchange_for`` resolves them. An EMPTY ``profiled`` disables
    the ``no_instrument_profile`` axis rather than flagging every symbol: "we
    could not read the profiles" and "this symbol has no profile" are opposite
    findings, and manufacturing the second from the first would bury the two
    axes that did measure something.
    """
    out: List[Dict[str, str]] = []
    for name, cfg in (accounts or {}).items():
        cfg = cfg or {}
        if cfg.get("enabled") is False:
            continue
        roster = cfg.get("strategies")
        if roster is None or (isinstance(roster, list) and not roster):
            continue
        declared = {_norm(s) for s in (cfg.get("symbols") or [])}
        for strat in roster:
            scfg = strategies.get(strat)
            syms = (scfg or {}).get("symbols") or []
            if not isinstance(scfg, dict) or not syms:
                out.append({
                    "kind": "strategy_unknown", "account": name,
                    "strategy": str(strat), "symbol": "",
                    "detail": ("absent from strategies.yaml"
                               if not isinstance(scfg, dict)
                               else "declares no `symbols:`"),
                })
                continue
            for sym in syms:
                n = _norm(sym)
                if n not in declared:
                    out.append({"kind": "pull_list_stale", "account": name,
                                "strategy": str(strat), "symbol": n,
                                "detail": ""})
                if profiled and n not in profiled:
                    out.append({"kind": "no_instrument_profile",
                                "account": name, "strategy": str(strat),
                                "symbol": n, "detail": ""})
    return out


def orphan_pulls(accounts: Dict[str, Any],
                 strategies: Dict[str, Any]) -> List[str]:
    """Declared symbols no rostered leg needs — CONTEXT, never a finding."""
    out = []
    for name, cfg in (accounts or {}).items():
        cfg = cfg or {}
        if cfg.get("enabled") is False:
            continue
        implied = set()
        for strat in (cfg.get("strategies") or []):
            for sym in ((strategies.get(strat) or {}).get("symbols") or []):
                implied.add(_norm(sym))
        for sym in (cfg.get("symbols") or []):
            if _norm(sym) not in implied:
                out.append(f"{name}:{_norm(sym)}")
    return out


def _load() -> tuple:
    import yaml  # noqa: PLC0415

    accounts = (yaml.safe_load(ACCOUNTS.read_text()) or {}).get("accounts") or {}
    strategies = (yaml.safe_load(STRATEGIES.read_text()) or {}).get("strategies") or {}
    try:
        from src.core.profile_loader import load_instrument_profiles
        profiled = frozenset(load_instrument_profiles() or {})
    except Exception as exc:  # noqa: BLE001
        print(f"  note: instrument profiles unreadable ({exc}) — the "
              "no_instrument_profile axis DID NOT RUN this invocation.")
        profiled = frozenset()
    return accounts, strategies, profiled


def _self_test() -> int:
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {label}: {'PASS' if cond else 'FAIL'}")
        ok = ok and cond

    print("roster-symbol-reachability self-test")
    profiled = frozenset({"BTCUSDT", "AVAXUSDT"})
    strategies = {
        "vwap": {"symbols": ["BTCUSDT"]},
        "avax_pullback_2h": {"symbols": ["AVAXUSDT"]},
        "no_symbols_leg": {"timeframe": "1h"},
    }

    # 1. PLANTED POSITIVE — the real 2026-09-22 shape: a real-money account
    #    rosters avax_pullback_2h while AVAXUSDT is absent from its pull list.
    a1 = {"bybit_2": {"strategies": ["vwap", "avax_pullback_2h"],
                      "symbols": ["BTCUSDT"]}}
    f1 = findings(a1, strategies, profiled)
    check("1 (rostered symbol absent from the pull list -> flagged as "
          "pull_list_stale, naming the symbol)",
          len(f1) == 1 and f1[0]["kind"] == "pull_list_stale"
          and f1[0]["symbol"] == "AVAXUSDT" and f1[0]["account"] == "bybit_2")

    # 2. NEGATIVE CONTROL — the same account with the pull list correct is
    #    SILENT. A guard that cannot be quiet is one everybody disables.
    a2 = {"bybit_2": {"strategies": ["vwap", "avax_pullback_2h"],
                      "symbols": ["BTCUSDT", "AVAXUSDT"]}}
    check("2 (pull list covers the roster -> clean)",
          findings(a2, strategies, profiled) == [])

    # 3. NEGATIVE CONTROL, the one that matters most: an ORPHAN declared symbol
    #    is legitimate additive data and must NEVER be a finding. 19 of these
    #    exist on the live config; flagging them would read as "delete these",
    #    which is the opposite of the operator's instruction.
    a3 = {"bybit_2": {"strategies": ["vwap"],
                      "symbols": ["BTCUSDT", "AVAXUSDT", "ADAUSDT"]}}
    check("3 (declared symbols no leg trades -> clean, and counted as orphans)",
          findings(a3, strategies, profiled) == []
          and sorted(orphan_pulls(a3, strategies))
          == ["bybit_2:ADAUSDT", "bybit_2:AVAXUSDT"])

    # 4. An account that trades nothing has no reachability question, and is
    #    counted rather than silently dropped.
    a4 = {"ib_live": {"strategies": [], "symbols": ["MES"]},
          "old": {"enabled": False, "strategies": ["vwap"], "symbols": []}}
    check("4 (strategies: [] and enabled: false -> skipped AND counted)",
          findings(a4, strategies, profiled) == []
          and sorted(skipped_accounts(a4))
          == ["ib_live (strategies: [])", "old (enabled: false)"])

    # 5. `strategies:` ABSENT is the legacy no-mapping account (loader keeps
    #    None distinct from []), which rosters nothing here.
    check("5 (no `strategies:` key -> nothing rostered, clean)",
          findings({"legacy": {"symbols": ["BTCUSDT"]}}, strategies,
                   profiled) == [])

    # 6. A roster naming a strategy we cannot resolve is UNKNOWABLE, not
    #    merely undeclared — a different remedy, so a different kind.
    a6 = {"x": {"strategies": ["ghost_leg"], "symbols": ["BTCUSDT"]}}
    f6 = findings(a6, strategies, profiled)
    check("6 (rostered strategy absent from strategies.yaml -> "
          "strategy_unknown, not pull_list_stale)",
          len(f6) == 1 and f6[0]["kind"] == "strategy_unknown")
    a6b = {"x": {"strategies": ["no_symbols_leg"], "symbols": ["BTCUSDT"]}}
    check("6b (rostered strategy declaring no `symbols:` -> strategy_unknown)",
          [f["kind"] for f in findings(a6b, strategies, profiled)]
          == ["strategy_unknown"])

    # 7. The venue axis: reachable to the fetch set but with no instrument
    #    profile, so the tick would build the PRIMARY account's connector.
    a7 = {"x": {"strategies": ["vwap"], "symbols": ["BTCUSDT"]}}
    s7 = {"vwap": {"symbols": ["ZZZUSDT"]}}
    kinds7 = sorted(f["kind"] for f in findings(a7, s7, profiled))
    check("7 (unprofiled symbol -> BOTH pull_list_stale and "
          "no_instrument_profile, since both remedies apply)",
          kinds7 == ["no_instrument_profile", "pull_list_stale"])

    # 8. `we could not read the profiles` must not manufacture findings on the
    #    axis it could not measure. Opposite states, never collapsed.
    kinds8 = sorted(f["kind"] for f in findings(a7, s7, frozenset()))
    check("8 (profiles unreadable -> venue axis silent, other axes still run)",
          kinds8 == ["pull_list_stale"])

    # 9. Normalisation: a case/slash difference is the SAME symbol, not a
    #    finding. Flagging it would send someone to 'fix' a correct pull list.
    a9 = {"x": {"strategies": ["vwap"], "symbols": ["btc/usdt"]}}
    check("9 (case + `/` differences normalise to the same symbol -> clean)",
          findings(a9, strategies, profiled) == [])

    print("self-test OK — a planted stale pull list goes red, a correct one "
          "and a 19-orphan pull list stay green, and an unmeasurable axis "
          "stays silent instead of firing."
          if ok else "self-test FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()

    try:
        accounts, strategies, profiled = _load()
    except Exception as exc:  # noqa: BLE001
        print(f"::error::roster-symbol-reachability could not read its inputs "
              f"({exc}) — NOTHING WAS CHECKED. This is not a pass.")
        return 2

    skipped = skipped_accounts(accounts)
    found = findings(accounts, strategies, profiled)
    orphans = orphan_pulls(accounts, strategies)

    print(f"roster-symbol-reachability: {len(accounts)} accounts, "
          f"{len(accounts) - len(skipped)} rostering at least one strategy "
          f"({len(skipped)} skipped: {', '.join(skipped) or 'none'}); "
          f"{len(strategies)} strategies in strategies.yaml; "
          f"{len(profiled)} instrument profiles; "
          f"{len(orphans)} orphan data-pulls (legitimate, never a finding).")

    if not found:
        print("OK — every rostered leg's symbol is reachable: declared in its "
              "account's pull list and carrying an instrument profile.")
        return 0

    print("\n::error::THE PULL LIST IS OUT OF DATE. "
          "`config/accounts.yaml::symbols` is an additive DATA-PULL list, not "
          "an execution gate — the roster decides what trades. Every row "
          "below is a symbol the pull list should name and does not. DO NOT "
          "resolve this by changing a roster or by demoting a leg: the leg "
          "trades regardless (the tick fetch set unions roster-implied "
          "symbols since 2026-09-22); what is missing is the DATA.")
    for f in sorted(found, key=lambda x: (x["kind"], x["account"], x["symbol"])):
        if f["kind"] == "pull_list_stale":
            print(f"  [pull_list_stale] {f['account']} rosters "
                  f"{f['strategy']} ({f['symbol']}) but its `symbols:` does "
                  f"not declare {f['symbol']} — add it to the pull list.")
        elif f["kind"] == "strategy_unknown":
            print(f"  [strategy_unknown] {f['account']} rosters "
                  f"{f['strategy']}, which {f['detail']} — what to pull for "
                  "it is UNKNOWABLE, not merely undeclared.")
        else:
            print(f"  [no_instrument_profile] {f['account']}/{f['strategy']} "
                  f"trades {f['symbol']}, which has no entry in "
                  "config/instruments.yaml — the tick can fetch it but "
                  "`_instrument_exchange_for` returns None, so it would be "
                  "read through the PRIMARY account's connector.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
