# RQ-20260922-003 — round-trip cost headroom of the two `breakout_1` legs

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Lane W6-PROP-O1 (manager-dispatched, 2026-09-27). Tier-1 offline research on
> committed evidence. No config, roster, sizing or execution change. Where this
> document PROPOSES, it proposes; the change is Tier-3.

## 0. Answer in five lines

| leg | n (OOS) | break-even, fee arm (slip 3 + funding 1 bps/8h held) | break-even, flat all-in round trip | Breakout stack at the modelled 3 bps spread+slip | room left for spread + slippage | B6 V2, fresh $5k, room k=0.33 (5 seeds) |
|---|---|---|---|---|---|---|
| `trend_donchian_eth_prop` | 169 | **22.2 bps** (arm read: 22.5) | **26.6 bps** | 8 + 3 + swap 1.6 = **12.6 bps** | **≈17 bps** | **indeterminate** 5/5; EV +$150…+$162, evidence p5 −$33…−$20 |
| `trend_donchian_sol_prop` | 65 | **24.9 bps** (arm read: none ≤ 22.5) | **30.7 bps** | 8 + 3 + swap 2.8 = **13.8 bps** | **≈20 bps** | **indeterminate** 5/5; EV +$122…+$136, evidence p5 −$45…−$40 |

- **Unit verdict (RULE-RQ0922-003-COST-BREAKEVEN, applied as registered):** ETH
  `breakeven_bps` = 22.5 → the "between 15 and 22.5" branch → report and close
  `no_action_warranted`. SOL never crosses at 22.5 → `cost_margin: robust`,
  `no_action_warranted`.
- **But the pooled number hides the finding.** ETH's headroom is carried by
  folds 1–2 (Sep 2025 – Mar 2026). Over its **last two folds (85 trades,
  2026-03-27 → 2026-09-23) the flat break-even is −0.9 and +7.1 bps** — below
  Breakout's 8 bps commission alone. See § 3.

## 1. What was run, over what

**Population.** The per-trade OOS rows committed beside each leg's current
evidence record — the exact rows the Stage-0 verdict was computed from:

| leg | evidence record (generated) | trade rows | config fingerprint |
|---|---|---|---|
| ETH | `comms/strategy_evidence/trend_donchian_eth_prop.json` (2026-09-26T17:56Z) | `comms/strategy_evidence/runs/2026-09-26/trend_donchian_eth_prop__trades.jsonl`, n = 169, 2025-09-28 → 2026-09-23 | `sha256:776a9ea6…` = current config |
| SOL | `comms/strategy_evidence/trend_donchian_sol_prop.json` (2026-09-25T10:31Z) | `comms/strategy_evidence/runs/2026-09-25/trend_donchian_sol_prop__trades.jsonl`, n = 65 | `sha256:788c4e29…` = current config |

Fingerprints were checked against `config/strategies.yaml` by
`prop_ev_sim.py --book breakout_1` (`fingerprint_matches: true` for both).

⚠️ **The unit's quoted numbers are stale.** It cites (on `f3746ab`)
`net_r_oos +7.13`, fee-only `+13.05`, slippage **5.0** bps. The record was
regenerated 2026-09-26 at slippage **3.0** bps: `net_r_oos +13.61`, fee-only
`+17.66`, folds positive **2 of 4** (not 3 of 4). Everything below uses the
current record. n is unchanged at 169.

**Method (paired re-pricing).** Tool: `scripts/research/prop_cost_headroom.py`
(new, `--self-test` passes). One bps of notional costs `1e-4 × entry / |entry − sl|`
R on a trade — the harness's own conversion; the tool reproduces every row's
recorded `net_r` from `gross_r` and its three cost terms (total absolute error
0.094 R over 169 ETH rows, 0.045 R over 65 SOL rows — 4-decimal rounding), and
the 7.5 bps arm reproduces the record (`+13.6094` vs `+13.6082`; `+5.1975` vs
`+5.1885`). Re-pricing cannot change which trades are taken: the trend harness
applies costs after the fact, so n is identical in every arm.

