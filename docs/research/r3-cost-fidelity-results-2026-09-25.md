# R3: per-leg cost fidelity, first measured run (2026-09-25)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Rule:** `RULE-R3-GATE1-COST-FIDELITY-v1`, in
[`r3-gate1-cost-fidelity-rule-2026-09-25.md`](r3-gate1-cost-fidelity-rule-2026-09-25.md).
It was registered in commit `91a904e` and pushed before any data was pulled.
This document and the record come in a later commit.
**Record:** [`comms/research/r3_cost_fidelity/2026-09-25.json`](../../comms/research/r3_cost_fidelity/2026-09-25.json)
plus `…__rows.jsonl` (1,585 measured entry/exit rows) and `…__fills_pull_log.json`.
**Tier:** 1. This run changed no config, no roster, no default and no evidence record.

**Pull:** 2026-09-25T13:23:21Z, read directly from the live API. It covers
trades ids ≤ 6168 and 90 days of fills. The fills page cap truncated two
accounts (`bybit_1`, and `ib_paper` MGC). `bybit_1` was re-pulled symbol by
symbol. `ib_paper` MGC stayed truncated at 1,000, the same limit D3 hit. The
real-money accounts were not truncated: `bybit_2` 331 fills, `alpaca_live` 4.

**Re-run:**
```
python3 scripts/research/realized_slippage.py pull --out-dir D      # needs DIAG_READ_TOKEN
python3 scripts/research/r3_cost_fidelity.py --in-dir D --as-of <pull time> --out <json>
```

## Rule parameters as read at run time

`N_FLOOR` = 10 per side per cell. `T` = 0.0 bps, read from
`MD-PROMOTE-S1-S2.bar.cost_tolerance_bps`. Venue-level pooled floor = 20.

## Per venue: which venues have enough n

<!-- population-ok: every row names its accounts and n; 95% is the CI level -->
| venue · basis | accounts | n entry / n exit | round trip (bps) | 95% CI | modelled | verdict | enough n (≥ 20 each side)? |
|---|---|---|---|---|---|---|---|
| **Bybit perps · market** | `bybit_2` | 89 / 42 | **+1.14** | **[−0.75, +3.11]** | 3.0 | `inconclusive` | **yes** |
| Bybit perps · simulator | `bybit_1`, `bybit_portfolio` | 1,032 / 113 | +0.90 | [−1.21, +3.29] | 3.0 | `inconclusive` | yes |
| Alpaca equities · market | `alpaca_live` | 3 / **0** | none | none | 5.0 | `insufficient_n` | **no** |
| Alpaca equities · simulator | `alpaca_paper`, `alpaca_portfolio` | 111 / **0** | none | none | 5.0 | `insufficient_n` | **no**: 0 measured exits |
| IBKR futures · simulator | `ib_paper` | 22 / 7 | none | none | 5.0 | `insufficient_n` | no |
| IBKR futures · market | `ib_live` | 0 / 0 | none | none | 5.0 | none | **no**: `dry_run`, empty roster |

**Only Bybit perps have enough n, on both bases.** Equities have no measured
exit on any account. Futures have 7 simulator exits and no market fills.

⚠️ **The perp default of 3.0 bps is now inside the market CI, not above it.**
E60 set 3.0 as D3's CI upper (+2.95, n=87/42), rounded up. One day later the
CI upper is +3.11. The point estimate moved from +0.87 to +1.14. Two new
`bybit_2` entries explain the whole move: trade 6143 (`ada_pullback_2h`,
+12.2 bps) and trade 6136 (`xrp_pullback_2h`, +11.3 bps); the rows file has
both. Under this rule the venue reads `inconclusive`, not `consistent`. That
is not a demotion. It means the 3.0 figure is not established at 95%
confidence either way. `mandate_resolver._cost_fidelity` read D3's venue
POINT estimate (0.868 in the committed D3 record), so it would have passed the
venue. That gap is closed by the change described below, which SHIPPED on
2026-09-25 (branch `lane/r3-resolver-cost-fidelity`).

