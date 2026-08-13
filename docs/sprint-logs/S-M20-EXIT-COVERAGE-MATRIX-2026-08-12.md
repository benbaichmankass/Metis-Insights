# S-M20-EXIT-COVERAGE-MATRIX-2026-08-12

## Date Range

- **Start:** 2026-08-12 ~21:50 UTC
- **End:** 2026-08-13 (in flight at the time of writing — see § Gaps not yet verified)

## Objective

**Primary:** advance M20's done-condition — the per-leg exit-coverage matrix
(`docs/research/exit-refinement-coverage.json`) — by working the largest open
block, `exit_head_ml`, through the `exit-refinement` skill's pipeline.

**Secondary:**

- Re-sweep the pullback-family stale/giveback cells under live-parity geometry.
- Merge PR #8814 on green (housekeeping carried in from the prior session).
- Keep Tier-3 decisions **queued for the operator**, not enacted (operator asleep;
  explicit standing instruction for this session).

## Tier

**Tier 1.** Research tooling, tests, a research data file, and one CI workflow.
No `src/`, no `config/`, no registry, no order path, no live env var. The two
findings that *would* be Tier-3 (a live lever change on `trend_donchian_avax_4h`
and on `gld_pullback_1h`) are **proposed and queued**, not applied — § Risks.

## Starting Context

- **Active roadmap item:** M20 exit refinement. Done-condition per the
  `exit-refinement` skill: *no `pending`/`blocked` rows on live legs*.
- **Prior sprint:** PR #8712 was the last write to the matrix.
- **The continuation prompt stated coverage as `304/376 = 80.9%` across 47 live
  legs**, with `exit_head_ml` the largest open block (31 of 57 pending cells).
- **Known risks carried in:** an un-run cell is `pending`, never a negative;
  `beats()` has no minimum-n; Path B's two thresholds are deliberately UNSET;
  `m20-sweep-corpus.jsonl` records no regime state.
- **Concurrent session** working the tick-chain / PR #8815. Scope agreed with the
  operator: leave those files to them, review their work, report gaps via the
  board — never edit their files. Honoured; one finding reported (§ Contradictions).

## Repo State Checked

- Branch `claude/m20-exit-coverage-matrix-8d3he7`, 12 commits ahead of `main`
  (`2e7250f` … `d25acbc`), PR **#8825** open.
- `main` verified clean **before** attributing any guard failure to my diff —
  this mattered twice (§ Validation).
- Trainer VM at `f2ca1fb7` (2026-08-12T23:19Z), `ict-trainer-git-sync.timer` armed.
- Canonical docs read: `CLAUDE.md` (§ Diagnostic provenance, § Collapsed states),
  `docs/CLAUDE-RULES-CANONICAL.md`, the `exit-refinement` skill, `ROADMAP.md` M20.

## Files and Systems Inspected

**Code**

- `scripts/research/m20_coverage_rollup.py` — **new**, 472 lines.
- `scripts/research/m20_fleet_exit_sweep.py` — read for `MIN_OOS_TRADES`, `classify`.
- `scripts/ml/train_exit_head.py` — `eval_split`, the fold loop.
- `scripts/ml/build_exit_head_dataset.py` — `load_harness_trades`, `load_live_trades`.
- `scripts/backtest_ict_scalp.py` — the emit dict (lines ~560-590).
- `scripts/research/m20_exit_head_round.py`, `scripts/ci/run_guards.py`.

**Config** — `config/strategies.yaml` (read-only: resolving the 47 live leg names).

**Data** — `docs/research/exit-refinement-coverage.json`,
`docs/research/m20-sweep-corpus.jsonl` (680 rows).

**Workflows** — `.github/workflows/m20-exit-lever-sweep.yml` (edited),
`.github/workflows/claude-pr-automerge.yml` (read for its trigger contract).

**Services** — trainer `ict-trainer-git-sync.timer`; live journal copies at
`/home/ubuntu/ict-trading-bot/{,data/}trade_journal.db`.

**Relays** — trainer-vm-diag issues #8822, #8824, #8827, #8832, #8833, #8837,
#8840, #8841, #8842, #8843, #8844, #8846, #8847, #8848.

## Work Completed

### 1. The M20 headline was never computed anywhere

Three sessions quoted three different figures for a file that had not changed:
**319** (PR #8712), **304** (the continuation prompt), **311** (a fresh
hand-count the same day). The 304 was **self-inconsistent with its own next
sentence** — the same prompt said "57 pending cells", and 376 − 57 = 319.

The divergence is not arithmetic: "closed" has three defensible cuts and no
session said which it used. `m20_coverage_rollup.py` now computes and **names**
all three, and reproduces all three historical figures.

**The headline and the done-condition are different questions.** The headline
counts `blocked` as closed; the skill's done-condition does not. So M20 needed
**61** cells resolved, not the 57 the prompt implied — a session reading only
the pending count under-scopes by four and never revisits the blocked ones.

### 2. `train_exit_head.py` graded per family; the matrix's unit is the leg

`eval_split` pools every symbol in the E0 dir — right to **train** on, wrong to
record a **verdict** from. Writing one pooled verdict into each of a family's
leg rows is `BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS` reappearing a
layer up. Each fold's test set is now cut by leg, each leg's denominator stated.
On the test fixture, one pooled family of 146 OOS trades resolves to **three
different verdicts**.

