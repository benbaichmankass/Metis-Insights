# Journal-open vs venue positions — the first fleet-wide audit, 2026-09-08

> **Doc status:** `live` · category `evidence` · last verified `2026-09-08` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)

**MI-177**, standing pipeline-integrity lane pass 4, under
`IN-20260903-TRADING-SYSTEM-HEALTH` / `CY-20260906-TRADING-TRUTH`. This is the
step [`WO-20260907-STANDING-PIPELINE-INTEGRITY-LANE-ARE-WE-ACTUALLY`](objects/WO-20260907-STANDING-PIPELINE-INTEGRITY-LANE-ARE-WE-ACTUALLY.yaml)
named after pass 1 and nobody had run: *"Tier-1 fix: journal-open vs
exchange_positions audit across all accounts."*

**MEASURE ONLY.** Nothing was closed, cancelled, repaired, re-armed or
reconciled. No order-path code was touched.

## Why it was worth doing

Every journal-vs-venue divergence this system has ever found was found by
accident, while someone was looking at something else — MGC's phantom lots
during a health review, Bybit's SOLUSDT divergence during a netting
investigation, `alpaca_portfolio`/TLT because MI-173 went looking at one
symbol. **The question had never been asked fleet-wide.**

## Verdict

**Of 11 declared accounts, 8 were readable on both sides. Across those 8, 24
symbol pairs were compared: 23 reconcile EXACTLY and 1 diverges.** The one
divergence is the already-owned MGC case. **Neither real-money account that
could be read diverges at all.** Three accounts were not readable on the venue
side and are reported as *not looked at* — never as clean.

## The per-account table

`journal` = signed sum of open journal rows. `venue` = signed size from
`/api/diag/exchange_positions`. Long positive, short negative.

| account | money | venue read_state | journal read_state | symbol | rows | journal | venue | divergence |
|---|---|---|---|---|---|---|---|---|
| `bybit_2` | **REAL MONEY** | READ | READ (complete) | ETHUSDT | 1 | 0.04 | 0.04 | **0 — exact** |
| `bybit_2` | **REAL MONEY** | READ | READ (complete) | XRPUSDT | 1 | 58.5 | 58.5 | **0 — exact** |
| `alpaca_live` | **REAL MONEY** | READ | READ (complete) | *(none)* | 0 | flat | flat | **0 — exact** |
| `ib_live` | **REAL MONEY** | **NOT READ** | READ (0 rows) | — | — | — | **not read** | **UNKNOWN** |
| `breakout_1` | **PROP** | **NOT READ** | READ (0 rows) | — | — | — | **not read** | **UNKNOWN** |
| `ib_paper` | paper | READ | READ (complete) | **MGC** | **2** | **54** | **11** | **+43 — DIVERGES** |
| `ib_paper` | paper | READ | READ (complete) | MES | 1 | 15 | 15 | 0 — exact |
| `ib_paper` | paper | READ | READ (complete) | MHG | 1 | 30 | 30 | 0 — exact |
| `bybit_1` | paper | READ | READ (complete) | ADAUSDT | 2 | 79855 | 79855 | 0 — exact |
| `bybit_1` | paper | READ | READ (complete) | AVAXUSDT | 2 | 13801.9 | 13801.9 | 0 — exact |
| `bybit_1` | paper | READ | READ (complete) | XRPUSDT | 1 | 7114.1 | 7114.1 | 0 — exact |
| `bybit_portfolio` | paper | READ | READ (complete) | ETHUSDT | 1 | 9.41 | 9.41 | 0 — exact |
| `bybit_portfolio` | paper | READ | READ (complete) | XRPUSDT | 1 | 11903.8 | 11903.8 | 0 — exact |
| `alpaca_paper` | paper | READ | READ (complete) | GDX | 1 | 162 | 162 | 0 — exact |
| `alpaca_paper` | paper | READ | READ (complete) | GLD | 1 | 39 | 39 | 0 — exact |
| `alpaca_paper` | paper | READ | READ (complete) | IAUM | 1 | 613 | 613 | 0 — exact |
| `alpaca_paper` | paper | READ | READ (complete) | IEF | 1 | −141 | −141 | 0 — exact |
| `alpaca_paper` | paper | READ | READ (complete) | QQQ | 1 | 9 | 9 | 0 — exact |
| `alpaca_paper` | paper | READ | READ (complete) | SLV | 1 | 345 | 345 | 0 — exact |
| `alpaca_paper` | paper | READ | READ (complete) | SPY | 1 | 11 | 11 | 0 — exact |
| `alpaca_paper` | paper | READ | READ (complete) | TLT | 1 | −707 | −707 | 0 — exact |
| `alpaca_portfolio` | paper | READ | READ (complete) | GDX | 1 | 252 | 252 | 0 — exact |
| `alpaca_portfolio` | paper | READ | READ (complete) | GLD | 1 | 123 | 123 | 0 — exact |
| `alpaca_portfolio` | paper | READ | READ (complete) | IEF | 1 | −152 | −152 | 0 — exact |
| `alpaca_portfolio` | paper | READ | READ (complete) | QQQ | 1 | 9 | 9 | 0 — exact |
| `alpaca_portfolio` | paper | READ | READ (complete) | SLV | 1 | 422 | 422 | 0 — exact |
| `alpaca_portfolio` | paper | READ | READ (complete) | SPY | 1 | 84 | 84 | 0 — exact |
| `alpaca_portfolio` | paper | READ | READ (complete) | **TLT** | **2** | −72 | −72 | 0 — exact *(see § blind spot)* |
| `oanda_practice` | paper | **NOT READ** | READ (0 rows) | — | — | — | **not read** | **UNKNOWN** |
| `alpaca_options_paper` | paper | READ | READ (complete) | *(none)* | 0 | flat | flat | 0 — exact |

