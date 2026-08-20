# Sprint Log: S-M20-E2-HORIZON-ARM-2026-08-20

## Date Range
- Start: 2026-08-20 (continued session; E2 itself merged the same day as #10029)
- End: 2026-08-20

## Objective
- **Primary goal:** run the **longer-horizon arm** of E2 — the follow-up
  [`e2-feature-information-2026-08-20.md`](../research/e2-feature-information-2026-08-20.md)
  named as the cheapest next probe. E2's `no_feature_beats_control` on `advantage_r`
  and `label_hold` was measured at a **12-bar (3 h) vertical barrier**, recorded then
  as a *condition on the answer, not a property of the fleet*.
- **Secondary goals:** put the arm on a lane that does not consume the trainer VM;
  make the horizon comparison actually valid (two confounds had to be pinned first).

## Tier
- **Tier 1** throughout.
- Justification: one dispatch-only research workflow, one research artifact, one
  sprint log, two backlog rows, doc/roadmap registration. No `src/`, no `config/`,
  no `ml/`, no unit file, no VM mutation, no order path. The trainer VM was **not
  used at all**.

## Starting Context
- Active roadmap items: M20 (exit levers), M31 (position telemetry).
- Prior sprint reference: `S-M20-E2-FEATURE-INFORMATION-2026-08-20` (PR #10029,
  merged `0cc80995`).
- Known risk at start: the E2 negative was being read as *"the panel carries nothing"*
  when what was measured was *"the panel carries nothing **at 3 hours**"*.

## Repo State Checked
- Branch/commit: `main` `0cc80995` → branch `claude/e2-exit-mechanism-info-n67zzs`.
- Coordination board #6927 read to a **proven** tail (`perPage=16 page=69` → 12 rows;
  a short page is the proof, a full page proves nothing) and a `▶️ START` posted.
- Merge slot was **held** by `llm-burst-2` (PR #10031) at start; not claimed until
  there was something to merge.

## Files and Systems Inspected
- **Code:** `scripts/research/e2_feature_information.py`,
  `scripts/research/build_intrabar_exit_panel.py`,
  `scripts/research/build_backtest_panel.py` (the `ict_scalp` adapter),
  `scripts/research/analyze_exit_head.py::_grouped_purged_folds`,
  `src/research/triple_barrier.py`, `src/research/intrabar_features.py`,
  `src/runtime/cross_asset_live.py`, `scripts/ops/fetch_backtest_candles.py`.
- **Config:** `config/cross_asset.yaml` (read), `config/strategies.yaml` (read).
- **Workflows:** `research-exit-head-build.yml` (read as the template),
  `branch-protection-sync.yml`, `guards.yml`, `pytest-run.yml`, `pytest-collect.yml`.
- **VMs:** none. Deliberately.

## Work Completed

### The two confounds that had to be pinned BEFORE the arm meant anything
1. **`expected_hold_bars` defaults to `time_stop_bars`** (`build_intrabar_exit_panel.py:214`),
   and `bars_in_trade_frac = n / expected` (`intrabar_features.py:122`). Left alone, the
   **feature set** would have moved with the horizon and any difference between rungs
   would have been uninterpretable. Pinned at 24 on every rung.
2. **`embargo_bars` had to track the horizon.** The splitter purges on each row's own
   `label_t1` (the *actual* touch offset), so the purge is already horizon-aware; the
   embargo is an additional buffer, and holding it at 12 would have shrunk it in
   relative terms as the horizon grew.

Verified by the substrate table: **rows, trades and feature count are identical across
a leg's four rungs.** Only the label varies.

### The ladder is anchored on a declared value
`build_backtest_panel.py:144` declares `timeout_bars: int = 24` for the `ict_scalp`
adapter and the panel builder exposes no override — so 12/24/48/96 = 0.5×/1×/2×/4×
the harness's own trade timeout. (My first look went to `config/strategies.yaml`,
which shows 48/48/200/200 — those belong to *other* strategies; the adapter default
is the one that governs this panel.)

### The lane
`research-e2-horizon-arm.yml` (new, dispatch-only) runs the sweep on a free
GitHub-hosted runner off the public Binance archive. Per the board's own routing rule
the work is CPU-only and does **not** belong on the single-core trainer. It refuses
rather than reports on an incomparable substrate (panel non-empty **and** cross-asset
`state: joined` at `row_coverage` ≥ 0.99) and fails red on any `harness_invalid` run
while printing `unmeasured` loudly as *not* a negative.

