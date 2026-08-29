# Phase 3: the per-account compat gate — ⚠️ HEADLINE RETRACTED, then RE-GRADED (same day)

> # 🛑 RETRACTED — DO NOT CITE THE VERDICTS BELOW
>
> **Everything below this box was measured correctly and interpreted wrongly.**
> The tool that produced it rescales the R sequence by the account's balance, so
> the survival / P(breach) arm of every standard verdict here is an artifact of
> account size rather than a property of the strategy or the account.
>
> `scripts/prop/account_compat_matrix.py` synthesises the ledger on a **$5,000**
> compounding walk (`--base-account-size 5000.0`) and then asks
> `ledger_to_r_sequence` to recover R with `initial_balance = the account's own
> size`. That round-trip is exact **only** when the two match. Measured on an
> 8-trade ledger of known `net_r`:
>
> | recovered at | first-trade R vs truth |
> |---|---|
> | **$5,000** (== base) | **1.000×** — exact |
> | $95,542 (`alpaca_portfolio`) | **0.052×** |
> | $200.10 (`alpaca_live`) | **24.99×**, diverging as the mis-scaled walk compounds |
>
> **That manufactures precisely the pattern I reported as the finding.** A small
> account receives ~25× its true R and breaches at P=1.0; a large one receives
> ~0.05× and reads survival 1.0 / P(breach) 0.0. "Every paper book ROUTEs, both
> real-money books skip, the gate split on account size" is what this bug
> produces on *any* ledger, including a perfectly good one.
>
> **What this retracts:** the headline ("the account size is the binding
> constraint"), every `survival` and `P(breach)` figure, the size-bound vs
> return-bound split, and the `tlt_pullback_1d` Tier-3 flag — that flag rests on
> an `alpaca_portfolio` verdict computed at 0.052× R.
>
> **What survives:** the Part-2 plumbing result is unaffected and still holds —
> 10 of 11 accounts graded with `ib_live` alone `UNGRADED` on a real
> `api_ok: false`, which tests the balance path, not the R math. The emitted
> ledger sizes (n=8 / 12 / 109 / 124) are also real.
>
> **What I got right by accident and should not take credit for:** the extension
> section already refuted the share-granularity story on the grounds that
> survival did not track share price. It does not track share price because it
> tracks *balance*, which I had not yet found.
>
> Filed as
>
> BL-20260829-COMPAT-MATRIX-RESCALES-R-BY-ACCOUNT-SIZE-SO-THE-VERDICT-TRACKS-BALANCE
>
> **The five legs HAVE been re-graded on the fixed tool — read
> § "CORRECTED VERDICTS" immediately below, not this box's successor text and
> not the retracted original.** The corrected result reverses the headline: no
> cell is size-bound, every refusal is a negative mean end-return, and the
> `tlt_pullback_1d` flag comes back on a sound basis.

## ✅ CORRECTED VERDICTS (re-graded on the fixed tool, same day)

Round-trip fixed in `92458d64`; all five legs re-emitted and re-scored on that
sha. Runs
[33252004498](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33252004498) (TQQQ) ·
[33252007816](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33252007816) (SLV) ·
[33252011005](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33252011005) (GLD) ·
[33252014088](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33252014088) (TLT) ·
[33252017891](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33252017891) (SCHA).
Fee 0 bps, 730 d, `--base-account-size 5000.0`, live balances (`size=measured`
on every cell — no default stood in).

**Population of every cell:** one Monte-Carlo over that leg's own emitted
ledger of `n` trades, scored against that account's ruleset at its live
balance. `ret` is the mean end-return over the simulated paths **as a fraction
of the starting balance**; `surv` is horizon survival; `pb` is breach
probability. All three are the same population and the gate reads all three
(ROUTE requires `ret > 0` **AND** `surv ≥ 0.9` **AND** `pb ≤ 0.1`).

⚠️ **THIS COVERS 2 OF THE 11 ACCOUNTS.** The one-line verdict prints
`alpaca_portfolio` and `alpaca_live` only — the two the routing question is
actually about. The other nine accounts are graded in each run's
`compat_*.json` artifact, which this session cannot download (no artifact-read
MCP), so **no post-fix claim is made about them here.** The retracted
eleven-account table below must not be read as standing in for them.

| leg | n | `alpaca_portfolio` (paper, $95.5k, dd 5%) | `alpaca_live` (real, $200.10, dd 10%) | binding term |
|---|---|---|---|---|
| `tqqq_trend_long_1d` TQQQ | **8** | **ROUTE** · ret 2.3418 · surv 1.0 · pb 0.0 | **ROUTE** · ret 2.3418 · surv 1.0 · pb 0.0 | — |
| `slv_trend_1h` SLV | **109** | **ROUTE** · ret 0.683 · surv 1.0 · pb 0.0 | **ROUTE** · ret 0.683 · surv 1.0 · pb 0.0 | — |
| `gld_pullback_1h` GLD | **124** | **ROUTE** · ret 0.706 · surv 1.0 · pb 0.0 | **ROUTE** · ret 0.706 · surv 1.0 · pb 0.0 | — |
| `tlt_pullback_1d` TLT | **13** | **skip** · ret **−0.3297** · surv 1.0 · pb 0.041 | **skip** · ret **−0.3333** · surv 1.0 · pb 0.0 | negative return |
| `scha_trend_long_1d` SCHA | **12** | **skip** · ret **−0.139** · surv 1.0 · pb 0.0 | **skip** · ret **−0.139** · surv 1.0 · pb 0.0 | negative return |

`dd=not_terminal` on all ten cells (the drawdown model is not the deciding term
anywhere here).

### The corrected finding: the verdict does NOT split on account size

**Every skip is now bound by the leg's own mean end-return, and nothing is
bound by survival or breach.** `surv = 1.0` and `pb ≈ 0` on all ten cells, and
the two refusals are refusals because the strategy loses money in simulation —
on a $95.5k book exactly as much as on a $200 one.

That is the whole retracted headline reversed. The claim was *"the account size
is the binding constraint"*; the measurement now says **account size binds
nothing in this set**, and the previous split was the R-rescaling artifact
wearing a plausible story.

**Arithmetic check that the fix is real, not just different.** `alpaca_live`'s
declared limits are strictly **looser** than `alpaca_portfolio`'s
(`max_dd_pct` 0.10 / `daily_loss_pct` 0.10 vs 0.05 / 0.05, read from
`config/accounts.yaml`). On one shared R sequence that *requires*
`pb_live ≤ pb_portfolio`. Observed: TLT **0.0 ≤ 0.041**, and equal at 0.0 on
the other four. **The pre-fix run inverted exactly this** — `alpaca_live` read
`pb = 1.0` while `alpaca_portfolio` read `0.0`, i.e. the looser-limit account
breaching more often, which is impossible on a shared sequence and is what
first exposed the bug.

### ➡️ SUPERSEDED IN SCOPE by the full-roster run

The operator's call on the `tlt_pullback_1d` flag was **widen the evidence
first** rather than decide one leg at a time. All 14 `alpaca_portfolio` roster
legs have since been graded on the same fixed sha —
**[`alpaca-portfolio-roster-compat-gate-2026-08-29.md`](./alpaca-portfolio-roster-compat-gate-2026-08-29.md)**.
Read that for the roster verdicts. Two things it adds: the flag is **three**
legs (`tlt_pullback_1d`, `ief_pullback_1d`, `spy_pullback_1h`), and the
size-story stays dead across 14 legs on books differing by 477x.

### What this changes

- **The Phase 2 shortlist is NOT overturned.** `tqqq_trend_long_1d` clears its
  own account's ruleset after all; so do `slv_trend_1h` and `gld_pullback_1h`.
  The "both candidates are refused on `alpaca_live`" conclusion is withdrawn in
  full.
- **The "size-bound vs return-bound" split collapses to one group.** There is no
  size-bound group. SCHA and TLT are return-bound, which is the one half of
  that framing that reproduces.
- **The `tlt_pullback_1d` Tier-3 flag is REINSTATED, on a sound basis.**
  `tlt_pullback_1d` is in `alpaca_portfolio`'s 14-leg roster (verified in
  `config/accounts.yaml`) and that account's own ruleset refuses it — now at
  `ret −0.3297` with `surv 1.0`, i.e. refused for *losing money*, not for the
  0.052×-R artifact the flag originally rested on. `scha_trend_long_1d` skips
  too and is still **not** a flag: it is not in that roster.
