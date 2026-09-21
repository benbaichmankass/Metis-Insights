# Data model — the canonical stores, provenance and collapsed states

> **Doc status:** `unknown` · category `lookup` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> Extracted verbatim from `CLAUDE.md` on 2026-09-21 by the operating reset.
> Reference material — read on demand, not at session start.
> Registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md).

```
src/
  runtime/
    pipeline.py         — main trading loop
    health.py           — 7-point health check suite
    outcomes.py         — structured logging helpers
  web/
    api/
      main.py           — FastAPI app, CORS middleware, router mounts
      auth.py           — session/token auth helpers
      routers/
        dashboard.py    — /api/bot/{stats,logs,positions,signals} (S-014)
        bot_config.py   — /api/bot/config (S-064)
        liquidity.py    — /api/bot/liquidity (S-064)
        trades_closed.py — /api/bot/trades/closed (#557)
        backtests.py    — /api/bot/backtests (read-only; M5 writer retired 2026-08-20)
        shadow.py       — /api/bot/shadow/{predictions,stats} (S-AI-WS8-PART-2)
        health_snapshots.py — /api/bot/health/{latest,history,snapshot,services} (#820, 2026-05-11)
        insights.py     — /api/bot/insights/{summary,recent,strategy/{name},health,history,usage} (M13 S1+S2)
        trade_scores.py — /api/bot/trades/scores (#820, 2026-05-11)
        diag.py         — /api/diag/* endpoints (S-051, token-gated read)
        pnl.py          — /api/pnl
        pnl_history.py  — /api/pnl/history (S-063, no-session)
        status.py       — /api/status
    runtime_status.py   — writes runtime_logs/runtime_status.json (DO NOT DELETE—imported by pipeline)
runtime_logs/
  signal_audit.jsonl    — structured pipeline audit log (primary log source for dashboard)
  heartbeat.txt         — mtime used to detect if bot is alive
trade_journal.db        — canonical SQLite (live VM: /data/bot-data/trade_journal.db).
                          trades carries reconcile_status (orphan-flap hardening
                          2026-06-24): NULL=unspecified / 'unreconciled' (an orphan
                          to resolve — the red-flag state) / 'reconciled' (tied to
                          its real order package) / 'superseded' (a phantom flap
                          duplicate void-flagged by the historical reconciliation
                          pass, excluded from analytics). Orphan is an EXPLICIT
                          queryable terminal state, never inferred from setup_type.
                          trades ALSO carries the protective-bracket REPAIR stamp
                          (2026-08-24, Tier-2 operator-approved): protection_repairs
                          / protection_repair_first_at / _last_at / _last_kind
                          (naked_rearm | partial_topup | reassert) /
                          _last_verified (both_legs_resting | stop_only |
                          no_legs_resting | unverified | call_failed). Written by
                          the ONE owner Database.stamp_protection_repair from the
                          three repair paths in order_monitor. ⚠️ It counts repairs
                          that REACHED THE VENUE, not ones that succeeded —
                          call_failed counts deliberately, because these paths
                          cancel the resting legs BEFORE they place, so a failed
                          repair is the state a reader most needs to find.
                          ⚠️ unverified is 'we did not look', NEVER a success: the
                          naked sweep adds no per-repair broker read-back (that is
                          the IB pacing-wedge shape), so it can attest the call was
                          accepted and nothing more — conflating the two is
                          BL-20260823-REASSERT-REPORTS-APPLIED-OK-ON-A-HALF-ARMED-BRACKET.
                          ⚠️ NULL means 'no repair RECORDED', never 'no repair
                          happened' — a trade opened before the writer deployed
                          carries NULL whatever was done to it; distinguish by
                          trades.created_at against the deploy, and never back-fill
                          zeros (that would assert an observation nobody made). The
                          ordinary strategy-driven trailing amend is deliberately
                          NOT stamped — that is the exit working, and counting it
                          would put dozens of increments on a healthy trade and
                          destroy the signal. Read surface is generic (the Data
                          Explorer /api/bot/db/table/trades + /api/diag/journal),
                          so the columns are queryable with no new endpoint; no
                          consumer BRANCHES on them yet
                          (BL-20260824-PROTECTION-REPAIR-STAMP-HAS-NO-BRANCHING-CONSUMER).
                          Tables: trades, order_packages, signals (dual-write),
                          backtest_results (HISTORICAL only — the M5 /test
                          writer was removed 2026-08-20; no producer remains),
                          daily_risk_state (per-account daily PnL + equity-high —
                          self-healing rebuild from trades + balance snapshot,
                          see src/units/accounts/risk.py), strategy_versions
                          (boot snapshot of config/strategies.yaml),
                          learning_progress (dashboard Learning-tab per-resource
                          progress — operator observability, no trading impact;
                          src/web/api/routers/learning.py),
                          account_context_snapshots (per-signal pre-decision
                          account state — equity, daily PnL, daily equity-high,
                          drawdown%, open-trades-count — keyed by
                          (order_package_id, account_id); S-MLOPT-S12 Part B,
                          best-effort writer in src.units.accounts.context_snapshot,
                          gated by ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED),
                          prop_tickets / prop_fills / prop_account_status
                          (Breakout manual-bridge P2/P3 — outbound prop tickets,
                          inbound fill/close + account-status report-backs;
                          ISOLATED from `trades` so prop never leaks into the
                          real-money/paper KPIs; src/prop/prop_journal.py).
trainer_store.db        — federated read-mostly sidecar (live VM:
                          /data/bot-data/trainer_store.db). Trainer/ML lifecycle
                          data ingested from runtime_logs/trainer_mirror/:
                          training_cycle, dataset_builds, db_pulls,
                          model_registry, experiment_runs, backtest_sweeps.
                          Browsable in the Data Explorer alongside the journal.
```

