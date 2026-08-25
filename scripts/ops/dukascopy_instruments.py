#!/usr/bin/env python3
# wiring: manual-only - a LIBRARY. It is imported by the span probe, the
# backtest candle fetcher and the e35 shard planner; it runs nothing itself, so
# a scheduled runner would be scheduling a lookup table.
"""THE ONE OWNER of "which Dukascopy instrument serves which of our symbols".

WHY THIS EXISTS
---------------
The mapping is an ADJUDICATION, not a lookup — it was decided by hand in
``docs/research/dukascopy-coverage-adjudication-2026-08-24.md`` after a raw
substring probe got two of eighteen wrong IN OPPOSITE DIRECTIONS:

* FALSE POSITIVE — ``MHG`` matched ``INSTRUMENT_NORWAY_MHG_NO_NOK``, which is
  **Mowi ASA, a Norwegian salmon farmer**. Ours is CME Micro Copper. A reader
  trusting the hit would have backtested copper against fish.
* FALSE NEGATIVE — ``XAUUSD`` read as unmatched while the venue carries
  ``INSTRUMENT_FX_METALS_XAU_USD``; an underscore defeated the search.

So the map is transcribed from that document and lives in exactly one place.
Three consumers import it — ``scripts/research/dukascopy_span_probe.py``,
``scripts/ops/fetch_backtest_candles.py`` and ``scripts/research/
e35_bracket_geometry_sweep.py`` — and a second copy is how one of them would
quietly start fetching a different instrument than the one whose depth was
measured.

RELATION IS CARRIED, NEVER DROPPED
----------------------------------
``same_ticker_cfd`` is the same underlying **but still a CFD** — financing and
fees differ from the ETF. ``proxy`` is a DIFFERENT INSTRUMENT that tracks
something related. Collapsing those two is the semantic substitution
``diagnostic-provenance-guard`` sub-class A exists to stop, so every consumer
gets the relation alongside the instrument and can surface it.

REFUSALS ARE FIRST-CLASS, NOT ABSENCES
--------------------------------------
``REFUSED`` records the symbols the adjudication decided must NOT be proxied,
with the reason. A caller asking for one gets a refusal it can print — not a
``KeyError`` and not a silent miss, because "we decided not to" and "we have no
entry" are different facts and only the first one is a decision anybody made.
"""
from __future__ import annotations

from typing import Dict, NamedTuple, Optional

#: Where the judgement lives. Quoted in refusals so a reader can go argue with
#: the decision rather than with this table.
ADJUDICATION_DOC = "docs/research/dukascopy-coverage-adjudication-2026-08-24.md"

REL_SAME_TICKER_CFD = "same_ticker_cfd"
REL_RELAXED = "relaxed"
REL_PROXY = "proxy"


class Mapping(NamedTuple):
    instrument: str
    relation: str
    note: str


#: OUR symbol -> the Dukascopy instrument the adjudication assigned it.
#: Several of our symbols share an instrument (SPLG->SPY, IAUM->GLD,
#: SCHA->IWM, MGC->XAU_USD); that is deliberate and is why the span probe keys
#: its measurements by INSTRUMENT while this table keys by SYMBOL.
SYMBOL_TO_INSTRUMENT: Dict[str, Mapping] = {
    # --- same-ticker US ETF CFDs (exact match) -----------------------------
    "GDX": Mapping("INSTRUMENT_ETF_CFD_US_GDX_US_USD", REL_SAME_TICKER_CFD, "same underlying, CFD"),
    "GLD": Mapping("INSTRUMENT_ETF_CFD_US_GLD_US_USD", REL_SAME_TICKER_CFD, "same underlying, CFD"),
    "IEF": Mapping("INSTRUMENT_ETF_CFD_US_IEF_US_USD", REL_SAME_TICKER_CFD, "same underlying, CFD"),
    "IWM": Mapping("INSTRUMENT_ETF_CFD_US_IWM_US_USD", REL_SAME_TICKER_CFD, "same underlying, CFD"),
    "QQQ": Mapping("INSTRUMENT_ETF_CFD_US_QQQ_US_USD", REL_SAME_TICKER_CFD, "same underlying, CFD"),
    "SLV": Mapping("INSTRUMENT_ETF_CFD_US_SLV_US_USD", REL_SAME_TICKER_CFD, "same underlying, CFD"),
    "SPY": Mapping("INSTRUMENT_ETF_CFD_US_SPY_US_USD", REL_SAME_TICKER_CFD, "same underlying, CFD"),
    "TLT": Mapping("INSTRUMENT_ETF_CFD_US_TLT_US_USD", REL_SAME_TICKER_CFD, "same underlying, CFD"),
    "USO": Mapping("INSTRUMENT_ETF_CFD_US_USO_US_USD", REL_SAME_TICKER_CFD, "same underlying, CFD"),
    # --- matched only via the punctuation-insensitive form ------------------
    "XAUUSD": Mapping("INSTRUMENT_FX_METALS_XAU_USD", REL_RELAXED,
                      "the relaxed form was needed; an exact substring missed it"),
    # --- adjudicated proxies: a DIFFERENT instrument ------------------------
    "SPLG": Mapping("INSTRUMENT_ETF_CFD_US_SPY_US_USD", REL_PROXY,
                    "close: same index, different share price + expense ratio"),
    "IAUM": Mapping("INSTRUMENT_ETF_CFD_US_GLD_US_USD", REL_PROXY,
                    "close: same underlying (gold), different vehicle"),
    "SCHA": Mapping("INSTRUMENT_ETF_CFD_US_IWM_US_USD", REL_PROXY,
                    "⚠️ A DIFFERENT INDEX: SCHA tracks DJ US Small-Cap, IWM tracks Russell 2000"),
    "MGC": Mapping("INSTRUMENT_FX_METALS_XAU_USD", REL_PROXY,
                   "spot vs future: no roll, different carry (contango/backwardation)"),
    "MES": Mapping("INSTRUMENT_IDX_AMERICA_E_SANDP_500", REL_PROXY,
                   "CFD vs future: different venue, hours, financing, settlement, "
                   "multiplier, no expiry/roll"),
}

