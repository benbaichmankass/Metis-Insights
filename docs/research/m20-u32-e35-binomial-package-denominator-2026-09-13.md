# MI-278 U32 — the e35 break attribution's binomial, re-derived per ORDER PACKAGE

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Instrument: [`scripts/research/e35_binomial_package_denominator.py`](../../scripts/research/e35_binomial_package_denominator.py) ·
36 pytest controls in [`tests/test_e35_binomial_package_denominator.py`](../../tests/test_e35_binomial_package_denominator.py) ·
subject: [`scripts/research/e35_break_attribution.py`](../../scripts/research/e35_break_attribution.py)

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

⚠️ **THIS DIGEST IS BYTE-IDENTICAL TO U31's**, and that is the first payoff of
U26's stanza rather than a coincidence worth ignoring. `rowset_digest`
`sha256:1752f298…` and `order_digest` `sha256:b073ee2c…` match the fingerprint
recorded in
[`m20-u31-bleed-record-package-denominator-2026-09-13.md`](m20-u31-bleed-record-package-denominator-2026-09-13.md)
exactly (PR #12164, merged 2026-09-13T02:37:48Z). So U31's numbers and U32's are computed over **the same 1000 rows in the
same order**, and any difference between the two memos is a difference of
*method*, never of *pull* — which is precisely the attribution
`BL-20260912-U15S-OWN-CODE-NO-LONGER-REPRODUCES-U15S-OWN-PUBLISHED-TABLE-ONE-CONTROL-PACKAGE-FLIPPED-AND-NOBODY-WOULD-HAVE-KNOWN`
asked for and could not make.

## The debt

`BL-20260911-A-RATE-PER-TRADE-ROW-COUNTS-ACCOUNT-FANOUT-AS-INDEPENDENT-AND-INFLATES-N-UNEQUALLY`
asks that a price-behaviour rate state its distinct package count beside its row
count, or be computed per package. MI-278 **U12** named 21 surfaces; **U19** paid
the dose table and **U31** paid the bleed record. This pays the third.

## Why this one is not a repeat of U31

U31's Fisher took its 2×2 cells from rows: one term, one substitution. This
statistic takes **two** terms from rows, by different paths:

```
P(X <= k)   for X ~ Bin(n, p)
            k, n   the E35 arm's POST wins and count
            p      the family-matched CONTROL arm's OWN post rate,
                   itself a rows/rows quotient
```

Fan-out therefore reaches the **sample size** and the **null hypothesis**
separately, and a single rows→packages re-derivation cannot say which one moved
the answer. The instrument computes **four cells** — every combination of
(n from rows | packages) × (p from rows | packages) — and classifies which
substitution carries the p-value across α = 0.05. The classification is
**threshold-free**: it asks only about crossings, never about magnitudes, so it
carries no tuned constant.

## What the live pull says

**Population** (pooled `bybit_*`, the parent script's own scope): **204 rows over
155 packages**, inflation **1.3161×**. Excluded exactly as the parent excludes:
`not_closed` 400 · `pairs_sleeve_exonerated` 279 · `fabricated_close` 50 ·
`off_venue` 38 · `pnl_null` 29. Of the 155 packages, **151 placed**, **4
disagreed** on win/loss across their own rows, **0 straddled** the deploy
boundary, and **0 rows carried no package id**. **27 of 155 packages (17.4%) span
more than one account** — that is the fan-out, measured rather than assumed.

### 1. The inference is stable only by CANCELLATION

| n basis | p basis | k | n | p_null | p-value | < α? |
|---|---|---|---|---|---|---|
| rows | rows | 1 | 20 | 0.208 | **0.0586** | no ← *the published figure* |
| packages | rows | 1 | 14 | 0.208 | 0.1779 | no |
| rows | packages | 1 | 20 | 0.250 | **0.0243** | **YES** |
| packages | packages | 1 | 14 | 0.250 | **0.1010** | no ← *fully re-derived* |

The four cells span **0.0243 → 0.1779, a 7.3× range**, and one of them crosses α.
The two pure bases agree — both read *not significant* — so a rows-vs-packages
re-derivation alone would have reported "no change" and stopped.

**It would have been wrong to stop.** The two substitutions move the p-value in
**opposite directions**: collapsing the e35 arm (20 → 14) makes the test less able
to reject, while collapsing the control arm raises the null rate (0.208 → 0.250)
and makes it more able. They cancel. The instrument grades this **`offsetting`**,
a state deliberately separate from `no_crossing`, because reporting a
cancellation as a confirmation is the reassuring direction and therefore the
dangerous one. The published 0.0586 is stable by coincidence, not by robustness.

### 2. The DESCRIPTION and the INFERENCE do not move together

The parent script's rendered table — the thing a reader actually quotes — is
deltas, not the p-value. Re-derived both ways:

| arm | rows | packages | |
|---|---|---|---|
| `e35` | −55.0pp (25→20) | −57.6pp (17→14) | stable |
| `control_same_family` | **+0.8pp** (5→24) | **−8.3pp** (3→16) | ⚠️ **SIGN FLIP** |
| `control_other` | −22.9pp (57→73) | −21.9pp (41→60) | stable |

**The e35 headline is robust** and marginally strengthens. **The family-matched
control's delta reverses sign.** That arm is the one the parent script's whole
three-arm design exists for: a control reading *"+0.8pp, flat"* supports
*"the geometry broke the e35 legs"*, and the same arm reading *"−8.3pp,
degrading"* supports *"the family broke and e35 is part of it"* — opposite
conclusions from one dataset and two denominators.

⚠️ **State the population before anyone leans on that flip: it rests on 3
packages pre.** The parent script already warns its family-matched PRE arm is
n=6 rows; on packages it is **three**. The sign flip is a finding about the
FRAGILITY of that arm, not a new verdict about the market.

### 3. Per-account, the whole effect vanishes — pooling IS the exposure

| scope | rows | packages | inflation | all four p-values | driver |
|---|---|---|---|---|---|
| pooled `bybit_*` | 204 | 155 | 1.3161× | 0.0243 – 0.1779 | `offsetting` |
| `bybit_1` | 143 | 138 | 1.0362× | **0.0720** (all four identical) | `no_crossing` |
| `bybit_2` | 32 | 32 | 1.0000× | 1.0000 ⚠️ degenerate | `no_crossing` |
| `bybit_portfolio` | 29 | 29 | 1.0000× | 1.0000 ⚠️ degenerate | `no_crossing` |

This reproduces **U31's transferable finding exactly**: fan-out is
**cross-account**, so a per-account analysis is essentially immune and a pooled
one is exposed. On every single account the four cells collapse to one number and
every arm's delta grades `stable`.

⚠️ **`bybit_1`'s residual 1.0362× is NOT fan-out** and must not be quoted as
such. It is **three** packages carrying 2–3 rows each, all `ict_scalp_*`, i.e.
one package writing several rows on ONE account — a different phenomenon, and it
lands entirely in the `control_other` arm, touching neither arm the binomial
reads. "Per-account is immune to fan-out" is true; "per-account means row ==
package" is not.

⚠️ **`bybit_2` and `bybit_portfolio` report p = 1.0000 and that is VACUOUS, not
reassuring.** Their family-matched control won **zero** of its post trades, so
p_null = 0.000 and P(X ≤ k | p = 0) = 1.0 for every k. The instrument flags
`degenerate_null` on the line rather than refusing, because the count behind it —
a control arm that won nothing — is itself worth reading.

## What this does NOT establish

- **It reverses no verdict.** The parent script's own caution stands verbatim: a
  package basis makes the n honest, it does not make it large. The e35 post arm
  is **14 packages** and the family-matched control's pre arm is **3**.
- **It changes no published figure and re-grades nothing live.** Both
  denominators are reported side by side, which is the clause the backlog row
  asks for.
- **It does not touch the three Tier-2 dashboard routes** U12 named
  (`/api/bot/stats` winRate, `/api/bot/performance`, `/api/bot/attribution`).
  Changing a number the operator reads is a decision, not a side effect.
- **It says nothing about the 2026-08-30 break's cause.**
  `OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED`
  needs per-leg stop-out rate and MFE-at-stop, e35 vs non-e35 — not a cleaner
  denominator on the same statistic.

## The refactor this carried

`e35_break_attribution.py`'s arm rule was inline in `grade()`. It is now
`arm_of(name)` with `ARMS`, and `grade()` is its first caller. A second copy of
the arm rule is how two analyses of one event come to disagree about which arm a
leg is in. **Behaviour-preserving, verified on the real pull**: the pre- and
post-refactor `--json` output over all 1000 rows is byte-identical, and
`test_arm_of_is_behaviour_identical_to_the_inline_rule_it_replaced` pins it.

## ⚠️ The fan-out debt has NOT shrunk, and three units in a row have said it did

**Re-measured today with U12's own instrument** — `fanout_denominator_survey.py`,
`grade_registry(find_candidates("."))` — `price_path_not_deduped` reads
**21**. It read 21 when U12 named it on 2026-09-12, it read 21 after U19, it
reads 21 after U31, and it will read 21 after this unit.
`scripts/research/e35_break_attribution.py` is on that list and **stays on it**.

That is not a measurement failure; it is what these units have actually been
doing, stated plainly for the first time. **U19, U31 and U32 each built a
re-derivation BESIDE the exposed surface rather than changing it** — deliberately,
because changing a published figure is a decision rather than a side effect, and
three of the 21 are Tier-2 dashboard routes a research unit does not get to
touch. `bleed_attribution_2026_09_11.py` is still on the exposed list after U31
re-derived it, for exactly the same reason this one will be.

**So "N of 21 remain" is the wrong sentence and I wrote it too** — and the three
quotes do not even agree with each other: U31's coordination-board note said
**17**, U31's own work-object progress entry said **19**, this unit's START
comment said **16**. Three numbers for one quantity inside one session, while the
instrument read **21** throughout. Filed as
`BL-20260913-THE-FANOUT-SURFACE-COUNT-HAS-BEEN-QUOTED-AS-SHRINKING-BY-THREE-CONSECUTIVE-UNITS-AND-THE-INSTRUMENT-SAYS-IT-HAS-NEVER-MOVED`.

⚠️ **Two other population figures in U12's memo HAVE moved and must not be
subtracted across.** U12 measured *"21 of 36 live surfaces"* with *"only 2
already deduping"*. Today the survey's raw scan reads **63 candidates / 42
reading the live journal / 11 mentioning `order_package_id`** — the tree grew,
partly from these very re-derivations. The **21** is stable because it comes from
the hand-maintained `DECLARED` registry (36 entries), not from the raw scan; the
raw counts are a different population and arithmetic across the two is invalid.

**What would actually shrink the count:** changing an exposed surface to report
both denominators, which for the 18 non-dashboard entries is Tier-1 and for the
three dashboard routes (`/api/bot/stats`, `/api/bot/performance`,
`/api/bot/attribution`) is a Tier-2 change to a number the operator reads — an
operator decision, not a research one.
