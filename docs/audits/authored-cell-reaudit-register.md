# Authored-cell re-audit register (STANDING — update in place)

**What this is.** The durable per-cell register for every authored cell in
`config/regime_policy.yaml`, per `BL-20260730-AUTHORED-CELL-REAUDIT-REGISTER`
(operator-directed: *a decision is not permanent evidence*) and P0.4 of
[`full-system-audit-2026-07-31.md`](./full-system-audit-2026-07-31.md). Every
cell records the evidence it was authored from, that evidence's **fidelity at
authoring time**, its exposure to the known evidence-defect classes, the
**last re-audit verdict**, and the **next due date**.

**Keying rule (learned the hard way):** rows key off the CELLS in
`config/regime_policy.yaml` — never off `coverage_debt`, which is a work queue
that ERASES an item when it is acted on (authoring the `gld_pullback_1h` cell
paid the strategy out of the matrix roster, which is exactly how a ~25×
fee over-charge on a live Tier-3 gate stayed invisible:
`BL-20260730-REGIME-CELL-UNAUDITABLE`).

**Cadence owner: `/system-review` (weekly window).** The weekly review's
`review_coverage.authored_cells` block (added alongside this register) fails
the run if any live-affecting cell is past its due date with no recorded
verdict. Editing a cell remains **Tier-3** — this register never changes
routing; it tells the operator when a cell's evidence has expired.

**Update discipline:** a re-audit run appends/overwrites the cell's *Last
verdict* + *Next due* in place and links the evidence doc. Do not fork a
dated copy — the register's value is being the ONE place a cell's evidence
age is visible.

---

## Evidence-defect classes (what can invalidate a shipped cell)

