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
| `NEWS_ENABLED` | `true` | Set to `false` to disable the entire module |
| `NEWS_API_KEY` | — | NewsAPI key (required when enabled) |
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

**Default posture: disabled.**  The `.env.live` template ships with
`NEWS_ENABLED=false`.  When disabled, `get_news_score` returns a neutral result
instantly (no network call, no latency) and the pipeline proceeds unchanged.

### Enabling the veto gate

1. Obtain a free NewsAPI key at <https://newsapi.org>.
2. In `.env.live`, set:
   ```
   NEWS_ENABLED=true
   NEWS_API_KEY=your_newsapi_key_here
   ```
3. Optionally tune the veto thresholds:
   ```
   NEWS_VETO_SENTIMENT_THRESHOLD=-0.3   # default: -0.3
   NEWS_VETO_IMPACT_THRESHOLD=0.5       # default: 0.5
   NEWS_CACHE_TTL=300                   # seconds; default: 300 (5 min)
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

## Adding a new data source (future)

1. Create `src/news/news_client_<source>.py` mirroring the `fetch_news(settings)`
   signature — return a list of raw dicts.
2. Add a normalizer branch in `news_normalizer.py` that maps the new raw format
   to the internal schema.
3. Select the active source via a new `NEWS_SOURCE` config key in
   `news_pipeline.py`.
4. The scorer, cache, and veto logic are source-agnostic and need no changes.
