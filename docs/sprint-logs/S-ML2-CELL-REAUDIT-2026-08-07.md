# S-ML2-CELL-REAUDIT-2026-08-07 — ML2 2-D regime-cell re-audit + two diagnostic-provenance fixes

## Date Range

- **Start:** 2026-08-06 (continuation session; the ML2 item carried over)
- **End:** 2026-08-07 01:25Z

## Objective

- **Primary:** grade the six authored `trend_vol` OFF-cells in
  `config/regime_policy.yaml` against a walk-forward, and establish whether the
  replayed vol labels reproduce what the live gate actually used.
- **Secondary:** finish the carried-over items from the prior session (netted
  duplicate PnL marking; the CI guard consolidation) and drive PR #8553.

## Tier

**Tier 1.** Everything shipped this session is research tooling, docs, tests, or
backlog. The one commit touching `src/` (`6ea245d`, `intents.py`) is Tier-1 in
content — an added audit key, no decision path altered — but is deliberately
held in a **draft PR for operator approval** because it touches the live
order-routing file. No cell was changed; every cell verdict here is a Tier-3
proposal only.

## Starting Context

- Active roadmap item: ML2 (2-D regime-cell re-audit).
- Prior sprint: `S-P1X-REALR-NETTEDPNL-20260806.md`.
- Carried-over state: item 1 (netted-duplicate marking) **complete and
  verified** in #8543/#8545 — 29 rows / $24,270.53 on `bybit_1` stamped
  `netted_duplicate_unattributed`, `pnl` unmodified. Merged this session's
  lineage: #8534 `cac7037`, #8539 `910e3163`, #8544 `2906975`, #8551 `9d7786e`.
- Known risk on entry: `verify` was returning `no_overlap_nothing_verified`,
  and the reason was not yet established.

## Repo State Checked

- Branch `claude/metis-insights-workplan-cont-fczb1e`, based on `main` at
  `9d7786e`.