| Class | What it is | Detection status | Canonical record |
|---|---|---|---|
| **C1 venue fees** | `regime_debt_matrix` hardcoded `--fee-bps-roundtrip 7.5` for every symbol incl. all 14 commission-free `(alpaca, spot)` instruments — over-charge ⇒ **false OFF cells** (never a fabricated edge). BTCUSDT/Bybit rows were always charged correctly, so C1 exposure is venue-scoped. | FIXED in harness (#7944 `f67df73`); re-grades run 2026-07-30 | [`regime-debt-matrix-corrected-cost-2026-07-30.md`](../research/regime-debt-matrix-corrected-cost-2026-07-30.md) · `PB-20260730-REGIME-EVIDENCE-VENUE-FEE-REGRADE` |
| **C2 self-erasing queue** | Re-audit rosters derived from `coverage_debt` drop a strategy the moment its cell is authored — shipped cells become the ONE thing the matrix never re-measures. | FIXED (`resolve_strategy()` #7958 makes celled strategies measurable; this register is the roster now) | `BL-20260730-REGIME-CELL-UNAUDITABLE` |
| **C3 feed sensitivity** | Per-regime net-R is feed-unstable: byte-identical params + the same 357 trades re-tagged on two near-identical OHLC feeds moved single buckets by ±25R (hard ADX cut-offs × heavy-tailed R). A verdict that flips across feeds is not decision-grade. | Detection shipped (boundary exposure + feed sensitivity on `regime_tag_emitted`); **per-cell figures not yet threaded through the walk-forward gate** | health-review backlog `[regime] per-regime net-R is feed-unstable` row (2026-07-30) |
| **C4 fabricated PnL** *(journal evidence only)* | 0%→65% (May→Jul) of closed journal rows carried mark-substituted/unrecorded PnL. Affects any cell evidence sourced from journal PnL. **The regime matrix/walk-forward harnesses backtest from candles, so cells authored from harness runs are NOT C4-exposed**; C4 matters for any FUTURE cell argued from live journal aggregates. | Read-side filter default-on since #8179 (`pnl_is_trustworthy`) | `S-PROVENANCE-EXITLEAK-ROOTCAUSE-2026-07-30.md` |

---

## Register — 1-D cells (`trending` / `transitional` / `chop`)

Fidelity vocabulary is the harness's own: `faithful` · `approximate` (a
declared lever unmodelled — cannot source or un-source a cell, rec #5
no-cosmetic-cell rule) · `unmeasurable`.

Default cadence: **90 days** from the last faithful verdict for live-affecting
cells; cells on strategies with no live flow are re-audited when the strategy
re-enters live routing (re-entry = due immediately).

| Strategy (venue) | Cells (regime → cell) | Authored from | Fidelity @ authoring | Defect exposure | Last re-audit verdict | Next due |
|---|---|---|---|---|---|---|
| `gld_pullback_1h` (alpaca spot) | trending `{long:on, short:off}` | rec #5 walk-forward, [`regime-cell-walkforward-2026-07-29.md`](../research/regime-cell-walkforward-2026-07-29.md) (#7920, #7923) | faithful — but **C1-charged** (7.5bps on a commission-free venue) | C1 ✅ re-graded · C3 **OPEN** (Yahoo-sourced; single-feed verdict) | **2026-07-30 (#7962): SURVIVES** — trending short −13.88R@37 = −0.375 R/t (predicted −0.32..−0.40), long +37.18R@54. No revert. Corrected-cost doc § A1. | **C3 second-feed re-tag** (open half of the feed-sensitivity backlog row) — then 2026-10-28 |
| `htf_pullback_trend_2h` (bybit BTC) | trending `{long:off, short:off}` · transitional `{long:on, short:off}` · chop `{long:off, short:off}` | trending re-authored **2026-07-30 post-correction** (#7968 walk-forward, operator-approved); transitional + chop still from the 2026-06-01 matrix | faithful | C1 n/a (BTC always charged correctly) · **evidence age** on transitional/chop: 2026-07-30 re-measure read transitional short **+0.86** vs the authoring "−4" and chop long **−0.39** vs "−8" (chop short −4.24 still warranted) — full-sample reads, not walk-forwarded | **2026-07-30 (#7963/#7968): trending cell re-authored on fresh walk-forward** (long_stable_drag TRUE, pooled −6.85R). Transitional/chop drift NOTED, not actioned (#7915 regime-of-sample precedent: full-sample inversion is a candidate, never a verdict). | 2026-10-28 (trending); transitional/chop: **walk-forward pass when queued by /system-review** |
| `trend_donchian` (bybit BTC) | trending `{long:on, short:off}` · transitional `{long:on, short:off}` · chop `{long:on, short:on}` | 2026-06-01 matrix ([`regime-roster-matrix-2026-06-01.md`](../research/regime-roster-matrix-2026-06-01.md)) | **approximate** (harness omits `exit_head_*` + `trail_decay`) | C1 n/a · **BLOCKED**: approximate-only rows can be measured but not acted on (`BL-20260730-DONCHIAN-APPROX-ONLY`) · **cosmetic**: all three `short` halves gate zero trades — strategy runs long-only (`BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS`) | 2026-07-30 (#7963): measured `approximate` — **not actionable either way** | blocked on harness lever support; cosmetic-short cleanup is a separate Tier-3 proposal |
| `squeeze_breakout_4h` (bybit BTC) | trending `{long:on, short:on}` · transitional `{long:on, short:on}` · chop `{long:on, short:on}` | 2026-06-01 matrix | n/a at authoring (pre-fidelity vocabulary) | C1 n/a · **BLOCKED**: harness errors `unclassifiable` — no mapping exists (`BL-20260730-SQUEEZE-NO-HARNESS`). Note: all-`on` cells = permissive, so the un-auditable state gates nothing extra; risk is an un-catchable false ON. | 2026-07-30 (#7963): **errored, unmeasurable** | blocked on a harness mapping |
| `fade_breakout_4h` (bybit BTC) | trending `{long:off, short:off}` · transitional `{long:on, short:on}` · chop `{long:on, short:on}` | 2026-06-01 matrix | n/a (pre-fidelity) | C1 n/a · strategy is **shadow** (no live flow) — cells currently gate nothing live | never re-audited | on live re-entry |
| `fvg_range_15m` (bybit BTC) | trending/chop `{long:off, short:off}` | 2026-06-01 matrix (−17 lifetime) | n/a (pre-fidelity) | C1 n/a · **shadow** — no live flow | never re-audited | on live re-entry |
| `vwap` (bybit BTC) | all three regimes `{long:off, short:off}` | 2026-06-01 matrix (net −6,179/−1,903/−2,642) | n/a (pre-fidelity) | C1 n/a · **shadow** — no live flow; the no-edge-any-regime verdict was decisive at large n | never re-audited | on live re-entry |

## Register — 2-D `trend_vol` cells

| Strategy | Cells | Authored from | Defect exposure | Last re-audit verdict | Next due |
|---|---|---|---|---|---|
| `trend_donchian` | trending/volatile `{long:off}` · transitional/calm `{long:off}` · chop/calm `{long:off}` | Design-A vol-split, [`A-vol-gating-OFFcell-design-2026-06-27.md`](../research/A-vol-gating-OFFcell-design-2026-06-27.md) (walk-forward PASS 4/4) | **BLOCKED**: no tool can re-measure ANY 2-D cell — the matrix has no vol-split (`BL-20260730-2D-VOL-CELLS-UNAUDITABLE`); also C3-exposed (vol bucketing shares the hard-cut-off fragility) | never re-audited (unreachable) | blocked on a vol-split harness |
| `squeeze_breakout_4h` | trending/calm `{short:off}` | Design-A vol-split (2026-06-27) | same as above + no 1-D harness mapping either | never re-audited (unreachable) | blocked (both blockers) |
| `ict_scalp_5m` | trending/volatile + chop/volatile `{long:off, short:off}` | Phase-4 gate packet, [`ict_scalp_5m-phase4-regime-gate-PROPOSAL-2026-07-20.md`](../research/ict_scalp_5m-phase4-regime-gate-PROPOSAL-2026-07-20.md) (k-fold OOS +20.3R/+29.1R, 3/4 folds; operator-approved 2026-07-20) | **BLOCKED** (2-D unreachable); ict_scalp carries ONLY 2-D cells, so its entire live gating is un-re-auditable | never re-audited (unreachable) | blocked on a vol-split harness |

## Roll-up (2026-07-31)

- **16 live-affecting cells: 4 re-audited** (gld 1 — survives; htf 3 — trending
  re-authored, siblings drift-noted), **12 blocked** by three named blockers
  (no vol-split ×6 · no harness mapping ×4 · approximate-only ×3, one cell
  double-counted). The blockers are the real debt — each has a backlog row.
- **Open re-grades:** the `gld_pullback_1h` **C3 second-feed re-tag** is the
  one shipped-cell re-grade still outstanding (venue-fee re-grades completed
  2026-07-30).
- **Shadow-strategy cells** (fade/fvg/vwap, 8 cells) gate nothing live today;
  their due-dates arm on live re-entry.
