# Post-value-exhaustion research pivot — two-track workplan (2026-07-27)

**Status:** ACTIVE. Anchor `MB-20260727-POST-VALUE-PIVOT`. Operator-endorsed
2026-07-27 ("I like both of those directions").

## Why this exists (one paragraph)

The M28 value sleeve is **cross-gate-conclusively exhausted** (ledger entry 16,
PR #7777): the full construction space — D1 transform · D2 conditioning incl.
regime · D3 cross-section · D4 composite · D5 horizon — was graded through BOTH
arbiters (S2/S3 horizon-IC and the P4 net-of-cost lifecycle gate) and produced
**zero cost-surviving edges**. That closes value alongside COT, crypto-funding,
implied-vol-FRED, credit/rates, and calendar seasonality — the whole free-FRED
daily-bar macro class is a *rigorously established boundary* with one narrow
non-deployable lead (`vix_term`). This workplan is the two directions the
program pivots to, both operator-endorsed. It **sequences + prioritizes** the
already-written detailed plans; it does not re-derive them.

This is the `RESEARCH-RIGOR-STANDARD.md` funnel applied to two genuinely
new **input classes** — the one lever the exhausted program never varied enough
(it varied *construction*; these vary the *input*).

---

## Track 1 — Higher-frequency microstructure off existing feeds (PRIMARY, unblocked)

**Home:** M30 (quant-research platform) + M36 Track D (the integration backbone).
**Tier-1, observe-only, no new cost. Actionable now.** This is the primary
near-term thread precisely because it needs nothing from the operator.

**Where it stands (verified):** the M30 platform is **built + validated + merged**
(C1 panel builder + C2 toolkit under purged/embargoed WF-CV + BH-FDR, cohort-
disciplined). The load-bearing prior: strategy **entries are ~coin-flip OOS**
(Study 7, ict_scalp 282 trades → clean null; the M18 coin-flip prior) — **edge
lives in exit-timing and regime/session conditioning, not entry.** The decisive
pivot already made: discovery runs on the **backtest engine (large N + native
candle path)**, not the row-starved ~376-row live journal; live/paper are
validation. The `build_backtest_panel.py` bridge (per-simulated-trade C1-schema
panel with native MFE/MAE/giveback excursions) exists.

**The next work, in priority order** (the turnkey ordered list + read-first
docs are the **M30 deep-research session prompt**,
[`M30-deep-research-SESSION-PROMPT.md`](M30-deep-research-SESSION-PROMPT.md) —
run studies VM-side on the trainer via the `trainer-vm-diag` relay against the
live `trade_journal.db`):

1. **P1 — C2 `--features` selector** (small, do FIRST) → unblocks the pooled
   common-core panel → **Study 3** (where `feat_model_score_mean` gets its OOS test).
2. **Exit-timing + regime/session-conditioned studies** — the real targets, per
   the "edge lives in exit/regime" prior. **P5 per-bar panel** is the exit-timing
   enabler; **P4 wider decision-time capture** (killzone/session from
   `order_packages.meta`) is the binding coverage gap Study 2 found.
3. **P2 per-strategy sweep driver** → self-serve coverage across every powered book.
4. **P3 hypothesis→backtest bridge** — route a confirmed feature into the standing
   `backtest_system.py` walk-forward gate.

**M36-D integration angle (what makes this more than a study mill):** every
panel/feature/result M30 produces **feeds the macro sleeves where relevant AND
especially the M16 conviction master model** (`conviction`/`conviction-meta-v1`),
so the whole system trains on the fullest picture. Design of record:
[`m30-to-m16-integration-backbone-DESIGN.md`](m30-to-m16-integration-backbone-DESIGN.md).

**Decision gate (unchanged, standing):** a "finding" survives **BH-FDR AND shows
positive OOS discrimination under purged WF-CV.** In-sample coefficients are never
a gate. A survivor is a *lead* → route to the net-of-cost walk-forward gate → live
only via **Tier-3 operator approval**. Every study (edge OR null) lands in
[`technical-quant-research-ledger.md`](technical-quant-research-ledger.md) — a
faithful null is the compounding asset.

**Honest scoping (recorded, not hidden):** true tape/book order-flow has **no free
historical feed** (M30's own finding — Bybit `recent-trade` is a short rolling
window, orderbook is snapshot-only). So the tractable free route is **intrabar
OHLCV shape + exit/regime structure on the backtest engine**, a real information
step-up over daily bars but not full order-flow. If a study shows the ceiling is
data-granularity (not method), that itself is the signal to escalate to Track 2 /
a paid tape feed — recorded as a decision, never a silent stall.

---

## Track 2 — Schwab options-implied skew (PARALLEL, one operator hand-off)

**Home:** M31 Track B. **Credential-free pipeline is already BUILT + offline-tested
on `main`** (`iv_skew_probe.py` skew features + `schwab_chain_adapter.py` vendor
normalization, 18 tests). This is a genuinely **forward-looking** input class
(positioning/fear priced into options) and it **overlaps live instruments**
(SPY/QQQ/DIA → the MES/MNQ legs; GLD → the gold cell FRED left blank).

**The one operator hand-off — the ONLY thing blocking this track:**
1. Register the **Schwab developer app** (Trader API product) at
   developer.schwab.com (~1–3 business-day approval; individual-developer tier is
   free and includes option chains + Greeks).
2. Add the **app key + secret** to repo Actions secrets as **`SCHWAB_APP_KEY`** /
   **`SCHWAB_APP_SECRET`** (I pre-create the empty secret slots via
   `init-actions-secrets` so you paste into existing slots), then do the one-time
   browser OAuth to mint the first refresh token.
3. **Standing operational cost (flagged, accepted):** the Schwab refresh token
   **expires every 7 days** → a weekly browser re-auth. Fine for a periodic
   research soak; a real recurring touch only if it's ever productionized to a
   live feed. Schwab is US equities/ETF/index/options only — no crypto.

**Turnkey build order once the token lands** (fully specified in
[`M31-track-b-soak-plan.md`](M31-track-b-soak-plan.md) — plug-and-play, no
re-design): OAuth token module → `iv_skew_soak.py` accumulator (offline-testable
now via injected `http_get`) → daily snapshot job (the timer is wired **only when
the secret exists**, never a workflow that fails every run — the normalized-alarm
anti-pattern) → the honest **S2 → S3 → S4-prep walk-forward** grader on the accrued
history, same rigor as Track A.

**The questions it answers** (why it's not a VIX re-run): does the equity-vol-term
effect **reproduce** on the SPY chain (validation control) and **generalize** to
QQQ/DIA (the free-FRED gap — VXN/VXD have no FRED 3-month sibling)? And it mines
**richer skew** (25Δ risk-reversal, butterfly, smirk slope) the FRED VIX family
cannot express — a real new construction dimension.

**Honest accrual caveat (recorded up front):** option chains are point-in-time, so
the soak IS the history — it accrues ~1 obs/session-day, and the first honest
S2/S3/WF read at tradeable horizons (H=5/10/21/42d) is **~1 quarter out**, not
days. This is a **slow-accrual parallel experiment**, which is exactly why it runs
alongside (not instead of) Track 1.

---

## Sequencing — how the two interleave

| | Track 1 (microstructure) | Track 2 (Schwab skew) |
|---|---|---|
| **Blocked on** | nothing | the one operator hand-off (Schwab app + 2 secrets) |
| **Starts** | **now** (next session) | when the secret lands |
| **Cadence** | active study loop (days per study) | slow soak (~1 quarter to first read) |
| **Cost** | none | free data; weekly re-auth touch |
| **Tier** | 1 (live only via the standing WF gate + Tier-3) | 1 (same) |

**The plan:** Track 1 is the active thread — start it next session and work the
M30 study queue exit/regime-first. Track 2 is dormant until the operator registers
the Schwab app; the moment the secret exists, I build the soak accumulator + wire
the daily job (I pre-create the secret slots so the paste is trivial), then it
accrues in the background while Track 1 keeps producing. Neither blocks the other.

## Definition of "done" for the pivot

Same as every honest funnel: **either** a construction clears the standing
net-of-cost walk-forward gate OOS (→ a Tier-3 productionization proposal), **or**
the input class is mapped and shown to carry no cost-surviving edge — and **both
are recorded results** in the relevant ledger. "We tried a couple of studies and
stopped" is neither. If Track 1's microstructure ceiling turns out to be
data-granularity, that finding is itself the trigger to lean on Track 2 / propose a
paid tape feed (operator-gated) — surfaced as a decision, never a silent stall.

## The one operator action (everything else is autonomous)

**Register the Schwab developer app and add `SCHWAB_APP_KEY` / `SCHWAB_APP_SECRET`
to Actions secrets** (Track 2's unblock). That is the single hand-off in this
entire workplan. Track 1 needs nothing — it starts next session.
