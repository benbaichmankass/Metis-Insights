# MI-278 U22 — wiring the two callers to the one declared rule, and what that says about yesterday's unexplained 30-vs-31

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> **Unit:** MI-278 U22 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Advances** `BL-20260912-WHICH-FANOUT-ROW-IS-ROWS-0-MOVES-THE-STOP-OUT-ADJUDICATION-AND-SWINGS-THE-HEADLINE-DID-BY-10-POINTS` **to the point its criteria actually ask for**, and materially narrows `BL-20260912-U15S-OWN-CODE-NO-LONGER-REPRODUCES-U15S-OWN-PUBLISHED-TABLE-ONE-CONTROL-PACKAGE-FLIPPED-AND-NOBODY-WOULD-HAVE-KNOWN`.
> **Tier-1.** Was **HELD** on PR #12103; that PR merged 2026-09-12T22:24:11Z and the hold is spent — see § 6.

---

## 0. The answer, in five sentences

U21 declared what a fan-out package's exit is; a declaration nothing imports closes nothing, and U21's own memo said so. This unit wires both known callers to it: `stop_width_counterfactual_2026_09_11.py` had a **verbatim second copy** of the rule inside `_per_package_rates`, now replaced by a call to the one owner, and `stop_integrity_both_arms.py` gains `package_verdict` / `package_exit` per unit and a package-level **band** per cell, stated **beside** its published row-selected figures and never instead of them. **Both edits are proven additive by loading the pre- and post-patch modules side by side over one journal pull and comparing key-by-key** — the counterfactual comes back *byte-identical*, and the integrity module adds 356 keys with **zero** changed or removed. The wiring then reproduced U21's four cell bands exactly, through a different code path, which is a real cross-check rather than a restatement. **And it answers something U21 left open:** the `31` in U15's published table is *exactly* this cell's band-high, so it is reachable with **no data change at all**.

---

## 1. What the row actually requires

`BL-20260912-WHICH-FANOUT-ROW-IS-ROWS-0-MOVES-THE-STOP-OUT-ADJUDICATION-AND-SWINGS-THE-HEADLINE-DID-BY-10-POINTS` says a fan-out package's exit must be *"DECLARED in one place that the analyses share"*, and adds:

> ⚠️ **SORTING IN ONE CALLER CLOSES NOTHING** — two callers already sort, differently, and the shared builder is still undefined for the next one.

U21 built the shared place. This unit makes the callers share it.

---

## 2. Caller 1 — deleting the second copy

`stop_width_counterfactual_2026_09_11.py::_per_package_rates` (added by U19) carried its own inline `unanimous` / `disagreement` / `ungradeable` classification: eleven lines that are the same rule written a second time. That file's own header already makes the argument against exactly this, one paragraph above its `BA` import — *"A second copy of either is how two analyses of the same event come to disagree about a row."*

It now loads `fanout_exit_unit` the same way it loads `bleed_attribution`, and calls `FX.package_exit_verdict`.

⚠️ **The CLASSIFICATION moved; the POLICY did not.** U19's helper puts a contradicting package in *neither* numerator nor denominator, while U21's `did_band` keeps it in the denominator and bands it. Those are different, and both are defensible — because *what a package's exit is* and *what a rate does with a contradiction* are different questions. The declaration owns the first; the caller keeps the second.

**Proof it changed nothing** (population: one `/api/diag/journal?table=trades` pull of **1000 rows**, `tol = 0.001`, all three `dose_response` cells):

```
keys ADDED: 0 | CHANGED or REMOVED: 0
VERDICT: IDENTICAL — the second copy and the declaration agree on every package
```

⚠️ **State what that does and does not mean.** It means the duplicate had **not yet drifted**, so this removes a **future** hazard and fixes no **present** disagreement. It would have been easy to report a byte-identical result as a validation of the wiring; it is equally evidence that the wiring was not yet urgent, and both halves belong in the record.

---

## 3. Caller 2 — stating the choice instead of hiding it

`stop_integrity_both_arms.py::build` sorts the sibling rows by `closed_at` and grades `rows_s[0]`, with the comment *"sorted by close so the unit is deterministic"*. Deterministic is not the same as declared: that is the earliest-close rule, pinned in one caller, which the row says closes nothing.