#: Symbols the adjudication REFUSED to proxy, and why. A refusal is a decision;
#: it is recorded so a caller can print it rather than report a bare miss.
REFUSED: Dict[str, str] = {
    "QLD": ("ProShares Ultra QQQ is 2x with a DAILY LEVERAGE RESET, so its path is "
            "not 2x the QQQ path — a QQQ series is not a substitute at any horizon"),
    "TQQQ": ("ProShares UltraPro QQQ is 3x with a DAILY LEVERAGE RESET, so its path is "
             "not 3x the QQQ path — a QQQ series is not a substitute at any horizon"),
    "MHG": ("CME Micro Copper. The only same-ticker hit in the whole 1388-line catalogue "
            "was INSTRUMENT_NORWAY_MHG_NO_NOK — Mowi ASA, a Norwegian salmon farmer"),
}


class Resolution(NamedTuple):
    """Three states, never collapsed.

    ``mapped``    we have an adjudicated instrument for this symbol.
    ``refused``   the adjudication DECIDED not to proxy it — a judgement, with a
                  reason a caller can print.
    ``unknown``   the symbol is in neither table. NOT the same as ``refused``:
                  nobody has ruled on it, so the remedy is to adjudicate it, not
                  to argue with an existing decision.
    """
    state: str
    instrument: Optional[str]
    relation: Optional[str]
    reason: Optional[str]


STATE_MAPPED = "mapped"
STATE_REFUSED = "refused"
STATE_UNKNOWN = "unknown"


def resolve(symbol: str) -> Resolution:
    """Resolve OUR symbol to a Dukascopy instrument, a refusal, or 'unadjudicated'."""
    sym = (symbol or "").strip().upper()
    m = SYMBOL_TO_INSTRUMENT.get(sym)
    if m is not None:
        return Resolution(STATE_MAPPED, m.instrument, m.relation, m.note)
    why = REFUSED.get(sym)
    if why is not None:
        return Resolution(STATE_REFUSED, None, None, f"{why} (see {ADJUDICATION_DOC})")
    return Resolution(STATE_UNKNOWN, None, None,
                      f"{sym!r} has not been adjudicated against the Dukascopy "
                      f"catalogue; see {ADJUDICATION_DOC}")


def instruments_with_symbols() -> Dict[str, Dict[str, object]]:
    """Invert the map to instrument -> {serves:[symbols], relation:str}.

    The span probe measures DEPTH per instrument, not per symbol: probing
    ``SPY`` and ``SPLG`` separately would issue twice the requests to learn one
    fact. The composite relation (e.g. ``same_ticker_cfd+proxy``) is built here
    so the probe never has to re-derive it.
    """
    out: Dict[str, Dict[str, object]] = {}
    for sym, m in SYMBOL_TO_INSTRUMENT.items():
        b = out.setdefault(m.instrument, {"serves": [], "relations": []})
        b["serves"].append(sym)  # type: ignore[union-attr]
        if m.relation not in b["relations"]:  # type: ignore[operator]
            b["relations"].append(m.relation)  # type: ignore[union-attr]
    for b in out.values():
        b["serves"] = sorted(b["serves"])  # type: ignore[arg-type]
        b["relation"] = "+".join(b.pop("relations"))  # type: ignore[arg-type]
    return out


def _self_test() -> int:
    checks = []

    def ck(name, ok):
        checks.append(bool(ok))
        print(f"  {'ok ' if ok else 'FAIL'} {name}")

    ck("a same-ticker CFD resolves", resolve("SPY").instrument
       == "INSTRUMENT_ETF_CFD_US_SPY_US_USD")
    ck("resolution is case/space insensitive", resolve(" spy ").state == STATE_MAPPED)
    ck("a proxy carries the PROXY relation, not same_ticker",
       resolve("SPLG").relation == REL_PROXY)
    ck("XAUUSD is marked `relaxed`, not silently equated with an exact hit",
       resolve("XAUUSD").relation == REL_RELAXED)

    # The three states must stay apart — this is the whole contract.
    r = resolve("QLD")
    ck("a REFUSED symbol is refused, not unknown and not mapped",
       r.state == STATE_REFUSED and r.instrument is None
       and "leverage reset" in (r.reason or "").lower())
    ck("an UNADJUDICATED symbol is `unknown`, NOT `refused`",
       resolve("NVDA").state == STATE_UNKNOWN)
    ck("...and refused != unknown, because only one of them is a decision",
       resolve("QLD").state != resolve("NVDA").state)
    ck("MHG's refusal names the salmon farmer, so nobody re-derives it",
       "Mowi" in (resolve("MHG").reason or ""))

    inv = instruments_with_symbols()
    ck("SPY and SPLG share ONE instrument (probe once, not twice)",
       set(inv["INSTRUMENT_ETF_CFD_US_SPY_US_USD"]["serves"]) == {"SPY", "SPLG"})
    ck("a shared instrument's relation is composite",
       inv["INSTRUMENT_ETF_CFD_US_SPY_US_USD"]["relation"] == "same_ticker_cfd+proxy")
    ck("11 distinct instruments cover 15 symbols",
       len(inv) == 11 and len(SYMBOL_TO_INSTRUMENT) == 15)
    ck("no refused symbol leaks into the mapped table",
       not (set(REFUSED) & set(SYMBOL_TO_INSTRUMENT)))

    ok = sum(checks)
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())
