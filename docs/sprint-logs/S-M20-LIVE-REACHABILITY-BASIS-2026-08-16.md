# S-M20-LIVE-REACHABILITY-BASIS-2026-08-16

## Date Range

- **Start:** 2026-08-16 (overnight M20 session, continued)
- **End:** 2026-08-16

## Objective

**Primary:** Execute the operator's 2026-08-16 Tier-3 instruction on
`xrp_pullback_2h` — *"widen the sample FIRST — no disposition change"* — by
measuring the **live** `order_packages` population rather than the truncated
6-row relay read the registry was carrying.

**Secondary:** Extend the same method to the two remaining `queued_tier3`
reachability entries (`trend_donchian_sol_4h`, `scha_trend_long_1d`), and file
the relay-transport defect that made the original sample untrustworthy.

## Tier

**Tier 1.** Measurement, one backlog row, and annotation blocks in
`config/lever_reachability.json`. No `verdict`, `disposition`, or `arm_r` was
changed; no `src/`, no order path, no live value. Resolving a `queued_tier3`
row remains the operator's Tier-3 call.

## Starting Context

- Roadmap: M31 (exit-lever reachability). Prior session `session_01Xk2ozj`
  established `cap_R = 0.099 / (atr_stop_mult × ATR/close)` and closed out at
  15:55Z, having annotated all five queued levers with a **backtest**
  entry-conditioned basis — explicitly labelled *"NOT the authoritative live
  order_packages/risk_per_unit population."*
- The live basis for `xrp_pullback_2h` was `observations: 6`, `basis:
  "TRUNCATED (55,429 of 224,462 bytes: the 6 NEWEST of up to 25)"`,
  `reach_share_pct: 33.3`, `cap_r_max: 8.38`.
- Known risk: the registry's own note said *"do NOT read 33.3% as a lifetime
  rate — the sample is truncated and recency-biased."*

## Repo State Checked

