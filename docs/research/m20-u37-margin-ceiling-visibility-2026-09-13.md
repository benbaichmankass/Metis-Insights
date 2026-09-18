# MI-278 U37 — the margin-ceiling record exists only where it does not matter

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Instrument: [`scripts/research/margin_ceiling_visibility.py`](../../scripts/research/margin_ceiling_visibility.py) ·
26 pytest controls in [`tests/test_margin_ceiling_visibility.py`](../../tests/test_margin_ceiling_visibility.py) ·
subjects: `src/units/accounts/execute.py`, `src/units/accounts/risk.py` (**Tier-2, read only, unmodified**)

<!-- input-provenance
source: /api/diag/journal?table=trades&limit=1000
n_rows: 1000
id_field: id
rowset_digest: sha256:1752f298bbd639395817aee0581c284561eef361787f4f6f6a03fd4a17058d3b
order_digest: sha256:b073ee2cd3f571022c802c446db077d5e9d2bc2a30daf695ae90574fe32f1a1f
fields_covered: account_class,account_id,bias,broker_order_id,closed_at,cost_source,created_at,direction,entry_price,entry_reason,exit_price,exit_reason,fee_maker_usd,fee_taker_usd,funding_paid_usd,id,is_backtest,is_demo,killzone,notes,order_package_id,pnl,pnl_percent,position_size,protection_repair_first_at,protection_repair_last_at,protection_repair_last_kind,protection_repair_last_verified,protection_repairs,reconcile_status,setup_type,sl_order_id,status,stop_loss,strategy_name,symbol,take_profit_1,take_profit_2,take_profit_3,timestamp,tp_order_id
id_first: 5731
id_last: 4732
n_rows_without_id: 0
n_duplicate_ids: 0
-->

## The claim

`PB-20260822-AVAX-SCALP-SIZED-OFF-MARGIN-NOT-RISK` (open, **high**, Tier-3):
*"`ict_scalp_avax_5m`'s order size IS the margin ceiling, not a risk number —
`position_size` equals `max_qty_by_margin` exactly"* — measured on **n=2**,
because the `margin_basis` stamp had only existed since 2026-08-13. Its
`next_action` asks for the pre-clamp risk qty to be stamped beside the cap.

A month has passed. Three things are now measurable that were not.

## 1. n=2 → 32, and it is one leg

| | n |
|---|--:|
| journal rows read | 1000 |
| carrying a `margin_basis` record | **208** |
| …gradeable | 207 |

Bands over the 208 recorded rows:

| band | n |
|---|--:|
| `zero_size` — the sizer returned nothing | **154** |
| **`at_ceiling`** (ratio ≥ 0.999) | **32** |
| `part_of_ceiling` (0.5–0.99) | 16 |
| `well_below` (< 0.5) | 3 |
| `near_ceiling` (0.99–0.999) | 2 |
| `ungradeable` | 1 |

**32 orders — 15.4% of every row that carries a ceiling record — were sized at
their margin cap.** By strategy: **`ict_scalp_avax_5m` 30**, `tlt_pullback_1h`
1, `ict_scalp_5m` 1. By account: **`bybit_1` 30**, `alpaca_paper` 1,
`bybit_portfolio` 1.

The row's leg accounts for 30 of the 32 — its claim reproduces at **15× its
original n**, on one leg, and is not a two-row artefact.

⚠️ **`tlt_pullback_1h` also appears**, on `alpaca_paper`. That leg is routed to
**real-money `alpaca_live`** (`OI-20260831`), so the ceiling behaviour is not
confined to a paper-only leg.

⚠️ **`clamp_did_not_bind: 0`** — no recorded size exceeded its own cap. A
measured zero, published so its absence is not an unchecked field.

## 2. ⚠️ The row's own equality test returns ZERO, and that is a trap

The row says the two values are equal *"to FULL FLOAT PRECISION"*. They are
not. The shipped size is **quantised to the venue step**:

```
position_size 33141.1000   max_qty_by_margin 33141.1001   ratio 0.999999997
position_size 22078.1000   max_qty_by_margin 22078.1343   ratio 0.999998
```

**An exact `position_size == max_qty_by_margin` probe finds nothing across all
1000 rows.** My first probe did exactly that and returned 0 of 207 — I would
have reported the effect as gone had I not run the ratio as a positive control.
The instrument therefore grades a **ratio band** and publishes the band counts,
so no threshold is hidden and the next reader is not sent to a clean negative.

