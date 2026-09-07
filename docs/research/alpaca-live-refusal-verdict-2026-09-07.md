# `alpaca_live` is NOT failing to place orders — it was asked twice, both times to short

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Date:** 2026-09-07 · **Tier:** 1 (measurement only — no order path, sizing, risk cap or
account mode touched) · **Task:** T-7 / `MI-140`

---

## VERDICT

**Correctly refusing — and the declaration is correct too.** Neither of the two
outcomes T-7 was framed around is what is happening.

There is **no order-path defect**, and there is **no defect in the declaration**.
`config/accounts.yaml` predicted this exact behaviour in writing, before the fact.

**The order path's live health is a separate question, and on it the honest answer is
`could not establish`**: since the roster was armed, **zero long signals have reached
this account**, so the order path has never been invoked. The leg is **not inert — it is
untested.** That is not a softer way of saying it works, and it is not a softer way of
saying it is broken. Nothing has been asked of it.

⚠️ **The premise "every signal journals `dry_run_no_order_placed`" is true and
substantively misleading.** It is **2 signals in 7 days, both short.** "Every" over a
population of two carries none of the weight the phrasing implies.

---

## POPULATION — stated on every count

Source: `GET /api/bot/db/table/{trades,order_packages}` on the live journal,
`filter_state: applied` asserted on **every** read below (an unknown filter column is
silently ignored by that route and returns the whole table — the counts here are not
subject to that trap).

**Code provenance.** The trader process runs `b596ac4f`; the web-api reports
`59bfa099`. `59bfa099` is an ancestor of `b596ac4f`, and
`git diff 59bfa099 b596ac4f` over `coordinator.py`, `execute.py`,
`silent_refusal_alert.py`, `cash_settlement.py`, `accounts.yaml`, `strategies.yaml`
and `account_state.yaml` is **empty** — so every code and config claim below was read
against exactly what is running. Verified, not assumed.

| population | n | window |
|---|---:|---|
| `trades` rows, `account_id = alpaca_live`, all time | **337** | 2026-06-25 → 2026-09-03 |
| — **before** roster arming (< 2026-08-31) | **335** | the dry_run / defunded era |
| — **since** roster arming (≥ 2026-08-31) | **2** | 2026-09-01, 2026-09-03 |
| `order_packages`, `strategy_name = tlt_pullback_1h`, all time | **76** | — |
| — since 2026-08-31 | **2** | both `short` |

**The two counts agree, and that is the point.** The signal side (`order_packages`)
and the dispatch side (`trades`) independently return **2**. No long signal was
generated and then lost before journalling — there were no long signals.

### The two post-arm rows, in full

| field | row 5306 | row 5415 |
|---|---|---|
| timestamp | 2026-09-01T16:05:36Z | 2026-09-03T13:30:33Z |
| symbol / direction | TLT / **short** | TLT / **short** |
| `position_size` | **2.0** | **2.0** |
| `account_class` / `is_demo` | `real_money` / 0 | `real_money` / 0 |
| `notes.is_dry` | **true** | **true** |
| `notes.reason` | `dry_run_no_order_placed` | `dry_run_no_order_placed` |

### Reason distribution, split at the arming date

| token | all time | pre-arm | **post-arm** |
|---|---:|---:|---:|
| `dry_run_sizing_skip: …` (all variants) | 161 | **161** | **0** |
| `zero_balance: …` (bare) | 33 | 33 | 0 |
| `risk_refused: …` (bare, ~$149–150 balances) | ~100 | ~100 | 0 |
| `sizing_failed: balance() returned None` | 20 | 20 | 0 |
| `account_mode_dry_run` | 20 | 20 | 0 |
| **`dry_run_no_order_placed`** | **2** | **0** | **2** |

`dry_run_no_order_placed` occurs **only** post-arm. `dry_run_sizing_skip` occurs
**only** pre-arm.

---

## WHY IT REFUSED — the gate the task did not count

T-7 says "both execution gates read live". That is correct and it is not the whole
chain. `coordinator.multi_account_execute` resolves `effective_dry` through **four**
gates, and the operative one is the fourth:

| # | gate | source | resolves to |
|---|---|---|---|
| 1 | `mode:` | `config/accounts.yaml` | **live** |
| 2 | `account_state.yaml` override | `config/account_state.yaml` | **no-op** — file lists only `bybit_1`/`bybit_2`; `alpaca_live` absent ⇒ fail-open |
| 3 | `execution:` | `config/strategies.yaml::tlt_pullback_1h` | **live** (field read, not the comment) |
| 4 | **`side_filter: long`** | `config/accounts.yaml::alpaca_live` | **DRY for every short** |

Gate 4 folds a suppressed direction into `effective_dry` — deliberately, so the
would-be trade is still journalled and the long-only decision stays measurable. That
routes into `execute_pkg`'s `is_dry` branch, where `_genuinely_dry` is True and the row
is written as `dry_run_no_order_placed` / `is_dry: true`. **Both post-arm signals were
short. Both hit gate 4.**

