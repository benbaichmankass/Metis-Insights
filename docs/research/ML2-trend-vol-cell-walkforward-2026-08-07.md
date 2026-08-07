# ML2 — walk-forward of the six authored `trend_vol` OFF-cells (2026-08-07)

**PROPOSE ONLY.** Every cell change is Tier-3. Nothing here was applied.

## Population (state it before reading any number)

- Cells: the six `trend_vol` entries in `config/regime_policy.yaml`.
- Tool: `scripts/research/regime_cell_walkforward.py --strategy X --regime Y --vol Z
  --vol-labels /tmp/btc_vol_labels.jsonl`, run on the trainer (relay #8564).
- Trades: BTCUSDT reconstructed from Binance-vision candles, 2024-08 → 2026-08.
  `trend_donchian` 1h (`fidelity=approximate`, 5 omitted levers);
  `squeeze_breakout_4h` 4h (`fidelity=faithful`).
- Vol labels: `ml_vol_label_replay` over `market_features` BTCUSDT 15m v520,
  175,272 rows, head `btc-regime-15m-lgbm-fc-pcv-v2`, volatile share 9.36%.
- Verdict basis: fixed `FOLD_PANEL=(3,4,5)`; `*_stable_drag` requires pooled<0
  AND strict majority-negative in EVERY panel member.

**Live fidelity of the labels is UNVERIFIED** — see "Parity" below. These are
backtest verdicts, not live-parity-confirmed ones.

## Results — graded on the direction each cell actually gates

| cell | authored | n | pooled R (gated dir) | maj-neg k3/k4/k5 | verdict |
|---|---|--:|--:|---|---|
| trending/volatile `trend_donchian` | `long: off` | 9 | **−4.9495** | T/T/T | **stable_drag — JUSTIFIED** |
| trending/calm `squeeze_breakout_4h` | `short: off` | 9 (6 short) | **+0.8713** | T/F/T | fold_sensitive — RETIREMENT CANDIDATE |
| transitional/calm `trend_donchian` | `long: off` | 30 | **+1.5624** | F/F/T | fold_sensitive — RETIREMENT CANDIDATE |
| chop/calm `trend_donchian` | `long: off` | 43 | −1.9697 | F/F/T | fold_sensitive — RETIREMENT CANDIDATE |
| trending/volatile `ict_scalp_5m` | `long+short: off` | — | — | — | UNGRADABLE |
| chop/volatile `ict_scalp_5m` | `long+short: off` | — | — | — | UNGRADABLE |

**1 of 6 affirmatively justified. 3 fail. 2 have no evidence path.**

Per Rule 1 of the `regime-selectivity` skill, `*_fold_sensitive` is a
retirement candidate, NOT a pass.

### The one that passes

`trending/volatile trend_donchian long`: pooled −4.9495 over 9 trades,
majority-negative under all three panel fold counts, not fold-sensitive.
Caveat: **n=9** — the volatile regime is only 9.36% of bars, so thin-n here is
structural, not incidental. The verdict is stable across the panel, which is
what the panel exists to test, but 9 trades is 9 trades.

### Two cells look inverted, not merely unjustified

**`squeeze_breakout_4h` trending/calm** gates **short** (pooled **+0.8713**)
while **long** is pooled **−2.4341 with `long_stable_drag=True`** and is left
**on** (`{ long: on, short: off }`). The authored cell gates the direction that
made money and permits the direction that passes the drag test. That is a
stronger claim than "unjustified" and is the one worth looking at first.

**`transitional/calm trend_donchian`** gates a **pooled-POSITIVE** long
(+1.5624 over 30 trades).

### `ict_scalp_5m` cannot be graded by this harness

Both its cells return `ERROR: unclassifiable (no donchian/pullback/squeeze
params or no symbol/timeframe)`. Two of six authored cells therefore have **no
walk-forward evidence path at all** — they are neither justified nor refuted.
That gap is itself a finding: they were authored under the 2026-07-20 Phase-4
packet and cannot currently be re-checked by the tool the gate doc points at.

## Do NOT compare these to the policy file's inline comments as-is

`config/regime_policy.yaml` annotates these cells with full-sample dollars from
the debt matrix (`−$224 / 136t`, `−$55 / 30t`, `−$356 / 43t`, `−$218 / 11t`).
Those are a DIFFERENT population from the R figures above and the trade counts
do not line up (the chop/calm comment says 11t where the walk-forward finds 43;
transitional/calm says 43t where it finds 30). This is not asserted as a
contradiction — the two were computed over different feeds and windows. It is
flagged because someone will otherwise read the two side by side and treat the
mismatch as either a confirmation or a refutation. It is neither until the
populations are reconciled.

## Parity: unverified, and honestly unmeasurable today

The labels file ends **2026-06-30T22:30:00Z** (dataset v520). The ML vol axis
went live **2026-06-29**. Overlap with the live audit corpus is **4 rows**.

`verify` previously reported `comparable: 208 / agreement_pct: 95.67` over that
window because it clamped 204 out-of-range rows onto the final bar; fixed in
`ecc414e` (PR #8553). Establishing real parity needs a dataset rebuilt through
August, re-replayed, then re-verified. Until then these cells are graded on
backtest evidence whose live fidelity is unconfirmed.

## Recommended next steps (all Tier-3 to enact)

1. Rebuild `market_features` BTCUSDT 15m through the current date; re-replay;
   re-run `verify` (now with `--min-overlap`) to establish parity properly.
2. Re-examine `squeeze_breakout_4h` trending/calm — the direction inversion is
   the highest-value item here and does not depend on parity.
3. Give `ict_scalp_5m` an evidence path, or record explicitly that its two
   cells rest on the Phase-4 packet alone.
4. Do not retire any cell on this evidence alone; walk-forward + parity first.

---

## CORRECTION — 2026-08-07 (later same day): these verdicts are NOT decision-grade

> Appended in place (this doc is maintained, not superseded). Session:
> `S-ML2-GATE-FLOOR-2026-08-07`. **Nothing above was acted on, and nothing
> should be.**

Two independent errors were found in the run that produced the table above. Both
change what the table means; neither changes any live cell.

### 1. The walk-forward gate had no sample floor — so `n=9` verdicts are artifacts

`*_stable_drag` is the gate `regime-selectivity` Rule 2 makes a Tier-3 OFF-cell
clear. It applied **no minimum-n test at all**, and `direction_walkforward.analyze`
builds **equal-COUNT folds by trade order**. Measured on fixtures (PR #8576):

| fixture | pooled | verdict as of the run above |
|---|--:|---|
| 3 losing long trades, one per fold | −1.5 R | `long_stable_drag=True`, `fold_sensitive=False` |
| 2 long trades, adjacent | **−80.0 R** | `long_stable_drag=False` |

The second fails only because the majority test is `neg > k/2` on the **fold
count**, so empty folds dilute the denominator. So the PASS/FAIL was driven by
**how many folds the trades happened to spread across**, not by the strength of
the evidence.

A re-partition of 9 trades into 3/4/5 contiguous slices is not out-of-sample
validation — it is the same 9 trades counted three ways, and the panel members
are correlated by construction.

**Consequence for the table above.** The floor now applied is
`MIN_DIRECTION_TRADES = 10` — not invented, but the *same* meaningful-sample
threshold as the evidence policy that **authored** these cells
(`scripts/ml/walkforward_cell_selection.py::MIN_TRADES`, *"OFF-cells =
meaningful-sample (>= MIN_TRADES) net-negative cells"*). Against it:

| cell | n | direction graded | status after the floor |
|---|--:|---|---|
| trending/volatile `trend_donchian` | **9** | long | **`insufficient_n` — the "JUSTIFIED" verdict does not stand** |
| trending/calm `squeeze_breakout_4h` | **9** (6 short / 3 long) | short gated, long on | **`insufficient_n` — the "inversion" does not stand** |
| transitional/calm `trend_donchian` | 30 | long | clears the floor; `fold_sensitive` verdict stands |
| chop/calm `trend_donchian` | 43 | long | clears the floor; `fold_sensitive` verdict stands |
| `ict_scalp_5m` ×2 | — | — | still ungradable (unchanged) |

So **"1 of 6 justified" is wrong.** The correct reading is **0 of 6
affirmatively justified**: two cells clear the sample floor and both are
`fold_sensitive` (retirement candidates, not passes), two are below the floor,
and two have no evidence path. Grading a cell on a smaller sample than authoring
it required was never a re-audit.

**The `squeeze_breakout_4h` "direction inversion" is withdrawn as a finding.**
It rested on 6 short and 3 long trades. It is not that the cell is correctly
authored — it is that this instrument cannot say either way at that n. Treating
it as the "highest-value, parity-independent" follow-up would have spent a
Tier-3 proposal on noise.

### 2. The parity blocker was a wrong path, not a stale dataset

`BL-20260807-ML2-DATASET-STALE-BLOCKS-VOLGATE-PARITY` (now marked **invalid**)
said the labels end 2026-06-30 because the dataset needs rebuilding through
August. Measured (trainer-diag #8570):

| dataset | rows | first_ts | last_ts | mtime |
|---|--:|---|---|---|
| **`v002`** — the dataset `btc-regime-15m-lgbm-fc-pcv-v2` *declares* | 175,272 | 2021-08-07 | **2026-08-06T22:30Z** | Aug 7 05:10, nightly |
| `v520` — `-v1`'s **pinned** dataset, frozen by design ~Jul 1 | 175,272 | 2021-07-01 | 2026-06-30T22:30Z | Jul 1 19:35 |

The replay ran the **`-v2` head over `-v1`'s frozen `v520`**. "Labels end
2026-06-30" was a **pointer artifact**. Both files are exactly 175,272 rows — a
fixed-width rolling window — so `v520` never stopped growing; it is a snapshot,
and reading its end date as a data ceiling is the trap. `fc_*` is live in
`v002`'s tail (present 2,000/2,000 on all six columns).

`CLAUDE.md` § "Diagnostic provenance" sub-class **B** (implicit input
selection), and `CLAUDE-RULES-CANONICAL` § "Green is not evidence" obligation 5
(*establish what actually BOUNDS accrual* — here, a filesystem path).

### What parity still needs

Parity remains **unmeasured**. The re-run over `v002` is blocked behind
`BL-20260807-TRAINER-JOURNAL-PULL-TORN-RSYNC` — the live audit corpus lives in
the trainer's journal mirror, which a hot-DB rsync left malformed on 2026-08-07
05:00. (The **live** money DB is healthy — verified via `/api/diag/db_info`,
all 16 tables, `error_per_table: {}`.)

When it is re-run it must be **pinned per-window** to the head that was
*actually advisory* in each sub-window:

| window | advisory head |
|---|---|
| 2026-06-29 → 2026-07-20 | `btc-regime-15m-lgbm-v2` |
| 2026-07-20 → 2026-08-04 | `btc-regime-15m-lgbm-fc-pcv-v1` |
| 2026-08-04 → now | `btc-regime-15m-lgbm-fc-pcv-v2` |

A single-head replay across the whole window is a cross-model comparison. All
three heads declare `v002`, so one dataset serves the whole run
(`--model-id` pins the head), and the trainer's `.venv` is required (numpy 2.4.4
/ lightgbm 4.6.0 — the system `python3` has neither).

### Standing recommendation, revised

The original step 2 ("re-examine `squeeze_breakout_4h` trending/calm — the
highest-value item, does not depend on parity") is **withdrawn**. Replacing it:

1. **Do not retire, flip, or author any cell on the evidence in this document.**
   Two of four graded cells are below the authoring policy's own sample floor.
2. Repair the trainer journal pull (Tier-2), then re-run parity per-window.
3. Re-grade all six cells against the floored gate and report `insufficient_n`
   honestly where it applies, rather than reading a thin verdict as a finding.
4. The `ict_scalp_5m` gap is unchanged: give it an evidence path, or record
   explicitly that its two cells rest on the 2026-07-20 Phase-4 packet alone.
