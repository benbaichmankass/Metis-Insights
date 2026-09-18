# The nine inverse-ETF candidates all clear both walls — and only two of them serve a leg this account routes

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-251**, answering the operator decision *"Scope inverse ETFs first"*
(2026-09-10T10:15Z). Instrument: `scripts/research/mi251_inverse_proxy_screen.py`
(22 selftests, 9 negative controls). Artifact:
`docs/research/mi251-inverse-proxy-screen-2026-09-18.json`.

⚠️ **THIS SCREENS AND PROPOSES. IT WIRES NOTHING.** `config/` is untouched.
Declaring an instrument on `alpaca_live` touches a **real-money** `symbols:`
list and the CI-enforced `alpaca_portfolio` mirror equality, so any wiring is a
separate Tier-3 decision with its own approval — per MI-251's own scope line,
*"a session that ends by opening a config PR has exceeded this item."*

---

## 0. The answer

**Affordability is not the constraint, and on this candidate set it never was.**
All nine priced candidates clear **both** hard walls with large headroom — the
most expensive is `SH` at $32.55 against a **$180.00** cash ceiling, and the
widest reconstructed stop is `DGZ` at $0.334 against a **$6.00** stop ceiling.

The binding constraint is a question the affordability sweep never asked:
**does this proxy serve a leg `alpaca_live` actually routes?**

| | candidates | which |
|---|---:|---|
| serves a **routed** leg | **2** | `TBF` (TLT), `TBX` (IEF) |
| serves a leg that exists but routes **elsewhere** (paper only) | 3 | `SH` (SPY), `PSQ` (QQQ), `RWM` (IWM) |
| **no parent leg anywhere** — a new exposure, not a proxy | 3 | `EUM` (EEM), `DOG` (DIA), `SEF` (XLF) |
| parent is metals — operator already ruled no proxy exists | 1 | `DGZ` (GLD) |
| **`price_unknown` — we could not look** | 1 | `DDG` (XLE) |

---

## 1. The finding that should change what gets built next

**The operator's sequencing decision was correct when it was made and its
premise has since gone away, and nobody revisited it.**

On 2026-08-25 the operator chose: *"build SPY→SH and QQQ→PSQ FIRST — the two
with real liquidity ($1B, $759M) — proving the whole proxy pattern end-to-end
before spending sessions on TBF ($80M) and especially TBX ($14M), which may
fail its own liquidity check."* The measured basis recorded beside it was *"the
four SPY/QQQ legs alpaca_live routes produced 136 packages, 79 of them SHORT."*

**That basis was true when written.** Measured over all 8 commits to
`config/accounts.yaml` since 2026-08-20 (the reachable history; the clone is
shallow to 2026-08-10, which is *before* the window, so this range is complete):

| commit | date | `alpaca_live` roster | SPY/QQQ legs |
|---|---|---:|---|
| `3d90c4527` | 2026-08-23T13:05 | 16 legs | **present** |
| `ad70907d7` | 2026-08-24T00:58 | 16 legs | **present** |
| `beb154732` | 2026-08-25T11:43 | 16 legs | **present** |
| `b05872423` | 2026-08-27T11:34 | 16 legs | **present** |
| **`c1f50fc5d`** | **2026-08-29T17:06** | **0 legs** | **gone** |
| `a8a045a61` | 2026-09-01T01:40 | 1 leg | absent |
| `f2b871e98` | 2026-09-07T00:27 | 1 leg | absent |
| `e88ef612a` | 2026-09-10T14:43 | 5 legs | absent |

The roster was **emptied four days after the sequencing decision** and rebuilt
to a different set. **SPY and QQQ never came back.** Today `alpaca_live` routes
five legs over four symbols — `TLT` ×2, `IEF`, `SLV`, `IAUM`.

**So the two proxies sequenced FIRST serve nothing on this account, and the two
that serve the current roster are the two sequenced SECOND** — including `TBX`,
the one flagged *"may fail its own liquidity check."* The sequencing is
inverted by a roster change nobody connected back to it.

⚠️ **This is not a claim that the decision was wrong.** It is a claim that its
input changed. The remaining work under it — instrument declaration, a strategy
leg, intent-multiplexer registration, backtest evidence, and an
`account_compat_matrix` run **per proxy**, all Tier-3 and multi-session — would
currently be spent on `SH`/`PSQ`, whose parent legs route to `alpaca_paper` and
`alpaca_portfolio`, both `paper`. Filed as
`BL-20260918-THE-INVERSE-PROXY-SEQUENCING-DECISION-RESTS-ON-A-ROSTER-THAT-WAS-EMPTIED-FOUR-DAYS-LATER`.

---

## 2. Populations — state them before reading anything above or below