- Branched from `origin/main` at `c8636c02` (this session's own #9759).
- Read `config/lever_reachability.json` on `origin/main` before editing, plus
  `config/strategies.yaml` (the `scha_trend_long_1d` block) and
  `config/accounts.yaml` (SCHA routing).
- Coordination board #6927 read to comment 917 (my own 17:18:58Z release).

## Files and Systems Inspected

- `config/lever_reachability.json` — all five queued entries
- `config/strategies.yaml:2186-2212` — `scha_trend_long_1d`
- `config/accounts.yaml:728,733` — `alpaca_paper` strategy + symbol lists
- `scripts/ci/check_lever_reachability.py` — the guard
- Live VM `trade_journal.db::order_packages` via the `vm-diag-snapshot` relay
  (issues #9760–#9763, #9766–#9770)

## Work Completed

### 1. The relay-transport defect (PR #9759, merged `c8636c02`)

The relay truncates the **tail** at 55,000 bytes, and both the db-explorer
envelope (`… rows, total, … filter_state, order_state`) and
`/api/bot/order-packages` (`count` after `rows`) place the fields that
**certify** a response *after* the part that looks like data. On any oversized
read the certification is the first casualty while the rows survive intact.

Measured four times this session. The worst (#9753): a filter on `strategy`
(the real column is `strategy_name`) was **silently ignored** per
`BL-20260813`, the query ran unfiltered, and **829,977 bytes** of the entire
table came back truncated to 55,000 — with `filter_state` (which would have
read `ignored_unknown_column`) truncated away. It was caught only because XRP
does not trade at 78,810.

Filed as `BL-20260816-TRUNCATION-STRIPS-THE-FIELDS-THAT-CERTIFY-A-RESPONSE`.

**The method this forces**, used for every measurement below: probe with
`limit=1` first (small enough that the tail survives) to establish `total` +
`filter_state`, then page at `limit<=6`, and verify **every** returned row
inline on its own `strategy_name`.

### 2. `xrp_pullback_2h` — live population widened (PR #9765)

Population: `order_packages WHERE strategy_name='xrp_pullback_2h'`,
**`total: 37`, `filter_state: applied`**. **27 of 37** rows recovered complete;
10 lost mid-row to truncation across three 12-row pages. All 27 self-verified
on `strategy_name`, 0 failures.

| population | n | cap_R min / med / max | reach 4.49R |
|---|--:|--:|--:|
| all complete rows | 27 | 1.84 / 2.96 / **4.46** | **0/27** |
| `closed` | 8 | 1.84 / 3.08 / 4.01 | 0/8 |
| `rejected` | 14 | 2.22 / 2.87 / 4.46 | 0/14 |
| `orphaned` | 3 | 4.25 / 4.33 / 4.35 | 0/3 |
| `open` (trade 4163) | 1 | 3.92 | 0/1 |

The executed-vs-unexecuted population split — flagged as an open question when
the sample was 6 rows — is **moot**: every stratum is 0.

**Correction recorded:** the row's `cap_r_max: 8.38` and `reach_share_pct: 33.3`
do not reproduce. Across 33 distinct live observations the maximum `cap_R` is
**4.4573**. And 33.3% is arithmetically impossible: 33.3% of 37 is 12.3 rows
while only **10** are unaccounted for, so the ceiling is **10/37 = 27.0%**.
Live reach share is bounded **[0.0%, 27.0%]**. The two stale fields were left
in place as the record of the truncated read; the new block is the correction
and says so.

### 3. `trend_donchian_sol_4h` — live population, COMPLETE

Population `total: 16`, `filter_state: applied`, **16 of 16 recovered** — every
page inside the byte budget with certification intact, so this is a complete
population and not a bounded interval.

**0/16 = 0.0%** reach the declared arm **5.57R**. `cap_R` min 1.1237 / median
1.7810 / max **4.3193**.

The arm requires `risk/entry <= 1.777%`; the leg's best (lowest-vol) entry ever
is **2.292%** and the median is 5.559%. The arm is **1.29× the best ceiling ever
observed** — out of range, not near-miss.

Agrees with the backtest basis (0/127, 0.0% in *every* year). Two independent
populations, both zero.

**This entry's `unmeasured` verdict was not blocked by what it appeared to be.**
It read `skipped_thin` (14 winner MFEs vs a 30 minimum) — but reachability does
not depend on that sweep: `cap_R` is entry geometry, measurable on every entry
the leg ever took, thin winners or not. The skipped sweep blocked a
*replacement arm*, never the reachability verdict.

### 4. `scha_trend_long_1d` — empty for a legitimate reason

`total: 0`, `filter_state: applied` — we looked, and the live journal holds
**zero** order packages for this leg.

Verified this is not a probe error: the identical query shape returned 37 and
16 on the two sibling legs, and `scha_trend_long_1d` is the exact key in
`config/strategies.yaml` (`enabled: true`, `execution: live`, symbol SCHA), with
SCHA present in `alpaca_paper`'s symbol list and the strategy in its roster.

**It is not a dead leg.** `git log --diff-filter=A` dates the declaration to
**2026-08-09**, which is exactly **5 US trading sessions** ago
(Mon 08-10 → Fri 08-14). Zero breakouts in 5 bars of a **1d donchian-30**
channel is unremarkable.

The decision-relevant consequence: a live-basis reachability measurement for
this leg **is not obtainable for a long time**. Its queued row must be decided
on the backtest basis or stay queued — and the reason is *"the live population
is empty because the leg is 5 sessions old"*, which is a different statement
from *"unmeasured."*

## Validation Performed

- `scripts/ci/check_lever_reachability.py` → exit 0, all 8 declared reach-gates
  disposed.
- `scripts/ci/run_guards.py` → **PASS 14 · FAIL 0 · SKIP 27**.
- Decision fields asserted unchanged **programmatically** before each write
  (`verdict`/`disposition`/`arm_r` compared pre- and post-edit; the script
  raises rather than writing if any moved).
- `reach_r` cross-checked against `0.099/(risk_per_unit/entry)` on every row
  carrying it — **0 mismatches** on 24 xrp rows and on every sol row checked.
  The bot publishes `cap_R` itself; it need not be re-derived.
- Row-level self-certification on all 27 xrp rows and all 16 sol rows.

### Gaps not yet verified

- **10 of 37 `xrp_pullback_2h` rows were never recovered.** The reported live
  reach share is an interval `[0.0%, 27.0%]`, not a point estimate. Recoverable
  with `limit<=6` paging if the operator wants the interval closed.
- **`cap_r_max: 8.38` is unexplained, not disproved.** It reproduces on no row
  I have seen (33 observations). It could sit in one of the 10 unrecovered rows
  — which would require `risk/entry` ≈ 1.18%, against an observed range of
  2.22–5.38% — but I did not find its source and do not claim it fabricated.
- `scha_trend_long_1d` has no live basis at all, and will not for months.

## Documentation Updated

- `config/lever_reachability.json` — `live_entry_conditioned_2026_08_16` blocks
  on `xrp_pullback_2h` and `trend_donchian_sol_4h`.
- `docs/claude/health-review-backlog.json` —
  `BL-20260816-TRUNCATION-STRIPS-THE-FIELDS-THAT-CERTIFY-A-RESPONSE`.
- This sprint log.

## Contradictions or Drift Found

1. **`cap_r_max: 8.38` / `reach_share_pct: 33.3` on `xrp_pullback_2h`** —
   not reproducible; the derived rate is arithmetically impossible on the live
   population. Corrected by annotation (the stale fields deliberately retained
   as the record of what the truncated read reported).
2. **`basis: "…the 6 NEWEST of up to 25"`** on the same row — the envelope
   `total` is **37**, not 25. Recorded in the new block's `envelope_total`.
3. **`trail_decay_arm_r` appears in only 2 of 27 `xrp_pullback_2h` rows**
   (2026-07-15, 2026-07-29) over a span starting 2026-06-19 — the lever was
   declared mid-July. This does not affect `cap_R` (pure entry geometry) but it
   means *"how often would it have armed"* and *"how often did it fail to arm"*
   are different questions on that leg. Not previously stated anywhere.

## Risks and Follow-Ups

- **Tier-3, operator:** `xrp_pullback_2h` stays `queued_tier3`. The sample is
  now widened as instructed; the decision is the operator's.
- **Tier-3, operator:** `trend_donchian_sol_4h` now reads 0.0% on **both**
  bases over a complete live population — the same evidential position that
  supported the `recorded_inert` decisions on `gld_pullback_1d` and
  `qqq_trend_long_1d`. Proposed, not taken.
- **`scha_trend_long_1d`:** not decidable on live data; needs a backtest-basis
  ruling or a long wait.
- **Relay fix (unimplemented):** cheapest remedy is in the *relay*, not the
  API — emit the tail keys after the truncation notice, or refuse to truncate
  mid-envelope and return a metadata-only summary.

## Deferred Items

- Recovering the 10 missing `xrp_pullback_2h` rows to close the interval.
- Locating the origin of `cap_r_max: 8.38`.
- `PB-20260816-ARM-SWEEP-POOLS-VOL-ERAS` half (2) — per-era p80 (unclaimed).

## Next Recommended Sprint

**Close the M31 reachability queue on the operator's ruling**, then M31 P4
(backtest↔live MFE parity) — which is the named binding blocker for the half of
arm reachability the sweep's own `p80_arm_reach` check cannot see
(`within_measured_median_ceiling` does **not** mean reachable in production;
`gld_pullback_1d` passes that check at 3.86R while being unreachable live).

Required verification for that sprint: any parity claim must be measured on
both populations for the *same* leg, since this session's whole finding is that
the backtest and live bases differ and neither substitutes for the other.

## Wrap-Up Check

- [x] Code/config inspected directly (not from memory) — registry, strategies,
      accounts, guard all read before asserting.
- [x] Docs reviewed/updated.
- [x] TRADE-PIPELINE — not applicable; no pipeline stage changed.
- [x] Roadmap checked — M31 row already points at
      `m20-m31-operator-decisions-2026-08-16.md`; no status change, since
      nothing was decided.
- [x] Contradictions recorded (three, above).
- [x] Unknowns stated explicitly (Gaps not yet verified).