- **The TQQQ evidence caveat survives unchanged.** n=8 over 730 d is below the
  `MIN_OOS_TRADES = 25` floor the exit work uses, and it is now the *most
  favourable* cell in the table (ret 2.3418) — a thin ledger producing the
  biggest number is the cell to trust least, not most. TLT (n=13) and SCHA
  (n=12) are thin too. Only SLV (109) and GLD (124) are solidly evidenced.
- **The granularity hypothesis stays refuted, and now for a stated reason.** The
  extension section below refuted it on the grounds that survival did not track
  share price. Post-fix, survival does not track anything: it is 1.0
  everywhere. The refutation holds; its original reasoning was luck.

**Still not a routing proposal.** `alpaca_live` carries `strategies: []` and
nothing here changes it. Clearing the compat gate is one of the constraints a
leg must satisfy, not the decision.

---

## (original, retained unedited below as the record of what was claimed)

# Phase 3: the per-account compat gate — and it REFUSES both candidates

**2026-08-29 · Tier-1 research. Nothing wired; `alpaca_live` still carries
`strategies: []`.** This is the mandatory evidence step the `backtesting` and
`new-strategy` skills require before a strategy is called gate-cleared for an
account, and it **overturns the Phase 2 shortlist**.

## What it took to run it at all

The standard arm was **inert on a GitHub runner** — no journal DB, so
`load_balance_snapshots()` returned `None`, every standard account resolved
`unreadable`, and the whole arm graded `UNGRADED`. Part 2 (operator-approved)
gave the runner the balances the bot already publishes. See the commit for the
planted controls; the run itself carries its own proof:

