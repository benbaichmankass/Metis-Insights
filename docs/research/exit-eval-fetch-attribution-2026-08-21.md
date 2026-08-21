# Where the exit-evaluation pass spends its time — and the one change that would give it back

**Date:** 2026-08-21 · **Session:** `wave0-8g7443` · **Item:** Wave 0.1 of
[`docs/claude/WORKPLAN-2026-08-21.md`](../claude/WORKPLAN-2026-08-21.md) ·
**Row:** `BL-20260821-EXIT-EVAL-BREACHES-60S-ON-A-THIRD-OF-CYCLES`

**This is a PROPOSAL. Nothing in `src/` was changed.** The operator's recorded
decision on this item is *"investigate and propose — do NOT flip."* The exact diff
is in § 6; it is reproduced as text rather than committed so it cannot merge by
accident.

---

## 1. The answer, in one paragraph

The exit pass is **96.7% candle-fetch time** (40.81 s of a 42.19 s mean pass). The
largest single cause is not TTL tuning and not the loop's cadence: it is that
**Interactive-Brokers candle frames cannot be cached at all**, at any TTL, because
`_client_cache_key` excludes IB from the connector memo, so `connector_for_symbol`
hands `fetch_candles` a *fresh* `IBMarketData` on every call and the candle cache —
which keys on a per-instance lifetime token — is guaranteed to miss. Measured on the
live trader, the one open IB 15m package was fetched from the venue **281 times in
281 consecutive passes**. The proposed change is to memoize the IB connector on its
resolved endpoint, which is safe in a way that is provable rather than argued: the
underlying `IBClient` is **already** shared process-wide, so the wrapper memo adds
no new socket and no new clientId.

**The knob named by the backlog row's own `next_action` — "raise
`CANDLE_CACHE_TTL_MAX_S` for the frames the exit loop re-reads" — is refuted for
these frames by direct measurement in § 5.** It would help the tick's Alpaca 1d
legs and do nothing at all for the exit loop's IB frames.

---

## 2. Populations

Every number below carries its basis. Two independent sources, plus one controlled
local reproduction.

| # | source | population | window |
|---|---|---|---|
| **A** | `/api/diag/tick_cost` | one process, `process_started_utc` 2026-08-21T13:43:00.710052Z; `ticks_measured` **57**, off-loop `monitor.strategy_monitor_loop` **n=281** passes | payload `generated_at` 2026-08-21T17:15:51Z |
| **B** | `/api/diag/log_file?name=exit_interval_soak&lines=1000` | the **most recent 1,000 rows** of a 3,245,512-byte file — **993** measured intervals, 7 first-pass rows (`interval_ms: null`), **8** processes | 2026-08-21T04:32:09Z → 17:30:29Z (13.0 h) |
| **C** | local reproduction, this repo at `a252119` | 5 identical `fetch_candles` calls per venue against stub connectors | n/a |
| **D** | `/api/bot/order-packages?limit=200&include_paper=true` | **12** open packages | read 2026-08-21T17:2xZ |

⚠️ **B is a TAIL, not the whole file.** 1,000 lines of a 3.2 MB log. It is the newest
1,000, so it is the relevant window, but it is not the population the file holds and
must not be quoted as one.

⚠️ **A is ONE process.** `tick_cost` counters reset on restart. The trader restarted
**8 times** inside B's 13-hour window, so A's `n=281` is the longest-lived process of
that day and its maxima are correspondingly the best-sampled — but a max over 281
passes is not the claim a max over 3,000 would be.

---

## 3. What was measured

### 3a. The requirement is still breached, and the max is the worst on record

Source **B** — 993 intervals, 8 processes, 13.0 h:

| | |
|---|--:|
| intervals over `EXIT_EVAL_MAX_INTERVAL_SECONDS=60` | **287 / 993 = 28.9%** |
| max | **95.9 s** |
| p99 / p90 / median / mean | 82.6 s / 73.8 s / 38.5 s / 46.5 s |

Against the review's reading (129/398 = 32.4%, max 89.1 s, mean 48.3 s, 3 processes):
the **rate** is slightly lower on a 2.5× larger sample, and the **max has grown
89.1 → 95.9 s**. Treat the rate difference as noise, not improvement — the honest
summary is *unchanged and still breached*.

