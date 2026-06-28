# Salvage — findings preserved from closed stale review PRs (M17 triage, 2026-06-28)

During the M17 full-system-audit stale-PR triage, several week-old **review-artifact**
PRs were closed (not merged) because their bases were far behind `main` and their
diffs were `dirty`/conflicted on the append-only grade + backlog files:

- #4098 (reviews 2026-06-21), #3984 (reviews 2026-06-19), #3950 (perf+health
  artifacts), #4201 (prop-monitor doc-freshness), #3949 (reconciler flap guard —
  superseded; its fix is already on `main`).

A loss-audit against `main` confirmed almost everything in those PRs is already
preserved (the `BL-20260620-*` data bugs, `BL-20260618-STALEHEARTBEAT`, the
`btc-regime-1h-lgbm-yz-v1` ML demote target, `BL-20260619-MGC-REDUCE-GUARD` —
referenced in `coordinator.py` + `scripts/ops/flatten_ib_position.py` — and the
regime-weighting design doc, which landed as
`docs/research/regime-conditional-strategy-weighting-DESIGN.md`).

**Four items existed ONLY in the now-closed PRs and are NOT on `main`** — two found
by this session's loss-audit (1, 2 below) and two **relayed from a concurrent
session's closed duplicate salvage PR #4949** (3, 4 below; #4949 was closed in
favour of this PR to avoid a `RELAY-INSIGHTS-GAP` duplicate, and its two unique
prop items handed over). All four are self-re-discovering, but they are recorded
here so nothing is silently dropped. A future `/ml-review` and `/health-review`
should re-verify each against current state and fold it into the canonical backlog
(`docs/claude/ml-review-backlog.json` / `docs/claude/health-review-backlog.json`),
then this salvage file can be deleted.

## 1. `MB-20260621-ZEROROW-DECISION-DATASETS` (from #4098) → ml-review-backlog
Decision-model dataset families — `trade_outcomes`, `execution_quality`,
`setup_labels`, `review_journal` — build **0 rows**, so the decision models are
starved by the small/flat real-money book. Structural data-wall observation, very
likely still true. **Next `/ml-review`:** confirm the row counts and re-file with
current numbers (or mark resolved if the real book has since produced enough
closed trades).

## 2. `BL-20260619-RELAY-INSIGHTS-GAP` (from #3984) → health-review-backlog
The `vm-diag-snapshot` relay can only reach `/api/diag/*`, so a PM-side / web
session cannot read the M13 analyst cache at `/api/bot/insights/*` through it —
insights cross-checks are blind on relay-only sessions. Known structural relay
limitation. **Candidate fix:** add a token-gated `/api/diag/insights` mirror (the
same pattern already used for `/api/diag/shadow_stats`, which mirrors the
non-diag `/api/bot/shadow/stats`). **Next `/health-review`:** decide whether to
build the mirror or accept the gap, and re-file accordingly.

## 3. `BL-20260622-PROP-REPORT-PROSE` (from #4201, relayed via closed #4949) → health-review-backlog
`.github/workflows/prop-report.yml` validates the **entire issue body** with
`jq -e 'type=="object"'`, so any prose around the JSON object fails (hit during
the prop-monitor work; a JSON-only retry worked). **Candidate fix:** extract the
first ```json fenced block / first `{...}` object before validating, matching the
leniency of the system-action body parser. Tier-1.

## 4. `BL-20260622-PROP-MONITOR-ANDROID` (from #4201, relayed via closed #4949) → health-review-backlog (cross-repo: ict-trader-android)
The bot emits a `prop_monitor` event kind (the 15-min prop pulse,
`src/prop/prop_monitor_pulse.py`). Mirror it into the Android app's
`EventKind.kt` / `NotificationKinds` for a dedicated notification channel, sibling
to `prop_fill` / `prop_closed`. Tier-1, cross-repo follow-up.
