# M9 — News-Augmented Trade Decision Layer

The `src/news` package is an **additive, isolated** layer that adjusts trade
probability using live news sentiment.  It does not replace or alter the
existing strategy stack; it exposes a single score that the pipeline can
optionally consume to nudge a decision up or down, or veto it entirely.

---

## Quick start

```python
from src.news import get_news_score, adjust_probability

# Call once per strategy tick (network result is cached for NEWS_CACHE_TTL seconds)
result = get_news_score(settings, symbol_tags=["BTC", "BTCUSDT"])

print(result.decision)    # "boost" | "reduce" | "veto" | "neutral"
print(result.adjustment)  # float in [-1, 1]
print(result.veto)        # bool
print(result.reason)      # human-readable explanation

# Apply the adjustment to a base probability
final_prob = adjust_probability(base_prob, result)
# adjust_probability returns 0.0 on veto, otherwise clamps to [0, 1]
```

`get_news_score` never raises.  If the module is disabled, the key is absent,
or any network/parse error occurs, it returns a neutral `NewsScoreResult`
with `adjustment=0.0` and `veto=False`.

---

## Internal schema

Each news article is normalized into this structure before scoring:

```python
{
    "timestamp":        "2025-01-06T08:00:00Z",   # ISO-8601 UTC publication time
    "source":           "newsapi:Reuters",          # "newsapi:<source-name>"
    "headline":         "Bitcoin surges ...",
    "summary":          "...",
    "url":              "https://...",
    "symbol_tags":      ["BTC"],                   # matched symbols found in text
    "sentiment_score":  0.4,                       # float in [-1, 1]
    "relevance_score":  0.8,                       # float in [0, 1]
    "is_macro_only":    false,                     # relevant only via shared macro keywords (no ticker hit)
    "impact_score":     0.6,                       # float in [0, 1]
    "freshness_minutes": 12.0,                     # minutes since publication
    "reason":           "positive sentiment; high relevance; high impact",
}
```

---

## Score formula

```
freshness_score  = max(0,  1 − freshness_minutes / NEWS_MAX_AGE_MINUTES)

item_score       = sentiment_score
                   × relevance_score
                   × freshness_score
                   × impact_score

# Weighted aggregation (NEWS_WEIGHTED_AGGREGATION=true, default):
news_adjustment  = Σ(item_score_i × relevance_i) / Σ(relevance_i)

# Plain mean (NEWS_WEIGHTED_AGGREGATION=false):
news_adjustment  = mean(item_scores)

# Probability nudge (±15 pp maximum):
final_prob       = clamp(base_prob + news_adjustment × 0.15,  0.0, 1.0)
```

**Veto** overrides the adjustment entirely.  When any article has
`sentiment_score < NEWS_VETO_SENTIMENT_THRESHOLD` **and**
`impact_score > NEWS_VETO_IMPACT_THRESHOLD`, `adjust_probability` returns
`0.0` and `result.veto` is `True`.

---

## Decision labels

| `result.decision` | Meaning |
|---|---|
| `"boost"` | `adjustment > 0.05` — news is net-positive for the trade |
| `"reduce"` | `adjustment < -0.05` — news is net-negative |
| `"veto"` | A high-impact adverse article triggered the veto gate |
| `"neutral"` | `|adjustment| ≤ 0.05` or no relevant/fresh articles |

---

## Logging

Log the full payload after each tick for audit purposes:

```python
import json, logging
log = logging.getLogger(__name__)

result = get_news_score(settings, symbol_tags=["BTC"])
log.info("news_score %s", json.dumps({
    "base_score":       base,
    "news_adjustment":  result.adjustment,
    "final_score":      adjust_probability(base, result),
    "decision":         result.decision,
    "reason":           result.reason,
    "item_count":       result.item_count,
    "veto":             result.veto,
}))
```

---

## Configuration reference

All keys are read from the `settings` dict first, then fall back to environment
variables (same pattern as the rest of the pipeline).