`exit_loop_health` on the live process concurrently reports
`requirement_state: "breached"`, `interval_breaches: 71`, `intervals_measured: 287`,
`max_interval_ms: 95871.6`.

### 3b. The PASS is the binding term — the cadence knob is inert

`interval == max(30 s, pass_ms)` holds within 1.5 s on **938 of 993** intervals
(94.5%). Only **144 / 993 (14.5%)** of passes finish under the 30 s cadence floor,
and **277 / 993 (27.9%) of passes exceed 60 s on their own**, before any sleep.

**So `EXIT_LOOP_INTERVAL_SECONDS` cannot fix this.** Lowering it changes nothing for
85.5% of cycles; raising it makes matters worse. The pass duration is the quantity.

### 3c. No time-of-day structure — this is not a market-hours artifact

Breach rate by UTC hour over B (n per hour 35–80):

| hour (UTC) | 04 | 05 | 06 | 07 | 08 | 09 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| breach % | 37.1 | 34.2 | 27.8 | 22.8 | 36.5 | 28.0 | 31.5 | 30.7 | 29.5 | 26.3 | 27.5 | 28.4 | 25.6 | 20.5 |

Flat within noise across all 14 hours, including hours when US equity venues are
shut. This is a **negative control**: it rules out "the venues are slow during RTH"
and points at a structural per-pass cost.

### 3d. The pass is 96.7% fetch

Source **A**, per pass over n=281:

| frame | fetches/pass | mean per fetch | s / pass | % of the 42.19 s pass |
|---|--:|--:|--:|--:|
| `fetch.1d` | 2.683 | 10 878.4 ms | **29.19** | **69.2%** |
| `fetch.15m` | **1.000** | 10 780.7 ms | **10.78** | **25.6%** |
| `fetch.1h` | 0.406 | 1 784.0 ms | 0.72 | 1.7% |
| `fetch.4h` | 0.498 | 184.6 ms | 0.09 | 0.2% |
| `fetch.2h` | 0.132 | 189.4 ms | 0.02 | 0.1% |
| **fetch total** | 4.719 | | **40.81** | **96.7%** |
| everything else the monitor does | | | 1.38 | 3.3% |

**The instrument validates itself, in both dimensions.** `fetchby.*` and `fetch.*`
are two independent cuts of the same seconds (CLAUDE.md § `/api/diag/tick_cost`):

- **time:** `fetchby.strategy_monitor_loop` totals 11 467.9 s; `Σ fetch.*` totals
  11 467.9 s — **delta 0.0 s**.
- **count:** 1 326 misses + 3 170 `fetch.cache_hit` = 4 496 = `fetchby` n exactly.

So per pass the exit loop issues **16.00 candle requests, of which 4.72 miss** — a
70.5% hit rate overall, and, as § 4 shows, **0% on IB**.

### 3e. The decisive line: one IB symbol, 281 fetches, 281 passes

Source **D** shows exactly **one** open 15m package: `ict_scalp_mgc_15m` on **MGC**,
which `config/instruments.yaml` routes to `interactive_brokers`. Source **A** shows
`fetch.15m` off-loop at **n=281 over n=281 passes — 1.000 per pass, zero cache hits.**

With a working cache the arithmetic is not ambiguous: 15m TTL is
`min(900 × 0.10, 300) = 90 s`, the mean interval is 42.19 s, so one symbol should
miss once per 2.13 passes → **≈132 fetches**, not 281.

The controls are present and behave correctly, which is what makes this a finding
rather than a coincidence. Predicted-vs-measured misses per pass, using each frame's
own open-package count from **D** and TTL 300 s / interval 42.19 s:

| frame | venue | open pkgs | predicted miss/pass | **measured** | verdict |
|---|---|--:|--:|--:|---|
| 4h | Bybit | 4 | 0.563 | **0.498** | cache working |
| 1h | Alpaca | 3 | 0.422 | **0.406** | cache working |
| 2h | Bybit | 1 | 0.141 | **0.132** | cache working |
| **15m** | **IB** | **1** | **0.470** | **1.000** | **never caches** |

Three venue-served frames land within 12% of prediction. The IB frame is 2.13× over,
which is precisely the ratio a **zero**-hit cache produces.

---

## 4. Root cause, read in the code

