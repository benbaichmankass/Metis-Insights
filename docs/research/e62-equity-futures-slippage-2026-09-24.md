# E62: what it takes to measure slippage for Alpaca equities and IBKR futures

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Date:** 2026-09-24 · **Checklist row:** E62 · **Tier:** 1 (measurement only).
This lane did not change `execution_costs.py`, `config/`, or any evidence record.
**The 5.0 bps default for equities and futures stays**, as the operator said,
*"a working artifact until we get evidence backed numbers"*.

**Record:** [`comms/research/e62_equity_futures_slippage/2026-09-24.json`](../../comms/research/e62_equity_futures_slippage/2026-09-24.json)
plus `…__rows.jsonl` (143 rows, one per measured entry or exit). Built on D3's
method, [`d3-realized-slippage-2026-09-24.md`](d3-realized-slippage-2026-09-24.md),
reused unchanged: same reference prices, same sign, same `MIN_N = 20`.
**Re-run:**

```
python3 scripts/research/realized_slippage.py pull --out-dir D        # needs DIAG_READ_TOKEN
python3 scripts/research/e62_slippage_accrual.py --in-dir D --as-of <pull time> --out <json>
```

Pull read 2026-09-24 ~15:44Z: trades ids ≤ 6141, fills 90 days back.

## The answer

<!-- population-ok: every number below names its account and n in the same row -->
<!-- checked: scripts/research/e62_slippage_accrual.py -->

**Neither venue has an evidence-backed round-trip figure today, and neither
gets one by waiting on the current roster.** Three things stand in the way:

1. **The exit side is structurally unmeasurable on Alpaca. A fix is built
   (held, Tier-2).** Root cause below.
2. **Even after that fix, `alpaca_live` accrues about 1 usable round trip
   per 60 days.** Its 3 legs are `_1d` pullbacks that rarely fire. Most of
   their closes are `exchange_flat_reconciled` (no bracket label), not
   `sl_cross`. See section C.
3. **`ib_live` is `dry_run` with an empty roster.** It accrues nothing until
   the operator puts a leg on it (Tier-3).

### A. Entry side, measured now

<!-- population-ok: every row names its account and n; 95% is the CI level -->
Reference = `order_packages.entry`. Positive = adverse. The CI is a 95% bootstrap
CI of the mean. Paper and simulator rows measure a fill model, not a market.

<!-- population-ok: every row names its account and n; 95% is the CI level -->
| account | basis | n | mean bps | 95% CI | median | state |
|---|---|---|---|---|---|---|
| `alpaca_live` | **real market** | 3 | −49.8 | — | −18.8 | **INSUFFICIENT (n=3): not used** |
| `alpaca_paper` | simulator (Alpaca paper) | 51 | +1.66 | [−2.37, +5.99] | −0.47 | measured |
| `alpaca_portfolio` | simulator (Alpaca paper) | 60 | +0.26 | [−2.90, +3.76] | −0.14 | measured |
| `ib_paper` | **simulator** (IB paper, maker fills) | 22 | +8.13 | [+3.95, +12.20] | +6.44 | measured, simulator |
| `ib_live` | real market | 0 | — | — | — | no fills (dry_run, empty roster) |

**The session-open sensitivity changes what the Alpaca entry number means.**
All Alpaca entries at |bps| > 30 were created at 09:30–09:34 ET (13:30Z). For a
`_1d` leg the package entry is the prior bar's level and the order fills at the
open, so those rows measure the **overnight gap**, not execution. Excluding
entries created at 09:30–09:35 ET:

| account | n (dropped) | mean bps | 95% CI |
|---|---|---|---|
| `alpaca_paper` | 39 (12) | −2.28 | [−5.24, +0.55] |
| `alpaca_portfolio` | 48 (12) | −2.86 | [−4.88, −1.04] |
| `alpaca_live` | 2 (1) | −9.0 | — (n=2) |

Read together: Alpaca's *paper* fill model charges no measurable entry cost off
the open. The open-window entries are a gap exposure the Stage-0 harness may or
may not model. Whether each equity harness enters at the signal close or the
next open is **not checked per harness here**. It decides which number is the
right comparison, so the first equity round-trip record must state it.

`alpaca_live`'s −131 bps row is trade 6097 (`iaum_pullback_1d`, IAUM): the
package entry was 43.48, the fill at the open was 42.91.

### B. Exit side: the structural gap, root-caused and fixed (held)

D3 found 0 MEASURED Alpaca sl/tp exits. The exits carried `verdict` or
`recorded_exit_price` (ESTIMATED). Census over sl/tp/giveback closes in this pull:
`alpaca_live` 1 (`verdict`), `alpaca_paper` 20 (13 `recorded_exit_price`,
7 `verdict`), `alpaca_portfolio` 18 (6 and 12). **MEASURED: 0 of 39.**

**The fills are not missing.** They are in the fills store. Trade 5920
(`alpaca_portfolio` SPY, 7 sh, `sl_cross`) has 6 sell fills summing to 7 sh,
0.4 s before its `closed_at`. The monitor never read them, because of a race:

- The close path cannot read an Alpaca fill at close time.
  `account_order_status` is Bybit-only, so it stamps `verdict`.
- The broker sweep (`_sweep_pending_pnl_from_bybit`) *does* resolve Alpaca exits
  from the fills store (`exchange_fill`, MEASURED). But it only selects rows with
  `pnl IS NULL`, and the fills pull runs hourly.
- The local-compute fallback (`_sweep_local_pnl_for_unpriced`) is supposed to
  wait 6h for the broker sweep. **It measured that 6h from `created_at`, the
  OPEN.** A position held more than 6h therefore had no grace left at its close.
  On the very next tick it was priced from the `verdict`, which set `pnl`, and
  the broker sweep never looked again.