## Per leg: the verdicts

Population: 55 legs, 83 (leg, account) cells. A leg is counted if it has an
evidence record or measured fills AND is on a roster or has filled.

<!-- population-ok: counts are over the 55 legs / 83 cells named above -->
| leg verdict | legs |
|---|---|
| `divergent` (demote + invalidate) | **0** |
| `consistent` (Gate-1 cost clause passes) | **4** |
| `inconclusive` | 0 |
| `insufficient_n` | 51 |

<!-- population-ok: every row names its account, basis and n -->
| leg | decided on | basis | n entry / n exit | round trip | 95% CI | modelled | overcharges? | Stage-0 verdict |
|---|---|---|---|---|---|---|---|---|
| `ict_scalp_5m` | `bybit_2` | market | 24 / 22 | −0.22 | [−2.53, +2.13] | 3.0 | yes | **fail** |
| `ict_scalp_avax_5m` | `bybit_1` | simulator | 73 / 28 | −1.13 | [−4.13, +2.13] | 3.0 | yes | **fail** |
| `ict_scalp_sol_5m` | `bybit_1` | simulator | 53 / 17 | −1.76 | [−3.70, +0.51] | 3.0 | yes | **fail** |
| `ict_scalp_xrp_5m` | `bybit_1` | simulator | 32 / 10 | −0.80 | [−3.59, +2.12] | 3.0 | yes | **fail** |

**All four `consistent` legs FAIL Stage 0.** So Gate 1's cost clause passes
for them, but none of them is promotable. Each one's record also over-charges
slippage (CI upper < 3.0). These are the same three 5m scalps D3 found
flipping fail→pass at the measured point estimate, plus `ict_scalp_5m`.
Re-pricing them is R1/D3's job, not this rule's. `ict_scalp_5m` is on no
real-money roster today, so its `bybit_2` cell comes from historical fills.

**The exit side is what keeps the rest below the floor.** Across all cells
there are 162 package-referenced exits and 1,257 entries. Closest to the
floor: `ict_scalp_eth_15m`/`bybit_1` 29/7, `ict_scalp_sol_15m`/`bybit_1`
33/7, `ict_scalp_5m`/`bybit_portfolio` 27/8, `eth_pullback_2h`/`bybit_2` 13/5.
No Alpaca or IB leg has a single measured exit except `ib_paper` (7 in total,
spread over legs).

`ict_scalp_mgc_15m` has no usable model. Its record is `harness_failed`
(yfinance window), so `cost_stack.slippage` is null. The four `pairs_*` legs
have no evidence record, and their exits never carry a package reference
(isolated M22 path).

## E62: is the done-when observable now?

**No.** No Alpaca sl-family close (`sl`, `sl_cross`, `giveback_stop`) with
`exit_price_source=exchange_fill` has occurred since the #12878 deploy
(2026-09-24T18:18:38Z). **No Alpaca close of any reason has occurred since
then.** Positive control: the same pull holds 21 post-deploy closes on the
Bybit accounts, so the probe can see post-deploy closes. 15 Alpaca positions
are open (13 paper, 2 live). Only about 1.7 US cash-session hours had elapsed
after the deploy when this pull was read (13:23Z, before the 2026-09-25 open).

One `exchange_fill` sl-family close exists, and it does **not** count: trade
6134 (`alpaca_paper`, `uso_trend_1h`, `giveback_stop`) closed
2026-09-24T16:22Z, **two hours before the deploy**. Some other path wrote it,
so it cannot prove the fix. `realized_slippage.measure` also cannot use it,
because `giveback_stop` has no package reference level.

**Accrual forecast**, from Alpaca sl-family closes over the last 60 days with
an exact Poisson 95% CI:
- **Current rosters (headline):** 16 closes, 0.27/day [0.15, 0.43]. Expected
  first close ≈ **3.8 days** from the pull. P(≥ 1 within 7 days) = 0.85,
  or 0.66 at the CI low.
