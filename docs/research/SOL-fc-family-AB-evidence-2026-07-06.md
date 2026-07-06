# M19 fc family, 3rd symbol — SOL fc-vs-base purged-CV A/B: evidence (2026-07-06)

**Question** (MB-20260705-FC-ADVISORY-READINESS follow-on / the overnight
program's SOL side-stream): do the six frozen chronos-bolt-tiny quantile-
forecast features (`fc_*`) add regime-head skill on a **third symbol**
(SOLUSDT 15m), as they did on BTC and ETH — widening the fc shadow soak's
volatile-episode coverage toward the powered fc→advisory gate?

## Setup (cleaner than the BTC/ETH precedent)

Both arms read **identical rows from one dataset build** —
`market_features/SOLUSDT/15m/v530`, built WITH
`forecast_path=datasets-out/forecasts/SOLUSDT/15m/v001` (41,399 forecast rows
from the frozen `amazon/chronos-bolt-tiny`, context 64 / stride 4 / horizon 1)
at the production label (`vol_threshold=0.005`) — so the fc-vs-base comparison
is **feature-set-only**: no dataset-window confound (the BTC/ETH precedent
compared across separately-built datasets). Identical purged 5-fold
walk-forward CV (label_horizon 5, embargo 1%), identical LightGBM params,
identical class/sample weighting. Trained trainer-side (CPU, $0);
code_revision `0b9c7bbc` on the trainer.

## Result

| model @ SOL 15m v530 | f1_volatile | macro_f1 | precision_v | recall_v | accuracy | n_eval |
|---|---|---|---|---|---|---|
| **fc arm (`sol-regime-15m-lgbm-fc-pcv-v1`)** | **0.3957** | **0.5491** | 0.259 | 0.845 | 0.614 | 82,788 |
| base control (`sol-regime-15m-lgbm-base-pcv-v530`) | 0.3719 | 0.5044 | 0.236 | 0.887 | 0.553 | 82,788 |

(volatile support 12,776 / 82,788 ≈ 15.4% base rate — SOL is structurally
more volatile than BTC at the same 0.005 label, closer to the BTC 0.003
sensitivity arm's prevalence.)

## Verdict: fc WINS — third-symbol confirm

The fc arm beats the base control on BOTH gate metrics beyond the ±0.01
noise bar used across the fc family: **f1_volatile +0.0238** (0.3957 vs
0.3719) and **macro_f1 +0.0447** (0.5491 vs 0.5044), with accuracy +0.061
— on identical rows, identical CV, identical params, feature-set-only
difference. This is the cleanest of the three fc confirms (BTC and ETH
compared across separately-built datasets; SOL compares within ONE build)
and establishes the quantile-forecast feature block generalizes across all
three symbols tested.

**Disposition — APPLIED (operator approved in chat, 2026-07-06 ~11:00Z):**
the second gate condition — the SOL forecast side-stream in production —
was wired the same morning: `FORECAST_SYMBOLS` default extended to
`BTCUSDT,ETHUSDT,SOLUSDT` in `scripts/ops/run_forecast_producer.sh` (repo
PR + applied on the trainer via the relay no-git path, issue #5701); the
first production wrote a valid `SOLUSDT.json` artifact (fresh `as_of_ts`,
populated `fc_row`), and `sol-regime-15m-lgbm-fc-pcv-v1` was promoted
candidate→**shadow** (stage_history 11:12:55Z — observe-only; shadow never
influences an order) with the mirror publish confirming
`published → 141.145.193.91`. fc now soaks across BTC+ETH+SOL. The
base-pcv-v530 control stays candidate forever (it exists only as the A/B
control). Live soak-accrual verification (shadow_stats + populated `fc_*`
feature_row) follows the BTC verification record.

## Honest bounds

1. Registered `candidate` — refused by the live shadow factory; never
   order-influencing. Candidate→shadow is proposed only on a clear fc win
   AND requires the SOL forecast side-stream in production
   (`FORECAST_SYMBOLS` + producer timer — a trainer-timer env/service change,
   **flagged to the operator, not applied**), mirroring the ETH graduation
   record.
2. Single seed (42), single CV protocol — same bounds as the BTC/ETH fc
   evidence.
3. Ops note: the trains ran through the relay no-git path (manifests shipped
   base64 onto the trainer; its `git pull` is broken by the repo going
   private — `BL-20260706-TRAINER-GIT-AUTH-BROKEN`).
