# The `ict_scalp` seam: the harness runs the live decision, and hands it one thing the live builder does not

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> MI-321 · `2026-09-18` · successor to MI-320 ([`harness-provenance-2026-09-18.md`](harness-provenance-2026-09-18.md), #12561)
>
> **Measurement only.** `config/`, `src/` and `scripts/backtest_ict_scalp.py` are
> read and never written. Findings are FILED.

## 0 — The answer

MI-320 established that `scripts/backtest_ict_scalp.py` **imports and calls the
live `order_package`** rather than reimplementing it — so the entry decision is
exact by construction and the question moves to what the harness FEEDS it. Two
seams were measured and they come out opposite ways.

| seam | verdict |
|---|---|
| **window** — harness slides 50 bars, the live builder fetches 200 | **`identical`** — 46 accepts each, **0** differing, **0** one-sided, over **9,879 graded bars** |
| **config** — the harness reads ONE hardcoded YAML block; the fleet runs **8** legs, **all `execution: live`** | **7 of 8 restorable; `ict_scalp_xrp_5m` is not** |

**The one finding: `ict_scalp_xrp_5m`'s off-cell regime suppression cannot be
expressed by this harness at all — and that leg's own evidence is conditional on
it.** The live variant builder's docstring says so in terms:

> *"Omit `off_cells`/`vol_spec` for a variant whose OWN evidence passes UNGATED
> (SOL, AVAX) — **XRP's pass was conditional on this gate (ungated 2/4 folds
> fails; gated 4/4 passes)**, so XRP carries both."*

So a run of this harness for that leg produces **the arm that failed its own
walk-forward**, and nothing in its output says so.

## 1 — Populations

| population | n |
|---|---:|
| candles graded, both windows, same bar | **9,879** (`data/btc_1m_sample.csv`, 10,080 bars, first 200 reserved so both windows are full) |
| accepts found | **46** at 50 bars, **46** at 200 |
| `ict_scalp` legs | **8**, all `execution: live`, resolved via `pipeline.monitor_unit_for` |
| harness CLI flags read from its AST | **32** |

## 2 — The window seam is closed, and MI-319's number must NOT be carried here

`fvg_range_15m` disagreed with its harness on **21.9% of bars** at its declared
minimum because its ADX is a three-deep `ewm(alpha=1/period, adjust=False)`
recursion — a quantity whose value depends on how much history preceded it.
**`ict_scalp` has no such quantity.** `_add_atr` is
`tr.rolling(period, min_periods=period).mean()` — finite — and the sweep,
displacement and FVG scans are bounded slices.

That is a reason to expect agreement, not evidence of it, so it was measured:
the **same live `order_package`** was called on every bar at both window
lengths.

```
graded bars : 9,879
accepts @50 : 46          accepts @200: 46
both accept : 46          of which differing geometry: 0
one-sided   : 0 / 0
```

⚠️ **The 46 is the load-bearing half of this negative.** Two probes that
accepted nothing would agree about nothing, which is why
`no_accepts_in_population` is a separate state and not folded into `identical` —
the same refusal MI-319 made about a shipped-parameter population.

⚠️ **The harness already knows the live window is 200.** `backtest_ict_scalp.py:561`
slices `df.iloc[max(0, i+1-200) : i+1]` under the comment *"Decision-time stamp
over the live builder's 200-bar fetch window"* — for the regime **stamp**, while
the entry call gets 50. The asymmetry is deliberate and documented (a sliding
window makes the walk `O(n·window)` instead of `O(n²)`), and this measurement
says it costs nothing on this population.

## 3 — The config seam, and why one predicate was not enough

The harness's `_load_yaml_params()` reads
`load_strategy_config().get("ict_scalp_5m", {})` — **hardcoded**. Seven of the
eight live legs run through `_ict_scalp_variant_builder` against their own
block. `--strategy-name` exists but, by its own help text, only *"stamp[s] the
leg name on every emitted row's `strategy` field"*.

Each differing key is graded by **two independent measurements joined**, not by
one clever predicate:

1. does the harness declare a CLI flag for it (read from the **AST**, so it is
   the value the parser uses rather than what the help text says)?