| Key | Default | Description |
|---|---|---|
| `NEWS_SOURCE` | `newsapi` | Feed backend **and activation gate** — `rss` (keyless, always active) or `newsapi` (active only when `NEWS_API_KEY` is set). There is no separate enable flag (the legacy `NEWS_ENABLED` gate was removed 2026-06-10). |
| `NEWS_API_KEY` | — | NewsAPI key — required for the `newsapi` source; its presence is that source's activation gate. Unused for `rss`. |
| `NEWS_QUERY` | `"Bitcoin OR BTC"` | NewsAPI search query |
| `NEWS_MAX_ARTICLES` | `10` | Articles fetched per call (1–100) |
| `NEWS_CACHE_TTL` | `300` | Seconds to cache fetched articles |
| `NEWS_MAX_AGE_MINUTES` | `120` | Articles older than this are ignored |
| `NEWS_WEIGHTED_AGGREGATION` | `true` | Weight items by `relevance_score` |
| `NEWS_POSITIVE_KEYWORDS` | — | Comma-separated words that add positive sentiment |
| `NEWS_NEGATIVE_KEYWORDS` | — | Comma-separated words that add negative sentiment |
| `NEWS_VETO_ENABLED` | `true` | Enable the veto gate |
| `NEWS_VETO_SENTIMENT_THRESHOLD` | `-0.6` | Sentiment threshold for veto |
| `NEWS_VETO_IMPACT_THRESHOLD` | `0.7` | Impact threshold for veto |

### Extending keyword lists

The built-in lists cover common crypto/market terms.  Add domain-specific
signals via the comma-separated settings without touching source code:

```
NEWS_POSITIVE_KEYWORDS=halving,etf,institutional
NEWS_NEGATIVE_KEYWORDS=delist,freeze,exploit,class-action
```

Custom words are additive — the built-in lists remain active.

---

## Module layout

```
src/news/
  __init__.py          Public API: get_news_score, score_news,
                       adjust_probability, NewsScoreResult
  news_cache.py        Thread-safe in-memory TTL cache (module singleton)
  news_client.py       NewsAPI /v2/everything fetcher; returns [] on any error
  news_normalizer.py   Raw article → internal schema; keyword sentiment scorer
  news_score.py        Weighted aggregator, veto gate, adjust_probability()
  news_pipeline.py     get_news_score(): fetch → normalize → score in one call

tests/
  test_news_layer.py      Unit tests for cache, normalizer, scorer (46 tests)
  test_news_pipeline.py   Integration tests for get_news_score (25 tests)
  test_news_scoring.py    Calibration tests for weighting and keywords (26 tests)
```

---

## Going live

The news veto hook is wired into `src/runtime/pipeline.py`.  It runs on every
actionable signal tick (after risk-counter injection, before `safe_place_order`).

**Default posture: inert (no usable source).**  The default `NEWS_SOURCE`
is `newsapi` with a blank `news.api_key`, so the layer is a neutral no-op until
either a `NEWS_API_KEY` is supplied (newsapi) or `NEWS_SOURCE=rss` is selected
(keyless). `scripts/render_env_from_master.py` always writes `NEWS_API_KEY`
into the rendered `.env.live` — its absence is a config bug, not a silent
default. (`NEWS_ENABLED` is no longer rendered or read — the separate enable
gate was removed 2026-06-10; activation is source-driven.)

> **Note:** the layer activates the moment a usable source is configured
> (`NEWS_SOURCE=rss`, or `newsapi` + `NEWS_API_KEY`). A live source **can**
> `news_veto` a trade — selecting a source is the deliberate activation, so
> there's no separate flag to forget.

### Enabling the veto gate

1. Obtain a free NewsAPI key at <https://newsapi.org>.
2. In `config/master-secrets.yaml` (plaintext, never committed), set:
   ```yaml
   news:
     api_key: "your_newsapi_key_here"
   ```
   (Supplying the key activates the `newsapi` source; there is no separate
   `enabled` flag — the legacy `NEWS_ENABLED` gate was removed 2026-06-10.
   For the keyless real-time path, set `NEWS_SOURCE=rss` instead.)
3. Re-render `.env.live`:
   ```bash
   python scripts/render_env_from_master.py \
     --master config/master-secrets.sops.yaml \
     --age-key-file age-keys.txt \
     --profile live --out .env.live --allow-live
   ```
4. Optionally tune the veto thresholds (add to the `news:` block):
   ```yaml
   veto_sentiment_threshold: "-0.3"   # default: -0.3
   veto_impact_threshold: "0.5"       # default: 0.5
   cache_ttl: "300"                   # seconds; default: 300 (5 min)
   ```

### Veto behaviour