The unit's `run:` block also names an `m20-exit-lever-sweep.yml` dispatch; one
landed (`research/results/RQ-20260922-003/36254494896.jsonl`) but it is a
**different population** — the m20 IS/OOS split's 49 OOS trades, graded against
`RULE-M20-EXIT-LEVER-IS-OOS-GATE`, not this unit's rule. Its base
`net_r_OOS −5.41` at 15 bps is consistent with § 3 (it is the recent-window
slice), but it is not the statistic registered here and is not used for the verdict.

Full numbers: [`cost-headroom-2026-09-27.json`](cost-headroom-2026-09-27.json).

## 2. The registered arms

| fee bps RT | ETH net_r_oos | SOL net_r_oos |
|---|---|---|
| 7.5 (record) | +13.61 | +5.20 |
| 15 | +6.67 | +2.96 |
| 22.5 | **−0.26** | +0.72 |
| exact fee break-even | 22.2 | 24.9 |
| exact flat all-in break-even (fee+slip+funding → one number) | 26.6 | 30.7 |

R per bps: ETH 0.00547 R, SOL 0.00460 R per trade — i.e. every extra bp of
round-trip cost removes 0.92 R from ETH's 169-trade total and 0.30 R from SOL's 65.

## 3. The split the pooled number hides

Flat all-in break-even per harness fold (fold boundaries from the record's
`fold_detail`):

| fold | window | ETH n / gross R / break-even bps | SOL n / gross R / break-even bps |
|---|---|---|---|
| 1 | 2025-09-28 → 12-18 | 42 / +6.92 / 39.6 | 16 / +2.24 / 36.4 |
| 2 | 2025-12-21 → 2026-03-26 | 42 / +15.87 / **72.6** | 16 / +0.35 / 4.8 |
| 3 | 2026-03-27 → 06-20 | 42 / −0.21 / **−0.9** | 16 / −0.82 / −10.9 |
| 4 | 2026-06-23 → 09-23 | 43 / +2.01 / **7.1** | 17 / +7.41 / 84.6 |

(SOL fold windows are SOL's own record's, within the same year.)

- **ETH**: 64% of its gross R over the year (15.87 of 24.60 R, n = 169 OOS trades) is fold 2. Over the
  most recent half (folds 3–4, 85 trades) the gross edge is +1.80 R and the
  flat break-even is **3.4 bps** — below Breakout's 8 bps commission before any
  spread or swap. On the recent half, at Breakout's own cost stack, the leg is
  **negative**. This is n = 85 and one regime; it is not a powered estimate
  (floor for d = 0.25 is 126), and it is a split chosen after the pooled
  result was read, so it is **descriptive, not a verdict**.
- **SOL**: lumpy at n = 16 per fold (fold 4 carries it). Too thin to say more
  than that its pooled headroom is not evenly earned either.

## 4. Break-even in Breakout terms

**Cost stack for `breakout_1` (Breakout 1-Step Classic $5k, DXtrade), every number sourced:**

| component | value | source / status |
|---|---|---|
| commission | 0.04% per side = **8 bps round trip** | Breakout FAQ "What are the trading fees and commissions" (intercom.help/breakoutprop/en/articles/11647195), as cited in `docs/research/eth-pullback-prop-swap-aware-2026-06-25.md`; operator screenshot + web corroboration in `docs/integrations/breakout-instruments-2026-09-27.md`. `prop_ev_sim.py` default `--commission-bps-rt 8.0`. |
| swap | **0.033%/day of notional** (3.3 bps/day); DXtrade books at 00:00 UTC, one debit per midnight crossed | Same FAQ article; `config/strategies.yaml` comment at the eth_pullback_prop block (~line 2648); `docs/integrations/breakout-instruments-2026-09-27.md` § account-level economics. |
| spread + slippage | **3 bps round trip** modelled | **ASSUMPTION.** It is the harness's slippage default and `prop_ev_sim.py`'s `--slippage-bps-rt 3.0`. No Breakout DXtrade spread for ETH/SOL is recorded anywhere in this repo (searched `docs/integrations/breakout*`, `config/prop_rulesets/`, the prop-state and automation docs), and no realized-fill measurement for `breakout_1` exists (checklist R3 is `landed_unproven`). Tickets are placed **manually** via the bridge (`prop_ev_sim.py` A1), so real slippage may exceed a bot's. 5 and 10 bps are bracketed below. |

**Swap from each leg's own holding time** (entry → exit of every OOS trade):

