# RQ-20260927-004 — soft regime-weight [15, 25] band result

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Dispatched:** trainer-vm-diag issue #13151, run 36320461693, commit d1da2ecdd
(branch `claude/w6-srq-regime-weighting`). **Landed:** 2026-09-27T13:12Z.

Decision rule: `research/queue/RQ-20260927-004.yaml::decision_rule` (`RULE-RQ0927-004-SOFT-WEIGHT-HOLDOUT`),
registered before this run.

## Result

All 10 cells FAIL `gate_all_folds_positive` at the fixed `[floor=15, ceiling=25, weight_min=0.0]`
soft-weight band (`scripts/ops/m15_ws_c_soft_weight_kfold.sh`, 5-fold/0.40 walk-forward, net of
fee+slippage+funding):

| cell | total_oos_net_r (7.5bps) | total_oos_net_r (15bps) | gate_all_folds_positive | gate_2x_fee_headroom | tier |
|---|--:|--:|:-:|:-:|---|
| pullback_ETHUSDT_2h | 43.79 | 38.98 | false | true | paper_ready |
| trend_ETHUSDT_4h | 4.46 | 0.86 | false | true | paper_ready |
| pullback_ADAUSDT_2h | 30.21 | 25.90 | false | true | paper_ready |
| trend_ADAUSDT_4h | 7.27 | 4.66 | false | true | paper_ready |
| pullback_SOLUSDT_2h | 20.34 | 16.94 | false | true | paper_ready |
| trend_SOLUSDT_4h | 8.98 | 6.89 | false | true | paper_ready |
| pullback_XRPUSDT_2h | 6.09 | 1.82 | false | true | reject |
| trend_XRPUSDT_4h | 23.82 | 21.12 | false | true | paper_ready |
| pullback_AVAXUSDT_2h | 2.65 | -0.61 | false | **false** | reject |
| trend_AVAXUSDT_4h | -2.02 | -4.11 | false | **false** | reject |

## Disposition, per the pre-registered rule

Zero cells reached the `soft_weight_candidate` bar (pass both `gate_all_folds_positive` AND
`gate_2x_fee_headroom`) — every cell has at least one negative OOS fold under this band. Per
`RULE-RQ0927-004-SOFT-WEIGHT-HOLDOUT`: **`refuted_at_band_15_25`**, closed `no_action_warranted`
on this specific band.

**This refutes the `[15, 25]` band, not the soft-weight axis.** Per the rule's own text and the
operator's 2026-09-27 standing principle ("never kill an idea because of one bad pass — refine or
re-scope, don't drop"): the next pre-registerable arm is a *different* fixed band (e.g. a wider
ramp `[10, 30]`, since several cells' pooled R stayed strongly positive even while individual
folds went negative — the signal has not disappeared, only failed the every-fold bar at this
particular anchor) or a per-family (not per-cell) band. Filed as the open follow-up rather than a
new queue unit in this landing PR, since it is scoped identically to RQ-20260927-004 with one
parameter changed — the next session dispatching it should copy RQ-20260927-004.yaml, change the
band, and re-register a fresh `decision_rule` (a rule may not be reused verbatim across a new
run — see `check_research_queue_decision_rule.py`).

Full per-fold JSON: trainer-vm-diag issue #13151 comment
(https://github.com/benbaichmankass/Metis-Insights/issues/13151#issuecomment-5856020631) — not
re-hosted verbatim here (10 cells × 2 fee arms × 5 folds); this table is the pooled summary.
