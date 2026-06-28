# S-AUDIT-F — pipeline / runtime (non-order-path) slice

**Session:** `session_01LGsKNEjcTeujEGVACSPESK-Fpipe` ·
**Branch:** `claude/audit-F-pipeline-runtime` · **Date:** 2026-06-28 ·
**Part of:** M17 Full-System Audit (`docs/audits/full-system-audit-2026-06-28.md`)

Disjoint slice from the order-path `-F` session (execute/order_monitor/coordinator/
intents/risk) and the prop-bridge `-F` session (`src/prop/*`). This slice is the
pipeline + runtime support code that is NOT the live order path.

## Files read IN FULL (line-by-line)

| File | Lines | Verdict |
|---|---|---|
| `src/runtime/pipeline.py` | 958 | clean logic; 1 comment drift (intent-layer default) |
| `src/main.py` | 736 | clean; 1 docstring drift (`_resolve_account_leverage` fallback) |
| `src/runtime/positions.py` | 323 | clean; 1 trivial docstring nit |
| `src/runtime/market_data.py` | 257 | clean |
| `src/runtime/heartbeat.py` | 148 | clean |
| `src/runtime/regime_bar_scoring.py` | 556 | clean; docs match code (Design-A publish path accurate) |
| `src/runtime/news_sizing.py` | 154 | clean |
| `src/runtime/exit_ladder_soak.py` | 228 | clean |
| `src/runtime/regime/__init__.py` | 41 | clean |
| `src/runtime/regime/detector.py` | 158 | clean |
| `src/runtime/regime/ml_vol_verdict.py` | 393 | module docstring drift (Phase-2 `use` is live, docstring said deferred) |
| `src/runtime/regime/policy.py` | 301 | clean |
| `src/runtime/regime/vol_detector.py` | 234 | clean |

## Method

Each file read in full; every candidate classified (real-bug / dead-code / drift /
latent-risk); verified before asserting (field beats comment; provenance via
`git log`; live wiring cross-checked by READING — not editing — the order-path
files). No behavioural (Tier-3) change proposed; the two real findings are
comment/docstring-only Tier-1 fixes.

## Findings

### F-PIPE-1 — DRIFT (Tier-1, FIXED) — ml_vol_verdict.py module docstring claimed use/enforce deferred, but they are LIVE

- Where: `src/runtime/regime/ml_vol_verdict.py` lines 1–18 (module docstring).
- Stale text: "Phase-1 (observe-only) … the gate DECISION still uses the frozen
  intent.vol_regime. Phase 2 (use) / Phase 3 (enforce) are deferred."
- Reality (verified by reading consumers): the module ships
  `ml_vol_regime_for_symbol` ("the decision-path resolver for Design-A", line 347);
  `intents._decision_vol_regime` (line 859) substitutes the ML vol label into the
  gate DECISION when `REGIME_ML_VERDICT_MODE == "use"`; `aggregate_intents`/enforce
  loop key the drop on it (lines 936/1072/1097–1099). Canonical CLAUDE.md: "use →
  substitute … into the gate DECISION (actually wired 2026-06-28)" + "BTC
  real-money enforce is LIVE (2026-06-28)".
- Provenance: commit `e0d052e7` (#4896) wired use/enforce AND added
  `ml_vol_regime_for_symbol` to this file but left the module header in its pre-use
  "deferred" wording. Field/code is truth → fix comment, not field.
- Tier 1 (docstring-only). FIXED in this PR.

### F-PIPE-2 — DRIFT (Tier-1, FIXED) — pipeline.py comment said intent multiplexer default is OFF; it is ON

- Where: `src/runtime/pipeline.py` ~lines 418–426 (run_pipeline builder selector).
- Stale: "Default is **off** so this change does not flip live behaviour on its own."
- Reality: `intent_multiplexer.intent_multiplexer_enabled` reads
  `os.environ.get("MULTI_STRATEGY_INTENT_LAYER","true")` (line 545); docstring
  "default flipped to **on** (2026-05-17)"; CLAUDE.md lists it "default on — the
  core intent-aggregation switch." The default builder IS the intent layer.
- Tier 1 (comment-only). FIXED in this PR.
- Sibling drift (outside slice): intent_multiplexer.py MODULE docstring (lines 7–9)
  also still says the legacy builder is the default → logged
  `BL-20260628-INTENTMUX-DOCDRIFT`.

### F-PIPE-3 — minor doc nit (logged) — main.py::_resolve_account_leverage docstring describes a fallback that does not exist

- `src/main.py` 238–262: docstring claims "Fallback: top-level `leverage` on the
  account object," but code only reads `account.risk_manager.leverage`;
  TradingAccount has no `leverage` (0 refs in account.py). Inline comment already
  states this honestly; behaviour correct. Logged `BL-20260628-LEVERAGE-DOCSTRING`
  (low value; kept out of this PR for one-concern hygiene).

### F-PIPE-4 — observation (no action) — positions.py current_net_position_qty TRADE_JOURNAL_DB note

- db_path docstring "TRADE_JOURNAL_DB takes precedence when None" is accurate
  (`trade_journal_db_path()` resolves env → $DATA_DIR → repo-root). Noted only.

### F-PIPE-5 — observation (no action) — exit_ladder_soak read_soak_records differing_pct

- With only_differing=True the summary aggregates the filtered set, so
  differing_pct degenerates to 100. Cosmetic; the dashboard doesn't rely on it.

## Liveness (Pass-2)

Every module in this slice is reachable + run on the live tick path (run_pipeline,
emit_regime_bar_predictions, market_data fetchers, write_heartbeat, positions
helpers, news_sizing hook, exit_ladder_soak, regime/*). No zombie / dead module.
No `*_ENABLED` Prime-Directive violation — the env gates present are
REGIME_BAR_SCORING_DISABLED (kill-switch), NEWS_INFLUENCE_MODE (*_MODE),
REGIME_ML_VERDICT_MODE / ML_VOL_VERDICT_THRESHOLD (*_MODE + threshold, Tier-3),
MULTI_ACCOUNT_DISPATCH / MULTI_STRATEGY_INTENT_LAYER (default-on opt-outs) — all
compliant shapes.

## No behavioural (Tier-3) changes proposed

No bug or latent risk warranting a runtime/sizing/routing change. The two fixes
are comment/docstring-only (Tier-1). Remaining items are on the health-review backlog.
