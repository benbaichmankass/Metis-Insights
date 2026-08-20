# Sprint Log: S-M20-E2-FEATURE-INFORMATION-2026-08-20

## Date Range
- Start: 2026-08-20 (continuation of the exit-mechanism thread opened 2026-08-18)
- End: 2026-08-20

## Objective
- **Primary goal:** run **E2** of
  [`docs/design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md)
  — per-feature information vs forward R over the E1-widened panel, purged and
  embargoed, grouped by `trade_id`, against a **shuffled-label control
  pre-registered before any feature score exists**. The process doc records this
  step as *"never been run… the reason step E3 has been guesswork."*
- **Secondary goals:** supply the two things E2 needed that did not exist (a
  non-leaky per-feature test and a shuffled-label control); establish the real
  substrate rather than concluding from the local repo.

## Tier
- **Tier 1** throughout.
- Justification: one new read-only research tool, one research artifact, three
  backlog rows, and read-only trainer relays. No `src/` file, no
  `strategies.yaml` / `accounts.yaml` / `risk_caps.yaml`, no unit file, no VM
  mutation, no heavy lock taken. The trainer's working tree was never checked
  out — the tool was `git fetch` + `git show` into `/tmp`.

## Starting Context
- Active roadmap items: M20 (exit levers), M31 (position telemetry).
- Prior sprint reference:
  [`S-M20-E0-E1-EXIT-SUBSTRATE-2026-08-20.md`](S-M20-E0-E1-EXIT-SUBSTRATE-2026-08-20.md)
  — E0 measured 52.4% of harness exits decided at entry and 92.0% of the
  remaining path exits still endogenous; E1 widened the peer map and joined the
  xa block to the panel.
- **A duplicate claim had to be resolved first.** A `m20-e2-feature-information`
  session posted an E2 START at 08:06:39Z and **retracted it** at 08:11:18Z on an
  operator correction that E2 was already assigned elsewhere. Their retraction was
  verified clean before proceeding (no remote branch, no PR, no dispatch), and
  their three handed-over findings were **re-derived independently rather than
  inherited** — all three held, one was understated.

## Repo State Checked
- Branch/commit: `claude/e2-exit-mechanism-info-n67zzs`, reset onto `origin/main`
  `d06022e` (the handoff's `096887a6` was three commits stale).
- ⚠️ **The session clone arrived SHALLOW (50 commits, `.git/shallow` present)** and
  was unshallowed to **3,469** before any history was read. This is the exact trap
  `BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE` names: a `git log -p` on a
  shallow clone returns a plausible wrong answer with no error. Whether the
  SessionStart guard fired here and was missed, or did not fire in this remote
  environment, was **not determined** and is not asserted either way.
- Canonical docs reviewed: root `CLAUDE.md`, `CLAUDE-RULES-CANONICAL.md`,
  `ROADMAP.md`, the process doc, coordination board #6927.

## Files and Systems Inspected
- **Code:** `scripts/research/analyze_exit_head.py` (call sites read directly),
  `scripts/research/build_intrabar_exit_panel.py`,
  `scripts/research/build_backtest_panel.py`, `scripts/candle_io.py`,
  `src/research/meta_label.py`, `scripts/research/market_raw_to_csv.py`.
- **Config:** `config/cross_asset.yaml`, `config/strategies.yaml` (read).
- **Data:** all 6 committed candle files; trainer `datasets-out/market_raw` and
  `data/ibkr_datasets/market_raw` (full enumeration).
- **Services/timers:** none mutated; read-only relays only.
- **Workflows:** `trainer-vm-diag` (#10014, #10016–#10022).

## Work Completed

### The two missing preconditions
- **`analyze_exit_head._univariate_fdr` is POOLED and UN-PURGED.** Line 270 calls
  it on the entire row set — outside the fold loop, no folds, purge, embargo or
  grouping — while the correct splitter `_grouped_purged_folds` sits at line 170
  in the same file and serves only the multivariate head. Its analytic BH-FDR
  q-values assume the row independence that overlapping triple-barrier labels
  violate. So the code that looks like E2 already existing is the **leaky**
  version. Filed `BL-20260820-UNIVARIATE-FDR-IS-POOLED-AND-UNPURGED`.
- **No shuffled-label control existed anywhere** (`shuffled_label` /
  `label_shuffle` / `permutation_test` → zero files; the only `shuffle` is
  permutation *importance*, which permutes a feature, not the label). E2's
  declared falsifier had no implementation.

### The tool — `scripts/research/e2_feature_information.py`
Imports the splitter, never calls the univariate (both asserted by self-test).
Statistic: absolute mean of per-fold Spearman vs `forward_r` on test rows.
Null: **trade-block** label shuffle. Decision rule: **Westfall–Young
max-statistic FWER threshold**, with the pointwise verdict reported for diagnosis
only. Positive and negative controls run every time and gate admissibility.
Underpowered returns `verdict: "unmeasured"`, never a negative.

### Substrate
The **local** repo cannot support this run — five of six committed candle files
are constant-price placeholders (300 rows, 1 distinct close each), and the one
real file has no peer. The trainer's `datasets-out/market_raw` holds six crypto
symbols at 15m with ~5 years of fully-overlapping history (#10014), so every
symbol in the widened peer map has a real series. Target `XRPUSDT` 15m — the
operator's own motivating case, and the symbol whose **both** configured peers
(ETHUSDT ρ 0.8763, SOLUSDT ρ 0.8451) have full-overlap data.

## Validation Performed
- **Self-tests: 26/26**, run locally AND **on the trainer** at every sha change.
- **Transport verified byte-for-byte**: sha256 on the trainer matched the reviewed
  commit at each of three refreshes.
- **Controls proven able to fire**, not merely present: a planted dead positive
  control makes the run return `harness_invalid`.
- **Two optimizations proven behaviour-preserving rather than assumed**:
  precomputed feature ranks vs direct Spearman, max delta **0.0**; grouped vs
  ungrouped null over 120 replicates × 4 features on an identical shuffle stream,
  worst delta **1.110e-16**.
- **Guards clean**: `claim-basis`, `impossibility-claim`, `collapsed-state`,
  `diagnostic-provenance`, `artifact-validity`, `provenance-consumer`.
- **Two of my own errors were caught by my own tests and are recorded, not
  quietly fixed**: the block-vs-row shuffle rationale was overbroad (the null
  widening needs *both* series trade-structured — measured 0.0917 vs 0.1045 on an
  i.i.d. probe), and two guard tests initially matched their own source lines and
  therefore proved nothing.
- **Not verified:** the retracted session's "64 rows from 4 trades" local panel
  figure could not be reproduced here — this environment has no pandas, so the
  builder returns 0 rows with an honest error note. Not disputed, just not
  independently confirmed.

## Documentation Updated
- New: [`docs/research/e2-feature-information-2026-08-20.md`](../research/e2-feature-information-2026-08-20.md).
- Backlog: three rows (see Contradictions).
- Rules/architecture/trade-pipeline: no change required — no pipeline stage touched.

## Contradictions or Drift Found
1. **`_univariate_fdr` pooled/un-purged** while the correct splitter sits in the
   same file — two halves of one analyzer disagreeing about row independence
   (`BL-20260820-UNIVARIATE-FDR-IS-POOLED-AND-UNPURGED`, medium).
2. **Trainer root fs back to 93–94% (42 of 45 GB)** against a row resolved on a
   *"back under ~80%"* criterion (`BL-20260820-TRAINER-DISK-BACK-TO-94PCT`,
   medium). A resolved row whose condition has returned must not stay resolved.
3. **Five of six committed candle files are constant-price with no marker** and
   `candle_io.load_candles` has no variance check
   (`BL-20260820-PLACEHOLDER-CANDLE-FIXTURES-CARRY-NO-MARKER`, medium).

## Risks and Follow-Ups
- **Tier-3 awaiting approval:** none opened this sprint.
- The **label horizon is a condition on the answer**: `forward_r` is a
  triple-barrier outcome at `--time-stop-bars 12` (3h on 15m) while the harness's
  own timeout is 24 bars, so any verdict here is a verdict *at 12 bars*.

## Deferred Items
- A second leg (`SOLUSDT` 15m, peers ETHUSDT + XRPUSDT — all present) as a
  generalization arm; not run in-session to avoid contending for the trainer's
  single core.
- A longer-horizon label arm, named as the cheapest follow-up.
- The equity/ETF correlation gap still blocks peers for 12 of 23 traded symbols.
- The coordination board's issue **body** still needs a human restore.

## Next Recommended Sprint
<!-- PENDING: depends on the E2 verdict. -->

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage was touched, so `docs/TRADE-PIPELINE.md` needed no update.
- [x] Roadmap status was checked.
- [x] Contradictions were recorded — three, all filed with resolution criteria.
- [x] Remaining unknowns were stated clearly.
