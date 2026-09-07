# The Alpaca over-close population, counted: 1 of 12 pairs is Alpaca, and 0 over-close events have fired

> **Doc status:** `live` · category `evidence` · last verified `2026-09-07` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)

**Date:** 2026-09-07 · **Tier:** 1 (measurement only — no close path, order path, sizing,
risk cap, config or account mode touched) · **Unit:** `MI-171` ·
**Answers:** `BL-20260907-ALPACA-CLOSE-OF-ONE-TRADE-LIQUIDATES-ITS-SIBLINGS`

---

## VERDICT

`BL-20260907-ALPACA-CLOSE-OF-ONE-TRADE-LIQUIDATES-ITS-SIBLINGS` ends with a
`Population note` that says **"We did not look."** We have now looked.

| question | answer | population |
|---|---|---|
| (a) how many of the multi-row pairs are Alpaca? | **1** — `alpaca_portfolio` / `TLT` | 12 pairs holding ≥2 simultaneously-open rows, out of 600 position-holding rows in the last 1000 journal `trades` rows (ids 4544–5543, 2026-08-10T15:00Z → 2026-09-07T21:02Z) |
| (b) how many actual over-close events are visible? | **0 — we looked and found none** | 32 Alpaca closes in that same population; and all 13 currently-open Alpaca rows checked against a live venue read |

**MEASURED 2026-09-07T21:54–21:57Z.** The measurement lives at
`scripts/research/mi171_alpaca_overclose_population.py`, and its inputs are
committed beside it at `docs/research/artifacts/mi171-alpaca-overclose/`
(`trades-4544-5543.json.gz`, `alpaca-exchange-positions.json`), so every figure
here re-derives with `python3 scripts/research/mi171_alpaca_overclose_population.py`
and no live access. Live reads were taken with `scripts/ops/diag_fetch.sh` direct
over `https://ict-bot.duckdns.org` (exit 0), not through the issue relay.

**The zero in (b) is not "the path is unused."** 32 Alpaca closes fired in this
window. The defect did not fire because none of those 32 coincided with an open
sibling — **not** because the close path is cold.

⚠️ **But (a) is a loaded gun, not an all-clear.** See § The loaded gun.

---

## 0. Population, and why it is a cap rather than a window

**Population: the 1000 rows `/api/diag/journal?table=trades` returns** — ids
4544–5543, `2026-08-10T15:00:59Z` → `2026-09-07T21:02:20Z` (28.2 days). Status
split: **573 `closed` · 370 `rejected` · 30 `exchange_rejected` · 27 `open`**;
**600** rows ever held a venue position (`closed` + `open`). Alpaca's share, across
all four Alpaca accounts: **171 rows** — 32 closed, 13 open, 125 rejected,
1 exchange_rejected.

⚠️ **28 days is the shape of a cap, not a chosen window, and MI-167's "28-day
window" should be read that way too.** `GET /journal` takes only `table` and
`limit`, and `limit` is clamped by `_clamp(limit, _DEFAULT_LIMIT, _MAX_LIMIT)`
with `_MAX_LIMIT = 1000` (`src/web/api/routers/diag.py:1541`, `:860`). There is
**no `offset` and no `since`** — unlike `/audit_query` in the same file, which has
both and explicitly documents reaching "arbitrary history". Verified rather than
inferred: asking for `limit=3000` returns the *same* 1000 rows, ids 4544–5543.

**Consequence, stated plainly: nothing before id 4544 is readable from this
endpoint, so no figure in this document is an all-time count.** MI-167's pass-3
item 3 asks for incidence "across all accounts and all time"; for the `trades`
table specifically, that read is **not available** through the diag surface as it
stands. That is the read I could not make, and why.

### Every negative here carries its positive control

A quiet probe and a broken probe render identically, so each null result below is
paired with the same probe finding a positive:

| null result | positive control on the SAME probe |
|---|---|
| 0 Alpaca closes fired with an open sibling | the identical scan returns **187** such closes on Bybit accounts — and MI-167 independently reported **187**, which is an exact agreement I did not tune for |
| 0 Alpaca open rows unbacked at the venue | the same diff finds **3** venue positions with no journal row, so it detects divergence in both directions |
| `alpaca_live` / `alpaca_options_paper` venue books empty | `error: null` on both, and the same endpoint returns 8 and 7 positions for the sibling accounts — a real read of an empty book, not a swallowed failure (`BL-20260826-OPEN-TRADES-COLLAPSES-A-READ-FAILURE-INTO-AN-EMPTY-BOOK`) |

⚠️ **Provenance caveat on the running code.** `/api/diag/version` at 21:53:50Z:
`git_sha 8e5b9c7c`, `git_sha_on_disk fd63b4f2`, **`restart_pending: true`**. Stated
because it bounds what code produced these rows, not because it changes any figure
— the journal rows and venue positions are data, and both SHAs predate any change
in this PR.

---

## 1. (a) Of the multi-row pairs, exactly one is Alpaca

A pair qualifies when **≥2 rows on the same `(account_id, symbol)` were open at the
same instant**. Rows are `closed` or `open` only: `rejected` and
`exchange_rejected` never reached the venue, so they hold no shares to be
over-closed and cannot be the sibling that gets over-closed.

| account | symbol | peak simultaneous rows | Alpaca? |
|---|---|--:|---|
| `alpaca_portfolio` | TLT | 2 | ✅ **yes** |
| `bybit_1` | ETHUSDT | 4 | no |
| `bybit_1` | SOLUSDT | 3 | no |
| `bybit_1` | ADAUSDT | 2 | no |
| `bybit_1` | AVAXUSDT | 2 | no |
| `bybit_1` | BTCUSDT | 2 | no |
| `bybit_1` | XRPUSDT | 2 | no |
| `bybit_2` | BTCUSDT | 2 | no |
| `bybit_2` | ETHUSDT | 2 | no |
| `bybit_portfolio` | BTCUSDT | 2 | no |
| `bybit_portfolio` | ETHUSDT | 2 | no |
| `ib_paper` | MGC | 2 | no |

**1 Alpaca · 11 non-Alpaca · 12 total.** `bybit_2` appears with BTCUSDT and
ETHUSDT, exactly as MI-167 recorded.

### Why 12 and not 13 — the backlog row's number is off by one, for a findable reason

The backlog row and MI-167 both say **13**. I re-derived rather than trusting it,
and get **12**. This is a real disagreement, not a rounding difference, and it has
a specific cause.

**The count is stable at 12 across every defensible variation** — `timestamp` or
`created_at` as the start, and whether an interval that *ends* exactly when
another *starts* counts as overlap. It becomes 13 under exactly one change:
**counting `exchange_rejected` rows as having held a position.** The 13th pair it
adds is `alpaca_paper` / `GLD`, and it qualifies only on row **5347**
(`exchange_rejected`, 2026-09-02T13:36:26, `gld_pullback_1d`) overlapping row
**5209** (`open` since 2026-08-29).

An `exchange_rejected` row never got shares. It cannot be over-closed and cannot be
the victim. So **12 is the count that answers the backlog row's question**, and 13
counts one pair that is not exposed.

⚠️ **Two things I cannot rule out, stated rather than glossed.** (1) The window has
slid by 4 rows since MI-167 read it — theirs was ids 4540–5539, mine 4544–5543 —
and ids 4540–4543 are **unreachable** (§ 0: no `offset`, no `since`), so a 13th
pair resting on a since-rolled-off row cannot be formally excluded. (2) I have not
read MI-167's own script, only its prose. What makes the `exchange_rejected`
explanation more than a guess is that it reproduces **13** *and* **2 Alpaca pairs**
simultaneously and exactly, which the roll-off hypothesis does not predict at all.
Graded **INFERRED**, on MEASURED inputs.