On top of that, the venue makes the point independently: `broker_account_status`
reports **`shorting_enabled: false`** on a cash account with `multiplier: 1`. Those
shorts were structurally unexecutable regardless of policy.

`config/accounts.yaml` wrote this down in advance: *"Expect roughly HALF this leg's
signals to be journalled as suppressed would-be trades. That is correct behaviour, not
a fault."* The measured post-arm split is 2 of 2 short — consistent with the recorded
55.4% short base rate on a population far too small to test it.

### The cleanest single piece of evidence

Package `pkg-c214602dd6ed42ab` (2026-09-03) fanned out to three accounts:

| trade id | account | class | status | size | reason |
|---|---|---|---|---:|---|
| 5413 | `alpaca_paper` | paper | rejected | 0.0 | `intent_noop:hold_to_bracket_reduce_non_derivative` |
| 5414 | `alpaca_portfolio` | paper | **open** | **56.0** | — (placed and held) |
| 5415 | **`alpaca_live`** | **real_money** | rejected | 2.0 | `dry_run_no_order_placed` |

**The same signal, through the same pipeline, on the same tick, placed 56 shares on
`alpaca_portfolio`.** The pipeline is not broken. `alpaca_live` alone declined it, for
the one reason that distinguishes it: `side_filter: long`, on a leg firing short.

---

## THE FIVE LEADS

### 1. `ALPACA_CASH_SETTLEMENT_MODE=apply` — **RULED OUT**

Read against the soak (`/api/diag/log_file?name=cash_settlement_soak`), not against the
task text. File **present**, 10,961 bytes, **27 records complete** (not a truncated
tail), 2026-08-31 → 2026-09-04 — spanning the entire armed period.

| account | soak rows |
|---|---:|
| `alpaca_paper` | 13 |
| `alpaca_portfolio` | 12 |
| `alpaca_options_paper` | 2 |
| **`alpaca_live`** | **0** |

Zero rows — not `would_have_reduced_usd: 0.00`, and not `basis_usd: None`. **The gate
has never evaluated on this account at all**, so it cannot be what is refusing.

The mechanism confirms the absence rather than leaving it as a bare negative:
`record_observation` sits inside the alpaca branch guarded by
`client is not None and not effective_dry`, and `client` is only constructed when
`not effective_dry`. Both post-arm signals were `effective_dry` at gate 4, so the block
was unreachable on both. The soak is silent for a reason that is fully explained, and
it will start recording the first time a long signal reaches dispatch.

### 2. `dry_run_sizing_skip` / `refusing_by_declaration` — **RULED OUT**

The worry was a `dry_run_sizing_skip` token still being emitted on a now-live account.
**It is not.** Post-arm: **0** rows carry it. All **161** occurrences are pre-arm, while
the account was `mode: dry_run` — exactly the era the token is for. There is no
token/mode mismatch on the live account.

For the record, both post-arm rows *do* bucket as `policy_skipped` (their token is in
`EXPECTED_DISPATCH_SKIP_REASONS`), which makes the correct `dead_leg` verdict
`refusing_by_declaration` — placed 0, refused 0, skipped 2. That is the accurate grade.
See lead 3 for why the live alert file does not say so.

### 3. `silent_refusal_alert` — **RULED IN. This is the real defect, and it is not on the order path.**

`/api/diag/log_file?name=silent_refusal_alert_state`, read live:

```json
"alpaca_live": {
  "alerting": true,
  "cause": "risk_refused",
  "refused": 5, "placed": 0,
  "verdict": "signalled_never_placed",
  "updated_at": "2026-08-21T12:38:38.231569+00:00"
}
```

`__last_check__` is **2026-09-07T15:56:48Z** — today. `bybit_1` and `ib_paper` both
carry that same fresh stamp. **`alpaca_live`'s entry is frozen 17 days back, at
2026-08-21 — ten days BEFORE the roster was armed.** Its `refused: 5 / placed: 0 /
cause: risk_refused` describes the defunded dry_run era and nothing since.

Two independent signs it was written by an older writer and never rewritten: it has
**no `alert_disposition` key at all**, and no `priority_causes` / `alerting_basis` —
all three of which the current `state[aid] = {...}` writer emits unconditionally.

**Consequence:** `silent_accounts()` — which the review skills read to decide whether to
flag an account — reports `alpaca_live` as **alerting, `signalled_never_placed`,
cause `risk_refused`** on 17-day-old pre-arm evidence. It is stale in the alarming
direction, and it is the most likely reason T-7 was framed as "cannot place an order".
The correct current grade is `refusing_by_declaration` (lead 2).

**Why it is stranded — inference, explicitly labelled as such.** `run_silent_refusal_check`
touches an account in exactly two places, and **both skip on the same set**:

