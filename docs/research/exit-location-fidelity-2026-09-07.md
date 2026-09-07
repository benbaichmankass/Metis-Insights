# Backtest↔live exit-location fidelity, per leg — and why the 2.5× is not a fidelity failure

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-155** · work object [`WO-20260907-PER-LEG-BACKTEST-TO-LIVE-EXIT-LOCATION`](../claude/work/objects/WO-20260907-PER-LEG-BACKTEST-TO-LIVE-EXIT-LOCATION.yaml) · branch `claude/mi155-exit-fidelity-20260907`

⚠️ **Tier-1, measurement only.** No per-leg `tp_r` value is proposed. `config/strategies.yaml` is not touched. No cell `status` in `docs/research/exit-refinement-coverage.json` is edited. Nothing is armed.

This is **condition 1 of MI-151's gate** — the one its § 5 says must clear before any leg's target changes.

---

## The answer in four lines

1. **All 44 enabled+live legs grade `insufficient_n`.** Not one is `fidelity_ok`; not one is `fidelity_failed`. **We could not look** — on any leg. (POPULATION: the 44 legs with `enabled: true` and `execution: live` in `config/strategies.yaml` at `main` e4fa09c7.)
2. **The abstain floor is `n_live ≥ 30`.** Max per-leg live n is **8**. Every leg falls below it, and the verdict is invariant for any floor in [10, 30].
3. **The ~2.5× crypto p90 disagreement did NOT reproduce as a fidelity failure. It is explained** — it is a **horizon-composition artifact**, not a backtest↔live divergence.
4. **The basis question resolves:** *measure* in percent-of-entry, *express* the knob as `tp_r`. They are different roles, not competing bases — but the conversion is **lossy**, and that is a finding rather than a footnote.

---

## Taken from priors — inherited, not re-measured

| fact | source |
|---|---|
| 25 of 44 enabled+live legs have no reachable take-profit; `_base.monitor` has declared `{"tp": float}` since it was written and no strategy has ever produced one (AST-verified) | MI-146 [`exit-lever-wiring-audit-2026-09-06.md`](exit-lever-wiring-audit-2026-09-06.md) |
| Percent-of-entry, never R: `trades.stop_loss` is the FINAL trailed stop and `order_packages.sl` is overwritten by the same `_apply_update` path | MI-148 [`bracket-calibration-2026-09-06.md`](bracket-calibration-2026-09-06.md) |
| At a 0.02 reference ATR/entry, 34 of 44 legs (77.3%) rest a target set by the venue clamp | MI-148 |
| ML-2 REFUTED; the answer is a per-leg MFE quantile, no model | MI-151 [`ml2-predictive-bracket-2026-09-06.md`](ml2-predictive-bracket-2026-09-06.md) |

**Re-run as a control, not as a re-derivation.** `config/strategies.yaml` yields **44** enabled+live legs, reproducing MI-146's and MI-148's count exactly.

---

## The instrument, and the positive control it had to pass first

**`scripts/research/exit_location_fidelity.py`** (+ `tests/test_exit_location_fidelity.py`, 23 tests; `--selftest`, 7 invariants). It extends MI-148's grader rather than replacing it: `quantile` is **imported** from `src/runtime/bracket_calibration.py`, so two definitions of p90 cannot drift apart, and `m31_mfe_parity.py`'s three refusals are inherited wholesale.

⚠️ **`m31_mfe_parity.py` already grades backtest↔live MFE parity — but in R** (harness `mfe_r` vs live `peak_r`), the basis MI-148 rules out. This module is that comparison **moved onto the percent-of-entry basis**. It is not a second opinion on the same quantity, and it is not a parallel grader.

### The positive control (mandatory before trusting any empty result)

Before quoting a number of its own, the pipeline had to reproduce MI-148's published View 2.

**POPULATION: `/api/diag/position_telemetry?limit=1000`, read 2026-09-07T~07:30Z; rows with `peak_gradeable: true` and readable `peak_r`/`cap_r` — MI-148's own gate. n=103 of 171 returned.**

| arm | MI-148 published (2026-09-06) | reproduced here (2026-09-07) |
|---|---|---|
| crypto (Bybit-traded) | n=63 · p50 1.39% · p75 6.15% · **p90 9.70%** · 4/63 reached cap | n=**64** · p50 1.49% · p75 6.18% · **p90 9.69%** · 4/64 |
| non-crypto | n=39 · p50 1.19% · p75 3.00% · **p90 3.42%** · 0/39 | n=**39** · p50 1.19% · p75 3.00% · **p90 3.42%** · 0/39 |

