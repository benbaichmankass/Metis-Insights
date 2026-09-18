# M20 U51 — the input stanza round-trips as text and not as evidence

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U51** · RESEARCH lane · Tier-1 · works `BL-20260912-U15S-OWN-CODE-NO-LONGER-REPRODUCES-U15S-OWN-PUBLISHED-TABLE-ONE-CONTROL-PACKAGE-FLIPPED-AND-NOBODY-WOULD-HAVE-KNOWN` (performance backlog, `severity: high`, `tier: 1`, `status: open`).

Instrument: [`scripts/research/memo_input_provenance.py`](../../scripts/research/memo_input_provenance.py) — **extended, not replaced** (80 self-test controls, up from 64; 19 pytest controls in [`tests/test_memo_input_provenance_localisation.py`](../../tests/test_memo_input_provenance_localisation.py); **12 planted defects, 12 caught by a named control in both suites**).

---

## Input provenance

Everything below is computed from files in this repository at branch point `origin/main` **8af09ef0d**; no journal pull, no network. Where a number is a census, the population is `docs/research/*.md` on that tree — **357 memos**, no sampling.

---

## 1. The row's second exit was shipped, and it does not work

The row offers two exits and closes the first itself — U15's pull was not retained and cannot be recovered. The second is:

> *"…or **memos gain a retained/hashed input so the next such difference is attributable when it happens**."*

MI-278 U26 shipped that: `memo_input_provenance.py`, a two-part digest declared in an HTML-comment stanza. The module's own docstring states what the third verdict delivers:

> `diff rowset -> content_changed <- hypothesis (b), with the changed rows NAMED`

⚠️ **On the only comparison a later session can make, the changed rows are not named — and the number that is printed instead is wrong in two opposite directions.**

`render_stanza` emits `rowset_digest`, `order_digest`, `fields_covered`, `n_rows`, the id bounds and the counts. It emits **no per-row digests**. `compare()` derives `rows_changed` / `rows_added` / `rows_removed` from `declared["row_digests"]` and `observed["row_digests"]` — a key a parsed stanza never carries.

---

## 2. Two shapes, both measured, both confident, both false

**Shape A — a memo against a fresh pull** (the `--against` path, the one a session actually runs). MEASURED on a 6-row fixture in which exactly one field of one row was re-stamped:

| | truth | pre-fix output |
|---|---|---|
| `rows_changed` | `['103']` | **`[]`** |
| `rows_added` | `[]` | **`['100','101','102','103','104','105']`** |
| `rows_removed` | `[]` | `[]` |
| `rows_not_localisable` | `0` | **`0`** |

The entire population rendered as *newly added*. `set(observed) - set(declared)` with an empty declared map is every observed id.

**Shape B — a memo against another memo**, and this one is on **real data, not a fixture**: U32's and U38's declared stanzas, the exact pair U38 used to establish that a row was re-stamped between two pulls (same ids `5731→4732`, `order_digest` **identical** `sha256:b073ee2c…`, `rowset_digest` **different** `sha256:1752f298…` vs `sha256:5b1cacd6…`).

```
state                    content_changed
rows_changed             []
rows_added               []
rows_removed             []
rows_not_localisable     0
```

A `content_changed` state beside *nothing changed anywhere*, and `rows_not_localisable: 0` positively asserting that every row was localisable.

⚠️ **The two shapes point at OPPOSITE causes.** Shape A renders a re-stamp (cause **b**) as a wholesale population change — which is cause **c**, the tail-window drift filed at `BL-20260912-A-1000-ROW-TAIL-PULL-CHANGES-A-FANOUT-PACKAGES-MEMBERSHIP-WITHIN-HOURS-SO-A-PACKAGE-LEVEL-RATE-OVER-A-TAIL-IS-UNSTABLE-BY-CONSTRUCTION`. Shape B renders it as no change at all. A module built to separate three causes was mis-assigning between them.

---

## 3. Why 64 green controls could not see it

The self-test compared two **live fingerprint dicts** in every case, and a live dict always carries `row_digests`. One control did reach the stanza path —

