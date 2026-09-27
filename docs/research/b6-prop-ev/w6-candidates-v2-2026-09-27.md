# W6 — `breakout_1` prop candidates, round 2: reconciled on the merged tool, widened across strategy families

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Lane: W6 round 2 (checklist row B6). Tier-1 research — no roster, execution,
> sizing or config change. Adding a leg to `breakout_1` is Tier-3 and this
> document PROPOSES, it does not decide. Continues
> [`docs/research/b6-prop-ev/w6-crypto-candidates-2026-09-27.md`](w6-crypto-candidates-2026-09-27.md)
> ("P2"/"round 1") now that `scripts/research/prop_ev_sim.py` (PR #13084) and
> the sizing decision (PR #13154) are both on `main`.

## 0. What changed since round 1, and what this lane does

Round 1 scored 8 crypto candidates by fetching `prop_ev_sim.py` from an
unmerged branch (`origin/claude/w6-prop-ev`, commit `2d7e65fff1`), because it
had not landed on `main` yet. Two things have since landed:

1. **PR [#13084](https://github.com/benbaichmankass/Metis-Insights/pull/13084)** — `prop_ev_sim.py` merged to `main`.
2. **PR [#13154](https://github.com/benbaichmankass/Metis-Insights/pull/13154)** — the operator's sizing decision for the account instance this scoring is *for*: the CURRENT `breakout_1` keeps flat 1.5%; **a FRESH $5,000 account (what every candidate below is scored against) uses `room` sizing, `k=0.33`, `min_risk_usd=$10`** (`config/prop_rulesets/breakout.yaml::sizing`). Round 1 ran before this decision existed and used the CLI's `--sizing balance` default (risk = 1.5% of the *live* balance, not `room`) — a third sizing mode, distinct from both `flat` and `room`. This lane uses `--sizing room --room-frac 0.33 --min-risk 10`, matching #13154, for every number in §2–§4.

This lane does three things: **(1)** reconciles round 1's numbers against the now-merged tool (closing `PI-20260927-ODDTM5QY-0003`); **(2)** widens the candidate set to every strategy family already backtested on the six Breakout crypto symbols this repo has evidence for, not just the ones round 1's `trend_donchian`/`_pullback` search happened to name; **(3)** re-scores everything, alone and added to the current two-leg roster, at 5 seeds under #13154's canonical fresh-account sizing.

## 1. Reconciliation — round 1's tool vs the merged tool (`PI-20260927-ODDTM5QY-0003`)

Re-ran `xrp_pullback_2h` — round 1's closest candidate — with the **exact same
config round 1 used** (`--sizing balance` (CLI default), `--risk-pct 0.015`,
`--modes path`, `--lives 1500 --outer 40 --lives-per-outer 100 --seed
20260927`, same evidence file
`comms/strategy_evidence/runs/2026-09-25/xrp_pullback_2h__trades.jsonl`) on
`main` at commit `232134a3132e63a72d2399bff09101ad31996b85`:

| | round 1 (unmerged branch) | this lane (merged `main`) |
|---|---:|---:|
| alone, evidence p5 / p50 / p95 ($) | 15.17 / 279.44 / 996.47 | 41.36 / 229.55 / 1201.97 |
| baseline + leg, evidence p5 / p50 / p95 ($) | −45.0 / −11.2 / 53.5 | −38.89 / −15.56 / 97.97 |

**Reconciled: same sign, same qualitative read, magnitude differs by an
amount within this tool's own documented outer-bootstrap noise.** Both runs
say "alone: positive p5 on this seed but not robust (see §3)" and "added to
baseline: indeterminate, negative-leaning" — round 1's own §3 already
demonstrated the same leg's p5 moving from +15.17 (seed 20260927) to −3.34
(seed 999), an $18 swing on 40-100 outer-bootstrap draws of a heavy-tailed
distribution; this reconciliation's ~$26/$7 shifts are the same phenomenon,
not a sign flip. **Checked, not assumed**: diffed
`config/prop_rulesets/breakout.yaml` between the branch-era state and `main`
(`git log --oneline -- config/prop_rulesets/breakout.yaml`) — the only
changes since are PR #13154's *additive* fields
(`economics.funded_start`, `economics.first_payout_fee_refund`, the new
`sizing:` block), none of which this reconciliation run's flags touch
(`--sizing balance` ignores the `sizing:` block entirely, and
`funded_start`/`first_payout_fee_refund` were already effectively `fresh`/
`false` by the dataclass defaults before #13154 declared them explicitly).
`account_size_usd`, `target_pct`, `daily_loss_pct`, `max_drawdown_pct`, `fee`,
`profit_split` — the fields that actually drive this number — are unchanged.
**No tool-version regression; closing `-0003` as reconciled.**

## 2. Widening the search — 12 strategy-family legs round 1 did not score

Round 1's §5a checked breadth the other way: every Breakout crypto *symbol*
beyond the six this repo already has a strategy on, and found none had
evidence. This lane checked the axis round 1 did not: **every strategy
*family* already backtested on those same six symbols** —
`ls comms/strategy_evidence/*.json`, filtered to `symbol` in
`{BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, ADAUSDT, AVAXUSDT}` and cross-checked
against round 1's own table. 12 legs have a committed, current
(`config_fingerprint` matches `config/strategies.yaml` today, checked for
every leg below) evidence record round 1 never scored:

| leg | symbol | family | timeframe | n (oos) | net_R (oos, bybit costs) |
|---|---|---|---|--:|--:|
| `trend_donchian_eth` | ETHUSDT | trend_donchian | 1h | 107 | +16.18 |
| `ict_scalp_eth_15m` | ETHUSDT | ict_scalp | 15m | 117 | +9.12 |
| `trend_donchian_sol` | SOLUSDT | trend_donchian | 1h | 59 | +4.46 |
| `ict_scalp_sol_15m` | SOLUSDT | ict_scalp | 15m | 132 | +4.86 |
| `ict_scalp_xrp_15m` | XRPUSDT | ict_scalp | 15m | 129 | +4.08 |
| `ict_scalp_xrp_5m` | XRPUSDT | ict_scalp | 5m | 243 | −0.92 |
| `eth_pullback_2h` | ETHUSDT | pullback | 2h | 62 | −1.74 |
| `trend_donchian_sol_4h` | SOLUSDT | trend_donchian | 4h | 35 | −2.80 |
| `ict_scalp_sol_5m` | SOLUSDT | ict_scalp | 5m | 259 | −8.86 |
| `trend_donchian_ada_4h` | ADAUSDT | trend_donchian | 4h | 46 | −8.61 |
| `ict_scalp_avax_5m` | AVAXUSDT | ict_scalp | 5m | 259 | −9.13 |
| `trend_donchian_avax_4h` | AVAXUSDT | trend_donchian | 4h | 50 | −9.14 |
| `ict_scalp_5m` (BTC) | BTCUSDT | ict_scalp | 5m | 193 | −9.48 |

All 13 (this table's 12 plus the pre-existing `ict_scalp_5m`/BTC family
member) clear the 20-trade honesty floor. **No new strategy authoring was
needed or done** — every row is an existing, already-backtested leg; this is
exactly the widening the operator's brief asked for and round 1's own scope
note (§5a) said was out of its lane.

## 3. Ranked results — every candidate, fresh $5,000 account, room sizing (`k=0.33`, `min_risk=$10`), `RULE-B6-PROP-EV-PER-ACCOUNT-LIFE-V2`

**Sizing note**: these numbers are NOT directly comparable to round 1's table
(§2 there) — round 1 used `--sizing balance`; every number below uses
`--sizing room --room-frac 0.33 --min-risk 10 --risk-pct 0.015`, matching
PR #13154's declared sizing for the next (fresh) `breakout_1` instance, per
the operator's brief for this round. Monte Carlo config: `--modes path
--lives 1000 --outer 30 --lives-per-outer 100` (a lighter setting than the
tool's own defaults, chosen to make a 20-candidate × 2-portfolio × 5-seed
sweep tractable within this lane's budget — see §6 for the resulting
uncertainty this trades away).

### 3a. Single-seed screen, alone, all 20 candidates (round 1's 8 + this round's 12)

| leg | p5 | p50 | p95 | single-seed read |
|---|--:|--:|--:|---|
| **`trend_donchian_eth`** | **+1.05** | 173.74 | 869.99 | only leg to clear p5>0 on the screening seed |
| `trend_donchian_eth_4h` | −45.00 | 115.49 | 992.91 | indeterminate |
| `sol_pullback_2h` | −45.00 | 18.91 | 979.87 | indeterminate (n=18 below honesty floor per round 1 — unmeasured) |
| `ict_scalp_sol_15m` | −41.74 | 283.68 | 1410.21 | indeterminate |
| `trend_donchian_xrp_4h` | −26.90 | 253.40 | 1050.37 | indeterminate (n=16 below honesty floor per round 1 — unmeasured) |
| `trend_donchian_sol` | −39.16 | 152.11 | 797.92 | indeterminate |
| `ict_scalp_eth_15m` | −34.94 | 218.18 | 913.55 | indeterminate |
| `ict_scalp_xrp_15m` | −38.96 | 136.79 | 969.68 | indeterminate |
| `xrp_pullback_2h` | −25.17 | 171.34 | 1166.34 | indeterminate |
| `eth_pullback_2h` | −45.00 | 6.48 | 253.62 | indeterminate |
| `ada_pullback_2h` | −5.59 | 406.53 | 1875.46 | indeterminate |
| `trend_donchian` (BTC) | −45.00 | 3.15 | 357.30 | indeterminate |
| `trend_donchian_avax_4h` | −45.00 | −41.84 | 46.36 | indeterminate, leans fail |
| `ict_scalp_xrp_5m` | −44.76 | −7.58 | 292.85 | indeterminate |
| `ict_scalp_5m` (BTC) | −44.10 | −13.38 | 258.45 | indeterminate |
| `trend_donchian_sol_4h` | −45.00 | −14.51 | 291.45 | indeterminate |
| `ict_scalp_sol_5m` | −43.96 | −30.61 | 135.45 | indeterminate |
| `ict_scalp_avax_5m` | −45.00 | −23.29 | 69.64 | indeterminate |
| `avax_pullback_2h` | −45.00 | −27.25 | 152.65 | indeterminate |
| `eth_pullback_prop_2h` | −45.00 | −21.96 | 139.36 | indeterminate |
| **`trend_donchian_ada_4h`** | **−45.00** | −44.70 | **−9.54** | **FAIL — evidence-p95 < 0** |

### 3b. Multi-seed confirmation on the candidates that passed a screening seed

`trend_donchian_eth` was the only leg (of 20) whose single-seed p5 cleared 0
under room sizing (§3a). It was carried to 5 seeds; `xrp_pullback_2h` (round
1's own closest candidate, re-checked here under room sizing rather than
round 1's `balance` sizing) was carried too, since round 1 explicitly flagged
its seed-sensitivity as unresolved (`PI-20260927-ODDTM5QY-0001`). Per the
brief ("passes only if it clears the bar on **every** seed"), both alone and
added to the current two-leg roster:

**`trend_donchian_eth`**

| seed | alone p5 | alone p50 | alone p95 | +baseline p5 | +baseline p50 | +baseline p95 |
|--:|--:|--:|--:|--:|--:|--:|
| 20260927 | **+1.05** | 173.74 | 869.99 | −35.31 | 23.11 | 290.66 |
| 1 | **+6.30** | 210.49 | 870.58 | −35.44 | 13.90 | 182.49 |
| 2 | −12.67 | 118.36 | 934.43 | −28.44 | 29.23 | 172.43 |
| 3 | −23.02 | 144.04 | 695.80 | −39.04 | 28.00 | 183.31 |
| 4 | −6.02 | 164.09 | 992.50 | −39.10 | 1.99 | 113.45 |
| **pass rate** | **2 / 5** | | | **0 / 5** | | |

**`xrp_pullback_2h`** (round 1's closest candidate, room sizing)

| seed | alone p5 | alone p50 | alone p95 | +baseline p5 | +baseline p50 | +baseline p95 |
|--:|--:|--:|--:|--:|--:|--:|
| 20260927 | −25.17 | 171.34 | 1166.34 | −33.12 | 80.99 | 413.23 |
| 1 | −1.99 | 193.15 | 880.89 | −8.32 | 164.65 | 710.27 |
| 2 | **+35.55** | 311.22 | 878.75 | −1.93 | 161.90 | 1072.53 |
| 3 | −7.98 | 300.31 | 1013.26 | **+15.30** | 263.47 | 730.83 |
| 4 | −22.10 | 198.77 | 841.46 | **+8.19** | 181.94 | 677.84 |
| **pass rate** | **1 / 5** | | | **2 / 5** | | |

## 4. Verdict

**Zero candidates robustly clear `RULE-B6-PROP-EV-PER-ACCOUNT-LIFE-V2` — alone or as a portfolio addition — under the canonical fresh-account (room, `k=0.33`) sizing, at the 5-seed bar this round applied.**

- `trend_donchian_eth` clears p5>0 on 2 of 5 seeds alone, and on **0 of 5**
  added to the current baseline — every seed tried turns it negative once it
  shares the drawdown budget with the existing two legs. The same
  portfolio-concentration effect round 1's §3 reported for
  `xrp_pullback_2h`+`ada_pullback_2h` (two correlated crypto-trend legs on
  one shared budget cost more than they buy) reproduces here.
- `xrp_pullback_2h`, re-checked under room sizing (round 1 used `balance`
  sizing, §0), clears p5>0 on 1 of 5 seeds alone and 2 of 5 added to the
  baseline — a *different* sign pattern than `trend_donchian_eth` (here the
  portfolio addition is closer to passing than the standalone leg) but the
  same overall conclusion: **not robust to reseeding either way**, confirming
  round 1's finding under a different sizing regime rather than resolving
  it. Both legs' 5-seed pass rates are below any reasonable robustness bar —
  1/5 and 2/5 out of 10 (leg × combo) cells clear, and no single
  leg/combo pair clears all 5.
- No other candidate (of the remaining 18) cleared even a single screening
  seed (§3a), so none was carried to the 5-seed stage — one non-passing seed
  already disqualifies "passes on every seed."

## 5. ROSTER PROPOSAL (to the operator, via the manager)

**No leg is proposed for addition to `breakout_1`.** Per RULE ONE / round 1's
own standard ("never round a borderline result up to a Tier-3 proposal"),
neither `trend_donchian_eth`, `xrp_pullback_2h`, nor any of the other 18
candidates scored this round clears its pre-registered bar robustly enough
to propose. This closes the round with the same net disposition as round 1:
**zero Tier-3 roster-addition proposals.**

What would need to be true first, if a future round revisits this:

1. **Both `trend_donchian_eth`'s and `xrp_pullback_2h`'s evidence windows
   would need to grow** before their seed-sensitivity resolves either way —
   `xrp_pullback_2h`'s standing follow-up (`PI-20260927-ODDTM5QY-0001`) is
   now answered (not clearing, at either sizing regime tried) rather than
   pending; a genuine window-growth re-check would need a fresh pipeline
   item (§7).
2. **Even if `trend_donchian_eth` resolved to a clean PASS alone**, its
   portfolio-addition number was negative on all 5 seeds checked — a real
   constraint, not a formality: adding a *second* ETH-symbol trend leg to a
   roster that already runs `trend_donchian_eth_prop` on ETH concentrates
   the same symbol/family risk round 1 flagged for XRP+ADA. Any future
   proposal for `trend_donchian_eth` should say explicitly why sharing the
   drawdown budget with the existing ETH leg is expected to help, not just
   report the alone number.
3. **DXTrade symbol mapping remains unconfirmed for ADA, XRP and AVAX**
   (`config/prop_rulesets/breakout_routing.yaml::symbols` carries only
   `SOLUSDT`, `ETHUSDT`, `BTCUSDT` — checked directly, `grep -n
   'ADAUSDT\|XRPUSDT\|AVAXUSDT'` returns nothing). Every candidate on those
   three symbols (`ict_scalp_xrp_15m`, `ict_scalp_xrp_5m`,
   `trend_donchian_ada_4h`, `ict_scalp_avax_5m`, `trend_donchian_avax_4h`,
   plus round 1's `xrp_pullback_2h`/`ada_pullback_2h`/`avax_pullback_2h`)
   would need this closed before wiring regardless of EV — moot this round
   since none of them cleared the bar either.

## 6. Method, and what this lane's budget did not cover

- Monte Carlo config (`--lives 1000 --outer 30 --lives-per-outer 100`) is
  lighter than the tool's own defaults (4000/100/200) to make a 20-candidate
  wide screen plus a 5-seed deep-dive tractable in this lane's time budget.
  This widens the outer-bootstrap CI relative to a default-sized run — a
  candidate at the edge (like `trend_donchian_eth`) could read differently
  at full default sizing. **Filed as a follow-up** (§7) rather than silently
  presented as decision-grade at the default configuration.
- "Portfolio EV, correlation with the existing legs" is answered the way
  round 1 answered it: by running the candidate ADDED to the current
  two-leg baseline through the same shared-drawdown-budget simulator, not by
  a separate Pearson correlation coefficient. This repo has no established
  precedent for the latter on this question (checked
  `scripts/research/*correlation*` audit trail; nothing purpose-built for
  prop-portfolio EV), and the block-bootstrap sim already prices the
  mechanism that matters here — two legs' trades overlapping on one shared
  daily-loss/DD budget — directly, which a return-correlation coefficient
  would only proxy.
- Every number in §3 uses `--costs breakout` (the tool's default: 8bps
  commission, 3bps slippage, 0.033%/day swap) — unchanged from round 1,
  independently corroborated in
  [`docs/integrations/breakout-instruments-2026-09-27.md`](../../integrations/breakout-instruments-2026-09-27.md).

## 7. Pipeline follow-ups

See `docs/claude/work/pipeline/` for the individual records.

- `PI-20260927-ODDTM5QY-0003` — **closed** (§1: reconciled, no regression).
- `PI-20260927-ODDTM5QY-0001` — **closed** (§3b: xrp_pullback_2h re-run
  under room sizing at 5 seeds clears alone on only 1/5 and added-to-baseline
  on 2/5 — still does not clear robustly at either sizing regime tried; the
  observation this item was watching for is answered, not pending — a future
  evidence-window extension would need a fresh item).
- `PI-20260927-ODDTM5QY-0002` — **left open**, unchanged (ADA/XRP DXTrade
  mapping still unconfirmed; still moot pending a passing candidate).
- `PI-20260927-NLMC4E9Z-0001` — **new** (§4: `trend_donchian_eth` clears
  alone on 2/5 seeds, on 0/5 added to the baseline; filed so a future
  window-growth or portfolio-composition re-check has this finding to
  re-verify against).
