# AUD-3 — Data / provenance integrity (E75 system audit, 2026-09-29)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Lane AUD-3, dispatched by AUD-LEAD. Findings: [`AUD-3.findings.jsonl`](AUD-3.findings.jsonl) (13 rows). Spec: `docs/plans/SYSTEM-AUDIT-PROPOSAL-2026-09-27.md` §4.3.

## 1. The question and the answer

**Question.** For every numeric field the three reviews and the `/api/bot/performance` and dashboard endpoints present as MEASURED, is the provenance chain measured upstream at runtime, or is an ESTIMATED, FABRICATED or UNVERIFIED value folded in? And is every three-state field in production registered in `check_collapsed_states.py` CONTRACTS?

**Answer.** No on both counts, with limits. The real-money `/api/bot/performance` counts reconcile exactly (338+22+4+78=442) and publish their denominators. What fails is the seams. (a) Fields named `*Measured` sum MEASURED and ESTIMATED rows: `/api/bot/stats` paper `totalPnLMeasured` is +94,618 while the MEASURED-only sum is negative, and the R4 gate evidence record uses the wider sum beside a MEASURED-only coverage figure. (b) The grade reads the exit-price SOURCE, never the arithmetic: 18 `ib_paper` rows graded MEASURED carry pnl exactly 0.0 against implied ~-$115k, and 10 `bybit_1` rows graded MEASURED have the wrong sign. (c) CONTRACTS holds 52 rows; `broker_truth.read_state`, the prop `open_risk_state`/`day_pnl_state`, IB `verify_state` and the FIX-CA-12 venue-read states are not among them, and the guard has no discovery step. FIX-CA-12 and FIX-CA-19 are merged, contained in the running sha, and observed at the level stated in the findings.

## 2. Findings

| id (SA-AUD-3-…) | sev | blast | tier | claim (short) |
|---|---|---|---|---|
| ib-execution-zero-pnl-graded-measured | high | accounting | 1 | 18/18 ib_execution rows pnl 0.0, graded MEASURED; implied ~-$115k |
| paper-totalpnlmeasured-sign-flipped-by-estimated-row | high | accounting | 1 | paper totalPnLMeasured +94.6k includes ESTIMATED; one row +249k |
| gate-evidence-net-usd-measured-includes-estimated | medium | money-at-risk | 2 | R4 record's net_usd_measured is MEASURED+ESTIMATED, coverage is MEASURED-only |
| measured-grade-ignores-own-arithmetic-sign-flips | medium | accounting | 1 | 16 bybit_1 exchange_fill rows diverge, 10 sign-flipped (-3,510.89) |
| collapsed-state-fields-unregistered-money-adjacent | medium | observability | 1 | broker_truth.read_state and others not in CONTRACTS; no discovery scan |
| performance-review-skill-aggregates-without-provenance | medium | docs | 1 | skill and template aggregate pnl with no coverage field |
| bybit-closed-pnl-rows-diverge-from-own-price-move | low | accounting | 1 | 34/114 bybit_1 closed-pnl rows diverge (probably netting, unverified) |
| fix-ca-12-venue-read-states-unregistered | low | observability | 1 | fix deployed and observed, no CI detector |
| headline-winrate-pf-drawdown-fold-nonmeasured-rows | low | observability | 1 | headline stats over all 442 rows; coverage published |
| broker-truth-ledger-stale-bybit2-flag | low | observability | 1 | ledger 78 days old; 30d journal reconciles within 0.6% |
| bybit1-portfolio-journal-vs-wallet-gap | low | accounting | 1 | 30d journal vs wallet 3-7% (gross reading) or 8-25% (net) |
| non-issue-performance-counts-reconcile | info | observability | 1 | NON-ISSUE: real-money counts reconcile |
| non-issue-ca19-backfill-rows | info | accounting | 1 | NON-ISSUE: no sign-inconsistent backfill row |

## 3. Coverage

**Behavioural (exercised against real data / asserted): 9 of 12.**
Exercised: (1) full live `trades` table read via `/api/diag/journal` (6286 of 6286 rows) and reclassified with `src.runtime.provenance.classify_pnl`; (2) `/api/bot/performance` all and 24h; (3) `/api/bot/stats`; (4) `/api/bot/trades/closed?account_id=bybit_2`; (5) `/api/diag/bybit_wallet_truth` reconciled against the journal for three accounts; (6) `/api/diag/{bybit_open_orders, alpaca_open_orders, venue_session, exchange_positions, version}`; (7) sign/magnitude arithmetic check on every non-IB closed row; (8) ancestry of the FIX-CA-12/19/31 merges against the running sha; (9) CONTRACTS enumeration against emitted `*_state` keys.
Asserted but not exercised: FIX-CA-31 (alpaca fills failure path) not exercised beyond ancestry; `/api/bot/strategies` and `/api/bot/strategy/attribution` fetched, not analysed (strategies stats read `total_pnl: 0.0`); the `ml-review` numeric fields (no live ML read was made).

**Reading.** Read: `src/runtime/provenance.py` (lines 1-250), `scripts/ops/column_provenance.py` (head), `src/web/api/routers/performance.py` (~lines 590-670, 940-985, 1105-1140, greps elsewhere), `scripts/ci/check_collapsed_states.py` (header, CONTRACTS names, no full body), `src/runtime/broker_truth.py` (journal_trust_map/for), `.claude/skills/performance-review/SKILL.md` (targeted), `docs/audits/code-audit-2026-09-27.md` (FIX-CA-19, §9). Not read: `column_provenance.py` was not RUN against columns (the journal reclassification replaced it); `dashboard.py`, `strategies.py`, `attribution.py`, `pnl_exchange.py` bodies; the consumers of the prop/IB state fields; `health-review` and `ml-review` SKILL.md beyond greps; all CA-*.md lane files apart from greps.

## 4. Could-not-look

- The 2026-09-27 diag 401 did **not** recur: direct HTTP through `scripts/ops/diag_fetch.sh` worked. No relay issue was opened.
- Root cause of the ib_execution 0.0 (finding 1): the raw IB fills store lives on the VM and I made no read of it.
- Whether the 16 + 34 divergent bybit rows are netting artefacts: no per-row netting join.
- Whether journal `pnl` is gross or net of fees: inferred from fit only.
- Paper/demo blocks of `/api/bot/performance` not reconciled; the API's exclusion predicates differ from my population (442 API vs 466 mine for real money; 1066 vs 1470 for paper).
- ML and health review numeric fields: no live ML read.
- `restart_pending: true` on `/api/diag/version`: not resolved which sha the process runs.

## 5. Spend

Meter not readable by the lane. Work was one journal pull (7 requests of ≤1000 rows), about 12 other reads, local analysis and no sub-agents. Well under the $25 ceiling by estimate.
