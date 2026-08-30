# The `alpaca_portfolio` roster against its own ruleset — all 14 legs

**2026-08-29 · Tier-1 research. Nothing wired, no config edit proposed.**

This is the widened evidence the operator asked for rather than deciding the
`tlt_pullback_1d` flag one leg at a time. It supersedes nothing in
`alpaca-roster-phase3-compat-gate-2026-08-29.md`; it extends that doc's
CORRECTED VERDICTS section from 5 legs to the account's whole 14-leg roster.

**All 14 ran on `74336d8f`**, which contains the R round-trip fix (`92458d64`).
Nothing on this page comes from a pre-fix run — the 11-leg sweep dispatched at
11:50Z on the unfixed sha was discarded unread, not reinterpreted.

## Method and population

`scripts/prop/account_compat_matrix.py --ledger` over the config-exact harness
emit, fee **0 bps**, **730 d**, `--base-account-size 5000.0`, live balances
(`size=measured` on all 28 cells — no default stood in for a balance).

Gate for a standard account: **`ret > 0` AND `surv ≥ 0.9` AND `pb ≤ 0.1`.**
`ret` is the mean end-return over the simulated paths as a **fraction of the
starting balance**; `surv` is horizon survival; `pb` is breach probability.
All three come from one Monte-Carlo over that leg's own emitted ledger of `n`
trades, so all three share the same population, and `n` is carried on every row.

⚠️ **THIS IS 2 OF THE 11 ACCOUNTS.** The one-line verdict prints
`alpaca_portfolio` and `alpaca_live` only. The other nine are graded inside
each run's `compat_*.json` artifact, which this session cannot download (no
artifact-read MCP), **so no claim is made about them here.**

## The 14 legs

`alpaca_portfolio` = paper, **$95,542.76**, `max_dd_pct` 0.05 / `daily_loss_pct` 0.05.
`alpaca_live` = real money, **$200.10**, `max_dd_pct` 0.10 / `daily_loss_pct` 0.10.

| leg | sym | n | `alpaca_portfolio` | `alpaca_live` |
|---|---|---|---|---|
| `gld_pullback_1d` | GLD | 11 | ROUTE · ret 3.651 · surv 1.0 · pb 0.0 | ROUTE · ret 3.651 · surv 1.0 · pb 0.0 |
| `spy_trend_long_1d` | SPY | 8 | ROUTE · ret 2.8102 · surv 1.0 · pb 0.0 | ROUTE · ret 2.8102 · surv 1.0 · pb 0.0 |
| `slv_pullback_1d` | SLV | 14 | ROUTE · ret 1.848 · surv 1.0 · pb 0.0 | ROUTE · ret 1.848 · surv 1.0 · pb 0.0 |
| `qqq_trend_long_1d` | QQQ | 10 | ROUTE · ret 1.5055 · surv 1.0 · pb 0.0 | ROUTE · ret 1.5055 · surv 1.0 · pb 0.0 |
| `gdx_pullback_1d` | GDX | 16 | ROUTE · ret 1.101 · surv 1.0 · pb 0.0 | ROUTE · ret 1.101 · surv 1.0 · pb 0.0 |
| `gld_pullback_1h` | GLD | 124 | ROUTE · ret 0.706 · surv 1.0 · pb 0.0 | ROUTE · ret 0.706 · surv 1.0 · pb 0.0 |
| `slv_trend_1h` | SLV | 109 | ROUTE · ret 0.683 · surv 1.0 · pb 0.0 | ROUTE · ret 0.683 · surv 1.0 · pb 0.0 |
| `tlt_pullback_1h` | TLT | 171 | ROUTE · ret 0.27 · surv 1.0 · pb 0.0 | ROUTE · ret 0.27 · surv 1.0 · pb 0.0 |
| `uso_trend_1h` | USO | 88 | ROUTE · ret 0.2334 · surv 1.0 · pb 0.0 | ROUTE · ret 0.2334 · surv 1.0 · pb 0.0 |
| `qqq_pullback_1h` | QQQ | 103 | ROUTE · ret 0.1817 · surv 0.9997 · pb 0.0003 | ROUTE · ret 0.1818 · surv 1.0 · pb 0.0 |
| `iwm_trend_long_1d` | IWM | 12 | ROUTE · ret 0.0897 · surv 1.0 · pb 0.0 | ROUTE · ret 0.0897 · surv 1.0 · pb 0.0 |
| **`spy_pullback_1h`** | SPY | **104** | **skip** · ret **−0.0194** · surv 1.0 · pb 0.0 | **skip** · ret **−0.0194** · surv 1.0 · pb 0.0 |
| **`ief_pullback_1d`** | IEF | **12** | **skip** · ret **−0.0605** · surv 1.0 · pb 0.0 | **skip** · ret **−0.0605** · surv 1.0 · pb 0.0 |
| **`tlt_pullback_1d`** | TLT | **13** | **skip** · ret **−0.3297** · surv 1.0 · pb 0.041 | **skip** · ret **−0.3333** · surv 1.0 · pb 0.0 |