- All legs, an upper bound (alpaca_portfolio was cut 14 → 2 on 2026-09-21):
  35 closes, 0.58/day. P(≥ 1 in 7 days) = 0.98.
- These assume every post-deploy sl-family close resolves to `exchange_fill`.
  That is exactly the claim E62 has not yet observed.

⚠️ **The first such close satisfies `MD-PROMOTE-S1-S2` condition (b)'s
parenthetical. It does not satisfy E62's own done-when.** That done-when asks
for a committed per-venue record at a stated n and CI that
`slippage_bps_roundtrip_for` uses. Under this rule a single Alpaca leg needs
≥ 10 measured exits. E62's own forecast puts `alpaca_paper` at 20 exits
around 2026-12-13 and `alpaca_live` no earlier than about 2027-04. `ib_live`
accrues nothing until a Tier-3 roster decision.

## MD-PROMOTE-S1-S2 `blocked_until`

- **(a):** not re-measured here. It is closed by #12937 per row B5 and is
  outside this lane's question.
- **(b): NOT met.** No post-deploy Alpaca `exchange_fill` sl-family close
  exists, and no committed realized-slippage record exists for Alpaca
  equities or IBKR futures at any n. This record reports them as
  `insufficient_n` with 0 exits.

## SHIPPED 2026-09-25 (was "Proposed, NOT shipped"): the resolver reads this rule

> ⚠️ **This section described a proposal until 2026-09-25.** It is kept in place,
> rewritten, rather than deleted, because the paragraph above points at it. The
> change was written on branch `lane/r3-resolver-cost-fidelity` and HELD for the
> operator (Tier-3: it edits `config/mandates.yaml`). **Merged is not deployed is
> not observed** — read this as "the diff exists and CI graded it", not as "the
> resolver is doing this in production"; nothing calls the resolver on a schedule
> in any case.

`scripts/ops/mandate_resolver.py` evaluated the cost clause off D3's venue
**point estimate**, both for `MD-PROMOTE-S1-S2` (`_cost_fidelity`) and for
`MD-DEMOTE-S1-OFF` (`_demote_s1_off`). With `T = 0.0` that had two problems.
It could promote on a venue whose CI straddles the model (today's perps). It
could also demote every leg on a venue at once on noise, as soon as a new D3
point estimate landed above modelled. Both now read the newest committed dated
R3 record's per-leg verdict instead:
- promote only on `consistent`;
- demote only on `divergent`;
- refuse on `insufficient_n`, `inconclusive` or `no_record`.

The equities/futures PROVISIONAL branch keeps the operator's "arm all venues"
decision, with one tightening: the 5.0 bps placeholder floor is checked FIRST
and is never weakened by a measurement, but a `divergent` verdict now REFUSES
on those venues too, where the old branch returned early and never looked. The
matching `fires_when` wording landed in `config/mandates.yaml` in the same PR;
no bar value, no cap and no `blocked_until` changed.

⚠️ **AND IT FIXED A DEFECT THIS DOC DID NOT KNOW ABOUT.** `latest_d3` globbed
`*.json` over a directory into which the producer writes THREE artifacts per
run — `<date>.json`, `<date>__fills_pull_log.json` and `<date>__rows.jsonl`.
The pull log sorts last, so `latest_d3` returned it; it is a JSON *list*, so the
resolver's `_json` returned `None`, so `realized` was always `None`. MEASURED
2026-09-25 by running the merged `latest_d3` against the committed tree.
**Both cost clauses were therefore inert** — refusing with "cannot compare" /
"could not measure" whatever the measurement said. It failed CLOSED, so no
promotion was ever wrongly fired on it, but the 0.868 figure quoted earlier in
this doc as what the resolver "reads" was never actually reached. The R3 reader
matches a strict `YYYY-MM-DD.json` and carries a regression test.