The non-crypto arm reproduces to four significant figures; the crypto arm gains one row overnight and moves 0.01pp. **The instrument is reading the same quantity MI-148 read.** Every "we could not look" below is therefore a real absence, not a dead probe.

---

## Verdict table — all 44 enabled+live legs, both n's on every row

**POPULATION (live):** `/api/diag/position_telemetry?limit=1000`, read 2026-09-07, 171 rows returned, 103 passing the grading gate. ⚠️ **`peak_r_is_lower_bound` is `true` on 171 of 171** — every live figure is a LOWER BOUND on the excursion.
**POPULATION (backtest):** the only committed percent-of-entry MFE distribution in the repo — **3 legs**, transcribed to [`data/backtest-mfe-reference-2026-09-07.json`](data/backtest-mfe-reference-2026-09-07.json) from MI-151's table (trainer run `34041656868`, issue #11154).

Legs with at least one live row (n_live descending). `n_bt = 0` on every row; the reason column says which side was missing.

| leg | tf | **n_live** | **n_backtest** | live p90 | bt p90 | verdict |
|---|---|---:|---:|---:|---:|---|
| `trend_donchian_avax_4h` | 4h | 7 | 0 | 9.86% | — | `insufficient_n` |
| `trend_donchian_sol_4h` | 4h | 7 | 0 | 9.57% | — | `insufficient_n` |
| `eth_pullback_2h` | 2h | 6 | 0 | 8.13% | — | `insufficient_n` |
| `trend_donchian_ada_4h` | 4h | 6 | 0 | 9.89% | — | `insufficient_n` |
| `qqq_pullback_1h` | 1h | 5 | 0 | 2.61% | — | `insufficient_n` |
| `spy_pullback_1h` | 1h | 5 | 0 | 1.28% | — | `insufficient_n` |
| `trend_donchian_eth` | 1h | 5 | 0 | 1.54% | — | `insufficient_n` |
| `trend_donchian_eth_4h` | 4h | 5 | 0 | 9.59% | — | `insufficient_n` |
| `xrp_pullback_2h` | 2h | 5 | 0 | 6.56% | — | `insufficient_n` |
| `ada_pullback_2h` | 2h | 4 | 0 | 7.86% | — | `insufficient_n` |
| `trend_donchian` | 1h | 4 | 0 | 7.27% | — | `insufficient_n` |
| `uso_trend_1h` | 1h | 4 | 0 | 3.20% | — | `insufficient_n` |
| `gld_pullback_1h` | 1h | 3 | 0 | 1.06% | — | `insufficient_n` |
| `mhg_pullback_1d` | 1d | 2 | 0 | 4.00% | — | `insufficient_n` |
| `sol_pullback_2h` | 2h | 2 | 0 | 4.99% | — | `insufficient_n` |
| `tlt_pullback_1d` | 1d | 2 | 0 | 3.27% | — | `insufficient_n` |
| `tlt_pullback_1h` | 1h | 2 | 0 | 0.30% | — | `insufficient_n` |
| `trend_donchian_sol` | 1h | 2 | 0 | 2.06% | — | `insufficient_n` |
| `gdx_pullback_1d` · `gld_pullback_1d` · `iaum_pullback_1d` · `ief_pullback_1d` · `iwm_trend_long_1d` · `mes_trend_long_1d` · `mgc_pullback_1d` · `qld_trend_long_1d` · `qqq_trend_long_1d` · `scha_trend_long_1d` · `slv_pullback_1d` · `spy_trend_long_1d` · `trend_donchian_xrp_4h` | — | 1 | 0 | 0.00–3.83% | — | `insufficient_n` (13 legs) |
| `ict_scalp_5m` · `ict_scalp_sol_5m` · `ict_scalp_xrp_5m` · `ict_scalp_avax_5m` · `ict_scalp_eth_15m` · `ict_scalp_mgc_15m` · `ict_scalp_sol_15m` · `ict_scalp_xrp_15m` · `splg_trend_long_1d` · `squeeze_breakout_4h` · `tqqq_trend_long_1d` · `trend_donchian_eth_prop` · `trend_donchian_sol_prop` | — | **0** | 0 | — | — | `insufficient_n` (13 legs) |

