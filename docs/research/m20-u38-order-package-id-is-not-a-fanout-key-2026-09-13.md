# M20 U38 — `order_package_id` is not a fan-out key, and the tail edge is the smaller of its two problems

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U38** · RESEARCH lane · Tier-1 · works
`BL-20260912-A-1000-ROW-TAIL-PULL-CHANGES-A-FANOUT-PACKAGES-MEMBERSHIP-WITHIN-HOURS-SO-A-PACKAGE-LEVEL-RATE-OVER-A-TAIL-IS-UNSTABLE-BY-CONSTRUCTION`.

Instrument: [`scripts/research/package_fanout_integrity.py`](../../scripts/research/package_fanout_integrity.py)
(68 self-test controls; 33 pytest controls in
[`tests/test_package_fanout_integrity.py`](../../tests/test_package_fanout_integrity.py)).

<!-- input-provenance
source: https://ict-bot.duckdns.org/api/diag/journal?table=trades&limit=1000
n_rows: 1000
id_field: id
rowset_digest: sha256:5b1cacd688858df3472bd0e07fa784947856685a163885e5c048739d9280d85b
order_digest: sha256:b073ee2cd3f571022c802c446db077d5e9d2bc2a30daf695ae90574fe32f1a1f
fields_covered: account_class,account_id,bias,broker_order_id,closed_at,cost_source,created_at,direction,entry_price,entry_reason,exit_price,exit_reason,fee_maker_usd,fee_taker_usd,funding_paid_usd,id,is_backtest,is_demo,killzone,notes,order_package_id,pnl,pnl_percent,position_size,protection_repair_first_at,protection_repair_last_at,protection_repair_last_kind,protection_repair_last_verified,protection_repairs,reconcile_status,setup_type,sl_order_id,status,stop_loss,strategy_name,symbol,take_profit_1,take_profit_2,take_profit_3,timestamp,tp_order_id
id_first: 5731
id_last: 4732
n_rows_without_id: 0
n_duplicate_ids: 0
-->

---

## One sentence

The row asks whether tail truncation needs excluding or is rare enough to ignore
— **it is rare enough (1 package in 793, 0.13%) and that turned out to be the
small half**: the reason no edge heuristic works is that **16 of 110 multi-row
packages are not fan-outs at all**, they are one `order_package_id` reused
across re-entries, and **all 10 of them that fall inside MI-278 U15's cells land
in one cell**, carrying **6 of that cell's 7 "package disagreements".**

---

## 1. Population

One live pull, `GET /api/diag/journal?table=trades&limit=1000`, taken
2026-09-13. Ids **4732 – 5731**. **793 packages, 110 multi-row (317 rows).** Zero
rows lacked an `order_package_id` or an `id`. The fingerprint above is
declarable, so a later re-derivation can grade agreement instead of asserting it
(MI-278 U26).

For § 4, additionally `GET /api/diag/journal?table=order_packages&limit=1000`
(1000 rows), joined at `tol=0.001` by U15's own arm builder.

---

## 2. `order_package_id` is not a fan-out key

```
what a multi-row package IS (denominator 110):
    simultaneous_fanout              94
    account_repeated                 16      <-- 14.5%, holding 41 of the 317 rows
    distinct_accounts_wide_spread     0
    ungradeable_no_account            0
    ungradeable_no_timestamp          0
```

**A package holding two rows for the same account cannot be one cross-account
fan-out.** All 16 are that. They are one id reused for successive re-entries:

| package | rows | account | strategy | opened |
|---|--:|---|---|---|
| `pkg-4f8140865a9a4441` | 4 | `bybit_1` ×4 | `ict_scalp_sol_15m` | 09-11 20:12 · 09-12 05:05 · 08:00 · 11:07 |
| `pkg-85265901ef3948ef` | 3 | `bybit_1` ×3 | `ict_scalp_5m` | 09-11 20:54 · 09-12 03:03 · 06:04 |
| `pkg-4b0f61d469e64c00` | 2 | `ib_paper` ×2 | `mgc_trend_1h` | 09-04 14:00 · 09-08 16:11 (**4.1 days**) |

### The two classes separate with no threshold to choose

| class | n | `created_at` spread |
|---|--:|---|
| all accounts distinct | 94 | **max 5.918 s**, median 2.16 s, **zero above 60 s** |
| an account repeated | 16 | **3 907 s – 353 436 s** |

Gap ≈ **662×, with nothing in between.** `SIMULTANEITY_WINDOW_S = 60` sits
inside an empty interval, so it is a separator, not a tuned parameter — any
value between 5.9 s and 3 907 s yields the same partition on this pull.