Evidence the mechanism is the one at work (same pull):

| population | held < 6h | held ≥ 6h |
|---|---|---|
| `ib_paper` sl/tp closes that resolved `ib_execution` | **7 of 8** | **0 of 10** |
| Alpaca sl/tp/giveback closes since the Alpaca fills reader landed (#8111, 2026-07-31) | 0 rows | 30 rows, 0 MEASURED |

The 4 Alpaca closes held < 6h (all `recorded_exit_price`) are from June and July,
before the Alpaca fills reader existed. They are not a counter-example.
Equity swing legs are always held ≥ 6h, which is why Alpaca missed 100%.

This defect was filed on 2026-09-05 as
`BL-20260905-LOCAL-PNL-BROKER-DEFER-GRACE-KEYS-ON-THE-OPEN`, with its sibling
`…-BYBIT-BROKER-PNL-SWEEP-WINDOW-ALSO-KEYS-ON-THE-OPEN`. Both went into a
backlog that was archived on 2026-09-21 without import, and neither was fixed.

**The fix is a held Tier-2 PR:** both windows are keyed on the close. It is
described in its own PR body. It does not back-fill any historical row.

⚠️ **The fix also needs the IB half:** 10 `ib_paper` sl/tp closes held ≥ 6h
missed `ib_execution` the same way. The same change covers them.

### C. When each venue reaches a usable n

A usable round trip is a closed sl/tp-family trade whose entry and exit are
both MEASURED against a package reference. **Today that count is 0 for every
Alpaca account, at any number of trades.** The dates below are what accrues
**after the held fix deploys**. Rates are exact Poisson 95% CIs over the 60 days
to the pull.

| venue / book | what is counted | rate / day [95% CI] | have | n = 20 | n = 30 |
|---|---|---|---|---|---|
| **`alpaca_live`**, entries | the 3 live legs' entries **on alpaca_paper** | 0.10 [0.037, 0.218] | 3 | 2027-03-13 (2026-12-11 … 2027-12-31) | 2027-06-21 (2027-01-26 … 2028-09-29) |
| **`alpaca_live`**, round trips | the same legs' sl/tp-family closes | 0.017 [0.0004, 0.093] | 0 | ~2030 (2027-04 … never, in practice) | ~2031 |
| **`ib_live`** | any | 0 | 0 | never, until a leg is put on its roster | never |
| `alpaca_paper` (simulator) | sl/tp-family closes | 0.25 [0.14, 0.41] | 0 | 2026-12-13 (2026-11-12 … 2027-02-14) | 2027-01-22 |
| `alpaca_portfolio` (simulator) | sl/tp-family closes | 0.30 [0.18, 0.47] | 0 | 2026-11-30 | 2027-01-02 |
| `ib_paper` (simulator) | sl/tp-family closes | 0.27 [0.15, 0.43] | 7 | 2026-11-12 (2026-10-24 … 2026-12-18) | 2026-12-19 |

The `alpaca_live` rate uses the **same 3 legs' `alpaca_paper` history**
(`ief_pullback_1d`, `slv_pullback_1d`, `iaum_pullback_1d`), because 3 live fills
cannot bound a rate. It is INFERRED that the live signal frequency matches the
paper one: same strategy, symbol and bars. `alpaca_portfolio`'s rate is its old
14-leg roster's; it was cut to 2 legs on 2026-09-21, so it **overstates** the
forward rate.

**Why the live round-trip rate is so low (MEASURED):** over the 60 days, the
live roster's legs on `alpaca_paper` closed 5 times. **4 were
`exchange_flat_reconciled`** (no fill price on any of them), and 1 was
`sl_cross`. `exchange_flat_reconciled` is excluded by D3's method because it
carries no bracket label. It is the reconciler noticing the position is gone.
`_classify_broker_exit` re-derives the label only for `reconciler_filled` and
empty reasons. **So the live venue's measurable n is bounded by that labelling
gap as well as by the fix above.** It is filed, not fixed here.

## What this could NOT establish

- **Any real-money round-trip figure for either venue.** The venue figures remain
  5.0 bps assumptions. `execution_costs.py` is unchanged (task item D).
- **Whether the fix works in the field.** It is merged to no branch, deployed
  nowhere and observed nowhere. It closes when a post-deploy Alpaca `sl_cross` row
  carries `exit_price_source: exchange_fill`.
- **The paper fill model vs the real market.** Paper entry means are near 0.
  With n=3 live entries they cannot be compared.
- **ib_paper MGC fills stayed truncated at 1,000** (same as D3). It does not
  affect the 22 entries matched here beyond that page.
- **A reference-price mismatch in the exit_from_fills window.** The broker sweep
  reads close-side fills from the open to *now*, with no upper bound at the close.
  A sibling trade on the same account and symbol could, in principle, be
  attributed. The qty check (tolerance: 5 percent of the trade's own qty) rejects most such cases. Not measured.

## Follow-ups filed

<!-- checked: scripts/ops/pipeline.py --check -->

- `PI-20260924-BOFCWBO7-0001` (dispatch_lane, due 2026-10-01): the labelling
  gap. `exchange_flat_reconciled` closes are never compared to the package
  bracket. 46 of them carry a MEASURED exit and no label (37 Alpaca, 9 ib_paper).
- `PI-20260924-BOFCWBO7-0002` (check_observation, every 14d): closes when
  the held fix is deployed and observed. On each re-run after that, file the
  proposed per-venue value once a real-money venue reaches `measured`. It never
  changes `execution_costs.py` itself. It supersedes the structural half of
  `PI-20260924-A87KVAFC-0002`.
- No value was proposed for either venue, because neither is measured.