**TALLY over the 44 enabled+live legs: `fidelity_ok` 0 · `fidelity_failed` 0 · `insufficient_n` 44.**

**Below the abstain floor (`n_live < 30`): 44 of 44.** Every leg. **No leg in the fleet may take a target from the MFE-quantile route today.**

### The abstain floor, and why it is 30

Two arguments; the **stricter** is adopted.

1. **An estimator floor of 20 — necessary, not sufficient.** `bracket_calibration.quantile` interpolates at `pos = q·(n−1)`. At q=0.90 the estimate lies between the top two order statistics whenever `floor(0.9(n−1)) ≥ n−2`, i.e. **for all n ≤ 11**. Below that, "p90" is a synonym for *the largest one or two trades this leg ever had*. n=20 is where the top decile holds ~3 observations and ≥2 sit strictly above the estimate.
2. **The repo's existing precedent is 30, and it is adopted.** `scripts/research/backtest_fidelity_calibrate.py` sets `MIN_LIVE_N = 30` for its own backtest↔live comparison, below which it returns `insufficient-live`. Inventing a *looser* floor here would be indefensible: this module estimates a **0.90 tail** quantile, which needs **more** n than the distribution-centre comparison that floor was set for, not less.

⚠️ **A floor is not a confidence threshold.** Binomial SE on a 10% exceedance rate at n=30 is 5.5pp — the tail is still estimated to within ±55% of itself. A leg clearing 30 has earned a *look*, not a *number*.

⚠️ **On today's fleet the verdict does not turn on this choice.** Max per-leg n_live is 8, so every leg abstains at any floor in [10, 30]. The floor is stated because it must be fixed **before** the soak deepens — not because it is currently doing the work.

---

## The ~2.5× — it did not reproduce, and here is what it was

MI-151 set backtest `trend_donchian_BTCUSDT_15m` **p90 3.87% (n=3,194)** against MI-148's live crypto **p90 9.70% (n=63)** and called it *"two populations disagreeing by ~2.5× about the quantity a target would be set from."*

**Both numbers are correct. They are not measurements of the same thing.**

### Fact 1 — the two populations have no timeframe in common

MEASURED, from `config/strategies.yaml` at `main` e4fa09c7 (POPULATION: all legs whose name begins `trend_donchian` and which are enabled+live — **10 legs**; their declared timeframes are exactly `{1h, 4h}`):

> **Every live `trend_donchian` leg runs at 1h or 4h. None runs at 15m.**

MI-151's corpus is **15m only**. So the backtest legs have **no timeframe-matched live counterpart at all**, and the live crypto arm it was compared against contains **zero 15m rows**.

### Fact 2 — the live crypto arm's p90 is set by its 4h legs

MEASURED, decomposing the reproduced crypto arm (POPULATION: the n=64 rows above):

| family within the crypto arm | n | p50 | p90 |
|---|---:|---:|---:|
| donchian | 37 | 1.99% | **9.77%** |
| pullback | 27 | 1.01% | 7.07% |

Of those 37 donchian rows, the four legs carrying the top of the distribution — `trend_donchian_avax_4h`, `_sol_4h`, `_ada_4h`, `_eth_4h` — are **all 4h**.

### Fact 3 — the horizon ladder, measured **inside the live book alone**

This is the control that settles it, because it involves **no backtest at all**.

**POPULATION: the n=64 Bybit-traded crypto rows of the reproduced arm, joined to each leg's declared `timeframe` in `config/strategies.yaml`.**

| timeframe | n | p50 | p75 | **p90** |
|---|---:|---:|---:|---:|
| 1h | 11 | 0.84% | 1.56% | **2.16%** |
| 2h | 27 | 1.01% | 4.10% | **7.07%** |
| 4h | 26 | 4.72% | 9.52% | **9.77%** |

**Moving 1h → 4h inside the live crypto book moves p90 by 4.5×** — larger than the 2.5× in question, with the harness nowhere in the picture.

### INFERRED — the conclusion, and its inputs

**INFERRED** (from Facts 1–3 above): MI-151's ~2.5× is **a horizon-composition artifact**. It compares a 15m book against a live arm whose p90 is set by 4h legs. Excursion in percent-of-entry grows with holding horizon; a 15m book *should* show a smaller p90 than a 4h book, and 2.5× is well inside the 4.5× the live book alone spans across that range. **Nothing in it is evidence that the harness misplaces exits.**