> **10 of 11 accounts graded. `ib_live` alone stayed `UNGRADED`** — and that is
> *correct*, because the live payload carries `ib_live: balance=None,
> api_ok=false`. One table holds both the positive control (real sizes flowed)
> and the negative one (the api_ok gate still refuses). Before Part 2 all 11
> were `UNGRADED`.

## The verdicts

Gate for a standard account: positive mean end-return **AND** survival ≥ 0.9
**AND** P(breach) ≤ 0.1. Runs
[33249719919](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33249719919)
(TQQQ, 8 emitted trades) and
[33249723196](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33249723196)
(SLV), fee 0 bps, 730 d, ref `830154ba`.

**Population of every cell below:** one Monte-Carlo over the leg's own emitted
ledger, scored against that account's ruleset at its live balance.
**`tqqq_trend_long_1d` emitted n=8 trades; `slv_trend_1h` emitted n=109** — both
over the same 730 d, both scored against the same 11 accounts. `ret` is the mean
end-return over the simulated paths, `P(b)` the breach probability, `surv` the
horizon survival; **all three are the same population** and the gate reads all
three. The n is repeated on every row because a rate without its denominator is
what this repo's `stated-population-guard` exists to stop.

⚠️ **THE TWO LEGS ARE NOT EQUALLY EVIDENCED, AND THE WEAKER ONE IS TQQQ.** Eight
trades over two years is a thin ledger for a survival estimate — below the
`MIN_OOS_TRADES = 25` floor the exit work uses — so the TQQQ row is the *less*
trustworthy of the two refusals. SLV's 109 is a solid sample, and SLV is also
the harsher verdict (survival 0.0). Do not read the two skips as equally firm.