Snapshots: venue `2026-09-08T03:24:41Z`, re-read `03:30:39Z` (identical).
Journal read at both times (29 open rows both times).

## The positive control

A clean verdict is worth nothing unless the probe can be shown finding a known
positive. Three known divergences existed to test against:

1. **MGC — FOUND.** `ib_paper` journal 54 (trades 5353 = 11, 5531 = 43) against
   venue 11, divergence **+43**. Reproduced on two independent reads six
   minutes apart. This is the exact case
   [`WO-20260907-ROOT-CAUSE-THE-43-PHANTOM-MGC-LOTS`](objects/WO-20260907-ROOT-CAUSE-THE-43-PHANTOM-MGC-LOTS.yaml)
   owns. **Recognised as already-owned and deliberately NOT re-filed.**
2. **Bybit SOLUSDT 451× — NOT TESTABLE TODAY.** No account holds any open
   SOLUSDT position on either side, so this control could not be exercised. It
   is not evidence the probe works and is not counted as one.
3. **`alpaca_portfolio` / TLT — FOUND, BUT NOT AS A DIVERGENCE.** See below.
   This one bounds what "clean" means and is the more useful result.

So the probe is demonstrated discriminating on one live known positive out of
three, and the 23 exact reconciliations are readable as discrimination rather
than blindness.

## The blind spot this audit has, stated explicitly

**A per-symbol sum cannot see the defect MI-173 found.** `alpaca_portfolio`
TLT is two journal rows — 5266 (`tlt_pullback_1d`, 16) and 5414
(`tlt_pullback_1h`, 56) — against one venue position of 72 short. They sum to
exactly 72, so this audit reports it **exact**, and would report it exact no
matter how the 72 were split. The venue nets; the journal does not.

That packing is **not confined to TLT**. Four symbols across three accounts
carry more than one journal row against a single venue position:

| account | symbol | rows | strategies |
|---|---|---|---|
| `ib_paper` | MGC | 5353, 5531 | `mgc_pullback_1d`, `ict_scalp_mgc_15m` |
| `alpaca_portfolio` | TLT | 5266, 5414 | `tlt_pullback_1d`, `tlt_pullback_1h` |
| `bybit_1` | ADAUSDT | 5417, 5479 | `trend_donchian_ada_4h`, `ada_pullback_2h` |
| `bybit_1` | AVAXUSDT | 5522, 5535 | `trend_donchian_avax_4h`, `ict_scalp_avax_5m` |