`MIN_OOS_TRADES` is **imported** from `m20_fleet_exit_sweep`, not mirrored, so
one matrix is never governed by two floors. An unimportable floor yields a third
state (`ungraded_no_floor`) and withholds verdicts rather than inventing a local
default; `insufficient_base` stays distinct from `honest_negative`.

### 3. A `status: null` had sat in the matrix since 2026-08-09

Not a legend value, so nothing could grade the cell. Set to `pending` per the
exploder's own documented rule, not a new inference.

### 4. The `ict_scalp` exit-head round had never been runnable

`backtest_ict_scalp.py` emitted no top-level `exit_time`, and
`load_harness_trades` drops any row without it — so 1170 emitted trades became
**0** E0 rows under the message `no trades loaded`. Also never emitted `symbol`,
and hardcoded `strategy: "ict_scalp_5m"` on every leg. Fixed; the builder now
names the **missing field** and states the population it read.

### 5. Seven of 47 "live" matrix legs do not exist in `config/strategies.yaml`

Surfaced by a sweep dispatch that failed on unknown names. Re-keyed
(`spy_trend_1d` → `spy_trend_long_1d`, and six siblings); `validate()` now fails
CI if any live leg cannot be resolved against config.

### 6. Evidence-vintage caveat, scoped rather than blanket

239 of 254 closed cells on the 38 legs whose harness modelled **no take-profit**
predate the 2026-08-10 TP-parity cutover (`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`); **0 of 396** matrix refs mentioned it. Rather than
flagging the whole fleet, each live unit was read against its harness — `scalp`
and `fvg` place a real target their harness models and are **clean**. The
roll-up now prints the caveat with its own denominator.

### 7. 13 cells closed by a live-parity re-sweep

Coverage moved **319 → 334 / 376 (88.8%)**.

### 7a. The `vol_trail` wave-2 block: 2 closed, 1 graded at a stated non-standard split, 7 blocked

The recovered wave-2 sweep (§ 8) graded all 10 legs. The result splits
**perfectly by timeframe**, which is what makes it structural rather than
per-leg luck:

| timeframe | legs | OOS n | outcome |
|---|---|---|---|
| 1h | slv, uso | 40, 27 | clear the 25 floor → **honest_negative** (both) |
| 1d | the other 8 | 3–8 | miss the floor → `insufficient_base` |

`slv_trend_1h` is a clean graded refutation — all three cells are the IS-only
overfit shape (net_R up in-sample, down out). `uso_trend_1h` has one cell at
`path_b_wf_pass`, but that verdict is **not** `rate ok`: its drawdown exchange
rate holds on OOS only (headroom −1.494 IS / +5.134 OOS), so nothing is
shippable; the Path-B row is named in the ref so a future threshold session
finds it rather than re-deriving it.

**A second sweep tested whether the split, not the data, was binding.** At
2023-01-01 only `tqqq` crossed (OOS 8 → 27, still `is_oos_fail`); the other
seven went to 12–21 while IS shrank. `tqqq` is recorded as `honest_negative`
**with the non-standard split stated in the ref** — safe only because the
answer is a refutation at both windows, i.e. moving the window made the cell
gradeable without manufacturing a pass. The seven are `blocked`, not
`honest_negative`: the cell terminated at "we had too few trades to look",
which is the opposite claim from "we looked and it failed".

### 7b. Why those seven can't be graded — a gate-ordering finding

`m20_fleet_exit_sweep.py:1442-1451`:

```
if _thin:            -> verdict=insufficient_base   (walk-forward SKIPPED)
elif candidate:      -> walkforward(...)
elif is_path_b_...:  -> walkforward(...)
```

The skip is deliberate; its comment reasons that the walk-forward "would be
measuring the same too-thin book". **It would not.** `_thin` is computed from
the post-split OOS window; the walk-forward's folds span the **full history** —
the one cell that reached it here (`uso_trend_1h/vt_cold10_t2`) ran six folds,
2021 through 2026. For a 1h leg the two denominators roughly agree; for a 1d
leg they diverge by an order of magnitude. So a 60–79-trade leg is refused a
six-fold test because its 3–8-trade window is thin.

Filed as `BL-20260813-THIN-OOS-BLOCKS-THE-WALKFORWARD-IT-COULD-PASS`. **Not
fixed here** — it changes the evidentiary standard by which a live exit lever
is judged, which is the operator's call, not a research-tooling edit.

Coverage after 7a: **344 / 376 = 91.5%**; done-condition 45 cells (32 pending
+ 13 blocked — blocked rose 6 → 13 on this finding, deliberately visible).

### 8. The sweep corpus could never reach `main` — and had silently discarded 4 runs

The `corpus` job pushes directly to the dispatch ref. Dispatched on `main`, that
is declined by branch protection (`GH006 … 3 of 3 required status checks are
expected`) — **structural, not a race**: a bare commit cannot satisfy a required
check. Four wave-2 runs (31647462929, 31648068353, 31648088122, 31648666524) went
**11 of 12 jobs green**, swept all ten legs, uploaded every artifact — and threw
every row away at that one step. Worse, the four-attempt backoff loop reported it
as a flaky push, which is how the real cause stayed hidden across four runs.

Fixed three ways: a default-branch dispatch is **retargeted** onto a corpus
branch that opts into `claude-pr-automerge.yml` (so the corpus reaches `main`
through CI rather than by weakening the protection that refused it); a
protected-branch decline now **fails fast and names the fix** instead of
retrying; and a rebase conflict on the append-only corpus **re-derives the
union** from the other side's copy instead of dropping this run's rows.