> ⚠️ **CONTIGUITY IS NOT THE TEST.** 12 of the 16 are id-non-contiguous but **4
> are contiguous**, and one reused id spans **105 ids**. A session reaching for
> "are the ids adjacent?" misses a quarter of the class and flags real fan-outs
> that happen to interleave. The account repeat is the proof; the id layout is a
> symptom.

---

## 3. The truncation question, answered with a number

`diag._journal_select` is `ORDER BY id DESC LIMIT ?` — **no offset, no filter**
— so a member the pull is missing has an id **below** the minimum. Two measured
properties of this pull turn that into a bound:

* **density** — every id in `[4732, 5731]` is present (**0 gaps over 1000 ids**),
  so a missing member cannot be an interior row;
* **monotonicity** — id order and `created_at` order agree (**0 inversions over
  999 adjacent pairs**), so a member below the edge was created no later than the
  edge row (`4732`, `2026-08-18T03:40:55Z`).

Given both, a package whose earliest member was created more than the observed
class spread after the edge row **cannot** have a sibling below it.

```
could the tail edge have truncated it (denominator 793 packages):
    beyond_observed_reach           776
    within_observed_reach             1      <-- pkg-d259cc311a024f9e [4732,4733,4734]
    unbounded_class                  16
    ungradeable_reach                 0
```

**1 of 793 packages (0.13%)** could have been truncated. That is the row's second
exit — *"the drift rate is measured and shown to be small enough that they need
not"* — and it is a derivation, not a re-run, which the row explicitly rules out.

> ⚠️ **THREE THINGS THIS DOES NOT SAY.**
>
> 1. **It is a bound from an OBSERVED maximum, not a proof**, which is why the
>    state is named `beyond_observed_reach` and never `complete`. A fan-out wider
>    than any in the pull would defeat it. The instrument prints the bound it
>    used (5.918 s) so a reader can see what the claim rests on.
> 2. **The 16 reuse packages are `unbounded_class` — neither safe nor at risk.**
>    Their spread is limited only by the window the pull covers, so a reused id
>    can straddle the edge from far above it. That is precisely why an
>    edge-proximity heuristic cannot work, and why § 2 had to come first.
> 3. **Single-row packages are graded too, and must be** — a truncated fan-out's
>    surviving remnant IS a single row. Exempting them would hide the case the
>    row is about. On this pull **0 of 683** singles were within reach.

### Three candidate detectors, each tested, each fails

| detector | result | control |
|---|---|---|
| `linked_trade_id` names a lost member | points **outside** the observed member set **0 of 520** times (273 non-numeric, 0 package rows absent) | 793 of 793 packages found in the packages pull |
| an `order_packages` field declares the expected fan-out width | no such field: the schema carries `linked_trade_id`, a single id | full field list read |
| `GET /api/bot/db/table/trades?filter_col=order_package_id` completes the package | **401** — `Depends(require_session)` | `/api/diag/version` → **200**, same host, same bearer |

So the row's *"pull by id range rather than tail limit"* candidate **is not
available to a research session**: the only surface it can read is tail-only,
and the filterable one is session-gated.

---

## 4. What the reuse class does to a published number

`scripts/research/package_fanout_integrity.py --cross-check-u15` imports U15's
own arm builder and U22's own band **unmodified** and varies exactly one thing:
whether `account_repeated` packages are in the cells.

**Positive control: 168 of 168 arm-builder unit packages are recognised in this
pull**, so a zero delta could not have been a lookup miss. (My first attempt at
this keyed on `order_package_id` where the unit field is `package`, and returned
a clean, confident **0** — the control is in the instrument because of it.)

```
reuse units per cell: treated_pre 0 · treated_post 0 · control_pre 0 · control_post 10
```

| | band | width | sign |
|---|---|--:|---|
| with reuse | **3.8 .. 29.6 pp** | 25.8 pp | survives |
| reuse removed | **6.1 .. 23.7 pp** | **17.6 pp** | survives |

| cell | n | disagreements |
|---|---|---|
| `treated_pre` | 18 → 18 | 2 → 2 |
| `treated_post` | 17 → 17 | 0 → 0 |
| `control_pre` | 62 → 62 | 3 → 3 |
| **`control_post`** | **71 → 61** | **7 → 1** |