```python
for aid, a in assessed.items():
    if aid in skip: continue          # never updated
...
for aid, prev in list(state.items()):
    if aid in assessed or aid in skip: continue   # never released either
```

An account absent from `assessed` (no rows in the 24h window — `alpaca_live` has
produced none since 2026-09-03) and *not* in `skip` would hit the release branch, fire
the "gone quiet" `[OK]`, and be **popped**. It is still present, still `alerting: true`.
An account in `assessed` would have been rewritten with today's stamp and the new keys.
It was not. The only reading consistent with the running code is that
**`alpaca_live` ∈ `SILENT_REFUSAL_SKIP`**, which strands any pre-existing latch forever.

⚠️ **I could not read the live process env to confirm this** — there is no Tier-1
read-only route to `/proc/<MainPID>/environ` on the diag surface, and I did not open a
Tier-2 action for it. So: the **stale latch is measured**; the **cause is inferred**.
Reading `SILENT_REFUSAL_SKIP` off the live unit is the one open step, and it decides
between "deliberate exclusion plus a stranded latch" and "a release-loop bug". Both need
the same one-line fix; only the framing differs.

### 4. `balance()` returning `None` while the account reads UP — **RULED OUT**

Measured three ways, all agreeing:

- **Live log, twice in the last few minutes** (`journalctl`, `ict-trader-live`):
  `account_open_positions(alpaca_live): alpaca empty snapshot verified via
  balance()=200.22` at 16:51:32Z and 16:53:24Z.
- **`/api/diag/broker_account_status`**: `status: ACTIVE`, `trading_blocked: false`,
  `account_blocked: false`, `error: null`, `cash / equity / buying_power = 200.22`.
- **The journal itself**: `balance = float(fetcher(account))` runs *unconditionally*,
  before and regardless of the dry short-circuit, and both post-arm rows carry
  `position_size: 2.0` — a size that cannot be produced from a `None` balance.

The 20 historical `sizing_failed: balance() returned None` rows are all pre-arm and are
not the current condition. `BL-20260814-REACHABILITY-PROBES-POSITIONS-NOT-BALANCE`
remains a real class; **it is not what is happening here.**

### 5. Whole-share sizing — **RULED OUT. The arithmetic was done, and it fits with room to spare.**

`alpaca` is in `risk.WHOLE_UNIT_QTY_EXCHANGES`, so a sub-1-share size would be a
refusal. Measured on row 5415's own numbers:

| term | value |
|---|---|
| balance | $200.22 |
| `risk_pct` | 2% | <!-- population-ok: a CONFIGURED parameter, not a sample statistic — alpaca_live risk.risk_pct: 0.02 is read from config/accounts.yaml:1111 (operator-directed 2026-08-29). The table's population is stated in the sentence above it: row 5415's own numbers, n=1 by construction. -->
| per-trade risk budget | **$4.00** |
| TLT entry | $82.34 |
| stop distance (`|82.685 − 82.34|`) | **$0.345 / share** |
| risk-ideal size | 4.00 / 0.345 = **11.61 shares** |
| **does ONE share fit the risk budget?** | **YES — $0.345 vs $4.00, it fits 11.6× over** |
| cash wall (0.9 × balance) | $180.20 → 180.20 / 82.34 = 2.19 |
| **whole shares** | **2** |
| **journalled `position_size`** | **2.0 — reproduces exactly** |

The sizer produced a positive, affordable, whole-share size on both signals. This is
**not** a sub-1-share refusal, and the "does one share even fit" question the task
posed answers cleanly: yes, by more than an order of magnitude. Cash is the binding
term (2 shares, not 11), exactly as `accounts.yaml` records — and cash binding the
*size* is not cash refusing the *trade*.

---

## TIER-3 DIFF

**None is warranted.** Nothing on the order path, sizing, risk caps, or the account's
mode is defective, so there is nothing there to propose. Changing any of them on this
evidence would be repairing a working instrument — the failure mode T-7 was explicitly
ranked to avoid.

The one actionable defect (lead 3) is in the **alerting** layer, not the order path.
The measurement stands on its own; the fix wants the `SILENT_REFUSAL_SKIP` read first,
because that determines whether the correct change is to prune skipped accounts' latches
in the release loop or to stop skipping this account. **Proposed and stopped here rather
than applied.**

## WHAT WOULD ACTUALLY SETTLE THE OPEN QUESTION

The order path is unexercised, and no amount of further reading changes that — only a
**long** `tlt_pullback_1h` signal will. Its long base rate is 44.6% (33 of 74 packages,
2026-06-22 → 2026-08-28), i.e. roughly one every 5 days of flow; the account has simply
not seen one in the 7 days since arming. The first long signal is the real test, and it
is the moment to watch: it exercises the order path, the whole-share sizing, and the
cash-settlement gate all at once — all three currently unobserved on this account.

Until then the correct statement is **"untested"**, and it must not be written up as
either "working" or "broken".
