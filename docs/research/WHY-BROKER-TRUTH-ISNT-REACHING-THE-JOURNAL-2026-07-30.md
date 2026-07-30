# Why we have so little real data — the acquisition-side root cause

**Status:** problem statement + verified evidence, written 2026-07-30 as a **handoff for a
fresh session**. Nothing here is fixed yet.

**Operator's challenge, which is correct and is the reason this doc exists:**

> "We're running an API-based system. There's absolutely no reason for this."

Right. The previous session (`S-PROVENANCE-IB-EXECUTIONS-2026-07-30`, PR #8069) made the
system **honest** about how much of its PnL is manufactured. It did **not** make the system
**acquire** the real numbers. Those are different problems and only the first one was solved.
60.8% measured coverage is the accurate reporting of a bad situation, not an acceptable
resting state.

---

## The one-sentence answer

**Every broker's fills are already being collected on a timer, and — until PR #8069's IB
path — nothing ever read them back into `trades.pnl`.** The system pulls broker truth into
`exchange_fills.sqlite`, uses it only to render a dashboard panel, and then prices the same
trades from a mark.

That is the *identical* defect class the provenance work just root-caused, one level up: a
signal that is **written and never read**. Last time it was `exit_price_source`. This time it
is the fills store itself.

---

## Verified evidence

### 1. 79% of the fabrication is two accounts that are STRUCTURALLY BARRED from broker truth

| account | demo? | fabricated / closed | share |
|---|---|---|---|
| **`bybit_1`** | **`demo: true`** | **152 / 323** | **47.1%** |
| **`bybit_portfolio`** | **`demo: true`** | **11 / 12** | **91.7%** |
| `bybit_2` | no | 8 / 406 | **2.0%** |

`src/units/accounts/clients.py::account_closed_pnl_for_trade` contains:

```python
if is_demo:
    return None
```

So for `bybit_1` and `bybit_portfolio` the broker lookup **is never attempted**. Not
rate-limited, not failing — never called. Those two accounts are **163 of the 206** fabricated
closed rows.

**`bybit_2` is the control that proves the API is fine.** Same exchange, same code path, same
symbols — 2.0% fabricated versus 47.1%. The difference is not the venue's data. It is one
`if`.

### 2. That `if` is a CORRECT fix for the wrong scope

It came from `BL-20260608-DEMOPNL` / `BL-20260620-CLOSEDPNL-LOOKUP-MISMATCH-DEMO`: Bybit's
**closed-pnl endpoint** genuinely mis-maps records on demo — distinct demo trades on the same
symbol shared records, and it booked the same −864.45 onto two different SOL trades. Disabling
it was right.

**But "the closed-pnl endpoint is unreliable on demo" was silently generalised into "there is
no broker truth for demo."** That second statement is false, and the repo already knows it —
see next.

### 3. The demo fills ARE reliable, ARE already being pulled, and nothing reads them

`src/runtime/exchange_accounts.py::live_bybit_fill_accounts`, verbatim:

> "Includes paper-class Bybit accounts (`bybit_1`, `bybit_portfolio`) — they trade the real
> Bybit demo/live venue and so have **real exchange-side fills** + funding worth capturing for
> cost truth."

`ict-exchange-fills-pull.timer` runs `pull_exchange_fills.py --all-bybit-accounts --days 7`
**daily**, covering both demo accounts, via `fetch_my_trades` — a **different endpoint** from
the unreliable closed-pnl one. `exchange_fills_store` already implements FIFO lot matching
(`fifo_pnl_by_symbol` / `_fifo_match`).

Every consumer of that store:

| consumer | what it does |
|---|---|
| `src/web/api/routers/pnl_exchange.py` | renders `/api/bot/pnl/exchange` — **display only** |
| `scripts/ops/backfill_broker_truth_costs.py` | offline, one-shot |
| `scripts/ops/reconcile_netting_pnl.py` | offline, one-shot |
| `src/runtime/exchange_fills_ib.py::closed_pnl_from_fills` | **the only read-back into `trades.pnl` — added by PR #8069, IB only** |

So the real fills land in a local SQLite, get rendered on a panel, and the monitor prices the
same trade from a mark six hours later.

### 4. It is not only Bybit demo — the pattern is system-wide

| account | fabricated / closed | fills puller exists? | in `BROKER_PNL_READER_EXCHANGES`? |
|---|---|---|---|
| `bybit_1` | 152 / 323 | ✅ daily | ✅ but demo-excluded by the `if` |
| `bybit_portfolio` | 11 / 12 | ✅ daily | ✅ but demo-excluded by the `if` |
| `alpaca_paper` | 26 / 44 (59.1%) | ✅ `pull_alpaca_fills.py`, daily | ❌ **not a member at all** |
| `alpaca_options_paper` | 6 / 8 (75.0%) | ✅ daily | ❌ not a member |
| `ib_paper` | 3 / 27 | ✅ hourly (new, #8069) | ✅ (new, #8069) |

**198 of the 206 fabricated rows (96%) sit on accounts whose fills are already being pulled on
a schedule.** The data is being paid for and thrown away.

---

## Why this matters more than the dashboard number

Paper **is** the ML training population. Measured on the live journal (trainer-diag #8100):
**206 of 829 labels (24.8%) have their `won`/`lost` decided by a mark-substituted price, and
71 of those are labelled WON.** A phantom win teaches a model that a losing setup pays. July's
fabricated share is 65.3%, so anything retrained recently is worse hit than the lifetime
figure suggests.

---

## What the new session should do

### Step 0 — measure before building (the load-bearing unknown)

**Everything above is verified from code and from the journal. What is NOT yet verified is
whether `exchange_fills.sqlite` on the live VM actually contains rows for `bybit_1` and
`bybit_portfolio`.** The puller is scheduled and its docstring says demo fills are real, but
`BL-20260713-EXCHANGE-FILLS-STORE-EMPTY` is precedent that a scheduled puller can accrue
nothing for months unnoticed.

Do not build the read-back until this is answered. Check:

- row counts per `account_id` in `exchange_fills`, and the `exec_time` span;
- whether demo-account rows carry usable `price`/`qty`/`exec_time`;
- whether `/api/bot/pnl/exchange` returns non-zero for the demo accounts.

If the store is empty for demo, the fix is upstream (creds/category/puller scope) and is
*cheaper* than the read-back. If it is populated, go to step 1.

### Step 1 — the read-back, generalised

`closed_pnl_from_fills` in `src/runtime/exchange_fills_ib.py` is the working template, merged
and CI-green. It is a **local SQLite read, not a broker call**, which is what makes it safe on
the monitor tick. Generalise it out of the IB module into a venue-neutral resolver and give
Bybit-demo and Alpaca branches in `account_closed_pnl_for_trade`.

Keep its **refusal** semantics exactly: qty mismatch >5%, any matched fill missing its PnL
input, or any unusable row ⇒ return `None` and fall through. Summing the subset that reported
would look clean and be quietly too small.

Note the asymmetry to handle: IB fills carry `realizedPNL` directly; **Bybit fills do not** —
they need FIFO lot matching (`_fifo_match`, already written) to derive realised PnL, and the
matching must be per-account and per-symbol. Alpaca **equities** likewise; Alpaca **options**
realised PnL comes from `/v2/account/activities` cash, not the equity formula (see
`order_monitor._reconcile_options_expiry_and_assignment`).

### Step 2 — the demo `if` becomes narrow instead of total

Replace the blanket `if is_demo: return None` with: *demo skips the **closed-pnl endpoint**,
then falls through to the **fills-derived** resolver.* The 2026-06-08 incident is still
honoured — that endpoint stays untrusted for demo — without throwing away the venue's fills.

### Step 3 — historical rows

Operator decision from 2026-07-30 stands: the historical pass is **RELABEL ONLY, never
re-price**. But note Bybit's execution history is retained far longer than IBKR's, so for
`bybit_1`/`bybit_portfolio` a *bounded* historical re-derivation from fills may be defensible
where a fill genuinely exists. **That is a Tier-2/3 call and needs an explicit decision —
do not assume it.**

### Step 4 — close the loop the honest way

`pnlCoverage` on `/api/bot/performance` is already live and is the metric to move. Re-run
`scripts/ops/provenance_exposure_audit.py` before and after. The target is not "100%" — it is
that every remaining fabricated row has a *stated reason* it could not be measured.

---

## What NOT to do

- **Do not widen `is_measured()`** to make coverage look better. The vocabulary in
  `src/runtime/provenance.py` is the one thing that is currently trustworthy.
- **Do not re-enable the mark substitution** in `_sweep_local_pnl_for_unpriced`. Anchoring +
  declaring is strictly better than inventing, even at lower apparent coverage.
- **Do not add a per-row broker call on the monitor tick.** That is the 2026-06-09 cold-start
  wedge shape. Reads come from the local store; the network half stays on its own timer.
- **Do not drop fabricated rows from the ML training set silently** — that is itself the
  silent population change this workstream exists to stop
  (`BL-20260730-ML-LABELS-IGNORE-PNL-PROVENANCE` carries the required shape).

---

## Backlog rows

- `BL-20260730-BROKER-TRUTH-COLLECTED-NEVER-READ` — **this doc**, the acquisition-side gap.
- `BL-20260730-ML-LABELS-IGNORE-PNL-PROVENANCE` — the consumer-side consequence, measured.
- `BL-20260608-DEMOPNL` / `BL-20260620-CLOSEDPNL-LOOKUP-MISMATCH-DEMO` — the correct fix whose
  scope was over-generalised.
- `BL-20260713-EXCHANGE-FILLS-STORE-EMPTY` — precedent that a scheduled puller can silently
  accrue nothing.