Supporting: backtest 15m p90 (BTC 3.87% · ETH 5.62% · ADA 6.84%) sits in the same range as **live 1h p90 (2.16%)** — one step up the ladder — rather than near live 4h (9.77%).

⚠️ **This is INFERRED, not MEASURED, and the check that would settle it does not exist.** A direct test needs either a live 15m donchian leg (there is none) or a 1h/4h backtest corpus (there is none). **I could not close it, and I am not claiming to have.** What I have established is that a horizon explanation is *sufficient* and is *measured within one book* — which removes the ~2.5× as evidence of infidelity, without positively establishing fidelity.

⚠️ **Do not now quote "backtest and live agree."** They have not been compared. The correct statement is: **the disagreement that was blocking the gate was not a disagreement about fidelity, and the gate is still not cleared** — for the separate reason that every leg is below the abstain floor.

---

## The basis ambiguity, resolved

MI-151 § 5 says "a per-leg MFE quantile **in R**". MI-148 says **percent-of-entry, never R**. MI-151's own condition 1 says fidelity is measured **on the percent-of-entry basis**.

**They are reconcilable, and nobody had written it down. Written down:**

> **MEASURE in percent-of-entry. EXPRESS the config knob as `tp_r`.**

Different roles, not competing bases:
<!-- population-ok: 9.9% is the declared constant TP_VENUE_CAP_PCT in src/runtime/tp_venue_cap.py, not a sample statistic — it has no population by construction. -->
- **Percent-of-entry is the only basis on which *prediction vs artefact* is decidable**, because `TP_VENUE_CAP_PCT` is itself 9.9% *of entry* (the declared constant in `src/runtime/tp_venue_cap.py`, not a measurement), and the R denominator is measurably contaminated (`trades.stop_loss` is the FINAL trailed stop).
- **`tp_r` is what `config/strategies.yaml` actually takes**, so any proposal must land there.

### ⚠️ But the conversion is LOSSY, and that is a finding

`tp_venue_cap.py` states that **no `tp_r` reproduces the clamp**: `cap_r = TP_VENUE_CAP_PCT × entry / risk` is a percent-of-entry set against a multiple-of-risk, so the two coincide **at exactly one risk value**. A leg whose measured percent-of-entry target is expressed as a single `tp_r` gets a target that **drifts with per-trade risk** — tighter than intended on wide-risk trades, looser on narrow-risk ones.

So the reconciliation holds, **with a stated defect**: the config schema cannot express the quantity that was measured. Three honest options, none of which is mine to take (all Tier-3):

1. Accept the drift and record the risk value the `tp_r` was calibrated at — the instrument reports `tp_r_at_median_risk` and labels it so it can never be read as an identity.
2. Add a percent-of-entry target key to the schema, so the measured quantity can be stated directly.
3. Keep `tp_r` and accept that the venue clamp remains the binding target on wide-risk trades — i.e. the status quo, but *declared*.

**Option 2 is the only one under which the measured quantity and the configured quantity are the same thing.** Recording that is the point; choosing is the operator's.

---

## Things I found that are broken, filed rather than walked past

1. **`position_telemetry` is blind to the entire `ict_scalp` family.** POPULATION: the 171 telemetry rows read 2026-09-07 — **0 rows** carry an `ict_scalp*` strategy, across 8 enabled+live `ict_scalp` legs. MI-148 graded **162** `ict_scalp` trades in its `--exits` view over 8 legs, so the trades exist; the telemetry does not see them. **The MFE instrument is structurally blind to the one family MI-146, MI-148 and MI-151 all call the fleet's existence proof of a calibrated bracket.**
2. **MI-147's path citation is wrong.** It names `scripts/ops/backtest_fidelity_calibrate.py`; the file is at **`scripts/research/backtest_fidelity_calibrate.py`**. `scripts/ops/` has no such file.
3. **MI-147's substantive claim is VERIFIED** (checked here, not inherited): the calibrator grades **win-rate agreement + a two-sample KS on realised R**. A term census over the file returns **0** occurrences each of `mfe`, `peak`, `excursion`, `exit_price`, `pct_of_entry`. It does not grade exit location, and `R_BASES = ("stop_distance", "sign_proxy")` puts it on the R basis MI-148 rules out. ⚠️ **I did NOT verify the "has never been run" half** — that needs a run-history read I did not perform.
4. **This session has NO GitHub API write access at all**, and it is worth naming precisely because two separate protocols silently degrade on it. `add_issue_comment`, `create_pull_request` and PR comments all return `403 Resource not accessible by integration` — on both repo names, via the MCP tool and via REST with `GH_TOKEN`. Only `git push` works. Consequences, both recorded rather than worked around:
   - **The coordination-board START comment could not be posted** to issue #6927. CLAUDE.md already records a prior session hitting `issue_write create -> 403` (`OI-20260901-CLAUDE-CHANNEL-SEPARATION-SHIPPED-BUT-UNPROVEN`). **The board protocol is unenforceable from a session with this token scope**, which means the board understates concurrent activity by an unknown amount.
   - **PR #11220's body is the `claude-pr-automerge` stub, not this work's description.** The automerge workflow opened the PR on the arming file before `pr-opener` could run with the real body, and `pr-opener` then correctly refused (*"a pull request … already exists"*, written to `automation/pr-results/`). The body could not be corrected afterwards for the same 403. **This memo is the durable artifact; the PR body is not.**