| | ETH | SOL |
|---|---|---|
| hold, median / mean / p90 / max (h) | 12 / 12.0 / 22 / 54 | 18 / 25.0 / 54 / 96 |
| share of trades crossing no 00:00 UTC | 46.8% | 35.4% |
| swap per trade, DXtrade model: mean / median / p90 (bps) | 1.86 / 3.3 / 3.3 | 3.25 / 3.3 / 6.6 |
| swap per trade, prorated model: mean (bps) | 1.65 | 3.44 |
| swap as an R-weighted flat equivalent (DXtrade) | **1.59 bps** | **2.81 bps** |

**Where the break-even sits:**

| | ETH | SOL |
|---|---|---|
| flat all-in break-even | 26.6 bps | 30.7 bps |
| − commission 8 − swap (DXtrade, R-weighted) | 17.0 bps left | 19.9 bps left |
| net_r_oos at spread+slip 3 bps | +12.96 R (+0.077 R/trade) | +5.05 R (+0.078 R/trade) |
| net_r_oos at spread+slip 5 bps | +11.11 R | +4.45 R |
| net_r_oos at spread+slip 10 bps | +6.48 R (+0.038 R/trade) | +2.96 R |

So over the full year the realistic Breakout stack (12.6 bps ETH / 13.8 bps
SOL at the modelled 3 bps spread+slip) sits **14 bps (ETH) and 17 bps (SOL)
below** the pooled break-even — about 2× headroom. At a pessimistic 10 bps
spread+slip (stack 19.6 / 20.8 bps) the margin shrinks to 7 and 10 bps. **Over ETH's most
recent six months the break-even (3.4 bps) sits ~9 bps below the stack**, i.e.
there is no headroom there at all.

## 5. B6 — `RULE-B6-PROP-EV-PER-ACCOUNT-LIFE-V2`