| account | class | `tqqq_trend_long_1d` | `slv_trend_1h` |
|---|---|---|---|
| **`alpaca_live`** | **real_money** | **skip** — ret 94.5%, P(breach) **1.0**, surv **0.4997** *(n=8)* | **skip** — ret 40.7%, P(breach) **1.0**, surv **0.0** *(n=109)* |
| `bybit_2` | real_money | **skip** — ret 48.2%, P(breach) 1.0, surv 0.4997 *(n=8)* | **skip** — ret 13.7%, P(breach) 1.0, surv 0.0 *(n=109)* |
| `ib_live` | real_money | UNGRADED (balance unreadable) *(n=8)* | UNGRADED *(n=109)* |
| `alpaca_portfolio` | paper | ROUTE — 6.9%, P(b) 0.0, surv 1.0 *(n=8)* | ROUTE — 3.0%, P(b) 0.0, surv 1.0 *(n=109)* |
| `alpaca_paper` | paper | ROUTE — ret 6.0%, P(b) 0.0, surv 1.0 *(n=8)* | ROUTE — ret 2.6%, P(b) 0.0, surv 1.0 *(n=109)* |
| `alpaca_options_paper` | paper | ROUTE — ret 4.9%, P(b) 0.0, surv 1.0 *(n=8)* | ROUTE — ret 2.1%, P(b) 0.0, surv 1.0 *(n=109)* |
| `bybit_1` | paper | ROUTE — ret 2.8%, P(b) 0.0, surv 1.0 *(n=8)* | ROUTE — ret 1.2%, P(b) 0.0, surv 1.0 *(n=109)* |
| `bybit_portfolio` | paper | ROUTE — ret 5.3%, P(b) 0.0, surv 1.0 *(n=8)* | ROUTE — ret 2.3%, P(b) 0.0, surv 1.0 *(n=109)* |
| `ib_paper` | paper | ROUTE — ret 0.4%, P(b) 0.0, surv 1.0 *(n=8)* | ROUTE — ret 0.2%, P(b) 0.0, surv 1.0 *(n=109)* |
| `oanda_practice` | paper | ROUTE — ret 4.9%, P(b) 0.0, surv 1.0 *(n=8)* | ROUTE — ret 2.1%, P(b) 0.0, surv 1.0 *(n=109)* |
| `breakout_1` | prop | skip — EV −$45, P(net>0) 0.0 *(n=8)* | ROUTE — EV $1,040, P(net>0) 0.8983 *(n=109)* |

## The finding: BOTH candidates are refused on `alpaca_live`, and the reason is size

**Every paper account ROUTEs. Both real-money accounts skip. The gate did not
split on strategy — it split on account size.** The paper books hold $82k–$1.34M;
`alpaca_live` holds **$200.10** and `bybit_2` **$305.04** (live balances, `source=db`,
`as_of 2026-08-29T11:01:03Z`).