---

## Honest limits — what I could NOT establish

- **I did not measure fidelity on any leg.** Zero legs had both sides at n. The deliverable is the verdict *table* and the *reason* each leg abstains — not a fidelity result.
- **The horizon explanation is INFERRED and not closeable today.** No live 15m donchian leg exists and no 1h/4h backtest corpus exists. It is sufficient and measured-within-one-book; it is not proven.
- **Every live figure is a LOWER BOUND** (`peak_r_is_lower_bound` true on 171/171). Safe for falsifying a too-far target; unsafe for justifying a too-near one.
- **The live horizon ladder is n=11/27/26 per rung** and confounds timeframe with symbol and family — the 1h rung is donchian-heavy, the 2h rung pullback-heavy. It is strong enough to explain a 2.5× and **too thin to calibrate anything**.
- **The 1d rung is excluded from the crypto ladder** and breaks monotonicity in the all-symbol view (p90 3.66%, n=16) because every 1d leg is a non-crypto equity/ETF — a different asset class, not a longer-horizon crypto trade.
- **The backtest side is a transcription, not a corpus.** The 9,814-row corpus was never committed. A key census over all committed corpora (`e35-bracket-corpus.jsonl` n=8,520 · `e35-bracket-corpus-history.jsonl` n=10,547 · `m20-sweep-corpus.jsonl` n=1,379 — **20,446 rows**) returns **zero** keys containing `mfe`, `peak` or `excurs`, against a positive control that does return those files' common keys. Re-deriving the backtest side needs a trainer dispatch.
- **`take_profit_1`'s cleanliness remains an argument from the fleet's current state**, per MI-148, and expires when a `tp` producer ships.
- **No P&L claim is made anywhere in this memo**, per E3.6's ordering.

---

## What this says to do next

**The gate is not cleared, and the blocker has moved.** It was *"the two populations disagree by 2.5× and nobody knows why"*. It is now *"there is no leg with enough live n to compare, and no timeframe-matched backtest corpus to compare it against."* That is a **more tractable** blocker and it names its own two remedies:

1. **A timeframe-matched harness run** — `trend_donchian` at **1h and 4h** on the symbols the fleet actually trades, emitting per-trade MFE in percent-of-entry. This is the un-circular source MI-148 § "What is NOT yet available" already named as the next unit of work, and it is **not blocked on anything in this thread**. It is what makes `n_backtest > 0` for a real leg.
2. **Soak depth on the live side**, which is a clock problem, not a build problem — plus the `ict_scalp` telemetry gap above, which is a build problem and currently costs the fleet its best-calibrated family's entire MFE record.

⚠️ **Neither is a reason to set a target now.** `insufficient_n` on 44 of 44 legs is the finding, and it means exactly what it says: **we could not look.**

---

*MEASURED 2026-09-07 against the live trader over Caddy (`/api/diag/position_telemetry?limit=1000`, 171 rows, read ~07:30Z) and `config/strategies.yaml` at repo `origin/main` e4fa09c7. Backtest side transcribed from MI-151 (trainer run `34041656868`, issue #11154) — corpus not committed, not re-derivable in this container. Reproducible offline with no numeric stack: `python3 scripts/research/exit_location_fidelity.py --selftest`.*