**Every one of the 10 is in `control_post`**, and they carry **6 of that cell's 7
disagreements**. `fanout_exit_unit`'s whole design is to report a
self-contradicting package as a band rather than arbitrate it — and in the
largest cell, six sevenths of the contradiction is two or more *separate
re-entry trades* under one id disagreeing about their own separate outcomes,
which is not a fan-out disagreement in any sense.

The band's **width is mostly an artefact of the grouping key**: removing the
non-fan-outs narrows it by **32%** while leaving all three other cells untouched.

> ⚠️ **NO EXCLUSION IS PROPOSED AND THE INSTRUMENT REFUSES TO PROPOSE ONE** (a
> planted "RECOMMENDATION: exclude…" line is caught by its own controls).
> Whether a re-entry sequence belongs in a fan-out study is the publisher's
> scoping decision. What is established is what the choice is worth.
>
> ⚠️ **THE SIGN SURVIVES UNDER BOTH**, so nothing here contradicts U15, U19 or
> U22 on the e35 stop-side effect's direction. Only the magnitude and the width
> move.
>
> ⚠️ **THIS IS ONE PULL.** The concentration in `control_post` is a property of
> this population; it is not a claim that reuse always lands on one side.

---

## 5. A second, unplanned finding: hypothesis (b) is observed

`BL-20260912-U15S-OWN-CODE-NO-LONGER-REPRODUCES-U15S-OWN-PUBLISHED-TABLE-ONE-CONTROL-PACKAGE-FLIPPED-AND-NOBODY-WOULD-HAVE-KNOWN`
names two causes for a moving table — (a) reordering, (b) a row **re-stamped**
between pulls — and records that they could not be told apart because U15's pull
was not retained. U26 then observed a **third** (the window moved).

Comparing today's declared fingerprint against **U31/U32's declared
fingerprint**, both in this repo:

| | U31 / U32 | U38 (today) |
|---|---|---|
| ids | 5731 → 4732 | 5731 → 4732 |
| `order_digest` | `b073ee2c…` | **`b073ee2c…` — identical** |
| `rowset_digest` | `1752f298…` | **`5b1cacd6…` — different** |

`order_digest` is over the id **sequence**; `rowset_digest` is order-independent
over the row **content**. Same ids, same order, different content ⇒ **at least
one row was re-stamped between the two pulls, hours apart on the same day.**
Hypothesis (b) is no longer a candidate explanation — it is an observed
behaviour of this journal.

> ⚠️ **IT DOES NOT ATTRIBUTE U15's 30-vs-31.** Digests alone cannot name the
> changed rows; that needs both row sets, and U15's is still gone. What is
> established is that the mechanism occurs, and on a timescale shorter than the
> gap U15's discrepancy spans.
>
> ⚠️ It is also **U26's stanza paying off a second time, and the first time it
> discriminated something** — the comparison needed no retained file, only two
> declared fingerprints.

---

## 6. Verification

* **68 self-test controls, 33 pytest controls.**
* **19 defects planted, all 19 caught.** Among them: *test simultaneity before
  the account repeat* (throws away a fact provable without a clock), *use
  contiguity instead of the account repeat* (3 controls fired), *a missing bound
  defaults to 0.0* (declares every package safe), *pool the reuse class into
  `beyond_observed_reach`*, *exempt single-row packages from the reach grading*,
  *skip the monotonicity precondition* (3 fired), *skip the density
  precondition*, *render only the non-zero states*, *fold
  `no_overlap_cannot_verify` into `no_reuse_in_cells`*, *print only the
  reuse-excluded band*, and *let the instrument recommend an exclusion*.
* **Two escaped on the first pass and both were control gaps, not module bugs** —
  the basis gate could be deleted because the only unusable fixture also had no
  bound, and nothing asserted the *report* renders a zero-count state. A
  gapped-but-bounded fixture and two render assertions close them; both plants
  now fire.
* **4 no-op sanity plants fired zero.**
* The five plants were re-run against the pytest surface: 5 / 5 caught, no-op
  green.

---

## 7. Scope

**No code outside `scripts/research/` and `tests/` is modified.** No `src/`, no
`config/`, no unit file, no workflow, no order path. `src/web/api/routers/diag.py`
and `db_explorer.py` were read only. No parameter is proposed and no Tier-3
change is implied.

---

## 8. Standing

`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED`
is `loud: true` and unchanged: **17 consecutive losing days, −$38,851.81**, onset
2026-08-27, bounded below by a **+$4,172.64 winning day on 2026-08-26**. U38 does
not address it. It touches the instrument that would attribute it — the e35
stop-side DiD — and narrows which units belong in that contrast without moving
its sign.
