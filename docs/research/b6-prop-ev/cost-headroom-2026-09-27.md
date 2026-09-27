# RQ-20260922-003 — round-trip cost headroom of the two `breakout_1` legs

> Lane W6-PROP-O1 (manager-dispatched, 2026-09-27). Tier-1 offline research on
> committed evidence. No config, roster, sizing or execution change. Where this
> document PROPOSES, it proposes; the change is Tier-3.

## 0. Answer in five lines

| leg | n (OOS) | break-even, fee arm (slip 3 + funding 1 bps/8h held) | break-even, flat all-in round trip | Breakout stack at the modelled 3 bps spread+slip | room left for spread + slippage | B6 V2, fresh $5k, room k=0.33 (5 seeds) |
|---|---|---|---|---|---|---|
| `trend_donchian_eth_prop` | 169 | **22.2 bps** (arm read: 22.5) | **26.6 bps** | 8 + 3 + swap 1.6 = **12.6 bps** | **≈17 bps** | __ETH_B6__ |
| `trend_donchian_sol_prop` | 65 | **24.9 bps** (arm read: none ≤ 22.5) | **30.7 bps** | 8 + 3 + swap 2.8 = **13.8 bps** | **≈20 bps** | __SOL_B6__ |

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

- **ETH**: 64% of its gross R over the year (15.87 of 24.60) is fold 2. Over the
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

__B6_TABLE__

**How to read it.**

- __B6_READ__

## 6. Proposal

__PROPOSAL__

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