1. [`src/runtime/market_data.py:48`](../../src/runtime/market_data.py) —
   `_client_cache_key` returns a memo key for `bybit` / `alpaca` / `oanda` and
   **falls through to `None` for `interactive_brokers`**, with a docstring at line 54
   explaining the exclusion.
2. So `_build_exchange_client` (line 116) never memoizes IB, and
   `connector_for_symbol` (line 274) constructs a **fresh `IBMarketData`** on every
   candle request.
3. [`src/runtime/market_data.py:448`](../../src/runtime/market_data.py) —
   `_candle_cache_key` keys on `_client_identity_token(client)` (line 397), a
   monotonic counter attached per *object*. A fresh wrapper ⇒ a fresh token ⇒ **a
   cache key that has never been seen and can never be seen again.**
4. Consequence, both directions: every IB request is a guaranteed miss, **and** each
   one *inserts* a permanently-unhittable entry into `_CANDLE_CACHE`.

### The premise of the exclusion is already satisfied one layer down

The docstring's stated reason is that *"an `IBMarketData` holds a live socket on a
specific clientId; handing one instance to concurrent callers is the documented
multi-client collision that BL-20260706-IBACCTUPDATES-COLLISION is about."*

**`IBMarketData` does not hold a socket.** Its `__init__`
([`src/exchange/ib_connector.py:248`](../../src/exchange/ib_connector.py)) sets
`use_rth`, `market_data_type`, and `self._client = get_ib_client(...)` — and
`get_ib_client` ([`src/units/accounts/ib_client.py:3501`](../../src/units/accounts/ib_client.py))
is a **process-wide registry keyed on `(host, port, client_id)`**. Every
`IBMarketData` built for the same endpoint therefore **already shares one
`IBClient` today**.

Memoizing the wrapper changes how many *wrapper objects* exist, not how many IB
sockets or clientIds do. That number is unchanged, so the collision the exclusion
guards against is untouched. This is the crux of the safety case, and it is a fact
about the code rather than a judgement about risk.

Consistent with this: `/api/diag/ib_state` read at 2026-08-21T17:30:47Z shows the
live clients `connected: true`, `breaker_open: false`, `consecutive_failures: 0`.

---

## 5. The obvious remedy is refuted

The backlog row's `next_action` proposes raising `CANDLE_CACHE_TTL_MAX_S` for the
frames the exit loop re-reads. Source **C**, run against this repo at `a252119`:

```
5 identical fetch_candles calls, connector re-resolved each call (as the live loops do)
exchange              tf      ttl_s  venue_calls   verdict
bybit                 4h        300            1   cache WORKS
alpaca                1d        300            1   cache WORKS
interactive_brokers   15m        90            5   NEVER caches
interactive_brokers   1d        300            5   NEVER caches
```

and with the cap raised to a full day:

```
REFUTATION TEST — CANDLE_CACHE_TTL_MAX_S=86400
  ttl computed for 15m : 90.0 s
  ttl computed for 1d  : 8640.0 s
  venue calls for 5 identical IB requests: 5   (expected 1 if the TTL were the binding term)
```

**At an 8 640-second TTL an IB frame still goes to the venue every single time.**
The TTL cap is a real knob for the tick's 13 Alpaca 1d legs; it is not the knob for
the exit loop, whose two dominant frames are IB-served. Raising it alone would have
produced a change that looked principled, measured as an improvement on the tick,
and left the 60 s breach exactly where it is.

The same reproduction also shows the poisoning half — after the IB probe the cache
holds **5 entries from 5 requests**, none of which can ever be hit.

---

## 6. The proposed change

**One resolver, two readers.** `_build_ib_market_data` currently resolves the
endpoint (settings → env → `accounts.yaml`) inside itself; the memo key needs the
same answer. Duplicating that resolution is exactly the drift `_connector_class_id`
already exists to prevent, so the resolution is lifted into
`_ib_connection_identity` and both call it.

Diff against `a252119` (`src/runtime/market_data.py`, +89 / −39):