- Trainer VM confirmed at `9d7786e1` (relay #8558) before any replay was run —
  i.e. it carried the `logged_at_utc` fix from #8551.
- Canonical docs reviewed: `CLAUDE.md` § "Number provenance", § "Diagnostic
  provenance"; `.claude/skills/regime-selectivity/SKILL.md`.

## Files and Systems Inspected

- `scripts/research/ml_vol_label_replay.py` (`_bar_key` 749, `run_verify`
  757-889) — read in full before editing.
- `scripts/research/regime_cell_walkforward.py` (`cell_verdict` 158-206, print
  block 243-253).
- `config/regime_policy.yaml` — the `trend_vol` block, lines 120-145.
- Trainer: `data/trade_journal.db` (748 MB, `signals` 1,398,598 rows),
  `datasets-out/market_features/BTCUSDT/15m/v520`, `/tmp/btc_vol_labels.jsonl`.
- Relays: trainer-vm-diag #8558, #8559, #8560, #8561, #8562, #8563 (failed),
  #8564.
- CI: PR #8553 check runs.

## Work Completed

1. **Established the live audit corpus.** Exported 3,900 `regime_hard_gate`
   rows from `data/trade_journal.db::signals` (100% BTCUSDT, 2026-06-08 →
   2026-08-06). ML-label coverage: 5/3,697 in June (pre-go-live), **162/162 in
   July and 41/41 in August — 100% since 2026-07-01**.
2. **Found and fixed `verify`'s stale-bar clamping** (`ecc414e`). `_bar_key` is
   an unbounded as-of lookup and `run_verify` never bounded the gap. Bound now
   derived from the labels' own median bar spacing; out-of-range rows split by
   cause; `overlap_pct` + `coverage_note` + `staleness_basis` added;
   `--min-overlap` added and checked *before* `--min-agreement`.
3. **Found and fixed the walkforward printing only the SHORT verdict**
   (`a09791e`). `long_stable_drag` was computed and dropped at the output
   layer. Added per-direction trade counts so a zero-trade direction is marked
   vacuous instead of reading as a measured False.
4. **Graded the four gradable cells** on the correct direction (relay #8564).
5. **Recorded findings** in
   `docs/research/ML2-trend-vol-cell-walkforward-2026-08-07.md` (`ac1ea85`).
6. **Filed three backlog items** and deleted the finished
   `docs/runbooks/merge-sequence-8534-8539.md`.
7. **Re-ran the P1.x trust map on the cleaned rows** (the item-1 follow-up),
   relays #8566-#8568. Verified first that it is not a no-op: the live
   population is filtered through `provenance.pnl_is_trustworthy`
   (`backtest_fidelity_calibrate.py:309-310`) and
   `netted_duplicate_unattributed` is in the FABRICATED set
   (`provenance.py:228`), so the 29 rows marked in item 1 now drop out.

   `--backtest-db datasets-out/backtest_trades.db --live-db data/trade_journal.db
   --trust-map`, `r_basis=stop_distance`:

   | leg | scanned | trusted | r_measured | r_coverage | verdict |
   |---|--:|--:|--:|--:|---|
   | `htf_pullback_trend_2h` BTCUSDT | 55 | **18** | 18 | 1.0 | insufficient-live |
   | `squeeze_breakout_4h` BTCUSDT | 5 | **1** | 1 | 1.0 | insufficient-live |
   | `trend_donchian` BTCUSDT | 28 | **21** | 21 | 1.0 | insufficient-live |

   **All three legs `insufficient-live`** against `min_live_n=30`.
   `htf_pullback_trend_2h` loses **37 of 55 rows (67%)** to the provenance
   filter. `r_coverage` is 1.0 on every leg, so the blocker is purely SAMPLE
   SIZE, not missing stop data — a materially different finding from "the
   stops aren't recorded".

## Validation Performed

- `tests/test_regime_vol_axis.py`: **38 passed** (36 pre-existing + 2 new).
- Both new tests **verified failing against the old unbounded lookup** by
  simulation: old → `comparable=3, after_labels_end=0`; fixed → `comparable=1,
  after_labels_end=2, overlap_pct=33.33`.
- `cell_verdict` per-direction counts verified on a fixture reproducing the
  live cell-1 shape: `long_trades=9, short_trades=0`.
- `scripts/ci/run_guards.py --base-ref main --event-name pull_request`:
  **PASS 26 · FAIL 0 · SKIP 4**, run three times (once per commit).
- PR #8553 CI on head `a09791e`: all four required checks **green**
  (`guards`, `pytest-collect`, `pytest-run`, `repo-inventory`).

### Gaps not yet verified

- **Live parity is NOT established and is currently unmeasurable.** Labels end
  2026-06-30; the vol axis went live 2026-06-29; the honest overlap is **4
  rows**. Every cell verdict below is backtest evidence whose live fidelity is
  unconfirmed.
- **The labels were replayed with `btc-regime-15m-lgbm-fc-pcv-v2`, which only
  became BTC's advisory head on 2026-08-04.** Live rows from 06-29 → 08-04 were
  produced by `-v1`. Any future parity run over that window is a cross-model
  comparison and must be labelled as such.
- `p_volatile_delta` is still `n=0` and will remain so until `6ea245d` merges
  **and** the live trader deploys it. Label-only agreement must not be reported
  as full parity.
- The walkforward's `fidelity=approximate` for `trend_donchian` omits five
  levers (`exit_head_*`, `trail_decay_*`). Not quantified here.
- **The `htf_pullback_trend_2h`/BTCUSDT live mean-R verdict is STILL unusable**
  — but the reason has changed and is now understood. Before, fabricated rows
  contaminated it; now they are correctly excluded and only 18 trusted rows
  survive against a `min_live_n` of 30. The trust map printed per-leg
  `live_mean_r` figures (0.0254 / −1.1222 / 0.1621); **those must not be quoted
  as verdicts** — every leg is `insufficient-live`, so they are numbers with no
  gate behind them. This closes the item-1 follow-up as *run and answered*, not
  as *resolved*.

## Documentation Updated

- **New:** `docs/research/ML2-trend-vol-cell-walkforward-2026-08-07.md`.
- **New:** this sprint log.
- **Updated:** `docs/claude/health-review-backlog.json` — three new items
  (`BL-20260807-ML2-DATASET-STALE-BLOCKS-VOLGATE-PARITY`,
  `BL-20260807-ICT-SCALP-5M-CELLS-HAVE-NO-EVIDENCE-PATH`,
  `BL-20260807-REQUIRED-CHECKS-LACK-WORKFLOW-DISPATCH`), plus
  `BL-20260807-TRAINER-AUDIT-LOG-IS-HARNESS-OUTPUT-UNMARKED` earlier.
- **Deleted:** `docs/runbooks/merge-sequence-8534-8539.md` (job finished).
- Not updated: `CLAUDE.md`, `ARCHITECTURE-CANONICAL.md` — nothing this session
  changes a documented contract.

## Contradictions or Drift Found

1. **Two diagnostic-provenance defects, both fixed** — a value computed
   correctly and then discarded or mislabelled at the output boundary. Together
   with the `p_volatile` drop that opened #8553, that is **three instances of
   the same shape in one subsystem**. The arithmetic was never wrong.
2. **The policy file's inline dollar annotations are a different population**
   from the walk-forward R figures, and the trade counts do not line up
   (chop/calm comment says `11t`; the walk-forward finds 43. transitional/calm
   says `43t`; the walk-forward finds 30). **Recorded, not asserted as a
   contradiction** — the two were computed over different feeds and windows,
   and reconciling them is separate work.
3. **Two authored cells appear directionally inverted** (see Risks).

## Risks and Follow-Ups

**Tier-3 product decisions — proposals only, nothing enacted:**

- `squeeze_breakout_4h` **trending/calm** gates **short** (pooled **+0.87 R**)
  while **long** is pooled **−2.43 R with `long_stable_drag=True`** and is left
  **on**. The cell gates the direction that made money and permits the one that
  survives the drag test. Highest-value item, and it does not depend on parity.
- `trend_donchian` **transitional/calm** gates a **pooled-positive** long
  (+1.56 R over 30 trades).
- `trend_donchian` **chop/calm**: pooled −1.97 R but fold-sensitive → not
  affirmatively justified.
- `trend_donchian` **trending/volatile**: **the one cell that passes**
  (pooled −4.95 R, majority-negative across the full `FOLD_PANEL`, not
  fold-sensitive). n=9 — thin, but structurally so: volatile is 9.36% of bars.

**Technical:**

- PR #8553 is **green on all four required checks but held in draft** pending
  operator approval, because `6ea245d` touches `src/runtime/intents.py`.
- The dataset rebuild (backlog `…DATASET-STALE…`) blocks any live-parity claim.

## Deferred Items

- Rebuilding `market_features` BTCUSDT 15m through the current date and
  re-establishing parity — a fresh workstream, better suited to a new session.
- Teaching the walkforward harness to classify `ict_scalp_5m`, or recording its
  two cells as packet-based and walk-forward-exempt.
- Reconciling the policy file's dollar annotations against the R figures.

## Next Recommended Sprint

**Rebuild the BTC 15m dataset and establish vol-gate live parity properly.**

Why: three of the six authored cells currently fail their gate and two cannot
be graded at all, but *no* cell should be retired on backtest evidence whose
live fidelity is unconfirmed. Parity is the missing input, and the tooling to
measure it honestly now exists (`--min-overlap`, the staleness bound).

Required verification before acting on any cell: a rebuilt dataset covering
July–August, a replay pinned to the head that was advisory during each sub-
window, and a `verify` run reporting a non-trivial `overlap_pct`.

## Wrap-Up Check

- [x] Code inspected directly — `ml_vol_label_replay.py` and
      `regime_cell_walkforward.py` read before editing; key names verified
      against the source rather than assumed (`{side}_trades` did **not**
      exist and had to be added).
- [x] Docs reviewed and updated — research doc, backlog, this log.
- [ ] TRADE-PIPELINE updated — **N/A**, no pipeline stage changed.
- [x] Roadmap checked — ML2 remains open; parity is the blocker.
- [x] Contradictions recorded — see above, including the population mismatch
      I did *not* resolve.
- [x] Unknowns stated — parity unmeasurable (4-row overlap); v1-vs-v2
      cross-model caveat; `fidelity=approximate` omitted levers; `n=9` on the
      one passing cell.