`dd=not_terminal` on all 28 cells. Runs: 33252282572 · 33252285636 ·
33252288701 · 33252291424 · 33252294152 · 33252297469 · 33252301049 ·
33252304762 · 33252308249 · 33252311928 · 33252315627 (this sweep), plus
33252007816 (SLV 1h) · 33252011005 (GLD 1h) · 33252014088 (TLT 1d) from the
5-leg re-grade.

## Finding 1 — the flag is THREE legs, not one. Widening was the right call.

`tlt_pullback_1d` was flagged because it is in this account's roster and the
account's own ruleset refuses it. Graded across the whole roster, **two more
legs are in exactly that position**: `ief_pullback_1d` and `spy_pullback_1h`.
Deciding the one flagged leg on its own would have left both unexamined.

**But the three are not equally evidenced, and that changes what to do with
them:**

- **`spy_pullback_1h` is the well-evidenced one and the mildest** — n=104, and
  `ret −0.0194`, i.e. about −1.9% mean end-return. A solid sample saying this
  leg is a touch under water.
- **`tlt_pullback_1d` (n=13) and `ief_pullback_1d` (n=12) are badly negative and
  thinly evidenced** — −33.0% and −6.1% on ledgers well below the
  `MIN_OOS_TRADES = 25` floor the exit work uses.

The comfortable reading — "the worst offenders are obvious" — is the one to
resist: the two worst numbers sit on the two thinnest samples, which is the
usual shape of noise, not of a strong finding.

## Finding 2 — the account-size story is dead across 14 legs, not 5

Every one of the 14 legs returns **the same verdict on both accounts**, on
books that differ by a factor of **477** ($95,542.76 vs $200.10). Only two
cells differ at all, and neither differs in verdict: `tlt_pullback_1d`'s `pb`
(0.041 vs 0.0) and `qqq_pullback_1h`'s fourth decimal.

That is the retracted Phase 3 headline ("the account size is the binding
constraint") failing on a 14-leg population spanning n=8 to n=171. **`surv` is
1.0 on 27 of 28 cells and the 28th is 0.9997; `pb` is ≤ 0.041 everywhere.**
Nothing in this roster is size-bound or survival-bound — every skip is a
negative mean end-return.

**The arithmetic check holds on the wider set too.** `alpaca_live`'s limits are
strictly looser (0.10 vs 0.05), which *requires* `pb_live ≤ pb_portfolio` on a
shared R sequence. Observed on both cells where they differ: 0.0 ≤ 0.041 (TLT
1d) and 0.0 ≤ 0.0003 (QQQ 1h). The pre-fix run had this inverted, which is what
exposed the bug.

## Finding 3 — the 1d half of this roster is thinly evidenced, whichever way it votes

**All eight** 1d legs emit **n ≤ 16** over 730 days (8 · 10 · 11 · 12 · 12 ·
13 · 14 · 16), every one below `MIN_OOS_TRADES = 25`; the 1h legs run 88–171.
**The 1d legs also carry the largest returns** (3.651 · 2.8102 · 1.848 ·
1.5055 · 1.101) — a thin ledger producing a spectacular number is the cell to
trust least, and five of the six biggest ROUTEs on this page are in that
bracket. Read the 1h ROUTEs (GLD 124 · SLV 109 · TLT 171 · USO 88 · QQQ 103) as
the evidenced ones.

An n of 8–16 is not a reason to distrust the *tool* — it is what a daily
trend/pullback leg genuinely produces in two years. It is a reason not to treat
these particular verdicts as settled.

## What this does NOT say

- **A ROUTE is not a routing proposal, and on `alpaca_live` the stakes changed
  TODAY.** That account is now **`mode: live` with `strategies: []`**
  (`config/accounts.yaml`, commit `624d8841`, operator-directed 2026-08-29 —
  read from the field, not inferred). The empty list is **the only thing keeping
  it flat**: `src/units/accounts/__init__.py` preserves the `None` / `[]`
  distinction, and an explicit `[]` blocks all while an *absent* key falls
  through to allow. **So adding the first leg to that roster is the
  live-trading moment on real money, with no mode flip in front of it, and is
  Tier-3 in its own right.** This page produces 11 ROUTE verdicts for that
  account; not one of them is a reason to add a leg. Clearing this gate is one
  constraint among several (the Phase 2 capital-efficiency work and the exit
  Path A/B gates are separate), and the cash-vs-margin decision in
  `OI-20260829-ALPACA-GOLIVE-BLOCKED-ON-CASH-VS-MARGIN` sits in front of all of
  them.
- **`uso_trend_1h` ROUTEs, and that is not a real-money green light.** The
  operator's standing exclusion of USO from real money is on non-financial
  grounds and survives any backtest result. This row is a paper-account
  measurement; do not carry it forward as a real-money candidate.
- **No de-routing is proposed for the three skips.** Removing a leg from
  `config/accounts.yaml` is Tier-3 and the operator's call. What this page adds
  is that the decision covers three legs, and that they differ in how well
  evidenced they are.
- **Nine of the eleven accounts are unmeasured on this page** (see § Method).