### 9. Documented the trainer's ≤15-minute worktree lifetime

`ict-trainer-git-sync` hard-resets to `origin/main` every ~15 min. A round
launched against `git checkout`-ed files loses them mid-run: `ict_scalp_eth_15m`
and `ict_scalp_sol_15m` completed with the fixed script; `ict_scalp_xrp_15m`,
invoked from the same loop seconds later, died on `unrecognized arguments:
--strategy-name`. Recorded as `docs/claude/trainer-vm-mode.md` § 9.a.1 with the
reflog evidence, so the next session merges rather than checks out.

### 10. The sweep cannot grade a lever it already contains — 31 corpus rows are a self-comparison

The prescription I wrote in §8's follow-up — *"re-sweep the shipped cells"* —
turned out to be unrunnable, and finding out why produced the session's largest
structural finding.

`base_args → declared_levers()` puts every YAML-declared exit lever **into** the
config-exact base. That is correct: a shipped lever is part of the leg's
baseline, and a new lever cell must be measured on top of it. The consequence is
not: a swept cell reproducing the leg's own declared values measures the base
**against itself**, so every cell asks *"does this alternative beat the shipped
one?"* and none asks *"is the shipped one worth anything?"*.

Measured over `docs/research/m20-sweep-corpus.jsonl`, population stated:

| | |
|---|---:|
| corpus cell rows | 860 |
| all-zero delta (`d_net_r` + `d_max_dd` = 0 in **both** windows) | 37 (4.3%) |
| …of those, on a leg whose YAML **declares** that lever | **31** |

Those 31 carry `gate_reason: tie_no_improvement`, `net_r_retained_frac: 1.0` —
and wear the verdict labels `is_oos_fail` (27) and `insufficient_base` (4).
Neither is true; **no comparison happened**. 20 distinct (leg, lever) pairs,
including 9 of the 13 gradeable stale decisions from §6.