2. does the key reach a **SKIP** — an `if`/`while` whose test names it and whose
   body reaches `continue` / `break` / `return` / `raise` — on **each side**?

⚠️ **A one-sided predicate was tried first and abandoned, and saying so is the
point.** *"Does this name reach a control-flow decision in the harness?"* gives
`vol_spec` a **TRUE** — it guards `if vol_spec:` inside the routine that stamps
a regime **label**. What separates `vol_spec` from `timeframe` is not how the
harness treats it but that **the live side SUPPRESSES AN ENTRY on it and the
harness never skips at all**. Measuring both sides the same way and joining them
says that; a predicate tuned until it produced the answer already believed would
not — and would be the fitted classifier MI-320 had to widen for the same
reason.

| leg | execution | differing key | verdict |
|---|---|---|---|
| **`ict_scalp_xrp_5m`** | **live** | `off_cells` | **`not_expressible`** — live skips on it, harness never does, **no flag exists** |
| **`ict_scalp_xrp_5m`** | **live** | `vol_spec` | **`asymmetric_gate`** — `--vol-spec-json` exists and reaches **no skip**, so a run can be *labelled* with the spec while behaving as if unset |
| `ict_scalp_xrp_15m` · `sol_15m` · `mgc_15m` | live | `timeframe` | `restorable` |
| `ict_scalp_eth_15m` | live | `stale_exit_bars`, `stale_exit_below_r`, `timeframe` | `restorable` |
| `ict_scalp_5m` · `sol_5m` · `avax_5m` | live | *(none)* | — |

**Positive control for the live half:** `strategy_signal_builders.py:797-802`
reads `off_cells = vcfg.get("off_cells") or []` and gates on
`if off_cells and vol_spec:`. **Negative control for the harness half:**
`off_cells` occurs **0 times** in `backtest_ict_scalp.py`, while `vol_spec`
occurs **12** — every one of them inside `_stamp_decision_time_regime`. **A
count reports it as implemented; the skip test does not.**

### 3.1 — What a DEFAULT run of this harness represents

With no flags passed, `_load_yaml_params()` gives the `ict_scalp_5m` block, so a
default run is faithful to **three** of the eight live legs exactly —
`ict_scalp_5m`, `ict_scalp_sol_5m`, `ict_scalp_avax_5m`, which differ from that
block in **0** consumable keys. It is faithful to the other five only if the
caller passed the right flags, and **never** to `ict_scalp_xrp_5m`, for which no
flag exists.

⚠️ **Whether any real caller passed those flags is UNMEASURED.** The claim here
is about what the harness CAN represent, not about what any particular run did.

## 3.2 — An incidental observation, recorded because its own backlog row is not on this branch

