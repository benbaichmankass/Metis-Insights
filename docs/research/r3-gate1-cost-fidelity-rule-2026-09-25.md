# R3: the Gate-1 cost-fidelity rule (registered before the run)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Rule id:** `RULE-R3-GATE1-COST-FIDELITY-v1` · **Registered:** 2026-09-25, in a
commit that contains no per-leg result. The measured record lands in a LATER
commit on the same branch (`git log --follow` on this file and on
`comms/research/r3_cost_fidelity/` shows the order).
**Checklist rows:** R3 (primary), E62 (feeds it). **Tier:** 1. This rule is a
test. It changes no roster, no config and no evidence record. The actions it
names are carried out by the mandates in `config/mandates.yaml`
(`MD-DEMOTE-S2-S1`, `MD-DEMOTE-S1-OFF`) or by the operator, not by this rule.

## What was already known when this was written (stated, so the order is honest)

The venue-level numbers in D3 (`docs/research/d3-realized-slippage-2026-09-24.md`)
and E62 (`docs/research/e62-equity-futures-slippage-2026-09-24.md`) were read
before this rule was written: Bybit perps round trip +0.87 bps, CI
[-0.91, +2.95] on `bybit_2`; Alpaca exit side unmeasured; `ib_live` no fills.
**No per-leg number had been computed by anyone.** The floor and the threshold
below are not tuned to those venue figures: the threshold is read from a field
the operator's mandate already carries, and the floor is argued from the
estimator, not from the data.

## The question

Per leg, per account: is the realized round-trip slippage the one the leg's
Stage-0 evidence record assumed?

## The unit

A **cell** is one (leg, account) pair. The leg is `trades.strategy_name`, and
it must equal the evidence record's `strategy` at
`comms/strategy_evidence/<leg>.json`. A cell with no record reads `no_record`.

## The measured quantity

Realized round-trip slippage in bps, positive = adverse. It is the unit of
`execution_costs.slippage_bps_roundtrip_for` and of the record's
`cost_stack.slippage`. It is measured exactly as D3 measures it
(`scripts/research/realized_slippage.py::measure`, reused, not re-implemented):

- **Entry side:** fill VWAP from the fills store (`exchange_fills`, matched on
  `trades.broker_order_id`) against `order_packages.entry`.
- **Exit side:** only closes whose `exit_price_source` classifies `MEASURED`
  under `src/runtime/provenance.py`, with reason `sl`/`sl_cross` (against the
  package's last `sl`) or `tp`/`tp_cross` (against the package's `tp`). Only
  package-referenced exits count, as in D3's headline.
- **Estimator:** `mean(entry bps) + mean(exit bps)` over the cell. **CI:** 95%
  bootstrap of that sum, sides resampled independently (D3's `_boot_ci`, seed
  20260924, 4000 reps).
- **n for the cell** = `min(n_entry, n_exit)`. Both are reported.

## The floor

**`N_FLOOR = 10` per side.** A cell with `n_entry < 10` or `n_exit < 10`
reads **`insufficient_n`**. It gets no verdict and triggers no action, however
large its point estimate is.

Why 10, and why not D3's 20: D3's floor gates an ESTIMATE that replaces a
venue default for every leg. This floor gates a TEST, and the CI is what
protects the test from a false demotion (a wide CI straddles the threshold
and reads `inconclusive`). Below about 10 draws a percentile bootstrap of a
mean under-covers badly, so the CI stops giving that protection. Venue-level
pooled figures, reported alongside, keep D3's 20.

## The threshold

**`T` = `config/mandates.yaml` → `MD-PROMOTE-S1-S2.bar.cost_tolerance_bps`,
read at run time.** It reads **0.0** today. That field is the stated tolerance
of plan § 5.3 clause 3 (*"Stage-1 realized cost within a stated tolerance of
the modelled cost"*). The mandate's own comment marks 0.0 as INFERRED, not
DECIDED. This rule does not pick a second number, because two tolerances for
one clause would let promotion and demotion disagree. If the operator sets
the field, this rule follows it with no edit.

## The verdicts

Let `M` = the record's `cost_stack.slippage` and `[lo, hi]` = the cell's CI.

<!-- population-ok: 95% is the CI level; the population is each cell's own n, stated in every record row -->
| verdict | condition | meaning |
|---|---|---|
| `insufficient_n` | either side below `N_FLOOR` | no verdict, keep accruing |
| **`divergent`** | `lo > M + T` | realized cost is worse than assumed, with 95% confidence |
| `consistent` | `hi <= M + T` | realized cost is at or below the assumption, with 95% confidence. This is **the Gate-1 pass** |
| `inconclusive` | otherwise (the CI straddles `M + T`) | no action, keep accruing |
| `no_record` | no evidence record for the leg | Gate 1 cannot be read |

A `consistent` cell whose `hi < M` also carries `record_overcharges: true`.
The record assumes more cost than the venue charges. Its verdict is
conservative, not dangerous. That is a re-pricing note, not a demotion.

**Only unfavourable divergence demotes.** A backtest that over-charges cost
understates its own edge. It cannot be what made a leg look profitable, so
it is no reason to take the leg off a roster.

## The consequence of `divergent`, which is the Gate-1 rule proper

A `divergent` verdict does two things:

1. **It demotes the leg one stage.** Stage 2 goes to Stage 1 (live account
   and mirror together, since Stage 2 is one stage). Stage 1 goes off. A leg
   that is not yet promoted stays put: a Stage-1 `divergent` is also a Gate-1
   **fail**, so it cannot be promoted until a re-run reads `consistent`.
2. **It invalidates the leg's Stage-0 record.** The record priced a cost the
   venue does not charge, so its `net_r_oos` is not net of the full cost
   stack, and clause 3 of the bar fails for it. The record stays invalid until
   it is regenerated at a slippage no lower than the cell's CI upper bound and
   still passes `RULE-D1-STAGE0-NET-OF-FULL-COST`.

## Basis: market vs simulator

The rule reads a cell on any account. Each cell carries `basis`: `market`
when the account's `account_class` is `real_money`, `simulator` otherwise.
The Stage-1 books (`bybit_1`, `alpaca_paper`, `ib_paper`) are paper or demo
fills, which measure the broker's fill model rather than a market. **The plan
defines Stage 1 as where cost fidelity is read, so the rule is applied there
as the plan says.** The basis is printed on every verdict, so no reader takes
a simulator figure for a market one. When a leg has both, the **market** cell
wins, because it measures the cost the leg actually pays at Stage 2.

## Re-run

```
python3 scripts/research/realized_slippage.py pull --out-dir D      # needs DIAG_READ_TOKEN
python3 scripts/research/r3_cost_fidelity.py --in-dir D --as-of <pull time> \
    --out comms/research/r3_cost_fidelity/<date>.json
```