```python
check("a round-tripped stanza compares as `reproduces`",
      compare(parsed, fingerprint(_rows_a()))["state"] == "reproduces")
```

— and it checks the **identity** case, where the row lists are empty whether the digests are present or not. The stanza was tested for *parsing* and never for *comparability*. **A near-miss, not an absence**: the path was covered on the one input where the defect cannot appear.

---

## 4. What changed

**(a) It stops answering.** `compare()` gains `localisation`, four states, never collapsed: `localised` · `declared_carries_no_row_digests` · `observed_carries_no_row_digests` · `neither_side_carries_row_digests`. When not `localised`, all three row lists and `rows_not_localisable` are **`None`** — the module's own `shared_order_preserved → None` discipline, applied to the quantity that needed it. The `why` string names the state, and the CLI prints `None` explicitly rather than skipping a falsy value, which is how *we could not look* and *we looked and found none* became one line of output.

The two one-sided states are kept apart because the remedy differs: re-render the memo's stanza, versus re-take the pull.

⚠️ **`reproduces` and `reordered` still report `[]`, and must.** There the empty lists are *entailed* by `rowset_digest` equality and need no per-row map. `rows_basis` says which produced an answer — `rowset_digest_equality` or `row_digests` — so refusing an answer the digests already give is itself a planted defect (P12) and is caught.

**(b) It becomes possible to answer.** `render_stanza(include_row_digests=True)` emits per-row digests, and the **prefix length is declared in the stanza** (`row_digest_prefix`) rather than assumed. `compare()` trims the observed side to the declared precision — comparing a 16-hex declared map against full observed digests would differ on every row, which is not a milder wrong answer but the same fabricated total turnover.

At the default 16 hex, 1000 rows cost ≈23 KB against ≈77 KB at full length; the collision exposure over 1000 rows is a birthday bound of about `1000² / 2 / 2⁶⁴ ≈ 2.7e-14`, so a **missed** change is possible in principle and vanishing in practice. `rowset_digest` and `order_digest` stay full length and are unaffected. The same 6-row re-stamp now returns `rows_changed: ['103']` from a **stanza alone**, in both shape A and shape B.

⚠️ **It is opt-in and `row_digests` is deliberately NOT added to `REQUIRED_STANZA_KEYS`.** Requiring it would retroactively grade all five existing declarations `incomplete` — i.e. un-declare the only adoption there is. Both are planted defects (P10, P11) and both are caught.

---

## 5. Adoption, with the denominator

| state | memos |
|---|--:|
| `declares_input` | **5** |
| `journal_cited_no_declaration` | **73** |
| `not_detected` | **279** |
| **total** | **357** |

⚠️ **`not_detected` is not a clean negative** — the probe is a five-token match over prose, so a memo that computed a table from a pull without saying so sits in that 279. The module says this in its own output; it is repeated here because the number is quoted.

**All five declaring memos are this lane's** (`m20-u26`, `m20-u32`, `m20-u33`, `m20-u38`, `m20-u39`), and **0 of the 5 can name a changed row.** So the row's second exit stood at 5/357 adopted and **0/357 attributable** — the capability existed, was used, and could not have delivered the thing it was built for.

---

## 6. What this does NOT establish

* **It does not attribute the 30-vs-31, and cannot.** U15's pull was not retained. The module's docstring already refuses this and still does; nothing here changes it.
* **It does not close the row.** The second exit says memos *gain* an attributable input. The capability is now real; **adoption is 5 of 357 and none of the five is attributable today** — re-rendering those five stanzas is a follow-up, not something done here, because re-rendering requires re-taking each memo's original pull, which no longer exists for any of them. What a re-render would produce is a fingerprint of *today's* journal under an old memo's heading, which is worse than an honest `neither_side_carries_row_digests`.
* **It does not make the five existing memos attributable retroactively.** Nothing can; that is the row's first exit, and it is closed.
* **No parameter is proposed and none may be.** No `src/`, no `config/`, no order path.
* **The collision bound is arithmetic, not a measurement.** No prefix collision has been observed because none has been looked for; at 2.7e-14 over 1000 rows there is nothing to look for, and the full-length option exists for anyone who disagrees.