| population | what it is | n |
|---|---|---:|
| candidates | the nine -1x tickers the 2026-08-25 runner sweep priced, plus `DDG` which it could not | **10** |
| prices / stops | `yfinance-lane-proof` run 32828398224 (job 97741405818), **as of 2026-08-24** | 9 priced, 1 `fetch_failed` |
| roster history | every commit to `config/accounts.yaml` since 2026-08-20 | **8** |
| flow | `order_packages` tail via `/api/diag/journal`, **2026-06-02 → 2026-09-18** | **1000 rows** |
| legs | `config/strategies.yaml` | 55 |

⚠️ **The flow figure is a 1000-row TAIL, not the table.** `/api/diag/journal`
hard-caps at 1000 rows `id DESC` whatever limit is asked, so this is the most
recent slice and **not** the whole-history basis the 2026-08-25 note used
(3,984–4,029 rows). The two are not comparable and are never pooled here.

⚠️ **Prices are 25 days old.** They are used to *illustrate* the ceilings, never
as a current answer — see §3.

---

## 3. The walls are emitted as CEILINGS, deliberately, because the predecessor's list decayed

`docs/research/alpaca-200-affordability-sweep-2026-08-25.md` recorded a list of
reachable symbols. **Its own superseding note says that was the defect:**
reachability is a function of price and volatility, both of which move without a
commit, and `GDX` had already fallen out of the set unnoticed
(`BL-20260910-THE-ALPACA-LIVE-REACHABLE-SYMBOL-SET-IS-RECORDED-AS-A-CONSTANT-AND-GDX-HAS-SINCE-FALLEN-OUT-OF-IT`).
Publishing a second verdict list 25 days later would reproduce that defect
exactly. So this emits the walls as functions of the account's own config:

```
cash ceiling = _MARGIN_SAFETY_BUFFER × available_usd = 0.9 × 200.00 = $180.00
stop ceiling = _ROUND_UP_BUDGET_MULT × risk_pct × equity = 1.5 × 0.02 × 200 = $6.00
```

A reader with *any* price can grade a candidate themselves; the ceilings only
move when the config does.

⚠️ **THE STOP CEILING IS NOT `risk_pct × equity`.** The whole-share path carries
a **round-up relaxation** (operator directive 2026-06-24): when the risk-ideal
size is under one share the sizer rounds **up** to one, provided that share's
stop risk is within `_ROUND_UP_BUDGET_MULT` of the budget. Omitting the 1.5×
understates the ceiling by a third and reports reachable instruments as refused.

**Positive control on the derivation, and it is not decorative.** MI-201
independently measured `GDX`'s ceiling at **$6.0066** against a $200.22 balance
at `risk_pct 0.02`. The formula reproduces that to the cent
(1.5 × 0.02 × 200.22). A selftest asserts it, so an edit to either constant
fails there rather than silently in a memo nobody re-runs.

### 3.1 A second staleness in the predecessor, independent of price

The 2026-08-25 verdicts were computed at **`risk_pct 0.05`**. The account's
declared `risk` block reads **`risk_pct: 0.02`** today. So **both** inputs to
the stop wall moved, not just the price — and the 0.05→0.02 change alone
tightens the stop ceiling from $15.00 to $6.00.

⚠️ **`DGZ`'s loudest caveat is therefore resolved and must not be re-quoted.**
The sweep flagged it as *"by far the largest single-trade risk in the set —
$9.69 of a $10.00 budget (4.8% of equity)"* — population n=1, one sized `DGZ`
position at the 2026-08-24 price on a $200 account at `risk_pct 0.05`.
**The new figure has the same n=1 population at today's declared `0.02`.** They are the
same instrument under two different risk settings, not a sample of anything.
Measured with the real sizer at 0.02: **11 shares, $3.68 risk, 1.84% of that
$200 equity** — mid-pack against the eight other candidates in §3.2, not an
outlier. (It remains out of scope for a different reason: gold is metals.)

### 3.2 The ceilings agree with the real sizer, end to end

The predecessor insists verdicts come from *"the real sizer, not arithmetic over
the table"*, so `--sizer` runs `RiskManager.position_size` at `equity 200.0`,
`whole_units=True`, `available_usd=200.0`:

| ticker | qty | notional | risk $ | % equity |
|---|---:|---:|---:|---:|
| `DGZ` | 11 | $52.80 | $3.68 | 1.84% |
| `RWM` | 13 | $175.37 | $1.75 | 0.88% |
| `EUM` | 11 | $175.89 | $2.50 | 1.25% |
| `DOG` | 8 | $170.56 | $1.23 | 0.61% |
| `TBF` | 7 | $177.52 | $1.46 | 0.73% |
| `PSQ` | 6 | $157.08 | $1.84 | 0.92% |
| `TBX` | 6 | $172.86 | $0.64 | 0.32% |
| `SEF` | 6 | $174.54 | $1.44 | 0.72% |
| `SH` | 5 | $162.75 | $1.05 | 0.53% |

