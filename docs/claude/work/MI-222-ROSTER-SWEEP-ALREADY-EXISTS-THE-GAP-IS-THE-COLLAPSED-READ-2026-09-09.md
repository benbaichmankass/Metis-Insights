# MI-222 — the roster-driven sweep already exists; the gap is that the read collapses states

> **Doc status:** `live` · category `evidence` · MI-222 · branch `claude/mi-222-venue-truth-detector`
> · investigated 2026-09-09 · session `session_01WmFdLwfq4U5aLLjFRDDy6b`

**Every finding below is a source read or an executed test. No order was placed, modified
or cancelled on any account. No live-path code is changed by this branch.**

---

## 0. Headline

MI-222 was spawned to BUILD a roster-driven position sweep over
`config/accounts.yaml::<account>.symbols`, on the stated premise that every protective
sweep anchors to one of exactly two enumerations and that the roster is *"the only
enumeration complete BY CONSTRUCTION rather than by observation"*.

**That sweep already exists.** It is inside `account_open_positions` itself
(`src/units/accounts/clients.py:1410`), it reads the roster from `accounts.yaml`, and it
**already ran on `bybit_2`/ETHUSDT and returned nothing** (MI-221 measured this). Building
it again would have produced a duplicate that reports the target position **CLEAN** — the
blind detector the work object forbids.

**The real gap is one layer down: the reader itself collapses three states into one.**
No detector layered on `account_open_positions` can honour "states are never collapsed",
because its reader has already merged them.

---

## 1. The correction, and how the premise went wrong

The brief characterised list (a) as *"the ACCOUNT-WIDE venue list via `account_open_positions`
→ bybit `get_positions(category=…, settleCoin="USDT")`"*. That describes only the **first
half** of the function. The second half is a roster sweep:

```python
# src/units/accounts/clients.py:1410
for sym in _bybit_configured_symbols(account):
    if not isinstance(sym, str) or not sym or sym in seen:
        continue
    r2 = client.get_positions(category=category, symbol=sym)
```

`_bybit_configured_symbols` (`clients.py:1221`) prefers the cfg's `symbols` key and
**falls back to loading `accounts.yaml` by `account_id`** — precisely the config-not-venue
source the brief named as the novel constraint.

It was added by `BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND` **for the identical failure shape
on the identical real-money account**: a live 0.001 BTCUSDT on `bybit_2` absent from the
settleCoin page, journal false-closed, position left unprotected and invisible. The lesson
this lane was asked to learn had already been learned and shipped, eight weeks earlier.

So list (a) is not the account-wide list. It is **account-wide ∪ roster-scoped-per-symbol**,
and ETHUSDT was inside its domain the whole time.

### 1a. Stronger than "it exists" — it cannot be switched off from the cfg dict

Found by getting a test assumption wrong and checking rather than adjusting the assertion.
Because `_bybit_configured_symbols` only prefers the cfg's `symbols` when **truthy**, an
explicitly empty roster does not suppress the sweep — it is backfilled from config:

| cfg | resolved roster |
|---|---|
| `{account_id: bybit_2, symbols: []}` | `['BTCUSDT','ETHUSDT','XRPUSDT','ADAUSDT']` |
| `{account_id: bybit_2}` (no key) | `['BTCUSDT','ETHUSDT','XRPUSDT','ADAUSDT']` |
| `{account_id: <not in yaml>, symbols: []}` | `[]` |
| `{account_id: bybit_2, symbols: ['ONLYUSDT']}` | `['ONLYUSDT']` |

For any account whose `account_id` is in `accounts.yaml`, the roster sweep is
**unconditional**. Pinned in
`tests/…_position_read_state_collapse.py::test_empty_roster_on_a_real_account_is_backfilled_from_config`.

---

## 2. What was verified, from source