When the veto fires the pipeline returns:
```python
{"status": "news_veto", "reason": "<reason string>", "signal": <signal dict>}
```
This is surfaced as `order_result.status == "news_veto"` in the outer return
dict and logged via `log_signal` alongside the signal audit trail.  A Telegram
notification is sent with `status=news_veto`.

Non-veto ticks log at INFO:
```
news: decision=reduce adj=-0.2100 items=3 reason=...
```

### Symbol tag derivation

The pipeline derives `symbol_tags` automatically from `signal["symbol"]`:

| Signal symbol  | Tags passed to `get_news_score` |
|----------------|----------------------------------|
| `"BTCUSDT"`    | `["BTC", "BTCUSDT"]`            |
| `"BTC/USDT:USDT"` | `["BTC", "BTC/USDT:USDT"]`  |
| `"ETHUSDT"`    | `["ETH", "ETHUSDT"]`            |

---

## Multi-asset support (2026-06-09)

The v1 layer was **Bitcoin-only**: its relevance dictionary
(`news_normalizer._SYMBOL_KEYWORDS`) only knew crypto tickers and it always
fetched the hardcoded `"Bitcoin OR BTC"` query. As the bot expands to
index/commodity futures (MES, MNQ, MGC, MHG, MCL …) that made the layer a
**silent no-op for every non-crypto symbol** — relevance scored ~0, so every
article was dropped before scoring.

Per-symbol behaviour now lives in **`config/news_symbols.yaml`** (loaded by
`src/news/news_symbols.py`). Each symbol *base* (the symbol with any
`USDT`/`PERP`/`USD` suffix and quote stripped — `BTCUSDT`→`BTC`, `MES`→`MES`)
maps to:

| Field | Effect |
|---|---|
| `query` | the NewsAPI search query fetched when that symbol is trading (S&P/Fed news for MES, gold for MGC, copper for MHG, …) |
| `keywords` | relevance keywords matched (lower-cased substring) in headline+summary |

**Query precedence:** per-symbol config `query` → explicit `NEWS_QUERY` →
the Bitcoin default. A config match wins so a global `NEWS_QUERY` can't
re-break a futures instrument.

**Relevance precedence:** `config/news_symbols.yaml` keywords → the built-in
`_SYMBOL_KEYWORDS` crypto map → the base's own lower-cased token. The loader
**never raises**: an absent/malformed file degrades to the built-in crypto
behaviour. Adding a new instrument is a **YAML edit, not a code change**.

## Macro / cross-asset relevance (2026-07-08)

The per-symbol `keywords` above answer *"is this article about the instrument
I'm trading?"* — but they're **ticker-only for crypto** (`bitcoin`, `btc`,
`crypto`, …). That reintroduced the original blindness for the *most-traded*
symbol: the `global` macro feed (Fed, MarketWatch, CNBC) **was** fetched for a
BTC signal, but a Fed / inflation / dollar article that never says "bitcoin"
scored relevance `0` and was dropped before scoring. So the layer only ever
weighed Bitcoin-specific headlines — never the general macro that actually
drives crypto (Fed liquidity, risk sentiment, the dollar). Index/commodity
symbols escaped this only because their `keywords` already inlined macro terms.

The fix is a **shared macro keyword layer** applied to *every* symbol
(`config/news_symbols.yaml::defaults.macro_keywords`, with a built-in fallback
in `news_symbols._BUILTIN_MACRO_KEYWORDS` so it never strands). Relevance is now
**tiered** (`news_normalizer._relevance_breakdown`):

| Article matches… | relevance contribution |
|---|---|
| the instrument's own `keywords` | **full** (1.0) — `symbol_matched=True` |
| **only** a shared macro keyword | **partial** (`defaults.macro_relevance_weight`, default `0.5`) |
| neither | `0.0` (dropped, unchanged) |

So a macro article informs **every** decision (crypto included) at a secondary
weight, while instrument-specific news still dominates the relevance-weighted
aggregate. Each normalized article carries **`is_macro_only`** (relevant only
via macro, no ticker hit).

**Veto stays scoped to instrument-specific news.** The adverse-news veto
(`news_score.score_news`) **excludes `is_macro_only` items**, so switching the
macro layer on does **not** change what blocks a live trade — macro moves the
sizing adjustment and the dashboard feed, never the veto on its own. (A macro
article that *also* hits an instrument keyword is not macro-only and vetoes as
before.)

Feeds: the shared **`global`** group in `config/news_feeds.yaml` now also
carries CNBC Economy + CNBC Markets, so every symbol pulls a solid macro/econ
feed regardless of its asset-class group.