Tool: `scripts/research/prop_ev_sim.py` (#13084, sizing modes from #13154) on
`main` at the commit recorded in each output, `--costs breakout` (8 bps
commission + 3 bps slippage + DXtrade swap), `--modes realized,path,stop`,
defaults 4,000 lives, 100 outer histories × 200 lives, 730-day horizon, 30-day
blocks. Each leg alone (`--trades LEG=PATH`) and the two-leg book
(`--book breakout_1`). Seeds 1–5; the stress arm (spread+slip 10 bps) seeds 1–3.

Tool commit for every run: `cf51e0b7`. Per-seed numbers:
[`cost-headroom-b6-seeds-2026-09-27.json`](cost-headroom-b6-seeds-2026-09-27.json).
EV = mean net-$ per account life, `path` model; p5/p95 = the outer-bootstrap
(evidence) percentiles the V2 predicate reads. Ranges are min…max across seeds.

**Fresh $5,000 account, $45 fee, room sizing k = 0.33, skip below $10** (the
sizing #13154 sets for the next instance). This is the arm where the V2 rule
can come out either way.

| portfolio | seeds | V2 verdict | EV per life | evidence p5 | evidence p95 | P(pass eval) | `stop` bound EV | `realized` bound EV |
|---|---|---|---|---|---|---|---|---|
| ETH alone | 5 | indeterminate ×5 | +$150 … +$162 | −$33 … −$20 | +$597 … +$959 | 0.52 … 0.55 | +$159 (mean) | +$157 (mean) |
| SOL alone | 5 | indeterminate ×5 | +$122 … +$136 | −$45 … −$40 | +$887 … +$1,380 | 0.54 … 0.56 | +$133 | +$126 |
| book (both) | 5 | indeterminate ×5 | +$110 … +$114 | −$34 … −$20 | +$375 … +$781 | 0.48 … 0.50 | +$29 | +$125 |
| ETH, spread+slip 10 bps | 3 | indeterminate ×3 | +$79 … +$82 | −$40 … −$38 | +$643 … +$718 | 0.42 … 0.44 | +$76 | +$76 |
| SOL, spread+slip 10 bps | 3 | indeterminate ×3 | +$78 … +$82 | −$45 … −$44 | +$800 … +$1,096 | 0.47 | +$80 | +$76 |
| book, spread+slip 10 bps | 3 | indeterminate ×3 | +$55 … +$57 | −$39 … −$32 | +$236 … +$340 | 0.40 … 0.42 | +$4 | +$64 |

**Current account, flat 1.5% = $75/ticket, started at $4,724, fee 0 (sunk).**
The $4,724 balance is the last measured value (manager, 2026-09-27T07:23Z,
`live-state-2026-09-27T0723Z.json`). This lane did not re-read it.

| portfolio | seeds | V2 verdict | EV per life | evidence p5 / p95 | P(pass eval) |
|---|---|---|---|---|---|
| ETH alone | 5 | pass ×4, indeterminate ×1 | +$16 … +$21 | +$0.0 … +$1.8 / +$67 … +$104 | 0.05 … 0.06 |
| SOL alone | 5 | indeterminate ×5 | +$17 … +$22 | $0.0 / +$106 … +$202 | 0.06 … 0.07 |
| book (both) | 5 | indeterminate ×5 | +$6 … +$8 | $0.0 / +$22 … +$33 | 0.04 |

The book result reproduces the precedent: #13154's fresh-account figure was
+$111.96 at k = 0.33, and these seeds give +$110 … +$114.

**How to read it.**

- **Both legs: INDETERMINATE on every seed, on the arm that can decide.** The
    point EV is positive and stable across seeds (EV varies by $12 for ETH and
    $14 for SOL across seeds). The evidence CI straddles zero on every seed (p5 −$45 …
    −$20), so the V2 predicate (p5 > 0) is not met. The seed spread is small
    next to the evidence spread. What leaves the verdict undecided is the
    one-year, n = 234 history, not simulation noise.
- **The ETH "pass" on the current account does not count as a pass.** With the
    fee at 0 (sunk), net-$ per life cannot go below zero, so its p5 is ≥ 0 by
    construction. The V2 predicate can never FAIL on that arm, and a 4-of-5
    "pass" flips on whether p5 is +$0.2 or $0.0. The only thing it shows is
    that continuing the current account costs nothing new, which was already
    known. P(pass eval) is 5–7% either way.
- **Each leg alone scores higher than the book** (+$157 / +$129 vs +$112),
    and the book's pessimistic `stop` bound is far lower (+$29 vs +$159 /
    +$133). That fits the simulator's stated mechanism: two legs open together
    share one daily-loss cushion. It is evidence that overlap costs the book
    EV. It is not evidence that dropping a leg would pass B6, because every
    single-leg arm is also indeterminate.
- **Spread sensitivity:** 10 bps spread+slip instead of 3 roughly halves
    fresh-account EV (ETH −49%, SOL −37%, book −50%). The book's `stop` bound
    falls to +$4. The spread assumption is the cost input the EV is most
    sensitive to, and it is the one that has not been measured.

## 6. Proposal

**No parameter or roster change is proposed.** The numbers do not support one:

- The cost rule closes `no_action_warranted` for both legs.
- B6 V2 is indeterminate for both legs on every seed, with positive point EV.
  Neither a cut (nothing FAILS) nor a promotion (nothing PASSES) is backed.
- The ETH recent-window weakness in § 3 was found after the pooled result was
  read. On n = 85 it can motivate a pre-registered test, not a change.

What the numbers do support is two measurements, filed in
`docs/claude/work/PIPELINE.jsonl` as `PI-20260927-W6PROPO1-0001`:

1. A pre-registered test of whether `trend_donchian_eth_prop`'s edge net of
   Breakout's cost stack holds after 2026-03-27. The rule has to be registered
   before the next evidence regeneration is read.
2. A measured spread+slippage figure for ETH/SOL on `breakout_1` (fill price
   vs the signal/ticket price). This belongs to R3/D3 cost fidelity. The spread
   is the input B6 EV is most sensitive to, and it is currently an assumption.

## 7. What this does not establish

- It does not measure Breakout's real spread or slippage. It shows how much
  there is room for. The measurement is R3/D3's job for `breakout_1`.
- The per-fold split in § 3 was chosen after the pooled result was read. It is
  descriptive, n = 85 for ETH's recent half, and below the unit's own power
  floor.
- The B6 simulator's intra-trade path (`path` model) is a model, not a bound.
  Its assumptions A1–A10 are in the tool's docstring. The room-sized lives
  mostly survive to the horizon and are scored at what they have banked, so
  their EV is understated. DXtrade's minimum order size is not modelled.