### Canonical persistence model (S-PERSIST-CANON, 2026-05-23)

One central, queryable store, federated across two SQLite files on the
OCI block volume (`/data/bot-data`), both browsable from the dashboard's
**Data Explorer**:

- **`trade_journal.db`** — everything the LIVE trader produces (trades,
  order_packages, signals, backtest_results, daily_risk_state,
  strategy_versions). Every Python caller resolves its path through the
  single `src.utils.paths.trade_journal_db_path()` resolver; the shell
  side uses `scripts/ops/_lib.sh::runtime_db_path`. The
  `canonical-db-resolver` CI guard forbids the CWD-relative fallback (and
  inline `TRADE_JOURNAL_DB` env-reads) in both shell and Python — that
  fallback is what created the stray duplicate journals under each
  process's working directory.
- **`trainer_store.db`** — everything the TRAINER produces, ingested from
  the file-based trainer mirror (`runtime_logs/trainer_mirror/`) by
  `src/units/db/trainer_store.py` (idempotent, lazy + mtime-gated). Kept
  separate from the money DB so ingest never contends with the live
  trader. The `/api/bot/ml/*` and `/api/bot/backtests/sweeps` file-based
  endpoints remain; the sidecar makes the same data SQL-queryable.

### Number provenance — is this value MEASURED or MANUFACTURED? (2026-07-30)

**One module owns this: [`src/runtime/provenance.py`](src/runtime/provenance.py).** Import
it; do not re-derive the vocabulary and do not add another bespoke `exclude_*` predicate
(four already exist in `src/web/api/_clean_trades.py`, one per past incident, and they
collectively still missed the general case).

Buckets: `MEASURED` (a broker fill / recorded exit) · `ESTIMATED` (a defensible
reconstruction, e.g. a bar anchored to `closed_at`) · `FABRICATED` (synthesised with no
anchor to the close — a mark read at an arbitrary later time, a proration) · `UNVERIFIED`
(no provenance recorded — **never** folded into `MEASURED`). `is_measured()` is strictly
binary; `coverage()` is the PnL analogue of the `rCoverage` pattern `/performance` already
uses correctly for R (*"transparency, never a raw-pnl fallback"*); `require_measured()`
raises rather than quietly averaging manufactured numbers.

**Why it exists.** The journal already recorded provenance and **nothing read it** —
`exit_price_source` written in 12 files, branched on in one (for an unrelated value), zero
references in the whole `ml/` tree. It produced a "−$6,358 Bybit scalp exit leak" that did
not exist. Every contributing component was individually correct, which is why
line-by-line audits kept returning clean: the defect lives at the seams. Full account:
`docs/sprint-logs/S-PROVENANCE-EXITLEAK-ROOTCAUSE-2026-07-30.md`.