## Shadow-soak log (`runtime_logs/news_decisions.jsonl`)

Before the news veto/influence is ever allowed to gate live money, we accrue an
**observe-only** track record — exactly like the shadow-model ladder. While the
layer is active (`NEWS_SOURCE=rss`, or `newsapi` + `NEWS_API_KEY`), `src/news/news_audit.py`
appends one JSON line per actionable signal it evaluated:

```json
{"ts":"…","symbol":"MES","side":"buy","strategy":"vwap","query":"S&P 500 …",
 "decision":"reduce","adjustment":-0.21,"veto":false,"item_count":3,"reason":"…"}
```

The writer is **best-effort** (swallows all errors — a failed write never
affects the trade) and **gated on `news_client.is_active`** so the log stays
empty until the layer is enabled. Readable via the diag surface:
`GET /api/diag/log_file?name=news_decisions&lines=N`. This is the data a later
review uses to answer "would enabling this veto/influence have helped?" against
real closed trades **before** flipping it on live.

> **Graduated influence (Tier-3):** the reductive *operator* now exists —
> `src/news/news_influence.py` (`news_size_factor`), designed in
> [`docs/news-influence-DESIGN.md`](news-influence-DESIGN.md). It reasons about
> whether the news (and an injected `event_risk`) **supports the trade
> direction or threatens to knock it off course** and returns a size factor in
> `[size_floor, 1.0]` — reductive-only, default-off (`NEWS_INFLUENCE_MODE`).
> It **is now wired** into `Coordinator.multi_account_execute` (step 2,
> `src/runtime/news_sizing.py`) — applied right after the advisory downsize and
> composed multiplicatively with it, **default-off** via `NEWS_INFLUENCE_MODE`
> (off/annotate/downsize). The pipeline stamps the news score onto `pkg.meta`;
> the sizing hook reads it. The `event_risk` input is fed by the
> **economic-calendar source** (`src/news/news_events.py` +
> `config/economic_calendar.yaml`, step 3): `event_risk = impact × proximity`
> for the traded symbol's relevant event classes, stamped onto `pkg.meta` (0.0
> when no event is in window). Applied downsizes are logged to
> `news_decisions.jsonl` and surfaced at `GET /api/bot/news/recent`. With the
> flag off the live path acts only on the veto.

## Source selection — RSS (free, real-time) vs NewsAPI

`NEWS_SOURCE` selects the feed backend (`news_pipeline._news_source`):

| `NEWS_SOURCE` | Backend | Key? | Latency |
|---|---|---|---|
| `newsapi` (default) | `news_client.fetch_news` (NewsAPI `/v2/everything`) | **yes** (`NEWS_API_KEY`) | free tier ≈ **24h delayed** → articles fail the 120-min freshness gate → layer effectively inert |
| `rss` | `news_client_rss.fetch_news_rss` | **no** | **real-time** (publisher `pubDate`, usually minutes old) |

**RSS is the recommended source** — keyless and real-time, so it sidesteps the
NewsAPI free-tier delay that otherwise leaves the layer scoring everything
`neutral`. Feeds live in **`config/news_feeds.yaml`** (per-asset-class groups +
a shared `global` group; `news_feeds.feeds_for_tags` resolves the set per traded
symbol). The RSS client is stdlib-only (urllib + `xml.etree`), handles RSS 2.0
and Atom, and emits the **same raw-article shape** the normalizer consumes, so
scoring / veto / multi-asset relevance are unchanged. Activation is
source-driven (no `NEWS_ENABLED` gate): RSS needs no key, so
`news_client.is_active` treats `NEWS_SOURCE=rss` as active outright; newsapi
needs a key.

Enable RSS on the VM (no key needed):
`set-env NEWS_SOURCE=rss` (service `ict-trader-live`).
Verify fresh articles first with the `news-key-check` workflow (it reports
`fresh(<=120m)` + `fresh&relevant` counts per symbol).

## Adding a new data source (future)

1. Create `src/news/news_client_<source>.py` mirroring the `fetch_news(settings)`
   signature — return a list of raw dicts.
2. Add a normalizer branch in `news_normalizer.py` that maps the new raw format
   to the internal schema.
3. Select the active source via a new `NEWS_SOURCE` config key in
   `news_pipeline.py`.
4. The scorer, cache, and veto logic are source-agnostic and need no changes.