`adjudicated` is **left exactly as it is**, so every published number survives. Added beside it: `package_verdict`, `package_exit`, and in `census` a `pkg_stop_rate_low` / `pkg_stop_rate_high` band plus `pkg_disagreement`.

| cell | published `rate_all_stop_outs` | **new** package band | contradicting packages |
|---|---|---|---|
| e35 pre | 0.1111 | 0.1111 – 0.2222 | **2** |
| e35 post | 0.4706 | 0.4706 – 0.4706 | 0 |
| control pre | 0.4839 | 0.4516 – 0.5000 | **3** |
| control post | 0.5775 | 0.5634 – 0.6620 | **7** |

**Cross-check:** those four bands and four disagreement counts are *identical* to U21's restatement, which reached them through `did_band` over rows re-joined from `trade_ids`. Two different code paths, same answer.

**Additivity proof** (same population, both arms, both eras, `build` and `census`):

```
keys ADDED: 356 | CHANGED or REMOVED: 0
VERDICT: ADDITIVE — every pre-existing key byte-identical
```

---

## 4. What this unit adds to the 30-vs-31 finding

U21 reported that `stop_integrity_both_arms`, run unmodified today, gives `control_pre` **30** stop-outs where the memo published **31**, and said the cause could not be distinguished between a row-order flip and a re-stamped `exit_price`. The package band makes that sharper:

```
control_pre — POPULATION: 62 packages
  unanimous stop-out packages ............ 28
  self-contradicting packages ............  3
  band low  = 28/62 = 0.4516   (all 3 contradictions counted as NOT stop)
  band high = 31/62 = 0.5000   (all 3 counted AS stop)
  `adjudicated` today .................... 30  (28 unanimous + 2 of the 3, resolved by the sort)
  U15's memo published ................... 31
```

**31 is exactly the band's high end.** So the published figure is reachable from *this* data with **no data change whatsoever** — it is what the cell reads when the third contradicting package resolves to stop, and 30 is what it reads when two of three do.

⚠️ **This narrows the finding; it does not close it.** A re-stamped `exit_price` could also produce 31, so hypothesis (b) is not eliminated — but it is no longer *required*, and the search is now three named packages rather than 62. The backlog row is updated, not resolved.

---

## 5. Two hazards I looked for and did NOT find

Both were plausible enough to be worth measuring, and both measure **zero** on this population (293 rows after `BA.population()`, 214 packages, 54 with more than one row). Recorded so the next reader does not spend the same hour:

| hypothesis | measured |
|---|---|
| `sorted(..., key=str(closed_at))` is a **string** sort, and `BL-20260730-TRADES-TIMESTAMP-FORMAT-MIXED` says encodings are mixed — so it could order rows wrongly | **0 packages** where a string sort picks a different row than a numeric-aware sort. All 293 rows are ISO-`T`; **0** unparseable. |
| `era = BA.era_of(rows[0])` reads **unsorted** `rows[0]` while adjudication uses `rows_s[0]`, so a package straddling the split instant would get an input-order era | **0 packages** whose siblings disagree about era. |

Both remain **latent**: the encodings are uniform *in this pull*, not by construction, and the era coincidence is a property of where the split falls. Neither is filed as a finding, because neither is one today.

---

## 6. What this does NOT do

- **It does not close the row.** Its criteria also require *"any published before/after rate names which rule it used"*, and the memos that published those rates are not retro-labelled here. The two callers now share one declaration, which is the structural half.
- **It changes no published number** — that is the point of §2 and §3, and it is proven rather than claimed.
- **It does not settle what a package's exit IS.** Earliest vs latest vs the managed leg remains unmade.
- **It does not touch the three live dashboard routes** MI-278 U12 named — `src/web/`, Tier-2.
- ⚠️ **It WAS held, and no longer is — recorded rather than tidied away.** `fanout_exit_unit.py` ships in PR **#12103**, so this branch was cut off that one and declared `landing: hold` / `depends_on_unmerged_pr`. The hold was real, not defensive: the first cut of this branch was off `main` and died at import with `FileNotFoundError`. **#12103 merged at 2026-09-12T22:24:11Z**, so the branch was rebuilt from `origin/main` plus this unit's own diff alone and re-declared `landing: self`. The hold fields are removed rather than left standing as a reason that has expired — a stale hold reads exactly like a live one.