**Anyone reading this audit's "23 of 24 exact" must not read it as "per-trade
attribution is correct".** It says the *quantities* agree. Whether each journal
row owns the share of the venue position it claims — which is what a per-leg
PnL or exit verdict depends on — is a different question this instrument does
not answer, and it is MI-173's.

## The methodological finding — why this audit nearly filed four false positives

`/api/diag/journal?table=trades` is `ORDER BY id DESC LIMIT <=1000`. The trades
table holds 5545 rows, so the reachable window was ids 4546–5545 and **4 of the
29 open rows fell below it**: 4194 (`alpaca_paper` IEF 141), 4195
(`alpaca_portfolio` IEF 152), 4347 (`alpaca_paper` SPY 11), 4350 (`ib_paper`
MES 15), all opened 2026-07-29 … 08-03.

Read off `/api/diag/journal` alone, those four present as *"the venue holds a
position the journal has no row for"* — **four phantom-position divergences
that do not exist**, two of them on the same accounts as real findings.

They were caught by not trusting either surface alone:

* open rows were re-read from `/api/bot/positions?include_paper=true`, which
  queries `WHERE status='open'` without an id window; and
* the resulting per-account notional was cross-checked against
  `RiskManager._open_gross_notional_from_db` (`src/units/accounts/risk.py:472`)
  — an **uncapped** `SUM(ABS(position_size*entry_price))` over all open
  non-backtest rows — served per account by `/api/diag/exposure`.

The two agree **to the cent on all 9 accounts where exposure is measurable**.
That agreement, not any single route's say-so, is what makes "journal read
state = complete" an assertion rather than a hope.

`/api/bot/positions` is itself capped at `LIMIT 50` (`dashboard.py:770`) with
no total and no truncation flag; it returned 29 rows, so it did not truncate
here. Filed as
`BL-20260908-BOT-POSITIONS-SILENTLY-CAPS-AT-50-OPEN-ROWS-AND-THE-FLEET-IS-AT-29`.

## Read states — the three accounts nobody looked at

`positions: null` from `/api/diag/exchange_positions` means *could-not-read*,
per that route's own docstring. It arrived with `error: null` for all three, so
the response itself does not distinguish them; the reasons below come from
reading `src/units/accounts/clients.py::account_open_positions`.

| account | money | why not read | resolves on its own? |
|---|---|---|---|
| `ib_live` | **REAL MONEY** | `mode: dry_run` — IB branch never dials a dry account | yes, on promotion to live |
| `oanda_practice` | paper | `mode: dry_run` — same gate in the oanda branch | yes, on promotion to live |
| `breakout_1` | **PROP, `mode: live`** | `exchange: breakout` is not one of the four exchanges the function supports | **no — no read path exists** |

`breakout_1` is live and not dormant (41 `prop_fills`, 91 `prop_tickets`) and
its venue state is unreadable by construction. Filed as
`BL-20260908-THREE-OF-ELEVEN-ACCOUNTS-HAVE-NO-VENUE-POSITION-READ-AND-BREAKOUT-1-CAN-NEVER-HAVE-ONE`.

## What this answers for an existing work object

`WO-20260907-ROOT-CAUSE-THE-43-PHANTOM-MGC-LOTS`'s `done_condition` asks
*"whether the same class touches `bybit_2` or `alpaca_live` (both REAL MONEY —
this outranks the MGC root cause if yes)"*.

**Measured answer: no.** `bybit_2` reconciles exactly on both open symbols
(ETHUSDT 0.04, XRPUSDT 58.5) and `alpaca_live` is flat on both sides with an
uncapped journal read confirming zero open rows. **The MGC root cause is not
outranked.** This says nothing about the third real-money account, `ib_live`,
whose venue was not read.

## What the next pass should check

Named here because this lane's contract is that each pass names the next one:

1. **Attribution, not quantity** — for the four multi-row symbols above,
   establish whether each journal row owns the share of the venue position it
   claims. That is the class this audit is blind to and MI-173 opened.
2. **The audit is a one-shot read, not a monitor.** Nothing re-runs it. A
   divergence appearing tomorrow is found by the next accident again unless
   this comparison is put on a cadence with the read-state discrimination
   intact.
3. **`ib_live` is real money and unverifiable.** Decide whether a dry
   real-money account should be readable, or record that it is trusted flat
   because it is dry.