**Either way the answer to the backlog row's actual question is unchanged**: 1 of
12 on the strict definition, 2 of 13 on the loose one. Alpaca is a small minority
of the precondition population under both.

### One correction to MI-167's blast-radius table

MI-167's Finding 3 graded **`alpaca_live` only** among Alpaca accounts. There are
**four** Alpaca accounts in `config/accounts.yaml` — `alpaca_live` (`mode: live`,
`real_money`), `alpaca_paper`, `alpaca_portfolio`, `alpaca_options_paper` (all
`mode: live`, `paper`) — and **all four share `alpaca_client.py`**, so all four are
exposed to the whole-symbol close by code. `alpaca_live`'s inertness does not cover
the other three, and **the only pair that actually reached the precondition is on
`alpaca_portfolio`, an account MI-167's table does not list.** MI-167's conclusion
about *real money* stands untouched; its coverage of the *code path* was
account-scoped where the defect is client-scoped.

---

## 2. (b) Zero over-close events — measured two independent ways

### 2.1 No Alpaca close ever fired while a sibling was open

Scanning all 573 closed rows for a close whose `closed_at` falls inside a sibling
row's open interval on the same `(account, symbol)`:

| accounts | closes with an open sibling |
|---|--:|
| `bybit_1` | 172 |
| `bybit_2` | 8 |
| `bybit_portfolio` | 7 |
| **all accounts** | **187** |
| **all four Alpaca accounts** | **0** |

**0 of 32 Alpaca closes** in this population coincided with an open sibling. The
187 is the positive control — and it independently matches MI-167's own "187
closed rows", from a script I did not see.

The Alpaca precondition never occurred, so the whole-symbol `DELETE` never had a
sibling to take with it.

### 2.2 No Alpaca journal row is open with nothing behind it

An over-close leaves a specific signature: a row still `open` while the venue holds
no position for it. Live venue reads at 21:57Z, `error: null` on all four accounts:

| account | open journal rows | backed, qty matches EXACTLY | unbacked | qty mismatch |
|---|--:|--:|--:|--:|
| `alpaca_live` | 0 | 0 | 0 | 0 |
| `alpaca_paper` | 6 | 6 | 0 | 0 |
| `alpaca_portfolio` | 7 | 7 | 0 | 0 |
| `alpaca_options_paper` | 0 | 0 | 0 | 0 |
| **total** | **13** | **13** | **0** | **0** |

All **13** open Alpaca rows are backed, and the journal quantity equals the venue
quantity **exactly** — not approximately — on every one. The sharpest single cell
is the one that matters most (§ 3): `alpaca_portfolio` TLT, journal rows 5266
(16 shares) + 5414 (56 shares) = **72**, venue short **72.0**.

This is the `journal-open vs exchange_positions` diff MI-167's pass-3 item 2 asked
for and did not build ("it would have found this in one call"). It is built,
read-only, and committed.

### 2.3 Three venue positions I could NOT grade — reported as unread, not as clean

The same diff found divergence in the opposite direction:

| account | symbol | venue | journal rows in the population |
|---|---|--:|---|
| `alpaca_paper` | IEF | short 141 | **zero rows of any status** |
| `alpaca_portfolio` | IEF | short 152 | **zero rows of any status** |
| `alpaca_paper` | SPY | long 11 | 4 rows, **all `rejected`** (ids 4780, 5174, 5300, 5477) |

These are venue positions with no open journal row behind them — the mirror of an
over-close, not an instance of one. **I cannot grade them.** IEF has zero rows at
all inside the 1000-row cap, so its rows must predate id 4544 and are unreachable
(§ 0); SPY's four rows are all rejections, so whatever opened the venue position is
also outside the cap. They may be perfectly ordinary long-held positions whose
opening rows have simply rolled off.

**This is "we did not look, and here is exactly why we could not", not a finding
and not an all-clear.** Grading them needs a `trades` read that reaches past 1000
rows, which the diag surface does not currently offer.

