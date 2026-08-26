# Bracket-geometry decision packet — 14 `passed_unshipped` cells

**Status: a PROPOSAL. Nothing here is applied.** Operator-directed 2026-08-26:
prepare the packet, do not ship. Every cell below changes live exit geometry
and is **Tier-3**.

## Read this before the table

A cell reaching `passed_unshipped` means *it cleared the gate*, *not* *it is a
free win*. Reading the 14 as fourteen improvements waiting to be collected is
the error this section exists to prevent — the gate's own fields say otherwise.

**Population: the 12 cells measured in run `32975514836` (2026-08-26) that are
present in `docs/research/e35-bracket-corpus.jsonl`.** The 2 ETH cells come from
the 2026-08-20 run, are not in the corpus, and are **excluded from every count
in this section** — their own refs are the only evidence for them and are quoted
in the table.

| what the gate actually says | count | of 12 |
|---|---|---|
| passed via **path B** (capital efficiency) rather than path A | **10** | 83.3% |
| **`leverage_contingent: true`** — the gain needs more notional per unit of risk | **11** | 91.7% |
| **max drawdown WORSE** than base (`d_max_dd > 0`) | **8** | 66.7% |
| passed via path A **and** not leverage-contingent | **1** | 8.3% |

⚠️ **Path B is not path A.** A path-B pass means the cell did *not* beat the base
on the straight IS/OOS test; it qualified on capital efficiency. Both are
legitimate gate outcomes and they answer different questions, so a decision to
ship a path-B cell is a decision about capital deployment, not about a better exit.

⚠️ **`leverage_contingent: true` means the measured `d_net_r` is not attributable
to geometry alone.** `leverage_multiple` reaches **1.91×** (`spy_trend_long_1d`).
A wider stop admits a larger position for the same risk budget, so part of the
gain is size. Shipping the geometry without the sizing does not reproduce the
measurement, and shipping the sizing is a separate Tier-3 change against
`config/risk_caps.yaml` — **which this packet does not propose**.

⚠️ **`d_max_dd` is signed: POSITIVE is worse.** Two thirds of the set deepens
drawdown, up to **+6.478 R** (`tlt_pullback_1h`).

## The 14 cells

`wf` is `wins_effective/usable` — inert folds excluded
(`BL-20260826-E35-GATE-COUNTS-INERT-FOLDS-AS-WALKFORWARD-WINS`, fixed the same
day). Every cell here carries **inert 0**, verified per cell rather than per leg.

| leg | cell | path | wf | d_net_r | d_maxDD | leverage | lev-contingent | execution |
|---|---|:--:|:--:|--:|--:|--:|:--:|---|
| `eth_pullback_2h` | `tp2_sm3.5_to48` | A | 6/6 | — | — | — | — | live |
| `eth_pullback_prop_2h` | `tp4_sm3_to48` | A | 6/6 | — | — | — | — | live |
| `mes_trend_long_1d` | `tp1_sm2_to24` | B | 4/6 | +1.039 | +0.239 | 1.4236× | **yes** | live |
| `mgc_pullback_1d` | `tp6_sm1.5` | B | 4/6 | +3.825 | +4.002 | 1.3331× | **yes** | live |
| `spy_trend_long_1d` | `tp2_sm1.5` | B | 5/6 | +8.156 | +0.827 | 1.9129× | **yes** | live |
| `qqq_trend_long_1d` | `tp3_sm2` | B | 5/6 | +6.195 | +0.291 | 1.247× | **yes** | live |
| `iwm_trend_long_1d` | `tp3_sm2` | B | 5/6 | +4.039 | -1.359 | 1.25× | **yes** | live |
| `scha_trend_long_1d` | `tp1.5_sm3` | A | 4/6 | +3.999 | -1.739 | 0.85× | no | live |
| `tlt_pullback_1d` | `tp2_sm1.5_to24` | B | 5/6 | +8.223 | +3.747 | 1.7584× | **yes** | live |
| `gld_pullback_1h` | `tp6_sm1.5_to24` | B | 6/6 | +65.351 | +2.272 | 1.7678× | **yes** | live |
| `spy_pullback_1h` | `sm1.5_to400` | B | 5/6 | +29.965 | -0.946 | 1.5988× | **yes** | live |
| `tlt_pullback_1h` | `sm2` | B | 5/6 | +11.513 | +6.478 | 1.252× | **yes** | live |
| `slv_trend_1h` | `sm1.5` | B | 5/6 | +46.196 | +5.861 | 1.68× | **yes** | shadow |
| `uso_trend_1h` | `tp4_sm2` | A | 5/6 | +23.608 | -3.158 | 1.2392× | **yes** | live |

⚠️ The two ETH rows show `—` because those columns **were not measured into the
corpus**, not because they are zero. Their refs state `wf 6/6 effective (inert 0)`,
`dOOS +3.6208` and `+1.6318`, `split_sensitive=false`, `pass_fraction=1.0`. The
2026-08-24 re-measurement of those two legs carries `gate_verdict: null` for these
exact cells — **not gated**, which is not *gated and failed* — so it neither
confirms nor contradicts them.

## What I would ship first, if anything

**`scha_trend_long_1d` `tp1.5_sm3`** is the only cell that is path **A**, **not**
leverage-contingent (0.85×, i.e. it uses *less* notional), and **improves**
drawdown (−1.739 R). It is the one row whose gain is attributable to geometry.
`wf 4/6` is the weakest fold record in the set, which is the argument against.

**`slv_trend_1h` `sm1.5`** is `execution: shadow` — shipping it changes no live
order. It is the free place to exercise the change end-to-end.

**Not recommended without a separate sizing decision:** the 11 leverage-contingent
cells. Their measured gain assumes a position size the current risk config does
not grant.

## What is NOT in this packet

- Any config diff. No `config/strategies.yaml` change is drafted.
- Any sizing/`risk_caps` proposal (see the leverage caveat above).
- A recommendation to ship the set. The set is not homogeneous and should not
  be approved as one.

---
_Generated by [Claude Code](https://claude.ai/code)_