```diff
     if name == "oanda":
         return (
             "oanda",
             _connector_class_id("src.exchange.oanda_connector", "OandaMarketData"),
             settings.get("OANDA_API_TOKEN"),
         )
+    if name in ("interactive_brokers", "ib"):
+        # IB is memoized on its RESOLVED connection identity, not on settings,
+        # because `_build_ib_market_data` resolves host/port/clientId/account
+        # from settings -> env -> accounts.yaml. Both readers share ONE
+        # resolver (`_ib_connection_identity`) so the memo key can never drift
+        # from what would actually be constructed -- the same reason
+        # `_connector_class_id` is in the key above.
+        #
+        # This adds NO new socket sharing. `IBMarketData.__init__` obtains its
+        # client from `get_ib_client()`, which is already a process-wide
+        # registry keyed on (host, port, client_id): every IBMarketData for one
+        # endpoint ALREADY shares a single IBClient today. Memoizing the
+        # wrapper stops allocating a fresh object per fetch; the number of live
+        # IB clientIds in the process is unchanged, so
+        # BL-20260706-IBACCTUPDATES-COLLISION is untouched.
+        identity = _ib_connection_identity(settings)
+        if identity is None:
+            # Unresolvable endpoint (no ib_port anywhere). Refuse to memo --
+            # today's behaviour exactly, and the same fail-safe posture as the
+            # `None` return below.
+            return None
+        return (
+            "interactive_brokers",
+            _connector_class_id("src.exchange.ib_connector", "IBMarketData"),
+        ) + identity
     return None
```

```diff
+def _ib_connection_identity(settings: Dict[str, Any]) -> Optional[tuple]:
+    """The RESOLVED IB market-data endpoint, or ``None`` if unresolvable.
+
+    ONE definition, TWO readers: `_build_ib_market_data` constructs from it and
+    `_client_cache_key` memoizes on it. Duplicating this resolution would let
+    the memo key drift from the client it claims to identify — the defect
+    `_connector_class_id` exists to prevent for the other venues.
+
+    Returns ``None`` when no ``ib_port`` can be resolved from settings, env, or
+    the IB account entry. The caller then declines to memo and `IBMarketData`
+    construction raises exactly as it does today.
+    """
+    try:
+        host = (settings.get("IB_HOST") or os.environ.get("IB_HOST")
+                or _ib_account_field("ib_host") or "127.0.0.1")
+        port = (settings.get("IB_PORT") or os.environ.get("IB_PORT")
+                or _ib_account_field("ib_port"))
+        if not port:
+            return None
+        account = (settings.get("IB_ACCOUNT") or os.environ.get("IB_ACCOUNT")
+                   or _ib_account_field("ib_account"))
+        exec_client_id = int(_ib_account_field("ib_client_id") or (int(port) % 1000))
+        md_client_id = int(settings.get("IB_MD_CLIENT_ID")
+                           or os.environ.get("IB_MD_CLIENT_ID")
+                           or (exec_client_id + 1))
+        try:
+            md_type = int(settings.get("IB_MARKET_DATA_TYPE")
+                          or os.environ.get("IB_MARKET_DATA_TYPE") or 3)
+        except (TypeError, ValueError):
+            md_type = 3
+        return (str(host), int(port), md_client_id,
+                str(account) if account else None, md_type)
+    except Exception:  # noqa: BLE001 — an unresolvable endpoint must not raise here
+        return None
+
+
 def _build_ib_market_data(settings: Dict[str, Any]):
-    """... IB has no API keys — connection identity ... is resolved from the IB
-    account entry in ``config/accounts.yaml`` ..."""
+    """... connection identity is resolved by ``_ib_connection_identity`` ..."""
     from src.exchange.ib_connector import IBMarketData
-    host = (settings.get("IB_HOST") or os.environ.get("IB_HOST") or ... )
-    port = ( ... )
-    if not port:
-        raise ValueError("IB market data: no ib_port (config IB account / IB_PORT env).")
-    account = ( ... )
-    exec_client_id = ...
-    md_client_id = ...
-    md_type = ...
+    identity = _ib_connection_identity(settings)
+    if identity is None:
+        raise ValueError(
+            "IB market data: no ib_port (config IB account / IB_PORT env)."
+        )
+    host, port, md_client_id, account, md_type = identity
     return IBMarketData(
         host=str(host), port=int(port), client_id=md_client_id,
         account=str(account) if account else None, market_data_type=md_type,
     )
```

The full 156-line patch (including the unchanged surrounding context of
`_build_ib_market_data`) is reproducible by applying the two hunks above; the
mechanical transformation is *lift the resolution, call it from both sites*.

### The test that must ship with it