| claim in the brief | verdict | evidence |
|---|---|---|
| (a) enumerates `account_open_positions(cfg)` | **confirmed** | `order_monitor.py:3128` |
| (a) is *only* the account-wide venue list | **REFUTED** | `clients.py:1410` |
| (b) enumerates journal `status='open'` | **confirmed** | `order_monitor.py:9846-9849`: `FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0`. Trade 5471 is `closed`, so genuinely missed. |
| there is no third path | **confirmed, and counted** | population = every position-enumerating site in `order_monitor.py`. Family (a), `account_open_positions`: lines **3128, 4337, 5386** (plus `_exchange_position_set` at 2885 converting its output). Family (b), `FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0`: lines **4208, 7871, 8390, 9365, 9848**. **8 sweep sites, 2 sources, no third.** |
| nothing runs `journal_venue_audit.py` | **confirmed** | references only in its own docstring + 2 docs; no workflow, timer or caller |
| ETHUSDT ∈ `bybit_2.symbols` | **confirmed** | `['BTCUSDT','ETHUSDT','XRPUSDT','ADAUSDT']` |
| a symbol-scoped read sees what the account-wide list misses | **NO** | MI-221 § 2: the cross-check ran on ETHUSDT, succeeded, returned no row with `size > 0`, twice — verified with a positive control that the failure-log probe was not vacuously quiet |

The last row is MI-222's open parameter, and the answer is the one that retires the small
build: **both position surfaces are blind here, and they are blind for the same reason.**

---

## 3. The actual gap — `_emit` collapses three states

```python
# src/units/accounts/clients.py:1379-1381
size = _f(p.get("size"))
if size <= 0:
    return              # no counter, no log, no positionIdx
```

Three materially different venue answers become one value:

1. the venue genuinely holds nothing;
2. the venue returned a **zero-size row**;
3. the venue returned **no row at all**.

This is the repo's own must-not — *we did not look / we looked and found nothing / we found
a divergence* — violated **inside the reader every sweep is built on**. It is inherited by
every consumer.

**It has now blocked root-cause on two real-money P1s one day apart**, on two different
functions: `BL-20260908-BYBIT-POSITION-PROTECTION-GRADES-A-SYMBOL-OFF-ROWS0-SO-A-HEDGE-BOOK-READS-FLAT-AND-A-LIVE-POSITION-IS-CLOSED` records
*"No repo surface exposes the raw get_positions payload; saying which would be a guess"*,
and MI-221 § 4c hit the identical wall. That makes it an **instrument gap, not a per-site
nit**.

`_emit` also dedupes by **symbol** and emits **no `position_idx`**, so under hedge mode —
armed on `bybit_2`/ETHUSDT since 2026-08-30 — a second live book is silently dropped
straight into the close decision at `order_monitor.py:4348`.

### 3a. A negative result that narrows the blast radius

A zero-size sibling listed first does **not** mask a live book: `_emit` returns on
`size <= 0` **before** `seen.add(sym)`, so the zero row consumes no dedupe slot. Only **two
non-zero books** collapse. This rules out one of the two candidate shapes for MI-221 § 4a
and means that mechanism still requires a second genuinely live ETH book at
2026-09-08T13:37Z — **still unestablished, no read exists from that moment**.

---

## 4. Cost, as a number, with its population

The per-cadence cost the brief asked for is **already being paid**. Per
`account_open_positions` call: **1 settleCoin page + one symbol-scoped read per configured
symbol not already surfaced**. Population = every `exchange: bybit` entry in
`config/accounts.yaml` (3 accounts):

| account | roster size | scoped reads (worst case, page empty) |
|---|---|---|
| `bybit_1` | 6 | 6 |
| `bybit_2` | 4 | 4 |
| `bybit_portfolio` | 4 | 4 |

A new roster-driven sweep would **duplicate this spend and add no coverage**.

---

## 5. What this branch ships, and what it deliberately does not

**Ships (Tier-1 only):** this document and
`tests/test_accounts_clients_position_read_state_collapse.py` — 10 characterization tests
that make the collapse an executable, permanent fact, plus a pin on the roster sweep so the
next lane handed the same brief finds an answer instead of rebuilding a duplicate.

Those tests are also the **known positive** the done-condition needs. A detector that has
only ever reported clean has not been shown able to report anything else; each fake here is
a venue state a correct reader would distinguish and this one does not — at zero cost and
touching no venue.