While verifying this PR, `check_pr_landing.py --base main` **FAILED** with
`state=undeclared`, naming **eight** files this diff never touched — four `src/`
paths as *"Tier-2 by name"* and four `.github/pr-landing/` declarations as
*"CHANGES THE LANDING MACHINERY"*. Cause: **local `main` was 4 commits behind
`origin/main`**, so the guard diffed across other sessions' merges. `git branch
-f main origin/main` and the same command returns `OK`.

**This is the third occurrence in this lane, and the third one changes the
claim.** The first two read as *a session forgot to fetch*. Here the branch had
been created FROM `origin/main` minutes earlier and was current — only the local
`main` **ref** was stale — and it had been re-pointed once already in the same
session. This repo lands automation commits to `main` continuously (PR-queue
receipts, work digests, settled-PR reconciliation), so the ref goes stale again
between guard runs. **"Fetch before running a guard" is therefore not a habit a
session can hold; the remedy is the guard printing its RESOLVED BASE**, which is
what `check_timestamp_comparisons` already does for its diff path.

The row that owns this class is filed on **PR #12561** (MI-320) and is **not on
`main` yet**, so this memo deliberately does **not** name it by id — from this
branch that id resolves to nothing, and `artifact-validity-guard` caught the
attempt and was right to. A memo about a guard failing on a stale baseline must
not itself carry a reference that reads as tracked while being tracked by
nobody. **A PR number resolves; an unlanded backlog id does not.** Once #12561
lands, this instance belongs in that row.

## 4 — What this does NOT establish

- **No backtest was re-run and none is proposed.** Whether `ict_scalp_xrp_5m`'s
  published evidence was produced by this harness, by the M27 Batch-1 tooling,
  or by something else **was not traced** — so this says the harness *cannot
  reproduce the gated arm today*, not that any published number is wrong.
- **One symbol, one candle file, one repo state** (`bd70cdd6c`). The window
  result is BTCUSDT 1m re-labelled 5m, which is the harness's own sample; a
  different instrument could in principle differ, though no history-dependent
  quantity exists for it to differ through.
- **The exit half is untouched.** Only the entry `order_package` call is
  compared.
- **`--strategy-name` is not shown to have caused a wrong published number.**
  It is shown to relabel output without rerouting the config block, which for
  six of the seven variants is recoverable by flags a caller may or may not have
  passed — and that is unmeasured.

## 5 — Filed, not fixed

`BL-20260918-THE-ICT-SCALP-HARNESS-CANNOT-EXPRESS-THE-OFF-CELL-SUPPRESSION-ITS-OWN-XRP-LEG-S-EVIDENCE-IS-CONDITIONAL-ON`.
The fix is in `scripts/backtest_ict_scalp.py`, which is outside this lane's
Tier-1 surface — and note `MI-242` (`ready`, operator-approved 2026-09-10) is a
standing item to add `scripts/backtest_*.py` to `TIER1_SURFACE`, which would put
it in reach.

### 5.1 — ADDENDUM 2026-09-22 (checklist row E28): HALF of this is now fixed, and it matters which half

⚠️ **The row id above no longer resolves to anything a session reads.** The four
review backlogs were retired by the 2026-09-21 operating reset and archived under
`docs/archive/2026-09-21-operating-reset/`; the intake is now
`docs/claude/work/PIPELINE.jsonl`. This paragraph is the carrier until something
re-files it.

**FIXED — the hardcoded config read (§ 3).**
`backtest_ict_scalp.py::_load_yaml_params(name)` now takes the block that
`--strategy-name` selects, and RAISES on a name that is not in
`config/strategies.yaml` rather than serving `ict_scalp_5m`'s block. The default
argument is still the historical literal, so every existing caller — including
`mi321_ict_scalp_seam.py`, which calls it with no argument precisely to describe
a DEFAULT run — is byte-identical, and § 3.1 stands unchanged. It was fixed
because E28 routes all eight legs to this harness through
`regime_debt_matrix.classify()`: at the old signature that would have measured
one leg's config eight times under eight names and written eight
`coverage_state: "measured"` records, which is worse than the `no_harness` they
replace — a wrong number reads as evidence and a missing one does not.

**NOT FIXED — `off_cells` / `vol_spec` (§ 3, the `not_expressible` and
`asymmetric_gate` rows).** No flag was added for either, and none should be
added for `vol_spec`: `--vol-spec-json` reaches no skip, so forwarding it would
LABEL a run with the spec while it behaves as if unset. `regime_debt_matrix`
grades `ict_scalp_xrp_5m` **`approximate`** and names both keys as omitted
levers, and its committed evidence record therefore measures the **UNGATED**
arm — the one the live builder's own docstring says fails 2 of 4 folds. Porting
an off-cell skip into the harness remains the real fix.

⚠️ **And § 4's first bullet still holds after the E28 run.** That run does not
establish that any previously published `ict_scalp_xrp_5m` number was wrong; it
establishes what this harness measures today, on a stated population, under a
decision rule registered before it.

## 6 — Reproduce

```bash
python3 scripts/research/mi321_ict_scalp_seam.py --selftest   # 26 checks
python3 scripts/research/mi321_ict_scalp_seam.py --run
```

Artifact: `docs/research/mi321-ict-scalp-seam-2026-09-18.json`.
Carrier: `docs/claude/work/objects/WO-20260918-DOES-THE-ICT-SCALP-HARNESS-FEED-THE-LIVE-DECISION-LIVE-INPUTS.yaml`.