⚠️ **Read the end-return beside the survival, never alone.** `alpaca_live` shows
the **highest** mean end-return of the 11 accounts scored (94.5% on TQQQ, over
that leg's 8-trade emitted ledger) *and* a survival of 0.4997 with
P(breach) 1.0 over the same paths. Those are not in tension — they are the
same fact: at one whole share of a $73.30 instrument on a $200 book, a single
position is ~37% of the account, so the paths that survive compound hard and the
rest breach. A mean end-return read on its own would have said this is the best
cell in the table. **That is exactly the reading the survival gate exists to stop.**

**So the Phase 2 shortlist does not survive Phase 3.** `tqqq_trend_long_1d` was
the one leg clearing every earlier filter — affordable, OOS 36, lever held
out-of-sample and cleared Path A. It does **not** clear its own account's
ruleset. `slv_trend_1h`, the most capital-efficient leg of the 19, is worse
here: survival **0.0**, i.e. no simulated path avoided a breach.

**Nothing here is a strategy verdict.** Both legs ROUTE on seven paper accounts.
The refusal is about routing *these* strategies to a *$200 whole-share cash
account*, and it composes with the three earlier constraints rather than
replacing them.

## What this does NOT say

- **It is not a reason to widen `risk_pct`.** The breach is driven by
  whole-share granularity against a small book, and `risk_pct` does not change
  the tradeable set (measured: identical at 0.02 and 0.05).
- **It is not a claim about `bybit_2`'s live routing.** `bybit_2` already trades
  and this run says its ruleset refuses these two ETF legs — which is expected,
  since neither is routed there. Nothing here proposes a change to it.
- **`breakout_1` ROUTEs `slv_trend_1h` at EV $1,040 / P(net>0) 0.8983.** That is
  a prop-ruleset verdict on a different account with a different economics
  block, and it is **not** transferable to `alpaca_live`. Recorded because the
  matrix computed it, not because it is being proposed.
- **`ib_live` is UNGRADED, which is neither pass nor fail.** Its balance reads
  `api_ok: false`, so nothing was measured against it.

## The decision this hands the operator

The `$200` account cannot currently carry either candidate under its own
ruleset. That is a real result, not a blocker to route around — and it makes
the go-live question *"what size does this account need, or what does a leg
need to look like to fit it?"* rather than *"which of these two do we wire?"*.


---

## Extension: three more legs, and the granularity hypothesis is REFUTED

I hypothesised the refusal was **share granularity** — one whole share of a
dear instrument being a huge slice of a $200 book — and tested it on two
cheaper symbols rather than asserting it.

| leg | emitted n | `alpaca_live` | `alpaca_portfolio` (paper) |
|---|---|---|---|
| `tqqq_trend_long_1d` (TQQQ $73.30) | 8 | skip · surv **0.4997** | ROUTE · ret 6.9% |
| `slv_trend_1h` (SLV $62.77) | 109 | skip · surv **0.0** | ROUTE · ret 3.0% |
| `gld_pullback_1h` (GLD $422.60) | 124 | skip · surv **0.001** · ret 26.8% | ROUTE · ret 3.2% |
| `scha_trend_long_1d` (SCHA $34.77) | 12 | skip · surv **0.172** · ret −14.8% | **skip** · ret −0.8% |
| `tlt_pullback_1d` (TLT $83.13) | not read | skip | **skip — FLAGGED** |

**The hypothesis does not survive its own test.** Survival against
`alpaca_live` runs 0.4997 (TQQQ, dearest but one, 1 share) · 0.172 (SCHA,
cheapest, 5 shares) · 0.001 (GLD, dearest) · 0.0 (SLV, 2 shares). There is **no
monotone relationship with share price or share count**, and the emitted-ledger
sizes differ by more than an order of magnitude (8 / 12 / 109 / 124), which
moves a Monte-Carlo survival on its own. So "buy cheaper shares and it will fit"
is **not** supported by this data and must not be carried forward as if it were.

## What IS supported: two DIFFERENT failure modes, and they need different answers

- **Size-bound (TQQQ · SLV · GLD).** Positive mean end-return on every account;
  every paper book ROUTEs; both real-money books breach with P=1.0. These legs
  work and the $200/$305 accounts cannot carry them.
- **Return-bound (SCHA · TLT).** Negative mean end-return on **every** account
  including the $82k–$1.34M paper books. Nothing about account size saves these
  — they simply do not clear a positive-return bar anywhere.

Conflating the two would be the expensive mistake: funding the account higher
fixes the first group and does nothing for the second.

## ⚠️ A genuine Tier-3 flag the run raised on its own

**`tlt_pullback_1d` IS in `alpaca_portfolio`'s roster and that account's own
ruleset REJECTS it** (measured against its real $95,542.76 balance, not a
default). The workflow's own words: *"a paper book trading a strategy its own
ruleset rejects — a genuine Tier-3 flag: consider whether to keep the routing."*

`scha_trend_long_1d` also skips there but is **not** in that roster, so it is
not a flag — it is in `alpaca_paper`'s 19-leg roster, which is the data-only
soak book that deliberately trades the full instrument set to accrue ML data.
Those two cases must not be reported the same way.

**No config edit is proposed here.** De-routing `tlt_pullback_1d` from
`alpaca_portfolio` is Tier-3 and the operator's call.
