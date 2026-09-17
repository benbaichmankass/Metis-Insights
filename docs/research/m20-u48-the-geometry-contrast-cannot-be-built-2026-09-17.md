# M20 U48 — the prescribed remedy cannot be run, and the row's own exit is unsatisfiable

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U48** · RESEARCH lane · Tier-1 · works `BL-20260912-THE-M20-WALKFORWARD-ARM-SET-CANNOT-GRADE-TARGET-GEOMETRY-BECAUSE-TP-GEOMETRY-IS-PERFECTLY-CONFOUNDED-WITH-FAMILY-AND-BLOCK-UNIT` (performance backlog, `severity: high`, `tier: 1`, `status: open`).

Instrument: [`scripts/research/tp_geometry_reachability.py`](../../scripts/research/tp_geometry_reachability.py)
(65 self-test controls; 27 pytest controls in
[`tests/test_tp_geometry_reachability.py`](../../tests/test_tp_geometry_reachability.py);
7 planted defects, 7 caught by a named control).

---

## Input provenance

* `docs/research/m20-fold-dispersion-arms-consolidated.jsonl` — `sha256:53d778456ea3ec73867425bddcd873c7f5031e3fc477d08685aea49eeef71888`, **246 arms**, 33 legs, 3 families.
* Producers read live, not copied: `scripts/research/m20_fleet_exit_sweep.py` (`sha256:afbc2451cee559b6…`) and `src/runtime/tp_venue_cap.py` (`sha256:f601182227f1fe58…`).
* Repo at `dc08a2424`.

**Population for every rate below:** all 246 arms in that file. No sampling, no window.

---

## 1. The row is right, and it stops one step short of the step that matters

It grades `tp_geometry` **perfectly confounded** with family and block_unit. Every figure reproduces exactly on today's read — 246 arms, 33 legs, `live_parity_uncapped ⇔ scalp ⇔ per_leg` (28), `live_parity_capped ⇔ {donchian, pullback} ⇔ family_pooled` (218), candidate rate **21/28 = 75.0%** against **66/218 = 30.3%**.

But "confounded" is a statement about a **sample**, and a confounded sample is the kind of thing a better-designed sample breaks. So the row's `next_step` prescribes designing one:

> *"(A) live_parity_capped AND live_parity_uncapped on the SAME family with the SAME block_unit (pullback is the obvious choice at 151 arms)"*

⚠️ **That arm cannot be built. Not on pullback, not on any family, at any cap.**

---

## 2. Why — the label is computed from the same predicate that gates the behaviour

`tp_geometry` is not a knob that happened to co-vary with family. `m20_fleet_exit_sweep.tp_geometry_for` — the declared **single owner** of the stamp — resolves it from family membership in `CLAMPING_FAMILIES`, and `base_args` decides flag delivery with the *same* test:

```python
if tp_cap_pct > 0.0 and fam in LIVE_TP_CAPPED_FAMILIES:
    a += ["--tp-cap-pct", str(tp_cap_pct)]
```

`CLAMPING_FAMILIES = {donchian, fade, pullback, squeeze}`; `scalp` is absent. Measured by **calling the real functions**, never by re-deriving them:

| family | in `CLAMPING_FAMILIES` | labels attainable at *any* cap | contrast pair | dose |
|---|---|---|---|---|
| `donchian` | yes | `NO_TAKE_PROFIT`, `live_parity_capped` | **capped only** | reachable |
| `fade` | yes | `NO_TAKE_PROFIT`, `live_parity_capped` | **capped only** | reachable |
| `pullback` | yes | `NO_TAKE_PROFIT`, `live_parity_capped` | **capped only** | reachable |
| `squeeze` | yes | `NO_TAKE_PROFIT`, `live_parity_capped` | **capped only** | reachable |
| `scalp` | no | `live_parity_uncapped` | **uncapped only** | **INERT** |

A clamping family at cap 0 grades `NO_TAKE_PROFIT` — a book with **no target at all**, not the uncapped-parity arm. Treating it as the second level would let a pullback run at cap 0 masquerade as the missing contrast, so the instrument excludes it from `CONTRAST_LEVELS` by construction and a test pins that.

**Verdict: `clause_a = unsatisfiable_by_construction`.** The row's `resolution_criteria` — *"Some family carries BOTH tp_geometry levels"* — can never be met. **The row as written can never close**, and it is currently named as blocking `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`'s `done_condition`.

### ⚠️ This is a proof, not a grid walk — and the proof asserts itself

A cap sweep could only ever say *"no value I tried changed it"*. The stronger claim holds because the cap enters `tp_geometry_for` **only** through `tp_cap_pct > 0.0` — a sign test — so `{0.0, any one positive}` is **exhaustive** over the cap's influence.

