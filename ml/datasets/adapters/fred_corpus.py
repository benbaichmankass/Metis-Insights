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

**Panel widened 2026-07-04 (M19 T1.2 Phase 3):** the ``CORPUS_SERIES`` dict below
is the authoritative catalog — the table above lists the founding series; the
2026-07-04 breadth pass roughly doubled it to 28 (fuller Treasury curve,
breakevens, VIX + broader equity, IG credit, the broad-dollar index + more FX
crosses) after the SSL encoder overfit the original thin 13-series panel. See the
inline comment block on the dict for the additions and rationale.

**T1.2 outcome (2026-07-04):** the wider corpus DID cut the encoder's overfit
(val_loss 2.0→1.3) but the `corpus_emb` block still lost the downstream BTC-15m
regime A/B to both the baseline and the frozen-Chronos T0.1 embedding — a clean
negative, replicated across both corpus widths
(`docs/research/T1.2-ssl-encoder-AB-evidence-2026-07-04.md`). The adapter + store
stay as sound reusable infra; the negative is about the daily-panel→intraday-vol
representation mismatch, not the corpus tooling.

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
    # --- Breadth widening (2026-07-04, M19 T1.2 Phase 3) --------------------
    # The 13-series panel was too thin for the SSL encoder to learn a
    # generalizable market state (train recon 0.036 vs val 2.006 → overfit; the
    # corpus_emb block LOST to the frozen-Chronos T0.1 emb on macro_f1 AND
    # f1_volatile in the first P2 A/B on BTC-15m regime). More series = richer
    # cross-asset structure for the masked-reconstruction objective and less
    # capacity to memorize. All keyless FRED daily; the per-series resilience
    # skips any id FRED discontinues (→ 404) so an over-reach can't zero the panel.
    #
    # Fuller Treasury curve (the whole-curve shape is high-signal for a regime encoder):
    "DGS3MO": ("ust3mo", "rates"),
    "DGS1": ("ust1y", "rates"),
    "DGS5": ("ust5y", "rates"),
    "DGS10": ("ust10y", "rates"),
    "T10Y3M": ("ust_3m10y", "rates"),
    "DFF": ("fed_funds", "rates"),
    # Inflation expectations (breakevens) — the real-vs-nominal axis:
    "T5YIE": ("breakeven5y", "rates"),
    "T10YIE": ("breakeven10y", "rates"),
    # Equity vol + broader equity breadth:
    "VIXCLS": ("vix", "equity"),
    "DJIA": ("dow", "equity"),
    # (WILL5000INDFC / Wilshire 5000 was dropped 2026-07-04 — that FRED id 404s;
    # the per-series-resilient fetch skipped it, so it never zeroed the panel, but
    # a known-dead id doesn't belong in the catalog. SP500/NASDAQCOM/DJIA/VIXCLS
    # already cover the equity breadth axis.)
    # Investment-grade credit (pairs with the HY OAS for the credit axis):
    "BAMLC0A0CM": ("ig_credit_oas", "credit"),
    # Dollar breadth + more FX crosses (carry / commodity-currency / haven):
    "DTWEXBGS": ("broad_dollar", "fx"),
    "DEXCAUS": ("usdcad", "fx"),
    "DEXUSAL": ("audusd", "fx"),
    "DEXSZUS": ("usdchf", "fx"),
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