**ALWAYS STATE THE POPULATION.** *(Promoted 2026-07-31 to a TOP-LEVEL binding
rule covering every quantitative claim in any artifact —
`docs/CLAUDE-RULES-CANONICAL.md` § "Always state the population"; what follows
here is the PnL-provenance instance that motivated it.)* Measured against the live journal on 2026-07-30
(`scripts/ops/provenance_exposure_audit.py`, trainer-diag #8073) the headline figure moves
by more than its own SIGN depending on which rows you count:

| population | rows | fabricated | fabricated PnL |
|---|---|---|---|
| **closed, non-backtest, `pnl NOT NULL`** — the decision population | 829 | 206 | **−$36,018.60** |
| any status, incl. backtest | 845 | 222 | **+$247,683.78** |

Both are correct. The widely-quoted **+$247,683.78 is the ALL-STATUS figure**, and it is
dominated by **4 `orphaned` `ib_paper` rows carrying +$284,084.92** — a stale mark times a
futures multiplier on rows that appear in neither Positions nor Trades. Restricted to rows
any consumer actually aggregates, the fabricated total is **negative** and concentrated in
**`bybit_1`** (152/323, 47.1%, −$18,125) and **`bybit_portfolio`** (11/12, 91.7%,
−$13,100); `ib_paper` closed rows are 3 of 27. What reproduces across both populations is
the **trend**: fabricated share of closed trades 0.0% (May) → 23.7% (Jun) → **65.3% (Jul)**.

A headline whose sign flips on a filter choice is exactly the kind of number this module
exists to stop trusting — including when it is ours. Quote the population or don't quote
the number.

**`pnl` provenance needs BOTH keys.** `pnl_source` alone is nearly information-free in
practice (live: only `(none)` ×576 and `local_compute` ×253), so keying coverage on it
reports 0.0 for every window including the (then) 504 rows whose exit price is genuine broker
truth. Use `provenance.classify_pnl(row)`, which takes the **worst recognised** bucket
across `pnl_source` + `exit_price_source` — `local_compute` describes the arithmetic, not
the evidence. Live coverage on that basis was **504/829 = 60.8% measured**, 206
fabricated, 119 unverified — *measured 2026-07-30, and BOTH of its terms have since
moved; do not re-quote it as current.*

⚠️ **RE-MEASURED 2026-08-24, same population definition (closed, non-backtest,
`pnl NOT NULL`), n = 1151: `494 measured / 235 estimated / 200 fabricated / 222
unverified` — coverage 494/1151 = 42.9%.** The drop from 60.8% is NOT a
degradation in what the venue told us; it is two separate things and conflating
them is the trap:

1. **The population grew 829 → 1151** (+322 rows of ordinary trading).
2. **The classifier changed.** `recorded_exit_price` (98 rows here) and `verdict`
   moved MEASURED → ESTIMATED on 2026-08-24 (Tier-2, operator-approved). Held at
   the old classification the same population reads 592/1151 = 51.4%, so the
   reclassification accounts for ~8.5pp and the population growth for the rest.

**`recorded_exit_price` was never broker truth.** It outnumbered every genuine
broker-truth source COMBINED (measured that day: 82 of 531 closed rows against 79
for `exchange_fill` + `bybit_closed_pnl` + `ib_execution`), all of it
`local_compute` with zero `close_fees_usd`, and 67 of the 82 came from
monitor-derived paths where the price is the bot's own declared level rather than
a fill. Real-money coverage barely moved across the change (25/37 = 0.676), which
is the check that genuine broker truth was left alone. Root cause fixed in the
same change: `order_monitor` overwrote `exit_price_source` **unconditionally**,
stamping a projection over a more specific existing stamp. Backlog:
`BL-20260824-RECORDED-EXIT-PRICE-OUTNUMBERS-ALL-BROKER-TRUTH-COMBINED`.

**Enforced by `provenance-consumer-guard`** (`scripts/check_provenance_consumers.py`) —
CI fails when a declared provenance key gains a writer but no consumer, the same shape as
`canonical-db-resolver` / `env-gate-guard` / `silent-empty-guard`. A signal that is written
and never read is worse than a missing one: reviewers see the field and assume something
acts on it.

**A confirmed close is anchored to its `closed_at`, NEVER to a live mark** (Tier-2,
operator-approved 2026-07-30). `order_monitor._sweep_local_pnl_for_unpriced` used to price a
trade that had *already closed* from `last_mark_price()` — the market at SWEEP time — which
is the single source behind the fabricated totals above (matched-pair proof: trade 4180 −$4.00
vs its mirror 4181 −$2,589.78, same strategy/symbol/bracket/minute). It now calls
`src/runtime/exit_anchor.py::bar_close_at`, whose **three-way status is the contract** —
`anchored` (stamp `candle_at_close`, ESTIMATED) · `deferred` (budget spent or a transient read
failure: **we did not look**, so retry, never declare) · `no_anchor` (venue asked and has
nothing: declare `UNMEASURED_MARKER`, never substitute a price). Collapsing any two of those
reintroduces a defect. Runtime bounds are load-bearing, not decoration — this runs on the live
trader's monitor tick, so an unbounded per-row fetch is the 2026-06-09 cold-start wedge shape:
5s per-call timeout, a per-tick budget (`EXIT_ANCHOR_FETCHES_PER_TICK`, a tuning knob whose `0`
**defers** rather than re-enabling fabrication), and positive **plus negative** caching so an
unsupported root costs one request per process, not one per row per tick.

**IBKR is a broker-truth reader now**, because the anchoring change alone would have made IB
<!-- population-ok: moved VERBATIM from CLAUDE.md on 2026-09-21 by the operating reset. The claim and its missing population are both PRE-EXISTING and unchanged — this is a file move, not a new assertion, and inventing a denominator here would be worse than recording that nobody stated one. -->
*worse-looking-but-honest* rather than correct: **IBKR historical-candle coverage is 0%**, so
every future IB close would land as a declared gap. `interactive_brokers` is in
`BROKER_PNL_READER_EXCHANGES`; `exchange_fills_ib.closed_pnl_from_fills` reads IBKR's own
`CommissionReport.realizedPNL` back from the exchange-fills store — a **local SQLite read, not
a broker call** — fed by `ict-ib-executions-pull.timer`. That timer is **hourly**: IBKR's
`reqExecutions` serves roughly the current trading day AND `_LOCAL_PNL_BROKER_DEFER_MS` is 6h, so a
daily pull would look correct and be inert. ⚠️ **This row used to say "hourly, not daily like
`ict-exchange-fills-pull`" — that contrast is GONE as of 2026-08-21 and must not be re-quoted.**
The reasoning was right and had simply never been carried across to Bybit; `ict-exchange-fills-pull`
is now hourly too (Tier-2, operator-approved 2026-08-21). Leaving the old wording would read as if
the Bybit sibling is still daily, which is the dangerous direction. What the daily cadence actually
cost, measured before the flip: real-money trade 4863 crossed its declared take-profit and was
booked at `candle_at_close` because the store held no `bybit_2` fill later than 2026-08-20T13:36Z,
and paper `pnlCoverage` had fallen 0.3668 lifetime → 0.1053 (7d) → 0.0625 (24h)
(`BL-20260821-ICTSCALP-TP-CROSSED-BOOKED-AS-ESTIMATE`).

**A broker closed-pnl record carries its own `source`; never stamp a literal.** All four
monitor sites that persist a broker close hardcoded `exit_price_source = "bybit_closed_pnl"` —
accurate while Bybit was the only reader, a provenance *lie* the moment IBKR was wired. Read
`rec["source"]` via `order_monitor._broker_pnl_source`. Related: any `*_prorated` source is
FABRICATED (`classify` handles it as a suffix, since the base varies per reader) — the SPLIT is
an assumption about attribution however measured the underlying record was.

### Diagnostic provenance — does the OUTPUT say what it actually computed? (2026-07-30)

The sibling of the number-provenance rule above, one level up: that one asks
whether a **stored value** is measured or manufactured; this one asks whether a
**human-facing diagnostic** states the derivation of what it printed.

**The class — UNPROVENANCED DIAGNOSTIC OUTPUT.** A tool reports a value under a
label that does not describe what it computed, and nothing in the output reveals
the substitution. The number is real, the label is confident, and a reader who
trusts the label reaches a confident *wrong* conclusion. Three sub-classes:

| | Shape | Canonical instance |
|---|---|---|
| **A** semantic substitution | the label names quantity `Q`; the code called accessor `f()`; `f() ≠ Q` | `max(proba)` printed as `P(volatile)` — **inverted**, a 97%-CALM head reads as saturated-volatile (`BL-20260730-PARITY-PROBE-MISLABELS-MAXPROBA`) |
| **B** implicit input selection | newest / alphabetically-last / a function **default** substituted for the declared or pinned input | `sorted(glob(...))[-1]` labelled "TRAINING dataset"; `market_features` defaulting `vol_threshold=0.003` while the canonical builder passes `0.005` — two different `regime_label` definitions, nothing marks which |
| **C** unasserted denominator | an empty / zero / truncated result reads as a clean negative | a `curl … \|\| echo '{}'` poller turning HTTP 403 into `0 checks`; "every audited symbol is fully SL-covered" printed over a 444.7% over-coverage |

A **failure message that names a cause no code path tested** is the A-variant
that bites hardest (a diag relay blaming "VM down" for a request that never
reached the VM) — fix it by branching on the actual failure *stage*, not by
rewording the label.

**Enforced by `diagnostic-provenance-guard`**
([`scripts/check_diagnostic_provenance.py`](scripts/check_diagnostic_provenance.py))
over `scripts/{ml,research,ops,macro,reports}/` and the guard scripts — the same
family as `canonical-db-resolver` / `env-gate-guard` / `silent-empty-guard` /
`provenance-consumer-guard`. ⚠️ **This row read "Diff-scoped in CI
(pre-existing sites are grandfathered); `--all` is the standing audit" until
2026-09-02, and BOTH halves are now false.** The guard runs a diff-scoped step
AND an **ungated whole-tree `--all` step** (the `api-tier-policy-guard`
pattern), so nothing is grandfathered and there is no separate standing audit
for anyone to forget to run — which is exactly what happened: the residue sat
at **exactly 52 findings for 26 days** across five review passes, because a
diff-scoped guard cannot see a site regress when an unrelated PR adds the
probability-shaped LABEL or deletes the `print` that made an input selection
visible three lines away. Drained to **0** on 2026-09-02 (measured, not
asserted: the command prints `diagnostic-provenance: OK`), which is what made
the ungated step survivable — before that it would have failed every PR on day
one. ⚠️ **Its `# inert:` override is now VERIFIED, not presence-only**: the
marker must NAME the parameter it excuses. Tightening it immediately exposed 11
markers across 4 files that named nothing while the tree reported OK.

### Collapsed states — can this field say "we did not look"? (2026-08-09)

The third member of this family, one level up again: number provenance asks
whether a **stored value** is measured; diagnostic provenance asks whether an
**output** states its derivation; this asks whether a **field can express the
state that matters**.

**The rule is canonical in
[`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) §
"Collapsed states"** — read it there, it is not restated here. The short form:
when a field encodes a condition, ask whether *"we did not look"* and *"we
looked and found nothing"* are distinguishable; if not, that is the bug. Five
instances in two days across two concurrent sessions (#8665 exposure ceiling ·
#8666 netting allowlist · #8667 pairs `half_open` · #8685 cost basis · #8687
coverage `shipped`), while the remedy sat correctly implemented in exactly one
module — [`src/runtime/exit_anchor.py`](src/runtime/exit_anchor.py)'s
`anchored`/`deferred`/`no_anchor`.

**Enforced by `collapsed-state-guard`**
([`scripts/ci/check_collapsed_states.py`](scripts/ci/check_collapsed_states.py)):
per declared contract, the producer must emit every state, every state must be
branched on by a real consumer, and no consumer may see only one. Its override
is **verified, not presence-only**, and registering a contract in its
`CONTRACTS` table is how a new three-state field becomes enforced.

Note the split with **`silent-empty-guard`**: that guard catches the *producer*
(a broad `except` returning `[]`); sub-class **C** catches the *consumer*
(reading `[]` as a clean, labelled answer). Neither covers the other.

**The override is verified, not presence-only.** `# provenance: <accessor> —
<meaning>` must name an identifier that actually appears in the file, and the
annotation is excluded from its own evidence. This is the direct lesson from
`new-table-wiring-guard`, whose presence-only `# data-wiring:` marker made the
cheapest way to silence a real finding *naming a table that does not exist* —
a guard that is cheaper to lie to than to satisfy is worse than no guard.

**One module owns "what is the shadow log's `score`?"** —
[`scripts/ml/_regime_score_semantics.py`](scripts/ml/_regime_score_semantics.py).
`score` is `ShadowPredictor.predict` → `wrapped.predict(row)`: P(positive) for a
binary head, `max(proba.values())` for a multiclass one. Every regime head is
multiclass. The live gate reads `predict_proba(row)["volatile"]` — a diagnostic
claiming to say anything about the gate must report *that*. Import the module;
do not re-derive the answer per probe (two probes re-derived it independently and
both got it wrong on the same day).