The failure this fixes is invisible to every existing test, because it is a
*performance* property expressed as a *call count*. The regression test asserts the
count directly, on all four venues, so a future change that re-breaks IB fails
rather than merely slows down:

```python
def test_repeated_requests_hit_the_cache_on_every_venue(monkeypatch):
    """5 identical requests => 1 venue call, IB included.

    IB regressed to 5/5 for as long as `_client_cache_key` excluded it
    (BL-20260821-EXIT-EVAL-BREACHES-60S-ON-A-THIRD-OF-CYCLES). Assert the COUNT:
    a cache that never hits is not slower-but-correct, it is a different system.
    """
    for exchange, symbol, timeframe in [
        ("bybit", "BTCUSDT", "4h"), ("alpaca", "QQQ", "1d"),
        ("interactive_brokers", "MGC", "15m"), ("interactive_brokers", "MHG", "1d"),
    ]:
        ...  # stub _build_exchange_client_uncached, count get_ohlcv calls
        assert venue_calls == 1, f"{exchange}/{timeframe} went to the venue {venue_calls}/5 times"


def test_unresolvable_ib_endpoint_declines_to_memo(monkeypatch):
    """No ib_port anywhere => identity None => no memo, and the same ValueError."""
```

### Validation already performed (this session, sandbox, repo at `a252119`)

| check | result |
|---|---|
| local reproduction after the patch | bybit 1/5 · alpaca 1/5 · **IB 15m 1/5** · **IB 1d 1/5** |
| fail-safe with no `ib_port` resolvable | `_ib_connection_identity → None`, `_client_cache_key → None`, `_build_ib_market_data → ValueError` (unchanged message) |
| `tests/test_s033_market_data.py` | 10 passed, 4 skipped |
| all collectible market-data / connector / cache tests | 60 passed, 10 skipped, **2 failed** |

⚠️ **The 2 failures are pre-existing sandbox gaps, verified rather than assumed.**
`TestConnectorRouting::test_connector_for_btc_is_bybit` and
`::test_connector_month_grammar_never_strips_crypto` fail with
`ModuleNotFoundError: No module named 'ccxt'`. I stashed the patch and re-ran them
on clean `a252119`: **identical 2 failed, 3 passed.** Likewise the 40 collection
errors are `No module named 'fastapi'`, reproduced on clean `main`. Neither is
attributable to the change, and neither has been shown to pass — CI on a runner with
the real requirements is where that gets established, not here.

---

## 7. Effect

### A measured floor, and a bounded projection — they are different claims

**Measured floor.** The 15m frame is fully attributable (one symbol, one venue, one
open package, exactly 1.000 fetch/pass). A working cache leaves
`1 × 42.19/90 = 0.47` misses/pass, so **149 of the 281 fetches were avoidable**:

> **5.73 s per pass, 13.6% of the mean pass — measured, not projected.**

**Projection for the rest.** `fetch.1d` is 2.683 misses/pass at 10 878 ms, mixing IB
(MHG, and MES while open) with Alpaca (QLD, SCHA). **`tick_cost` splits fetches by
timeframe and by consumer phase but NOT by venue or symbol**, so this cannot be
decomposed from the instrument — see § 9. Bounding it instead:

| assumption on `fetch.1d` | saving / pass | projected mean pass |
|---|--:|--:|
| conservative — only MHG is IB (1.000/pass), the rest already cache optimally | **15.1 s** | **27.1 s** |
| upper — all 2.683/pass are IB | **30.3 s** | **11.9 s** |

Both ends put the mean pass under the 30 s cadence floor or close to it, i.e. the
mean interval returns to `max(30 s, pass) = 30 s`.

**The max is deliberately NOT projected.** Every frame that includes an IB symbol
shows a maximum of 26.0–26.8 s (`fetch.1d` 26 795 ms, `fetch.15m` 26 515 ms,
`fetch.1h` 26 665 ms, `fetch.5m` 26 048 ms), while `fetch.2h` (677 ms) and
`fetch.4h` (323 ms) — the two frames with no IB leg — do not. That is *consistent
with* `_IB_FETCH_QUEUE_TIMEOUT_S` (29.0 s = `IB_FETCH_TIMEOUT_S × 3 + 5`,
[`src/exchange/ib_connector.py:113`](../../src/exchange/ib_connector.py)) and with
`BL-20260816-IB-QUEUE-TIMEOUT-EXCEEDS-EXIT-BUDGET`, but it is **not confirmed**:
2h and 4h have far lower n (150 and 82 on-loop) and may simply have missed the stall
windows. Confirming it needs the venue split § 9 says does not exist.

