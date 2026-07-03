"""FRED wide-corpus adapter — multi-group daily context series (M19 corpus C2).

The second free adapter feeding the standing corpus store
([`ml.datasets.corpus_store`](../corpus_store.py); design:
[`docs/research/T0-data-corpus-DESIGN.md`](../../../docs/research/T0-data-corpus-DESIGN.md)
§ C2). Where `fred_macro` covers the vol/rates/dollar macro complex that the tree
heads already consume, this adapter broadens the panel across the **asset groups the
encoder needs but we don't trade** — equity indices, commodities, and credit — plus
the fuller Treasury curve. All keyless FRED daily series, so **no API key, no spend**.

It reuses `fred_macro`'s keyless `_daily_values` fetch + the shared
`ICT_OFFVM_BUILD_HOST` guard verbatim (DRY — one fetch path, one off-VM contract),
and returns the **raw** per-series `{date, value}` panel for
`corpus_store.write_series` to ingest. **No feature computation, no
`market_features` schema change** — the encoder reads the raw panel; feature
transforms stay the tree heads' business.

Default series (override per build via ``series=``), grouped for the catalog:

| FRED id | name | group | why it carries context |
|---|---|---|---|
| ``SP500`` | ``sp500`` | equity | broad US equity level — risk-on/off backdrop |
| ``NASDAQCOM`` | ``nasdaq`` | equity | growth/tech tilt vs the broad index |
| ``DCOILWTICO`` | ``wti_oil`` | commodity | the growth/inflation commodity bellwether |
| ``DCOILBRENTEU`` | ``brent_oil`` | commodity | global crude benchmark (WTI/Brent spread = logistics stress) |
| ``BAMLH0A0HYM2`` | ``hy_credit_oas`` | credit | high-yield credit spread — the cleanest risk-appetite gauge |
| ``DGS2`` | ``ust2y`` | rates | front-end policy expectations |
| ``DGS30`` | ``ust30y`` | rates | long-end growth/term-premium |
| ``T10Y2Y`` | ``ust_2s10s`` | rates | the 2s10s slope FRED publishes directly (recession tell) |
| ``DEXJPUS`` | ``usdjpy`` | fx | yen carry / risk-sentiment barometer (JPY per USD) |
| ``DEXUSEU`` | ``eurusd`` | fx | the deepest FX pair — dollar vs the euro bloc (USD per EUR) |
| ``DEXUSUK`` | ``gbpusd`` | fx | sterling — a second major to triangulate dollar strength (USD per GBP) |
| ``GVZCLS`` | ``gold_vol`` | commodity | CBOE gold-ETF volatility index — the haven metal's stress/risk read (the LBMA gold *fixing* series `GOLDAMGBD228NLBM`/`GOLDPMGBD228NLBM` were discontinued by FRED → HTTP 404, so the gold-complex signal is its vol index, not a price level) |
| ``DHHNGSP`` | ``natgas`` | commodity | Henry Hub natural gas — energy complex breadth beyond crude |

All keyless FRED daily series. Off-VM only; read-mostly; never `trade_journal.db`.
Tests monkeypatch `fred_macro._download` so CI never touches the network.

**Per-series resilience:** a single upstream series that FRED discontinues (→
404) must never zero the whole corpus, so `fetch_fred_corpus_series` fetches each
series independently and **skips** any that fail (recording them under
``_skipped``), rather than aborting. That is how the dead `GOLDAMGBD228NLBM`
surfaced — one 404 had aborted the entire fetch (0 series ingested).
"""
from __future__ import annotations

import sys
from typing import Any, Mapping

from . import fred_macro
from .fred_macro import OffVmGuardrailViolation  # re-exported for callers/tests

# FRED series id -> (corpus series name, group)
CORPUS_SERIES: Mapping[str, tuple[str, str]] = {
    "SP500": ("sp500", "equity"),
    "NASDAQCOM": ("nasdaq", "equity"),
    "DCOILWTICO": ("wti_oil", "commodity"),
    "DCOILBRENTEU": ("brent_oil", "commodity"),
    "BAMLH0A0HYM2": ("hy_credit_oas", "credit"),
    "DGS2": ("ust2y", "rates"),
    "DGS30": ("ust30y", "rates"),
    "T10Y2Y": ("ust_2s10s", "rates"),
    # FX majors (2026-07-03) — the biggest gap in the panel; dollar strength +
    # yen-carry/risk sentiment. All keyless FRED daily H.10 rates.
    "DEXJPUS": ("usdjpy", "fx"),
    "DEXUSEU": ("eurusd", "fx"),
    "DEXUSUK": ("gbpusd", "fx"),
    # Commodity complex beyond crude (2026-07-03): the haven metal's stress read
    # + the energy second leg. NB the LBMA gold *fixing* series
    # (GOLDAMGBD228NLBM / GOLDPMGBD228NLBM) were discontinued by FRED (404), so
    # the gold-complex signal is the CBOE gold-ETF vol index, not a price level.
    "GVZCLS": ("gold_vol", "commodity"),
    "DHHNGSP": ("natgas", "commodity"),
}

__all__ = ["CORPUS_SERIES", "OffVmGuardrailViolation", "fetch_fred_corpus_series"]


def fetch_fred_corpus_series(
    *,
    start: str,
    end: str | None = None,
    series: Mapping[str, tuple[str, str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Raw daily observations for each corpus series, keyed by FRED series id.

    Returns ``{fred_id: {"name": <corpus name>, "group": <group>, "rows":
    [{"date": "YYYY-MM-DD", "value": <float>}, ...]}}`` — ascending, keyless.
    ``series`` fully replaces the default catalog when given (unlike `fred_macro`'s
    additive override, since here the *set* of series is the whole point). Off-VM
    guarded via the shared `fred_macro` contract.

    **Per-series resilient:** each series is fetched independently and a failure
    (e.g. an id FRED has discontinued → HTTP 404, or a transient fetch error) is
    **skipped** — recorded under the sentinel key ``_skipped`` (``{fred_id:
    error_str}``) and logged to stderr — rather than aborting the whole corpus.
    A single dead upstream series must never zero the panel (the `GOLDAMGBD228NLBM`
    incident). ``_skipped`` is metadata, not a series (callers that iterate the
    result to register series should skip the ``_skipped`` key).
    """
    fred_macro._enforce_offvm()
    catalog: dict[str, tuple[str, str]] = dict(series) if series else dict(CORPUS_SERIES)
    out: dict[str, dict[str, Any]] = {}
    skipped: dict[str, str] = {}
    for fred_id, (name, group) in catalog.items():
        try:
            values = fred_macro._daily_values(fred_id, start, end)
        except Exception as exc:  # discontinued id (404) / transient fetch error
            skipped[fred_id] = f"{type(exc).__name__}: {exc}"
            print(
                f"[fred_corpus] WARNING: skipping series {fred_id} ({name}): {exc}",
                file=sys.stderr,
            )
            continue
        out[fred_id] = {
            "name": name,
            "group": group,
            "rows": [{"date": d, "value": values[d]} for d in sorted(values)],
        }
    if skipped:
        out["_skipped"] = skipped  # type: ignore[assignment]
    return out