**Deliberately not shipped:**

* **No new roster sweep.** It exists (§ 1).
* **`journal_venue_audit.py` is not scheduled.** Its venue side reads `_diag("exchange_positions")`
  — the same `account_open_positions`, hence the same collapse. Scheduling it buys a green
  light over an unread book.
* **No change to `_emit` or `account_open_positions`.** Their return value feeds a live
  real-money close decision (`order_monitor.py:4348`). That is **Tier-2/3**, and MI-221
  explicitly **HELD** remediation for exactly this reason. Proposed below, not shipped.
* **No order-path change of any kind.**

## 6. Proposed next action (NOT this lane's to ship)

**Expose the raw `get_positions` payload — zero-size rows and `positionIdx` included —
additively, without changing what `account_open_positions` returns.** It is #1 on MI-221's
"what would settle it", it de-collapses the three states for every future detector, and it
unblocks two stalled P1s. Keeping it additive is what holds the order path unchanged; a
route on `ict-web-api` is itself Tier-3 per § VM authority split, so the surface needs the
manager's call.

**Only after that is a non-blind detector possible at all** — until the reader can say
*which* of the three states it saw, a detector on top of it can only ever say "clean".

## 6a. Where the finding must land — named and verified, not assumed

The brief requires that a finding reach a surface a human reads, and that the surface be
named. Verified from source rather than repeated:

* **`logger.error` / `logger.warning` reach the systemd journal and nothing else.** They do
  not write `runtime_logs/outcomes.jsonl`, so they do not reach Telegram,
  `/api/bot/notifications`, or the hourly report. Both existing sites in the collapse story
  use exactly this: `_emit`'s cross-check failure path logs
  `"per-symbol cross-check for %s failed"` via `logger.warning` and stops there.
* **`src/runtime/outcomes.py::report()` is the single call that reaches a human.** Its own
  docstring states it decides "whether to push to the in-process AlertsQueue (for
  `/alerts`)", "whether to append to `runtime_logs/outcomes.jsonl` (audit trail)" and
  "whether to send a Telegram message", with per-fingerprint and hourly rate limits, and it
  is documented to **never raise**. `outcomes.jsonl` is consumed by
  `src/web/api/routers/notifications.py` (`/api/bot/notifications`),
  `src/runtime/hourly_report.py` (WARN+ events in the last hour) and
  `src/web/api/routers/dashboard.py`.

**So any detector built on the § 6 surface must report through
`outcomes.report(..., level=Level.ERROR|CRITICAL, ...)`, not `logger.error`.** This is the
gap that kept the Bybit over-cover condition invisible for ten days while the detection
worked perfectly throughout — the detector fired, and its finding went only to the journal.

⚠️ **And it must report all three states distinctly**, which is the whole point of § 3: a
detector that reports only "divergence found" and stays silent on "could not read" has
re-created the collapse one layer up. `report()` carries arbitrary structured kwargs, so the
state can travel as a field rather than being encoded in whether a call happens at all.

## 7. What is NOT established

1. **Whether the venue holds the ETH position.** Three independent venue reads say flat;
   the operator says open. Unreconciled (MI-221 § 5.1). Not re-investigated here.
2. **Whether the hedge-book mechanism closed 5471** — § 3a narrows it, does not settle it.
3. **⚠️ Done-condition clause (3) is currently unsatisfiable against the real position.**
   It requires observing the detector report the known positive. There is no *observable*
   known positive: no repo surface can see the ETH, and MI-221 § 5.5 records that the
   operator's own figures do not reconcile (reported PnL +0.6464 on 0.04 implies a mark near
   2470.13, not the stated 2457.59, which yields +0.1448). **That is not a basis for
   dismissing the operator's report** — it means "is there a live ETH position at all" is
   open. The synthetic known positives in § 5 clear the *detector-capability* half of the
   clause; the *live* half needs either the raw-payload surface (§ 6) or a planted control,
   and planting one touches a real-money venue.
