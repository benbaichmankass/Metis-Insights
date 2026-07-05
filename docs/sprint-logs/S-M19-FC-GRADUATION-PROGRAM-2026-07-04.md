# Sprint Log: S-M19-FC-GRADUATION-PROGRAM-2026-07-04

## Date Range
2026-07-04 (extended autonomous session, post-T1.2-closeout — the "graduate the
winner + all-of-above next phases" program).

## Objective
With M19's representation frontier closed as exhausted (T0.1 marginal, T1.1 TCN
negative, T1.2 SSL negative) and the T0.4 `fc` forecast head as the one durable
win, push the milestone forward: (1) aggressively broaden the fc soak toward the
shadow→advisory gate; (2) run the operator-approved "all of the above" next
phases — cost-data unlock, extend-fc, and the T1.3 ranker — sequenced by
dependency; report honestly where each actually stands.

## Tier
Tier 1 throughout (research, offline analysis, observability, docs) + one
trainer-autonomous candidate→shadow promotion (observe-only, operator
pre-authorized). No `src/` runtime, `config/`, risk, account-mode, or live-order
change; nothing here influences an order. GPU spend this session: **$0**.

## Starting Context
T1.2 SSL corpus-encoder A/B closed as a clean negative (merged #5551). fc head
`btc-regime-15m-lgbm-fc-pcv-v1` was live at shadow (117 preds / ~28h). Operator
directed: move aggressively toward soaking, then pursue the next phases (all of:
cost-data, extend-fc, T1.3 ranker), with the gross-of-fees label basis chosen.

## Work Completed
**Aggressive fc soak — broadened fleet-wide.** Trained + promoted
`eth-regime-15m-lgbm-fc-pcv-v1` candidate→shadow (v521 purged-CV macro_f1 0.584 /
f1_vol 0.31). The live fc producer already serves ETHUSDT (`FORECAST_SYMBOLS`
default `BTCUSDT,ETHUSDT`), so the ETH fc head soaks with real live `fc_*`
features immediately — doubling volatile-episode coverage (BTC + ETH). SOL
deferred (needs a new fc side-stream + producer symbol + manifest).

**Phase 1 — cost-data unlock: audited + decided.** The gap is smaller than the
M18 design (2026-06-29) implied: `trades` already carries
`fee_taker_usd`/`fee_maker_usd`/`funding_paid_usd`/`cost_source`, an M18-P0a
fixed-model fee **estimate** writer runs on close, and **Bybit broker-truth fees
are already stored** in `exchange_fills.sqlite` (per-fill `fee`/`fee_currency`/
`is_maker`). So the remaining work is just a `exchange_fills → trades`
**join-writer** (Tier-2, deferred). **Operator decision (locked): use GROSS
labels** — net-R label = gross_pnl/risk, fees carried as a **separate explicit
cost feature**, never folded in (avoids the Bybit-net vs local-gross
double-count). This makes the label a pure offline transform and demotes the
broker-truth writer from blocker to a fee-feature refinement.

**Phase 3 — T1.3 ranker: assessed → DEFERRED (data-constrained both ways).**
- Label feasibility: only **214 labelable order_packages** (180 paper + 34 real;
  op→resolved-closed-trade join), and `model_scores` present on just **218/2729**
  ops. A learned ranker + 16–32 embedding dims on 214 rows = overfit — the same
  label wall as T0.3 (n=20) and the parked M18 ranker (OOS AUC 0.51).
- The rules-based **EV_R allocator IS soaking** live (`allocator_soak.jsonl`,
  `score_kind=ev_net_r` — the cost-aware M18-P1 scorer), but the **decision space
  is thin**: ~24 multi-candidate ticks over 5 days, almost all same-strategy
  account/timeframe variants (`trend_donchian_*_prop/_4h`); ~75% "disagree" but
  mostly small-regret variant-routing (prefer the higher-EV api leg over the prop
  leg), with only a few genuine divergences (TLT 1h-short vs 1d-long, regret
  6.45). Near-term alpha bounded.
- **Decision:** defer both the learned ranker and allocator-graduation until
  labels grow (~500+) and genuine cross-strategy competition increases;
  reprioritize active effort to phase 2.

**fc-head readiness — RG4 first-look (train/serve skew gate).** Ran
`rg4_targeted.sh` for the fc heads. Caveats dominate: the trainer's mirrored
shadow log is **stale** (mtime 03:26 UTC, ~19h old) → only 64 BTC-fc rows (48
labeled), 0 ETH-fc rows (ETH promoted at 17:39, after the sync). On ~48 labeled
rows BTC-fc returned `ANTI_PREDICTIVE / AUC=None` — **noise-dominated and
untrustworthy** (rare volatile class, uncomputable AUC, stale mirror), but a
**watch-flag**: the live fc head is not yet showing clean positive
discrimination. A real RG4 verdict needs a fresh mirror + weeks more soak (RG4 is
inherently a soak gate). Not a red alarm; do not rush fc→advisory.

## Validation Performed
- ETH fc promotion confirmed via the promote-stage output (`target_deployment_stage:
  shadow`, the field the shadow factory gates on).
- Label counts + feature availability queried against the fresh
  `data/trade_journal.db` (3,150 trades, 2,729 order_packages, latest trade today).
- Allocator soak tail (`/api/diag/log_file?name=allocator_soak`) reviewed for
  accrual + regret pattern.
- RG4 replay ran (low-power; caveated above).

## Documentation Updated
- This log.
- ROADMAP M19 — fc soak broadened; phases 1/2/3 status (companion edit).
- Tasks tracked (#39 fc readiness, #40 soak, #41 cost-data, #42 extend-fc, #43 ranker).

## Contradictions or Drift Found
None. Two tooling gotchas recorded for future relays: heredoc bodies in an
indented `cmd:|` block break (`IndentationError`) — use one-line `python -c`; and
the trainer has **no `sqlite3` CLI** — use the Python `sqlite3` module.

## Risks and Follow-Ups
- **RG4 watch-flag:** re-run RG4 after a fresh shadow-log mirror (next live→trainer
  sync) with more soak before trusting any live-discrimination verdict. If it
  stays anti-predictive with power, that's a live train/serve skew that would
  block fc→advisory — investigate fc feature serving parity.
- **fc money-gate walk-forward:** the backtest harness resolves the vol head by
  STAGE, not id — pinning the fc head as the vol source needs a scratch-registry
  step; set it up deliberately (not a one-command run).
- **Phase 1 writer** (exchange_fills→trades broker-truth fees): Tier-2, deferred,
  needs one operator OK before ship.
- **Phase 3** deferred until labels/decision-space grow.

## Deferred Items
- Phase 2 (extend fc — fc→SL/TP geometry offline backtest): the reprioritized
  next active lever (non-data-constrained); scope + run deliberately.
- SOL fc head + serve.
- The broker-truth cost writer.

## Next Recommended Sprint
Phase 2: an **offline** analysis of fc-informed SL/TP geometry — for historical
trades, would sizing stop/target from the fc quantile forecast (`fc_range_rel`,
`fc_q10_rel`, `fc_q90_rel`) have improved net-R / win-rate / maxDD vs the actual
SL/TP? Self-contained (fc side-stream + historical trades), Tier-1, no order-path
change. In parallel: re-run fc RG4 on a fresh mirror as the soak matures.

## Wrap-Up Check
- [x] Objective met (soak broadened; all three next-phases honestly assessed).
- [x] Verified reality reported (real counts / soak data / RG4, with caveats — no overclaiming).
- [x] No live-path / config / risk change; one observe-only shadow promotion.
- [x] GPU ledger untouched ($0).
- [x] Findings preserved durably (this log + ROADMAP + tasks).