**Ceiling-vs-sizer disagreements: 0 of 9.**

⚠️ **NEGATIVE CONTROL, asserted rather than reported:** `GDX` ($99.43 price,
7.665 stop) **refuses — qty 0.0**, reproducing MI-201's independent measurement
that it sized on **0 of 59** observed setups. A run in which `GDX` sizes means
this harness is not reproducing the live gate, and every PASS above would then
be worthless.

---

## 4. The flow actually at stake

Over the 1000-row tail, for the five legs `alpaca_live` routes:

| leg | packages | short | short % | proxy |
|---|---:|---:|---:|---|
| `tlt_pullback_1h` | 21 | 7 | 33.3% | `TBF` |
| `tlt_pullback_1d` | 4 | 4 | 100.0% | `TBF` |
| `ief_pullback_1d` | 1 | 1 | 100.0% | `TBX` (thin) |
| `slv_pullback_1d` | 2 | 0 | 0.0% | *none — metals* |
| `iaum_pullback_1d` | 1 | 0 | 0.0% | *none — metals* |
| **total** | **29** | **12** | **41.4%** | |

**`TBF` alone would serve 11 of the 12 short packages (91.7%).** `TBX` serves
the remaining one.

**Control — the legs the proxy programme was sized on:** `spy_pullback_1h` 6
packages / 3 short, `qqq_pullback_1h` 7 / 3. Six short packages, on legs that
route to `alpaca_paper` and `alpaca_portfolio` only.

⚠️ **n IS SMALL AND THIS IS NOT AN EDGE CLAIM.** 29 packages over ~3.5 months is
far below any evidence floor, and MI-251's own criteria say `insufficient_n` on
track record is **the expected reading and not a blocker**, because this is a
plumbing/expressibility question. Do not read the per-leg percentages as rates.

⚠️ **`order_packages` rows are per-STRATEGY, not per-account.** These are the
leg's packages fleet-wide, which is the same basis the 2026-08-25 note used. A
package is produced once and fanned out, so this counts the flow a proxy would
have to express — not fills on this account.

---

## 5. Three candidates are affordable and serve nothing

`EUM` (emerging markets), `DOG` (Dow) and `SEF` (financials) have **no parent
leg anywhere in `config/strategies.yaml`** — measured, with the positive control
that the query does find `TLT`, `IEF`, `SPY` and `QQQ`.

They are affordable, mapped, and are **not proxies for anything**. Declaring one
would add a new exposure to a real-money account, not recover suppressed flow.
`no_parent_leg` is reported as its own state for exactly this reason: pooling it
with "reachable" is how an affordability screen turns into a shopping list.

---

## 6. What this does NOT establish

- **Nothing about edge.** No backtest was run and none is proposed here. The
  evidence gate on `BL-20260823-NO-INVERSE-ETF-INSTRUMENTS-DECLARED` stands:
  the edge is decided offline, with the **0.89–0.95% expense ratios and the
  daily-rebalance drift INSIDE** the backtest, before anything is built.
- **Nothing about liquidity or tracking error.** `TBX` remains flagged thin
  (~$14M) on its 2026-08-25 source and this screen does not re-check it.
- **Nothing about current prices.** They are 25 days old. Re-grading needs one
  `yfinance-lane-proof` run; the ceilings do not change with it.
- **Nothing about the mirror-leg design's blockers, which are unchanged.**
  `docs/research/alpaca-proxy-signal-vs-order-symbol-2026-08-25.md` established
  that the aggregator drops a cross-symbol intent, that the emitter overwrites
  the symbol, that `supported_symbols()` validates against `accounts.yaml`, and
  — the correctness break — that **the monitor re-evaluates the exit on the
  ORDER symbol's candles**, i.e. an inversely-correlated series. None of that is
  touched here, and none of it is made easier by this screen.
- **`DDG` is `price_unknown`, not refused.** The 2026-08-25 run graded it
  `fetch_failed`. It is carried rather than dropped because a candidate nobody
  could price is not a candidate that failed.

---

## 7. Filed, not fixed

- `BL-20260918-THE-INVERSE-PROXY-SEQUENCING-DECISION-RESTS-ON-A-ROSTER-THAT-WAS-EMPTIED-FOUR-DAYS-LATER`

---

## 8. Reproduce

```bash
python3 scripts/research/mi251_inverse_proxy_screen.py --selftest   # 22 checks, 9 negative controls
python3 scripts/research/mi251_inverse_proxy_screen.py --sizer --report
python3 scripts/research/mi251_inverse_proxy_screen.py --sizer \
        --json docs/research/mi251-inverse-proxy-screen-2026-09-18.json
```

The roster history and the flow figures are reproduced by the commands in §1
and §4 respectively: `git log -- config/accounts.yaml` since 2026-08-20, and
`/api/diag/journal?table=order_packages&limit=1000` via
`scripts/ops/diag_fetch.sh`.