The numbers were produced by running those same commands locally — `data.binance.vision`
is reachable from the sandbox (200; only `api.binance.com` is geoblocked), which gave
immediate feedback instead of merging an unvalidated workflow in order to dispatch it.

### The result
Full detail: [`e2-horizon-arm-2026-08-20.md`](../research/e2-horizon-arm-2026-08-20.md).
**`label_hold` flips** — negative at h=12/24 on both legs, `informative_features_found`
at h=48 (XRP) and h=96 (both). `advantage_r` stays negative wherever admissible.
`forward_r` is informative everywhere and remains the wrong question.

## Validation Performed
- **Replication control passed on BOTH legs.** The h=12 rung reproduces the reference
  configuration on a *different feed*: FWER counts matched exactly (XRP 6, SOL 5) and
  all three verdicts were identical, on 9,761/503 and 10,724/567 against the trainer's
  10,103/530 and 10,786/580.
- **Not a max-statistic artifact:** one feature tracked across the whole ladder with its
  own threshold beside it (both move), monotone in statistic *and* gap on both legs,
  fold sign-agreement 0.50 → 1.00.
- Tooling: `e2_feature_information --selftest` **31/31**; `tests/test_e2_feature_information.py`
  **5 passed**; `scripts/ci/run_guards.py` **16 PASS / 0 FAIL** (re-run *after* committing —
  the first run scanned nothing, because guards are commit-range scoped and said so).
- `claim-basis-guard` clean on both new backlog rows.
- **Gaps not verified:** whether the `label_hold` signal is tradeable — E2 measures
  information, not edge, and the barrier-geometry alternative is untested here.

## Contradictions or Drift Found
1. **`feat_bars_in_trade_frac` is rank-identical to `feat_bars_in_trade` BY CONSTRUCTION**
   (constant divisor ⇒ rank-preserving ⇒ identical under Spearman and under trees).
   The reference logged this as an empirical coincidence, *"identical to 16 s.f."*
   Filed `BL-20260820-BARS-IN-TRADE-FRAC-RANK-IDENTICAL`.
2. **E2's harness gate uses the POINTWISE bar for the negative control**, so it
   false-invalidates at α ≈ 5% by construction. Observed **4/24 = 16.7%**, P(X≥4) =
   0.0298, **all four on SOL** — pointing at a panel-specific factor rather than bad
   luck. Filed `BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER`.
3. The reference's *"E2 says nothing about order flow"* scope limit is **closed** on
   this lane — the Binance archive carries `taker_buy_base`, both taker columns are
   dense, and neither clears at any rung.
4. My own interim filing of (2) at n=21 said *"within range"*; at the final n=24 it is
   below the gate's own α. The row was corrected rather than left standing.

## Risks and Follow-Ups
- **Technical:** the four inadmissible SOL cells leave `advantage_r` on that leg
  effectively unmeasured beyond h=12, and put a hole at the decisive h=48 `label_hold`
  rung. The finding survived only because h=96 replicated — luck, not design.
- **Tier-3 awaiting approval:** none opened.
- **Blockers:** none for E3 on `label_hold` at a long horizon.

## Deferred Items
- Whether the `label_hold` signal is **edge or barrier geometry** — routes to the M20
  net-of-cost gate, not to E2.
- The negative-control gate fix: must be decided **before** the next run, and if
  adopted the full sweep is re-run under the new rule rather than the four cells
  patched.
- Why all four discards landed on SOL — compare the negative control's null
  distribution between panels at matched horizons.

## Next Recommended Sprint
- **Suggested next sprint:** **E3** — design levers over `label_hold` at a long
  horizon, swept jointly per the process doc, over the endogenous features that
  actually cleared (`dist_to_stop_atr`, `upnl_r`, `running_mae_r`).
- **Why next:** E2 now licenses it on a stated target at a stated horizon, which is
  precisely what the process doc said was missing when it recorded E3 as guesswork.
- **Required verification before starting:** decide the negative-control gate question
  first; and state the horizon in every claim, because the same panel returns opposite
  verdicts three rungs apart.
- **Disposition if E3 returns negative:** §3.1 — regroup and widen. Unchanged.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched, so `docs/TRADE-PIPELINE.md` needed no update.
- [x] Roadmap status was checked and a new entry added.
- [x] Contradictions were recorded — four, two of them filed with resolution criteria.
- [x] Remaining unknowns were stated clearly (edge vs geometry; the SOL clustering).
