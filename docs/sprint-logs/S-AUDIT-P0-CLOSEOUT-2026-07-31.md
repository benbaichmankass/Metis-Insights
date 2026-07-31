# S-AUDIT-P0-CLOSEOUT-2026-07-31 — full-system-audit P0 execution (provenance loop closed)

## Date Range
2026-07-31 → 2026-07-31 (single session; continuation of the audit session that merged #8178)

## Objective
**Primary:** execute the P0 tier of `docs/audits/full-system-audit-2026-07-31.md`
("close the poisoned-number loop"): P0.1 read-side trusted-PnL filter, P0.3
3-repo provenance consumer surfacing, P0.4 the authored-cell re-audit register.
**Secondary:** keep P0.2 (operator decision on PR #8163) surfaced, not decided —
it is the operator's call.

## Tier
Tier 1 throughout — read-path/API additive fields, consumer rendering, tests,
docs, skills, backlog. No order-path, config, or live-service file touched.
(The P0.1 dataset/gate filters affect *research/promotion inputs*, not live
routing; the promotion gate itself remains operator-gated.)

## Starting Context
- `docs/audits/full-system-audit-2026-07-31.md` merged (#8178) with the P0–P3 plan.
- Prior session's P0.1 branch `claude/p0-provenance-read-side` ready.
- Known risk: consumers silently drop unknown API fields, so P0.3 had to land
  bot-side field + both clients as a coordinated set.

## Repo State Checked
- Metis-Insights `main` at `dfe1a20d` (start) → `aa09bd1a` (post-#8180).
- ict-trader-dashboard `main` (fresh fetch; branch re-rooted from it).
- ict-trader-android `main` (fresh fetch; branch re-rooted from it).
- Canonical docs re-read: CLAUDE.md § provenance, the corrected-cost re-grade
  doc, `BL-20260730-AUTHORED-CELL-REAUDIT-REGISTER` backlog row.

## Files and Systems Inspected
- `src/runtime/provenance.py` (classify_pnl/classify_row/pnl_is_trustworthy — full read)
- `src/web/api/routers/trades_closed.py`, `src/web/api/routers/performance.py` (full read)
- `tests/test_web_api_trades_closed.py`, `tests/ml/test_promotion_cli.py`, `tests/test_performance_pnl_coverage.py`
- `config/regime_policy.yaml` (all authored cells, both axes)
- `docs/research/regime-debt-matrix-corrected-cost-2026-07-30.md` (incl. the A1–A5 addendum)
- `docs/claude/health-review-backlog.json` (register + feed-sensitivity + blocker rows)
- Dashboard: `streamlit_app.py` (`page_trades`, `_render_exec_summary`, `_format_closed_trades_df`), `webapp/src/{lib/api.ts, routes/Trades.svelte, components/ExecSummary.svelte}`
- Android: `core/network/BotApi.kt`, `feature/trades/TradesScreen.kt`, `feature/performance/PerformanceScreen.kt`, `.github/workflows/release.yml` (PR-trigger check)

## Work Completed
1. **P0.1 MERGED (#8179)** — `pnl_is_trustworthy` read-side filter default-on
   across promotion attribution, regime-alignment calibrators, setup_candidates
   (M23 holdout/EV), conviction_meta, research panel, strategy-review packets
   (`n_closed_untrusted_pnl` reported); loud population-change logging; tests
   prove each filter fires. One CI round-trip: the filter correctly excluded the
   promotion-CLI fixture's NULL-notes row (`bd81dd1b` stamped measured
   provenance in the fixture).
2. **P0.3 bot half MERGED (#8180)** — per-row `pnlProvenance` on
   `/api/bot/trades/closed` (null when `realizedPnl` null); CLAUDE.md API table
   updated for it AND for the previously-undocumented `/performance`
   `pnlCoverage`/`pnl*Count` fields (doc drift).
3. **P0.3 dashboard half — PR ict-trader-dashboard#203 (draft, CI running):**
   Streamlit Trades "PnL source" glyph column + unmeasured caveat; exec-summary
   coverage caption; Svelte SPA typed fields + row marker/tooltip + captions.
4. **P0.3 android half — PR ict-trader-android#115 (draft, CI running):**
   nullable DTO fields; trade-card "PnL source" row + inline ⚠/? marker; list
   caveat; Performance coverage caption.
5. **P0.4 DONE (this commit)** — standing
   `docs/audits/authored-cell-reaudit-register.md`: every authored cell (7
   strategies 1-D + 3 strategies 2-D) with authoring evidence, fidelity at
   authoring, C1–C4 defect-class exposure, last verdict, next due. Cadence
   owner wired: `/system-review` SKILL.md gains a **mandatory weekly**
   `review_coverage.authored_cells` block. Backlog row
   `BL-20260730-AUTHORED-CELL-REAUDIT-REGISTER` → resolved.

## Validation Performed
- Bot: `tests/test_web_api_trades_closed.py` 43/43 (6 new provenance tests);
  `tests/test_performance_pnl_coverage.py` 9/9; full `pytest-run` green on both
  merged PRs (9,506 passed); ruff clean; `provenance-consumer-guard` OK.
- Dashboard: `svelte-check` 0 errors/0 warnings (124 files); `vite build`
  clean; `streamlit_app.py` `ast.parse` clean.
- Register: every referenced evidence doc verified to exist on disk; cell
  inventory transcribed from `config/regime_policy.yaml` at `aa09bd1a`, not
  from memory.
- **Gaps not yet verified:** (a) dashboard #203 + android #115 CI outcomes
  pending (android compile rides the PR's release.yml — no SDK in this
  container); (b) live dashboard render of the new column/captions is only
  verifiable post-merge per the dashboard repo's verify-live-on-prod workflow;
  (c) the new API field is on `main` but NOT yet observed on the live VM
  (lands with the next `ict-git-sync` deploy + web-api restart cycle — no
  restart was dispatched this session); (d) the first enforced
  `review_coverage.authored_cells` block will exist only when the next weekly
  /system-review runs.

## Documentation Updated
- CLAUDE.md API table (`/trades/closed` + `/performance` provenance fields).
- `docs/audits/authored-cell-reaudit-register.md` (new, standing).
- `.claude/skills/system-review/SKILL.md` (authored_cells coverage block).
- `docs/claude/health-review-backlog.json` (register row resolved with note).
- This sprint log; ROADMAP ledger row added this commit.

## Contradictions or Drift Found
- `/performance` pnlCoverage fields shipped 2026-07-30 but were absent from the
  CLAUDE.md API table — fixed in #8180.
- Test-fixture drift: `tests/ml/test_promotion_cli.py` seeded provenance-less
  rows — the exact population P0.1 exists to exclude; fixed by stamping
  measured provenance (the filter firing on it was correct behaviour).

## Risks and Follow-Ups
- **P0.2 remains an OPERATOR DECISION** — draft PR #8163 + the Tier-2
  relabel-only pass (~4% of fabricated exits recoverable; Jun 8–Jul 13
  permanently unverifiable).
- The 12 blocked cells in the register stay blocked on their three tool gaps
  (`BL-20260730-2D-VOL-CELLS-UNAUDITABLE`, `BL-20260730-SQUEEZE-NO-HARNESS`,
  `BL-20260730-DONCHIAN-APPROX-ONLY`) — the register makes the blockage
  visible weekly instead of silent.
- The `gld_pullback_1h` C3 second-feed re-tag is the one outstanding
  shipped-cell re-grade (tracked in the feed-instability backlog row).
- Consumer PRs (#203, #115) must merge for P0.3 to be complete — watched by
  this session with an armed check-in.

## Deferred Items
- P1 (trainer honesty), P2 (enforcement coherence), P3 (hygiene) tiers of the
  audit plan — each scoped as its own session in the plan.
- Dashboard live-render verification (post-merge step of #203).

## Next Recommended Sprint
**P1 — trainer honesty** (vt004 ManifestDatasetMismatch, vol_threshold-less
dataset dirs, outcome-family starvation, disk, rc=0 ambiguity): it is the
next tier in the operator-approved plan, self-contained, and mostly Tier-1/2
trainer-side. Required verification: trainer relay reads before/after each fix.

## Wrap-Up Check
- [x] Code inspected directly (all touched files read in full; cells
  transcribed from config, not comments)
- [x] Docs reviewed/updated (CLAUDE.md table, register, SKILL.md)
- [x] TRADE-PIPELINE untouched — no pipeline stage changed
- [x] Roadmap ledger row added (this commit)
- [x] Contradictions recorded (§ above)
- [x] Unknowns stated (§ Gaps): consumer-PR CI pending, live deploy unobserved