What *can* be said about the max without that split: `_IB_FETCH_EXECUTOR` is a
`max_workers=1` pool ([`src/exchange/ib_connector.py:103`](../../src/exchange/ib_connector.py))
shared by the tick thread and the exit thread, so **fewer IB submissions is
strictly less queue depth**. Off-loop IB submissions would fall from ~2/pass toward
~0.6/pass and on-loop from 4/tick toward ~1/tick. That should reduce how often a
29 s queue wait is reachable inside an exit pass. It is a mechanism, not a
measurement, and the post-flip soak in § 10 is what would settle it.

### It reaches the TICK too — which is the point, not a side effect

There are **4 enabled, non-shadow strategy legs routed to IB** (verified against
`config/strategies.yaml` × `config/instruments.yaml`): `mes_trend_long_1d`,
`ict_scalp_mgc_15m`, `mgc_pullback_1d`, `mhg_pullback_1d` — 4 distinct
`(symbol, timeframe)` frames. All four are re-fetched from the venue on **every
tick** today, at ~10.9 s each on the off-loop evidence. Tick mean is 165.1 s
(max 209.6 s, n=57), `run_one_tick` 137.2 s of it, and `pipeline.signal_build` is
88% fetch. Caching the four IB frames removes work from the tick as well.

---

## 8. What it costs: price staleness behind live orders

This is the half the done-condition insists on, and it is a real cost, not a
formality.

> ⚠️ **CORRECTION 2026-08-21T18:05Z — I checked whose money is behind these legs only
> AFTER writing this section, and it lowers the stake.** The **only** account trading
> MES/MGC/MHG is **`ib_paper`** (`account_class: paper`, `mode: live`); the sole
> real-money IB account, `ib_live`, is **`mode: dry_run`** and shelved. Verified by
> reading `config/accounts.yaml`, not inferred.
>
> So every staleness figure below lands on **paper** legs, while the faster exit pass
> benefits **every** leg — including the real-money Bybit ones. The phrase *"behind live
> orders"* is accurate about the ORDER PATH and misleading about the RISK, and the
> distinction is exactly the sort this repo insists on. The section is left standing
> rather than rewritten, because the geometry it describes is unchanged and the original
> framing is the record of what I asserted before I checked.

`candles_df["close"].iloc[-1]` is read as the **current price** for entry geometry
(`_base.py`, `trend_donchian.py`, `turtle_soup.py`, `vwap.py`, `ict_scalp.py`) and by
the monitor for exit decisions. Making IB frames cacheable means those reads may be
served a frame up to `min(bar_seconds × 0.10, CANDLE_CACHE_TTL_MAX_S)` old:

| leg | frame | new max staleness |
|---|---|--:|
| `ict_scalp_mgc_15m` | MGC 15m | **90 s** |
| `mes_trend_long_1d`, `mgc_pullback_1d`, `mhg_pullback_1d` | 1d | **300 s** (live `CANDLE_CACHE_TTL_MAX_S=300`) |

**This reaches ENTRY, not only exits.** Those four legs would size and place entries
off a frame up to 90 s / 300 s old. That is the change the operator is being asked to
approve, stated plainly.

Two pieces of context for judging it — neither is a reason to skip the decision:

1. **The system already imposes more staleness on itself than the cache would add.**
   Exit evaluations are 42.19 s apart on the mean and **95.9 s** apart at the max
   (source B); ticks are 165.1 s apart on the mean and 209.6 s at the max. A 90 s
   frame on a 15m leg is *inside* the 95.9 s the exit loop already lets elapse
   between looks. On a 1d bar, 300 s is **0.35%** of the bar.
2. **Not caching is itself what creates the staleness.** 40.81 s of the 42.19 s pass
   *is* these fetches. Serving a frame that may be 90 s old, in a pass that then runs
   ~5× more often, is very likely a net *reduction* in end-to-end staleness — but
   that is a hypothesis about the joint effect, and it is what § 10's soak would
   measure rather than something this document establishes.