That is not assumed. `cap_enters_only_as_a_sign_test()` asserts it over 12 magnitudes spanning `1e-9 → 1e9` (and, in the self-test, 40 random positives), on **5 of 5** families, and ships `holds: True` beside every verdict. A future change making the label depend on the cap's **size** fails it loudly and **downgrades every `unreachable_by_construction` to `unknown`**. Read `sign_test.holds` before quoting any verdict here.

---

## 3. The row's unestablished caveat, now established

The row noted that the uncapped arms carry `tp_cap_pct: 0.099` too, and said this *"may mean the field records declared config rather than what was applied; that was not established."*

**It is established, and it is the declared-config reading.** Asking `base_args` whether each arm's recorded cap would actually have been delivered:

| | arms |
|---|--:|
| cap **delivered** to the harness | **218** |
| cap **recorded but never delivered** | **28** |

The 28 are exactly the scalp arms. `--tp-cap-pct 0.099` was dropped by the `fam in LIVE_TP_CAPPED_FAMILIES` test and never reached their books.

**Consequence the row does not draw: clause (B) is unreachable on scalp too.** Its `next_step` also asks for *"at least three tp_cap_pct values rather than only 0.099"* — but on a non-clamping family the flag is never passed at **any** value, so a dose sweep there is **inert by construction**. The dose is reachable only on `{donchian, fade, pullback, squeeze}`.

---

## 4. Of the 44.7pp between-level gap, 18.8pp is already present *inside* one level

If geometry carried the 75-vs-30 gap, the `live_parity_capped` bucket should be reasonably homogeneous. It is not:

| level | family | candidate | rate |
|---|---|--:|--:|
| `live_parity_capped` | `donchian` | 29/67 | **43.3%** |
| `live_parity_capped` | `pullback` | 37/151 | **24.5%** |
| `live_parity_uncapped` | `scalp` | 21/28 | 75.0% |

* largest **within**-level family spread: **18.8pp**
* **between**-level spread: **44.7pp**
* **within / between = 0.42**

So 18.8pp of the 44.7pp gap attributed to the label — a within/between ratio of **0.42**, over the 218 capped arms (donchian 67, pullback 151) and 28 uncapped — is already present between two families *carrying the same label*. That does not refute a geometry effect — it says the same data that produced the headline also shows family-level variation of nearly half its size, inside the level. `live_parity_uncapped` holds one family, so its spread is reported as `None`, **never `0.0`** — one family has no spread, and a zero would read as "the families agreed".

---

## 5. What IS runnable

Only one axis survives, and it is the cap **level** inside a clamping family:

* **Runnable:** ≥3 distinct positive `tp_cap_pct` values on `pullback` (151 arms) or `donchian` (67), folds and OOS balanced across arms. The `tp_geometry` label stays `live_parity_capped` throughout — which is fine, because the label was never the variable of interest. The **cap** is.
* **Not runnable, ever:** both contrast levels within one family (§2).
* **Not runnable on scalp:** any `tp_cap_pct` sweep (§3).

⚠️ **The scalp target has a separate, already-built axis and it is sitting unmerged.** `BL-20260912-THE-SCALP-FAMILY-S-TARGET-HAS-NO-SWEEP-E35-EXCLUDES-IT-BY-DESIGN-AND-THE-FLEET-SWEEP-DOES-NOT-SWEEP-A-TARGET` covers the scalp side through `tp_at_r` rather than the venue cap; MI-278 U30 built it in **PR #12158**, which is `landing: "hold"` / `unvouchable_paths` and has needed a **human merge click since 2026-09-13**. That matters more than it sounds: U2 established the take-profit is the **only** lever with attributed mass (18 of 49 winners ended exactly at target), so the one instrument that can grade it has been blocked on a click for four days.

---

## 6. What this does NOT establish

* **It does not say target geometry is irrelevant.** It says this axis cannot test it, which is a different and more useful claim — the row's own framing, applied one level deeper.
* **It does not refute the 75-vs-30 gap.** The gap is real and reproduces; only its *attribution* is refused.
* **No parameter is proposed and none may be.** Target geometry is Tier-3.
* **The corpus is a snapshot.** 246 arms as consolidated on 2026-09-06; a later screen could add a family, and the `unreachable` verdicts are about the **producers**, not the corpus, so they would survive it — but the rates would not.
* **`fade` is graded and is not live.** It carries the clamp in its unit file and `fade_breakout_4h` is `execution: shadow`, so it appears in the reachability table and contributes **zero** arms to the corpus.
