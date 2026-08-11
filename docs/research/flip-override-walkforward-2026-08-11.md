# Walk-forward — the LIVE flip-confidence override (0.15 / 4.0), 2026-08-11

> **Status:** Tier-1 research. **Evidence for a Tier-3 operator decision — nothing here
> is enacted.** Closes the evidence half of `BL-20260811-FLIP-OVERRIDE-NEVER-WALKFORWARDED`.
> Driver: [`scripts/walkforward_flip_policy.py`](../../scripts/walkforward_flip_policy.py)
> (`hold_confgap` arm). Runner:
> [`.github/workflows/flip-override-walkforward.yml`](../../.github/workflows/flip-override-walkforward.yml),
> run [31523739722](https://github.com/benbaichmankass/Metis-Insights/actions/runs/31523739722),
> both fold jobs `success`.

## The question

`FLIP_CONFIDENCE_THRESHOLD=0.15` + `FLIP_MIN_POSITION_AGE_HOURS=4.0` are **live on every
account including real money** (measured 2026-08-10 via `get-env`, issue #8755;
operator-confirmed deliberate 2026-08-11). They override the incumbent `FLIP_POLICY=hold`,
which itself earned its place on a 24-cell walk-forward
([`walkforward-flip-policy-2026-05-30.md`](../audits/walkforward-flip-policy-2026-05-30.md)).

The override had **never** been walk-forwarded. That asymmetry — an unvalidated arm
displacing an evidence-backed incumbent — is what this run closes.

## Run inputs

- **Feed:** BTCUSDT 5m, **2020-06-01 → 2026-02-28**, Binance-vision (604,512 rows).
- **Roster:** `4mem` (`trend_donchian`, `fade_breakout_4h`, `squeeze_breakout_4h`, `fvg_range_15m`).
- **Folds:** the May run's, unchanged — A: train 2020-06..2023-12 / OOS 2024-01..2026-02;
  B: train 2022-01..2024-06 / OOS 2024-07..2026-02.
- **Arms:** `hold` (incumbent) · `hold_confgap` (**gap ≥ 0.15 AND held-age ≥ 4.0h**, the live
  values) · `reverse` (reference).
- Account model $10k, risk 0.3%/trade, 3% daily-loss cap, 15m clock.

## THE ARM FIRED — this is a measured result, not an untested arm

**34 overrides across the four cells** (13 / 6 / 11 / 4). This is the first thing to read:
a `hold_confgap` row with `fired = 0` would be the incumbent under another name, and its
PnL matching `hold` would mean nothing. That is not what happened.

## Result

| cell | `hold` net | `hold_confgap` net | Δ net | `hold` maxDD% | override maxDD% | Δ maxDD | fired |
|---|---|---|---|---|---|---|---|
| A / train | $672 | $113 | **−$559** | 8.20 | 11.04 | **+2.84** | 13 |
| A / oos | $242 | $180 | **−$62** | 9.23 | 9.23 | 0.00 | 6 |
| B / train | $1157 | $445 | **−$712** | 5.55 | 7.60 | **+2.05** | 11 |
| B / oos | −$449 | −$396 | **+$53** | 8.67 | 8.67 | 0.00 | 4 |
| **total** | **$1622** | **$342** | **−$1,280** | | | | **34** |

Full rows including `reverse`, trades and conflict counts are in the run's job logs and the
per-fold artifacts (`flip-override-walkforward-fold-{A,B}`, 14-day retention).

## Findings

1. **The override costs money on this population: −$1,280 across four cells, a 79%
   reduction in net.** It is worse on net in **3 of 4** cells.

2. **It never improves drawdown in any cell.** Worse in 2 of 4 (+2.84pp, +2.05pp),
   identical in the other 2, better in **0**. There is no "worse PnL but safer" trade-off
   to weigh — the arm is not buying drawdown protection with its losses.

3. **Both TRAIN halves — where the sample is largest (13 and 11 fires) — are worse on
   BOTH axes simultaneously.** That is the cleanest signal in the table.

4. **The single positive cell is noise, and should not be read as OOS support.**
   B/oos is +$53 on **4** fires, with maxDD *identical* to `hold`, on a book that loses
   money either way (−$449 vs −$396). A 4-fire, sign-flipped, drawdown-neutral result on a
   losing book is not evidence the arm works OOS; it is what a null effect looks like.

5. **This CONFIRMS the repo's existing prior rather than contradicting it.**
   [`M26-P1-conflict-taxonomy-2026-07-22.md:203`](M26-P1-conflict-taxonomy-2026-07-22.md)
   already lists this exact arm as `A_confgap_flip` — *"Demoted by P0 (same-clock flip lost
   −$7.1k)"*. The design doc demoted it on theory; this measures it and agrees.

## Limits of this result — read these before acting on it

- **BTCUSDT + `4mem` only.** The live override applies to **every symbol and every
  account**. This run cannot speak to MES/MGC/ETH/SOL legs or to the `6mem` roster.
- **Different feed from the May audit.** That run used a qashdev parquet mirror; this used
  Binance-vision. Absolute PnL is therefore **not** comparable cell-for-cell to the May
  table. The `hold` vs `hold_confgap` comparison *within* this run is apples-to-apples —
  same feed, same cells, same process.
- **Not a clean paired comparison after the first fire.** `conflicts_observed` differs
  between the arms (162 vs 151, 116 vs 102, …) because once the override flips a position,
  the subsequent position history diverges and the two arms stop seeing the same conflict
  population. This is inherent to any policy A/B, not a defect, but it means the per-cell Δ
  is a *path* difference, not a per-conflict treatment effect.
- **The M26 tf_ratio split was CAPTURED but is NOT REPORTED here.** The conflict ledger
  records `tf_class` (`cross_clock` / `same_clock` / `unknown`) per conflict and
  `by_tf_class` per run, but the workflow's summary table does not print it and the MCP
  cannot download run artifacts. So the "does it lose on same-clock specifically?" question
  — the one M26 P0 says is decisive — **is not answered here**. Fix: print `by_tf_class`
  in the workflow summary and re-run. Tracked as the follow-up below.
- **The driver's `Verdict: … Overall: FAIL` lines in each job are an ARTIFACT, not a
  finding.** `_evaluate_pass_criteria` spans both folds, but the matrix runs one fold per
  job, so the other fold's cells are always `missing_cell` → automatic FAIL. Those criteria
  also test `hold` vs `reverse` (the May question), not the override. **Do not read them as
  this run's verdict.** Fix: compute the verdict only over folds present in the run.

## Recommendation (Tier-3 — proposed, NOT enacted)

**Disarm the override**: set `FLIP_CONFIDENCE_THRESHOLD=0.0` on the live VM, which returns
routing to the walk-forward-validated `hold`. One env flip + restart, no redeploy, and
`FLIP_MIN_POSITION_AGE_HOURS` becomes inert automatically (it only gates a positive
threshold).

The case: the arm is measured to lose $1,280 over 34 fires on the only population it has
ever been tested on, never improves drawdown, is worse on both axes in both larger-sample
halves, and the repo's own M26 P1 analysis had already demoted it. The incumbent it
displaced passed a 24-cell walk-forward.

**What would change this recommendation:** a per-symbol run showing the arm wins on legs
this test did not cover, or the tf_ratio split showing it is strongly positive on
`cross_clock` conflicts and the loss is confined to `same_clock` — in which case the right
move is a TF-aware gate (M26's `A_coexist_crossclock`), not the current TF-blind one.
Both are cheap now that the harness and workflow exist.

This is a **Tier-3 order-routing change on real money** and is the operator's call.