---

## 3. The loaded gun — the precondition is live on Alpaca RIGHT NOW

The zero in (b) is a statement about the past. The present is worse than it reads.

**`alpaca_portfolio` / TLT holds two simultaneously-open rows at this moment:**

| row | opened | direction | size | strategy |
|---|---|---|--:|---|
| 5266 | 2026-08-31T13:30:26Z | short | 16 | `tlt_pullback_1d` |
| 5414 | 2026-09-03T13:30:32Z | short | 56 | `tlt_pullback_1h` |

Venue: TLT short **72.0** — the two rows summed, both fully backed. **The next
close of either row flattens all 72 shares and leaves the other row open with
nothing behind it.** That is the defect firing, on its first opportunity.

I verified the mechanism in the code rather than taking the backlog row's word:

* `AlpacaClient.close(self, symbol: str)` — `src/units/accounts/alpaca_client.py:859`.
  **The signature accepts no quantity at all.** Its docstring calls the operation a
  "flatten DELETE" and a "native flatten (`DELETE /v2/positions/{symbol}`)".
* `execute.close_open_position` receives a per-trade `qty` and, on the alpaca
  branch, **calls `exchange_client.close(symbol)`** —
  `src/units/accounts/execute.py:2964`. Two lines above, the IB branch calls
  `exchange_client.close(symbol, direction, qty)` (`:2927`). The comment at the
  alpaca branch says it outright: *"Whole-position flatten only … the qty argument
  is informational here (partial-close is not wired)."*

So MI-168's defect claim reproduces on a first-hand read. **INFERRED** from that:
the pair above will over-close on its next close. I did not test it — testing it
would mean firing a close on a live account.

⚠️ **`alpaca_portfolio` is `class: paper`, so this is not a real-money loss.** But
it is the same client, the same code path, and the same shape that `alpaca_live`
(`mode: live` / `real_money`) inherits the moment MI-140 is remediated. **The
coupling in the backlog row is real and this pair is the proof that the
precondition occurs in practice**, not merely in principle.

---

## 4. What I did NOT do, and one thing nobody should do

* **I changed no close path.** `AlpacaClient.close` and
  `execute.close_open_position` are untouched. That is Tier-3, it belongs to
  MI-168's PR #11279, and #11279 is blocked on two operator decisions. This
  document is a measurement, not a fix.
* ⚠️ **Do not apply MI-167's proposed remedy to Alpaca.** MI-167 proposed
  re-scoping the close confirmation at `alpaca_client.py:842` and `:1049`. On
  Alpaca the confirmation is symbol-scoped **and so is the operation**, so the two
  already agree. Narrowing the confirmation to a trade-sized reduction while the
  venue liquidates the whole symbol would report SUCCESS for a close that stranded
  a sibling — re-introducing
  `BL-20260707-ALPACA-CLOSE-NOT-CONFIRMED-FLAT`'s false-SUCCESS on a
  real-money-capable path. The backlog row says this; my read of the two call sites
  agrees with it.
* **I did not read MI-167's measurement script**, only its prose (§ 1). The 12-vs-13
  reconciliation is INFERRED on that basis.

## 5. What the next session should measure

1. **Grade the 3 venue-orphan positions** (§ 2.3). Needs a `trades` read past the
   1000-row cap. The cheapest route is probably adding `offset`/`since` to
   `GET /journal` — `/audit_query` in the same file already implements exactly that
   pattern, so it is a small, read-only, Tier-1-shaped change to a diag route.
   **That is a proposal, not something I did.**
2. **Re-run this script when MI-140 is remediated.** `alpaca_live` currently has
   0 open rows and an empty venue book; the moment it can place orders it joins the
   population that § 3 describes, and this doc's (b) needs re-deriving on real money.
3. **Watch `alpaca_portfolio` TLT specifically** (§ 3). It is the one pair that will
   demonstrate the defect, and it will do so with no code change at all — just the
   next exit signal.