**Rollback is one env flip, no redeploy:** `CANDLE_CACHE_TTL_FRACTION=0` serves every
request fresh on every venue, restoring today's behaviour for IB and for everything
else. There is no IB-specific off switch in this diff; adding one would be a second
gate over a capability and is deliberately not proposed (Prime Directive § "no third
gate").

---

## 9. What is NOT measured — stated rather than glossed

1. **`tick_cost` has no venue or symbol axis.** `fetch.*` splits by timeframe,
   `fetchby.*` by consumer phase. Neither can answer *"how much of `fetch.1d` is
   IB?"*, which is why § 7 bounds instead of measuring, and why the 26 s ceiling
   stays "consistent with" rather than "confirmed". Filed as
   **`BL-20260821-FETCH-COST-HAS-NO-VENUE-AXIS`**.
2. **`_CANDLE_CACHE` has no read surface for its SIZE.** Hit/miss counts are
   readable (`fetch.cache_hit`), but `_candle_cache_put` clears the map **wholesale**
   at >512 entries rather than evicting LRU, and nothing reports the entry count or
   the flush. The IB poisoning measured in § 5 (5 dead entries from 5 requests) feeds
   that bound at a rate I can estimate but cannot observe. Filed as
   **`BL-20260821-CANDLE-CACHE-SIZE-AND-FLUSH-UNOBSERVABLE`**.
3. **The 2 test failures and 40 collection errors were shown pre-existing, not shown
   to pass.** They need a runner with the real requirements.
4. **No claim is made about the max interval after the change** — see § 7.

---

## 10. The ask, and how it would be verified

**Tier-3 decision requested.** `src/runtime/market_data.py` is not on the Tier-3
hard-limit file list, but the change alters price freshness behind live orders on
four IB legs, which CLAUDE.md's `CANDLE_CACHE_TTL_*` row classifies as Tier-3 for
exactly that reason. It is not being self-merged.

If approved, the change ships as a normal PR with the § 6 tests, and the
done-condition is measured, not assumed:

- **Immediately after the restart:** `/api/diag/tick_cost` off-loop `fetch.15m`
  must fall from **1.000 per pass** toward `interval/90`. That single ratio is the
  cleanest falsifier available — one symbol, one venue, no decomposition needed. If
  it stays at 1.000, the change did not take and nothing else in this document
  matters.
- **Over ≥ 500 intervals spanning ≥ 3 processes:** the row's own
  `resolution_criteria` — a 60 s breach rate **below 1%**, with the max stated beside
  `intervals_measured`, from `exit_interval_soak` (whole file, not a tail).
- **Watch for the counter-effect:** any rise in `close_reason` distributions or a
  fill materially away from a declared level on the four IB legs. A faster loop
  acting on a slightly older price is the risk this trades for, and it is the thing
  that would justify reverting.

### Alternatives considered and rejected

| # | option | why not |
|---|---|---|
| A | raise `CANDLE_CACHE_TTL_MAX_S` | **refuted for these frames** by § 5 — IB misses at 8 640 s. Helps the tick's Alpaca 1d legs only; would have read as a fix and moved nothing. |
| B | lower `EXIT_LOOP_INTERVAL_SECONDS` | inert — `interval == max(30 s, pass)` on 94.5% of cycles (§ 3b), and 27.9% of passes breach 60 s unaided. |
| C | lower `_IB_FETCH_QUEUE_TIMEOUT_S` | `BL-20260816-IB-QUEUE-TIMEOUT-EXCEEDS-EXIT-BUDGET` declines to propose a value for good reason: a shorter timeout buys degraded (`None`) fetches, and a `None` fetch means the monitor evaluates on **no candles** — the MONITOR BLIND condition, not a cheaper outcome. This proposal attacks the queue **depth** instead of its timeout, which needs no such trade. |
| D | give the exit loop its own IB fetch thread | raises concurrent IB clientId usage — directly against BL-20260706-IBACCTUPDATES-COLLISION and against the `#9240` pin that exists to serialise this. |
| E | a per-pass fetch budget (mirroring `REGIME_BAR_SCORING_BUDGET_S`) | defers whole symbols, i.e. a position goes un-evaluated to keep the loop fast. Worth having as a **backstop** once the cost is fixed, but as the primary remedy it makes the loop look compliant by monitoring less — the shape `run_exit_evaluation_tick`'s own docstring rejects (*"a monitor that skips a position to stay inside a budget is a monitor that stopped monitoring"*). |