## 3. ⚠️ The record is REFUSAL-ONLY — so the row's criterion cannot be checked

`notes.margin_basis` has exactly one writer:
`execute.log_rejection_to_journal`, whose own docstring says it exists to *"log
a refusal event to the trade journal … used by `Coordinator.multi_account_execute`
from its `except RiskBreach` and generic exception blocks so every refusal lands
a row."* **A placed order can never carry it.**

Measured, with the positive control that makes the negative mean something:

| | n |
|---|--:|
| placed rows (`closed` / `open`) | **623** |
| …carrying **any** notes dict | **623** ← *positive control* |
| …carrying a `margin_basis` record | **0** |
| …whose notes were `_truncated` | 138 |

Every placed row has notes. **None has the stamp.** The absence is structural,
not missing data.

**And the row's criterion is exactly about placed orders:**

> *"no `ict_scalp_avax_5m` order **places** at a size equal to a ceiling (margin
> OR venue) without that decision being in place."*

**That is unverifiable from the journal today.** So is the row's
`why_it_matters`, which is entirely about the transition:

> *"When the venue clamp is eventually fixed the order will be capped at 22,000
> AVAX and will PLACE rather than be refused — so fixing the plumbing converts a
> loud refusal into a silent fill at a size no risk model chose."*

**The evidence disappears at precisely the moment the row is worried about.**
The 21 refusals are visible *because* they were refused. The ceiling record is
on the wrong side of the transition.

## 4. A second, independent hazard on the same field

`margin_basis` is a nested **dict** in `notes`, written through
`json_notes.dump_capped(notes, 500)`, and it is **not** in `_DEFAULT_PROTECTED`.
`_shrink_dict` trims *strings* and sheds whole unprotected values it cannot
trim — a dict is exactly that, and it is typically the largest one present.
**138 of the 623 placed rows are already `_truncated`.**

So even if the stamp were written on the placement path it would be among the
first things dropped on a full row. ⚠️ **This is NOT the same finding as §3 and
must not be folded into it**: one is a write site, the other a cap, and fixing
either alone leaves the other standing. Same class as MI-278 U33's finding about
the netting-attribution stamp.

## 5. What the 154 zero-size rows are, and why they are their own band

**154 of 208 recorded rows — 74%** — have `position_size = 0`: the sizer
declined to size at all. That is a *refusal*, not a conservatively-sized order,
and pooling it with `well_below` would let 154 refusals read as prudence. It is
its own band and is not analysed further here.

`basis_kind`: `venue_available` 181 · `free_balance` 19 · `equity_minus_pledged`
6 · `coin_derived` 2. `basis_provenance`: **direct 202, estimated 6** — six caps
were computed from a reconstructed basis (`ESTIMATED: open notional / 3x`), so
those six are at an *estimated* ceiling.

## What this changes about `PB-20260822`'s `next_action`

The row asks for one Tier-1 stamp: the pre-clamp risk qty beside
`max_qty_by_margin`. **That is necessary and not sufficient**, because it would
land on the same refusal-only path. The proposal it should become:

1. **Stamp the pre-clamp risk-derived qty** beside `max_qty_by_margin` — the
   row's own ask, unchanged. **[Tier-2, `risk.py`/`execute.py`, not applied
   here.]**
2. **Write the ceiling record on the PLACEMENT path too**, not only on refusal,
   or the criterion stays uncheckable the moment the clamp is fixed.
   **[Tier-2.]**
3. **Protect it from the cap** — add `margin_basis` to
   `json_notes._DEFAULT_PROTECTED`, or store the flag outside `notes`. **[Tier-2.]**

⚠️ **None of the three is applied here.** `risk.py` and `execute.py` are Tier-2;
per-leg sizing is Tier-3.

## What this does NOT establish

- **It does not say any of the 32 was wrongly sized.** A size at the margin cap
  is the clamp working; whether the *risk* model should have been asking for
  more than available margin at 3× is the Tier-3 question the row poses, and it
  is unanswerable until step 1 lands, because the pre-clamp qty is not recorded.
- **It does not measure placed orders at all** — that is the whole point of §3.
  Every number here is over refused orders.
- **One window.** 1000 rows, ids 4732–5731, the journal's hard tail. A row
  outside it is unread, not absent.
