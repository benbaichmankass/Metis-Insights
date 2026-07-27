# M30 — Deep quant-research session prompt (paste-ready)

> **How to use:** paste the block below into a fresh deep-research session to
> start. The M30 platform is **built + validated** (2026-07-27); this session
> *uses* it to find + develop strategies, finishing the small self-serve
> loose-ends first. Anchor `MB-20260726-M30-QUANT-RESEARCH-PLATFORM`.

---

You are running an M30 **deep quant-research** session on the Metis-Insights
trading bot. The technical quant-research platform is already built, validated,
and merged — your job is to **use it to discover feature→outcome edge**, and to
finish the few small self-serve loose-ends that make it turn-key. Tier-1
observe-only research throughout; nothing goes live except via the existing
net-of-cost walk-forward gate + explicit Tier-3 operator approval.

**Read first (do not re-derive):**
- `docs/research/technical-quant-research-platform-scoping-2026-07-27.md` — the platform spec.
- `docs/research/technical-quant-research-platform-validation-2026-07-27.md` — the go/no-go verdict + the P1–P6 loose-ends inventory + defect status.
- `docs/research/technical-quant-research-ledger.md` — the compounding record (Studies 0/1/2 done; queued studies + the P-item dependencies).

**What already works (validated by execution + audit):** C1 panel builder
(`scripts/research/build_research_panel.py`), C2 toolkit
(`scripts/research/analyze_research_panel.py` — conditional-edge + logistic/OLS
regression + permutation importance + VIF, under purged/embargoed WF-CV +
BH-FDR; cohort-disciplined so real+paper are never blended), the ledger, and the
L3 paper-book eval population (`event_source='live_paper'`, breaks the ~376-row
wall). Guards proven to bite (signal recovered OOS, null rejected, leakage
hard-refused). Run studies **VM-side on the trainer** via the `trainer-vm-diag`
relay against the live `trade_journal.db` (the sandbox has no live DB); use the
trainer venv python for numpy.

**Do, in order (each its own PR; analysis-scripts/docs/ledger merge on green;
anything touching `src/` runtime → draft for operator review):**

1. **P1 — C2 `--features` selector (small, do FIRST).** Add a `--features`
   arg to `analyze_research_panel.py` restricting the multivariate fit to a
   chosen dense subset, so the **pooled** panel (block-sparse by strategy) can
   finally run regression/importance/VIF on the strategy-agnostic dense columns
   (`feat_confidence`, `feat_model_score_*`, `cat_regime`, `feat_adx_14`). This
   unblocks **Study 3** (common-core pooled) — where `feat_model_score_mean`
   gets its OOS test (it was a lead in Study 1).
2. **Study 3 — common-core pooled panel** (run once P1 lands). Ledger entry.
3. **P4 — widen C1 decision-time feature capture**, esp. **killzone/session**
   (persisted in `order_packages.meta`, not yet extracted by C1) + bias
   direction. This is the binding gap Study 2 found (even the dominant `vwap`
   book instruments only 2 graded features). `src/research/component_vector.py`
   + reading `order_packages.meta` in `build_research_panel.py`. Draft if it
   touches `src/`.
4. **P2 — per-strategy sweep driver** (medium): a script that runs C1→C2 across
   every strategy above a power floor + pools thin books by asset class →
   self-serve coverage instead of hand-running each.
5. **First real study targets** (from the scoping's ranked list + the
   load-bearing prior that entries are ~coin-flip OOS — edge lives in
   exit/regime): **exit-timing** and **regime/session-conditioned** studies.
   The per-bar panel (P5, large) is the enabler for exit-timing — build it when
   you reach that study.
6. **P3 — C3 hypothesis→backtest bridge**: wire a confirmed feature into
   `scripts/backtest_system.py` / the per-strategy walk-forward harness so a
   discovered edge can be routed to the standing gate. (P6 SHAP is a low-priority
   interpretation nicety.)

**Data rule:** build on existing data first (`trade_journal.db`,
`market_features`/`market_raw`, candles); L3 now admits paper-book eval volume.
If a study genuinely needs a NEW external source, STRONGLY prefer keyless/free —
never sign up for a key; recommend a key-gated source to the operator instead.

**Bar for a "finding":** survives BH-FDR **AND** shows positive OOS
discrimination under purged WF-CV. In-sample coefficients are never a gate. Log
every study (edge OR null) in the ledger — a faithful null is the compounding
asset.