The worked example is the one that makes it a finding rather than a curiosity.
`qqq_pullback_1h` `vt_hot80_t2.5` is the exact cell the matrix called a *LONE
PASS*; its YAML has carried `trail_vol_above_pctl: 0.80` / `trail_vol_tight_mult:
2.5` since 2026-08-09 (#8683); its corpus row reads `verdict=is_oos_fail`, both
deltas `0.0`, `wf_ran=false`. A reader takes that as *"the live cell failed
out-of-sample"*. Nothing did — diagnostic-provenance **sub-class A**, sitting in
the corpus rather than in a probe. I had nearly recorded its mirror image
earlier the same session, reading one such cell as *"the shipped lever is
INERT"*.

**The lever-OFF arm** (PR #8868) is the instrument that answers it.
`--without-declared-lever <lever>` removes a declared lever from the
config-exact base and emits one `shipped_<lever>_<values>` cell putting it back
at the leg's own live values, so the delta the sweep already computes becomes a
verdict on the **shipped** cell. The invariant it rests on is asserted over all
22 real declaring legs rather than a fixture — `base-OFF + shipped cell ==
base-ON`, **22/22**, 0 mismatch. If that did not hold the A/B would measure the
lever plus whatever else drifted, and the verdict would still be attributed to
the lever.

Four properties are deliberate, not incidental: the drop is enforced in `opt()`
so a family branch cannot route around it; a dropped key is **omitted, never
passed as `0`** (an armed lever at a degenerate threshold is a different book
that looks like the right one); `trail_geometry` is **not offerable** because
`trail_mult` is a continuous parameter with no OFF state; and `--census` is
refused in combination, since it would print a capture distribution for a book
that is not the live book under the same column headings.

**Reach, stated rather than implied: 13 of the 21 stale live decisions.** The
other 8 need a different instrument — `trail_geometry` (4) by design,
`exit_head_ml` (3) and `mhg_pullback_1d stale_stop` (1, correctly
`passed_unshipped`) because no leg declares them, so there is nothing in the
base to remove.

Designing the actual run then surfaced a gap in the arm itself. Dropping two
levers from one leg removes **both**, so the cell restoring one measures its
contribution in a book still lacking the other — a clean one-lever A/B, but
against a counterfactual base rather than the live configuration. Two legs are
affected (`trend_donchian_eth`, `trend_donchian_eth_prop`). Every row now
carries `base_missing_other_levers`, the plan prints a `!! MULTI-LEVER BASE`
line, and the flag help says drop one lever per run. Note my own round-trip test
dropped all levers at once — that verifies the cells **collectively
reconstruct** the live base, which is a weaker statement than any single cell
having been measured against it.

`BL-20260813-SWEEP-GRADES-SHIPPED-LEVERS-AGAINST-THEMSELVES` stays **open**. Its
`resolution_criteria` explicitly refuse any run of the sweep *without* the new
flag as evidence.

### 10b. The arm ran — 13 shipped levers graded, none inert, five negative out-of-sample

Four dispatches, live parity (`tp_cap_pct 0.099`), split `2025-07-01`, **one
lever dropped per run** so every cell's base differed from live in that lever
only. `base_missing_other_levers: []` on all 18 corpus rows is the field that
asserts it rather than leaving a reader to derive it.

| leg | shipped lever | Path A | ΔnetR IS | ΔnetR OOS | ΔmaxDD IS | ΔmaxDD OOS |
|---|---|---|--:|--:|--:|--:|
| trend_donchian | trail_decay | **PASS** | +7.46 | +2.03 | -0.28 | 0.0 |
| trend_donchian_xrp_4h | stale_stop | **PASS** | +4.69 | +2.63 | -4.90 | -0.66 |
| tlt_pullback_1h | trail_decay | wf_fail | +6.92 | +3.21 | -3.25 | -2.18 |
| uso_trend_1h | giveback_stop | wf_fail | +0.62 | +4.66 | -1.18 | -1.45 |
| gld_pullback_1d | trail_decay | insufficient_base | +19.18 | +1.00 | -0.55 | -1.00 |
| iaum_pullback_1d | trail_decay | insufficient_base | +0.43 | +1.00 | -0.98 | -1.00 |
| mhg_pullback_1d | trail_decay | insufficient_base | +0.15 | +0.74 | -0.75 | -0.74 |
| slv_pullback_1d | trail_decay | insufficient_base | +14.15 | 0.0 | -4.72 | 0.0 |
| sol_pullback_2h | trail_decay | is_oos_fail | +14.82 | **-0.52** | -6.73 | -1.15 |
| trend_donchian_sol | stale_stop | is_oos_fail | +6.37 | **-1.21** | -5.01 | -1.58 |
| qqq_pullback_1h | vol_trail | is_oos_fail | +8.59 | **-1.96** | +0.87 | **+4.49** |
| trend_donchian_eth | stale_stop | is_oos_fail | +24.19 | **-10.55** | -15.75 | **+4.67** |
| trend_donchian_eth | vol_trail | is_oos_fail | +5.83 | **-6.07** | -10.69 | -0.96 |

**Not one is inert.** Every shipped lever adds in-sample net_R — the outcome I
was least expecting, having nearly filed the opposite off the artifact two hours
earlier.

**Five hurt out-of-sample**, two on drawdown as well. Checked against
`accounts.yaml` rather than assumed: all five are on **paper** (`bybit_1`,
`alpaca_paper`) or **prop** (`breakout_1`), so no real-money leg carries an
OOS-negative lever and nothing is urgent — though `trend_donchian_eth` and
`trend_donchian_sol` run on `breakout_1`, where prop payout is real. The
converse is the reassuring half: **both passing levers run on `bybit_2` real
money.** The two carrying real money are the two that validated. Removal is
Tier-3 and queued (`BL-20260813-FIVE-SHIPPED-LEVERS-MEASURED-OOS-NEGATIVE`,
ordered by measured cost); nothing was flipped.

`slv_pullback_1d`'s OOS `0.0` reads as **not armed**, not neutral — the lever
never fired in that window. `trend_donchian_eth`'s two rows carry different base
n (599/117 vs 704/145) **by design**: separate runs dropping one lever each, so
the two bases are genuinely different books. The refs say so, because a reader
comparing them otherwise sees an inconsistency.

**A prior status of mine is taken back.** `trend_donchian` `trail_decay` was
`shipped_gate_failed`, recorded 2026-08-12 when the re-sweep "did not reproduce"
it. That cell measured the base against itself — an all-zero tie labelled a
failure. It was never re-measured; it had never been measured at all. It
**passes**. Stale live decisions **21 → 8**, and the 8 remaining are exactly the
set the reach analysis predicted the arm cannot touch.

### 10c. The obvious remedy for the four ungradeable legs was tested and does not work

Four results came back `insufficient_base` (OOS n = 4–7 against the 25 floor).
The obvious move is an earlier split, so it was **run** rather than reasoned
about — same four legs, same arm, split `2024-01-01`:

| leg | OOS n | what moved |
|---|---|---|
| gld_pullback_1d | 4 → 10 | ΔmaxDD OOS -0.9994 → **+0.5526** — sign flip |
| mhg_pullback_1d | 7 → 18 | ΔnetR IS +0.1479 → **-0.1305** — sign flip |
| slv_pullback_1d | 6 → 19 | ΔnetR OOS 0.0 → **-0.3761**, ΔmaxDD 0.0 → **+1.1343** |
| iaum_pullback_1d | 4 → 10 | ΔmaxDD OOS -0.9994 → **+0.5313** — sign flip |

**Not one reaches the floor**, so all four stay `insufficient_base` and the
matrix keeps the statuses recorded at the standard split. But the second finding
is the more useful one: **four of four change sign on at least one axis when
only the split moves.** A verdict that inverts under a split change is not one a
longer window would merely sharpen — that is stronger evidence of ungradeability
than thin-n by itself. `slv_pullback_1d` is the clearest: its `0.0` meant the
lever never fired, and given a window where it does, it is negative on both axes.

`iaum_pullback_1d` turns an arithmetic prediction into a measurement — from its
30-trade total I argued no split could yield both a 25-trade OOS and a usable
IS; at 2024-01-01 it returned IS=19 / OOS=10, exactly as the count implied.

### 10d. What now blocks the largest remaining block, measured

`exit_head_ml` is 30 of the 37 pending cells. Two independent blockers, both now
resolved or quantified:

1. **Attribution** — `backtest_trend.py` and `backtest_pullback.py` stamped one
   hardcoded family literal on every emitted row, and the E0 dataset buckets by
   that field, so 14 distinct 1d legs would have collapsed into one
   unattributable verdict. Both now take `--strategy-name`, defaulting to their
   historical literal; the round asks `--help` rather than hardcoding which
   harnesses support it.
2. **Frames** — the round had been pointed at `/home/ubuntu/m27_data`, which
   holds **only 5m and 15m**. The 1d/1h frames live in
   `/home/ubuntu/ict-trading-bot/data` (20 × 1d, 10 × 1h), which is where the
   sweep already reads them. With that dir, **19 of the 30** pending cells are
   runnable: 13 at 1d, 6 at 1h. The rest lack a native frame — `MES`, `MGC`,
   `MHG` (proxies, which the round refuses for head training) and
   `squeeze_breakout_4h` — **CORRECTED 2026-08-13: this said "no 4h frame
   anywhere" and that is false.** Asked `resolve_data` itself rather than
   inferring from filenames: it returns `data/BTCUSDT_15m.csv` with
   `resample=4h` and **`proxy=False`** — native BTCUSDT resampled, which is the
   identical shape `trend_donchian_eth_4h` uses (`ETHUSDT_5m` → 4h), and that
   leg already carries graded `exit_head_ml` cells. So the resample path is
   established, not novel, and the cell was `pending` with **no ref** because
   nobody ran it — not because it was blocked. Round dispatched.

### 10a. One matrix cell was stale, found by the arm's own denominator check

`qqq_pullback_1h` `vol_trail` read `passed_unshipped` while the YAML has it
armed. **Field beats comment** — now `shipped`, with the paper-book context (the
leg runs on `alpaca_paper`; its `alpaca_live` leg is shelved `dry_run` since
2026-07-15, which is why the declare was made without the yearly walk-forward
the previous ref names as its gate — that gap is real, unclosed, and not
money-at-risk). `resolved-only` 324 → 325; headline unchanged at 346/376, both
statuses being closed.

### 11. The `symbol` fix was one of FOUR missing keys — and I reported it closed

**This is the session's main methodological failure and it is worth the detail.**

§10d closed with the trend/pullback attribution fix. The follow-up finding —
three harnesses never emitted `symbol`, so `build_exit_head_dataset.py` dropped
their rows — shipped as #8889, and I recorded it as resolved on the strength of
the code landing.

**It did not work.** The re-run at the merged sha returned *byte-identical*
numbers: `trades_in 1332`, `skipped {no_candles: 697, unresolvable: 63}`, only a
`pullback` family. The builder refuses a row missing **any of four** keys —
`entry_time`, `exit_time`, `entry`, `sl` (`build_exit_head_dataset.py:193`) —
and those three harnesses emitted only `entry_time`.

Measured per emit file (1d round):

| | rows | usable |
|---|--:|--:|
| 7 trend legs | 371 | **0** (`exit_time`/`entry`/`sl` missing on every row) |
| 6 pullback legs | 578 | 578 |

and `trades_in 1332 == 578 harness-usable + 754 live-usable`, which is the
arithmetic proof the 371 **never entered the population**. So the causal story I
had shipped — "every one landed in `no_candles`" — was also false: `no_candles`
is a later stage they never reached. That claim came from a subtraction that
happened to reconcile (`1332 − 697 − 63 = 572 =` the pullback count), and I put
it in three source files as a comment. Corrected in all three (#8901).

**Two diagnoses in a row were wrong the same way: reasoning from the NAME of the
counter the survivors landed in.** What resolved it in one step was diffing the
emitted key set against the family that *works*:

```
in PULLBACK but NOT in TREND : ['entry', 'exit_reason', 'exit_time', 'sl']
in TREND but NOT in PULLBACK : []
```

`tests/test_harness_emit_schema.py` makes that permanent and **imports the
requirement from the builder's own guard** rather than restating it — a
hand-copied list would be a second definition free to drift, and the test would
then pass while the builder rejected the rows. Verified non-vacuous: it fails on
exactly the three broken harnesses at the pre-fix sha and passes on the two
working ones.

**Verified end-to-end after merge** (relay #8910): load went 578/949 → **949/949**
(1d) and 1482/1992 → **1992/1992** (1h); `trades_in` +371 and +510, matching the
trend counts exactly; and a donchian-side family exists for the first time in the
harness's existence.

### 12. Why a 100% drop of one family stayed invisible

The load-stage `missing:*` counters that name the real cause were printed **only
inside the `if not trades:` total-failure branch**. A *partial* drop — one family
at 100%, another at 0% — is exactly what they exist to catch and exactly what
they could not report. `build_report.json` therefore showed only the candle-stage
counters, `trades_in` counted the survivors, and even the denominator gave
nothing away.

The instrumentation was added after the 2026-08-12 `ict_scalp` incident: it
covered the failure that had already happened and not the one that had not.

Now surfaced unconditionally as `skipped_at_load` + `rows_seen_at_load` +
`rows_loaded_at_load`, kept **separate** from the candle-stage `skipped` —
"rejected on shape" and "had no candles" are different failures with different
fixes. Post-fix both reports read `skipped_at_load: {}`, and that empty dict is
trustworthy **only** because the seen/loaded pair ships beside it and is equal.

### 13. The 1d fleet cannot clear the E1 fold gate — and that is most of M20

The 1d round loaded 568 healthy pullback trades and produced **zero** verdicts.
`train_exit_head.py:471` skips a fold under `--min-fold-trades` (default **50**);
the 19 per-calendar-year folds ran **12–42**. Zero could pass. (Folds sum to 548,
+20 in the never-tested first year = the 568 reported.)

Pooling does not rescue it: `donchian` 1d ~23.2/yr, `pullback` 1d ~28.4/yr, every
1d leg pooled ~47.5/yr — still short. 1h pullback is ~145/yr and grades without
difficulty, which is why the scheme has looked healthy.

**The reframing this forces.** Over the live-leg done-condition population:

| lever | open 1d cells | why |
|---|--:|---|
| `exit_head_ml` | 16 | folds 12–42 vs 50 |
| `vol_trail` | 7 | OOS n = 3,4,4,4,5,5,6 vs `MIN_OOS_TRADES` 25 |
| `giveback_stop` | 2 | `insufficient_base` |

**25 of the 39 open cells (64.1%) are the 1d fleet**, across three levers and
three code paths, with one cause: a daily-bar leg produces 31–72 trades *total*
over ~16–20 years against standards calibrated on intraday volume. Each cell is
filed accurately, so the matrix presents 42 items of roughly equal weight when it
is one item of weight 25 and seventeen others — and neither plumbing fix this
session moves any of the 25.

Left as an operator/research-design decision, not chosen in-session: picking a
fold threshold ad hoc so cells go green is the cosmetic anti-pattern, and it
changes the OOS reliability of every verdict the harness produces.

Sibling fix shipped: the skip message named one condition for two predicates and
quoted neither bound. Now `skipped — test 35 < 50 (--min-fold-trades)`. It earned
its keep within the hour — the post-merge round output diagnoses itself.

### 14. Pooling is the binding constraint on the 1h trend cells — measured, then demonstrated

`family_of()` (dataset dir) and `classify()` (harness picker) are two independent
string-matchers answering the same question; they **disagree on 24 of 55 legs
(43.6%)**. Filed at 05:45 as deliberately-not-fixed, because changing the pooling
unit changes every future verdict.

Then measured what it costs:

```
slv_trend_1h  292 trades  -> folds clearing 50 ALONE: 0
uso_trend_1h  218 trades  -> folds clearing 50 ALONE: 0
POOLED        510 trades  -> 8 of 9 test folds clear
```

and the post-merge round **demonstrated** it rather than predicting it: each
trend leg trained alone in its own dir, folds 11–38, all under 50, every family
`folds=0`.

**A correction I owe the record.** I told the board pooling the donchian side was
"strictly additive — it cannot invalidate a recorded result". That is wrong:
eight `trend_donchian*` legs already pool and carry verdicts, **three of them
shipped and live since 2026-07-12**. Additive only for a round scoped to the
currently-ungraded legs. Corrected on the board the same hour.

### 15. A bundled matrix row survived the per-leg explosion

`shadow fleet (turtle_soup, fade_breakout_4h, vwap, ict_scalp_5m,
trend_donchian_1h, mgc_trend_1h, *_prop)` carried **one** set of 8 lever statuses
for six named legs plus a wildcard — exactly
`BL-20260809-COVERAGE-MATRIX-MULTILEG-ROW-ONE-STATUS`, which the 2026-08-09
explosion was performed to eliminate.

Two of the six also had their own rows and **disagreed** with it: `ict_scalp_5m`
on **8 of 8** cells, `mgc_trend_1h` on 6 of 8. The matrix asserted two different
things about one leg depending on which row you read.

Removed those two from the label — both have per-leg evidence of their own, so
the contradiction goes without inventing a status. The rest stays bundled
deliberately: those statuses are fleet-level, and splitting them per-leg would be
fabricating a verdict per cell.

**No guard caught it** because `m20_coverage_rollup.py:306` scopes the
leg-name-resolves check to `execution == "live"` rows — so it skips exactly the
row whose identity is least resolvable. Proposed the all-rows invariant; did not
ship a check that would go red on a known-unfixable row.

## Validation Performed

**Tests** — 14 new tests in `tests/test_exit_head_per_leg.py`. **Each was
verified against a planted defect in the real source**: the named test failed
when the floor check, the maxDD clause, the hard-rule comparison, the no-floor
state, or the usable-fold count was removed, and passed again on restore. A test
that survives a broken implementation is worse than none.

**The roll-up was verified able to FAIL**, not just to pass: it reported the
`status: null` before the fix and `OK` after.

**Reproduction** — all three historical figures (319 / 315 / 311) reproduced
from the one script, so the claim about the divergence is measured, not asserted.

**The corpus-push fix was simulated with `git` stubbed**, four cases: dispatch on
`main` retargets and pushes; a still-declined push fails in **0 s** rather than
burning the 30 s backoff; a `claude/**` dispatch does **not** retarget and does
**not** create the request file; and a rebase conflict re-derives the union
**with the automerge opt-in surviving the hard reset** — that last case caught a
real bug I had just written (`reset --hard` deletes a staged-but-new file, so the
branch would have been pushed with no PR opened, which looks like success).

**Guards** — diff-scoped `dry-run`, `env-gate`, `silent-empty`,
`new-table-wiring`, `strategy-risk`, `writer-conformance`,
`diagnostic-provenance` clean. New `exit-coverage-matrix-guard` registered.

**Self-corrections during the session, each caught by re-probing rather than by
review:**

- A sqlite probe printed `total trades: 0` and then died on `no such function:
  chr`. I refused to treat that `0` as established and re-probed; the re-probe
  found the real cause.
- I recorded `live_trades: 0` as "the arm was not evaluated". It was **my wrong
  `--db`** — I took the first `find` hit (an 8.2 MB stub, mtime Aug 2) over the
  declared data dir (767 MB, synced Aug 12, 4585 trades). That is sub-class **B
  implicit input selection**, the class this repo guards for, committed by me.
  Corrected in `d25acbc`; `load_live_trades` now reports its population.
- A probe reported every corpus row's strategy as `?`. The rows key on `leg`,
  not `strategy` — my probe was wrong, the corpus was fine. Verified by dumping
  the actual keys before concluding.
- A `grep` for `exit_time` in the scalp harness returned five hits, every one an
  unrelated pre-existing use, while the emit dict still lacked the field. Reading
  the **dict** rather than grepping for the name is what caught it; the later
  relaunch was **gated** on that check so an hour of trainer time could not be
  spent rediscovering 0 rows.
- I claimed the daily legs were "structurally unreachable" and should be blocked.
  The arithmetic refuted it (7 of 8 carry 57–79 lifetime trades). I then over-
  corrected to "the split is the binding constraint" — a second sweep refuted
  *that* too: only 1 of 8 crossed at an earlier split. Both readings were stated
  and both were tested; the recorded disposition is the measured one.
- **I preserved the wrong artifact.** I kept the eth/sol 15m E1 reports rather
  than recomputing, reasoning "only their live arm was missing". That is self-
  defeating — the missing live arm is exactly what the corrected `--db` fixes, so
  preserving them preserved the defect. Confirmed still `live: 0` in #8852; a
  re-run is queued behind the running round (#8853).
- I pushed one commit while a guard was failing (`artifact-validity-guard`,
  missing `resolution_criteria`), because a shell `&&` chain masked the exit
  code. Caught on the next CI wake and fixed in the following commit — but the
  push should not have happened.
- `run_guards --base main` reported PASS while `check_backlog_criteria --base
  main` exited 1 — the same flag, the same word, opposite answers. Rather than
  take the friendly reading, I traced it: `run_guards` **prepends `origin/`** to
  its base, so `--base main` there resolves to `origin/main`; the standalone
  script does not, so it used my **local** `main`, which had drifted to a commit
  sharing **no merge base** with HEAD, degenerating the diff to "everything" and
  tripping on a pre-existing row outside my change. Fixed by repointing the local
  ref (`git branch -f main origin/main`), after which both agree at exit 0. Worth
  recording because the failure mode is a guard reporting on a population nobody
  asked about while naming the same flag as the one that scoped it correctly.

### 16. Audited every data-availability claim in the matrix — the record holds

Having found one false data claim (§15/§10d, `squeeze_breakout_4h`), I tested
**all** of them rather than assuming that was the only one: every open cell whose
ref makes a data claim, run through `resolve_data` itself.

**Result: clean.** The four "needs native IBKR history" claims are correct —
`mes_trend_long_1d` (MES→`ES_F_1d`), `mgc_pullback_1d` (MGC→`GC_F_1d`),
`mhg_pullback_1d` (MHG→`HG_F_1d`) and `ict_scalp_mgc_15m` all return
`proxy=True`, and the round driver refuses proxy data for head training by
design. The scalp and equity legs return `proxy=False` and ARE runnable — and
their refs say so, blocking on the E1→E2 **live arm** rather than on data. They
surfaced here only because my keyword filter matched "native"/"history" in the
prose.

So the only false data claim was the one already corrected. Recording the
negative result because *"we checked"* and *"nobody checked"* are different
states, and this file is where the next session looks.

**One error of mine, caught before it became a finding.** The probe reported
`ict_scalp_5m` as *"genuinely no data"*. That is wrong: I passed `data/` as the
data dir for every leg, and the scalp legs resolve from a different one — that
leg produced 14,787 rows in the 5m round. **Implicit input selection**
(CLAUDE.md § "Diagnostic provenance", sub-class B) — a default substituted for
the declared input, in a probe written to audit exactly that class. The audit's
other 17 answers are unaffected; those legs resolve from `data/`, which is what
the sweep and the 1d/1h rounds pass.

### Gaps not yet verified

- **The `ict_scalp` E1 round has NOT been re-run against the correct journal.**
  The two 15m verdicts on record were produced with `live_trades: 0`, so the
  E1→E2 gate's *"live validation set agrees in sign"* arm — which the program doc
  calls a hard stop — is **unevaluated**, not passed. The matrix refs say so.
- The 5m round (4 legs) has not produced usable output at all; it must re-run
  after PR #8825 reaches the trainer via git-sync.
- `ict_scalp_xrp_15m` never completed.
- **The 10 wave-2 `vol_trail` verdicts exist only as workflow artifacts and job
  logs.** They swept successfully but never reached the corpus, so those 10 cells
  remain `pending` in the matrix — correctly, since an un-run-*into-the-corpus*
  cell is `pending`, never an inherited verdict.
- The corpus-push fix is verified by simulation, **not yet by a live run**.
- PR #8825 was still awaiting `pytest-run` at the time of writing.

## Documentation Updated

- `docs/claude/trainer-vm-mode.md` — new § 9.a.1 (worktree lifetime).
- `docs/research/RESEARCH-CAPABILITY-INDEX.md` — routing row for the roll-up.
- `docs/research/exit-refinement-coverage.json` — 13 sweep cells, 7 leg re-keys,
  1 null fixed, 2 `exit_head_ml` cells + a `CORRECTED 2026-08-12` note.
- `docs/claude/health-review-backlog.json` — `BL-20260812-SWEEP-CORPUS-CANNOT-PUSH-TO-MAIN`
  filed with a fix suggestion.
- **This log.**
- ROADMAP.md — **not yet updated** (§ Deferred).

## Contradictions or Drift Found

1. **Three live figures for one unchanged file** (§ Work 1) — resolved by
   single-homing the computation.
2. **Seven matrix legs naming strategies config does not declare** (§ Work 5) —
   the matrix and config had drifted; config wins.
3. **The whole closed-cell population is conditioned on a geometry production
   does not run** for 38 of 47 legs, and no ref said so (§ Work 6).
4. **Not mine:** PR #8815 (concurrent session) wraps a `@contextmanager` in a
   non-guarding `try/except` — the guard cannot fire because the exception is
   raised at `__enter__`, not at construction. Verified by repro and reported on
   the coordination board. Their file, not edited.

## Risks and Follow-Ups

**Technical**

- The E1→E2 gate is still a `gate_note` string a human reads. The per-leg block
  now states the arithmetic; nothing mechanically enforces it.
- The corpus fix ships unexercised against a real protected-branch dispatch.

**Tier-3 product decisions — QUEUED FOR THE OPERATOR, deliberately not enacted**

1. **`trend_donchian_avax_4h` — `trail_decay`.** Path A **PASS** on 3 cells:
   beats net_R **and** maxDD in **both** IS and OOS, improves drawdown in both,
   OOS n=34 (clears the 25 floor).
2. **`gld_pullback_1h` — `trail_decay`.** Three **Path-B** candidates, `rate ok`=Y
   with positive headroom in both windows. Path B's thresholds are **UNSET by
   design**, so this is a candidate to *judge*, not a pass to apply.

Neither was applied. Both change live exit behaviour on real-money legs.

3. **Five shipped exit levers measured net-negative out-of-sample** (all
   paper/prop; `BL-20260813-FIVE-SHIPPED-LEVERS-MEASURED-OOS-NEGATIVE`), largest
   `trend_donchian_eth` `stale_stop` at −10.55R OOS on a 599/117 base.

**Research-design decisions — ALSO QUEUED, and these two gate the milestone**

These are not Tier-3 in the live-lever sense — nothing they touch is on the order
path — but each changes the evidence standard behind every future verdict, so
neither was chosen at 06:00 with the operator asleep.

4. **The 1d fleet's evidence standard (§13).** 25 of the 39 open cells. The
   options are not equivalent: (a) a different standard for daily bars
   (multi-year folds / lower OOS floor / pooled OOS) — cost: 1d verdicts stop
   being comparable to intraday ones, so the standard must be recorded on every
   cell it grades; (b) an explicit terminal *"not gradeable at this standard"*
   status, which makes the done-condition reachable honestly at the cost of
   admitting 25 cells will never carry a verdict; (c) leave them open — the
   current default, which quietly makes M20 unreachable while reading as "in
   progress". **(c) is the only one nobody has chosen deliberately.**
5. **The exit-head pooling unit (§14).** Pooling is the difference between a
   verdict and no verdict for the 1h trend legs (0 folds alone, 8/9 pooled), and
   per-leg attribution survives it because `per_leg_summary` already cuts inside
   a pooled family. But it is **not** free: eight `trend_donchian*` legs already
   pool and three carry shipped live heads, so a mixed round would change what
   those train on. Needs to be per-round-scoped or an explicit per-family table,
   not "make `family_of` match `classify`".

**Blockers** — the 5m round is blocked on PR #8825 reaching the trainer.

## Deferred Items

- ROADMAP.md M20 status row (pending the wave-2 cells landing, so the figure
  written there is the settled one).
- `doc-freshness` skill pass at session close.
- The `vol_trail` wave-2 re-dispatch (blocked on the corpus fix merging).
- Re-run of `ict_scalp_xrp_15m` and the full 5m round.

## Next Recommended Sprint

**Re-dispatch the wave-2 `vol_trail` sweep and the corrected `ict_scalp` E1/5m
rounds, then close the remaining `exit_head_ml` block.**

*Why:* the machinery that was blocking both is now fixed but not yet exercised —
the sweep's evidence path (corpus push) and the round's data path (emit schema +
correct `--db`). Both fixes are cheap to validate and each unblocks a large
tranche: 10 cells for `vol_trail`, up to 29 for `exit_head_ml`.

*Required verification before trusting the output:* confirm the corpus branch
actually opened a PR (not merely pushed); confirm the E1 report's `live_trades`
is **non-zero** before reading any live-agreement verdict; and re-run
`m20_coverage_rollup.py --validate` before quoting a new headline.

## Wrap-Up Check

- [x] **Code inspected directly** — every file listed in § Files was read, not
      inferred; the emit dict and the corpus keys were read rather than grepped
      for, twice catching a false read.
- [x] **Docs reviewed and updated** — trainer-vm-mode, research index, matrix,
      backlog, this log.
- [x] **TRADE-PIPELINE** — not applicable; no pipeline stage changed.
- [x] **Roadmap checked** — M20 read; the status row is deliberately deferred
      until the in-flight cells settle (§ Deferred).
- [x] **Contradictions recorded** — four, including one not mine (§ Contradictions).
- [x] **Unknowns stated** — § Gaps not yet verified lists six, including that the
      headline figure this session produced rests on cells whose geometry vintage
      the roll-up now prints beside it.
- [x] **Tier-3 items proposed, not enacted** — two, § Risks.
