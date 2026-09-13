# MI-278 U26 — a journal-derived memo can now say what it was computed from, and the U15 discrepancy has a THIRD cause nobody named

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**Row:** `BL-20260912-U15S-OWN-CODE-NO-LONGER-REPRODUCES-U15S-OWN-PUBLISHED-TABLE-ONE-CONTROL-PACKAGE-FLIPPED-AND-NOBODY-WOULD-HAVE-KNOWN`
**Module:** [`scripts/research/memo_input_provenance.py`](../../scripts/research/memo_input_provenance.py) — 64 controls, 0 failures

---

## 0. ⚠️ What this does NOT do

**It does not attribute the 30-vs-31, and it cannot.** U15's original pull was not
retained, so the historical difference stays unattributable. The row offers two
exits and only the second is reachable:

> *"Either the 30-vs-31 difference is ATTRIBUTED to one of the two causes with
> evidence, **or memos gain a retained/hashed input so the next such difference is
> attributable when it happens**."*

This is the second. Reporting it as closing the historical question would be the
unprovenanced claim the row was filed about.

---

## 1. The exposure, measured

`--census` over `docs/research/*.md`, **337 memos** at `origin/main` `b209768c2`:

| state | n |
|---|---|
| `declares_input` | **0** |
| `journal_cited_no_declaration` | **66** |
| `not_detected` | 271 |

⚠️ **`not_detected` is NOT a clean negative and is deliberately not called
*"not journal-derived"*.** The probe is a token match over prose
(`/api/diag/journal`, `diag_fetch`, `trade_journal.db`, `journal pull`,
`journal tail`), so a memo that computed a table from a pull without saying so is
indistinguishable from one that never touched the journal. Calling that a clean
negative is the unasserted-denominator defect (sub-class C) this repo files. Every
memo's matched token set ships in `signals`, so the census can be re-read under a
narrower rule without re-running the probe.

**Positive control:** the probe finds the state it is looking for — a fixture
memo carrying a stanza grades `declares_input`, and this memo (below) is the
first real one.

## 2. This is a RECURRENCE of a class the repo already ruled on

`docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md` § R1 already requires
every result row to carry **"on what data — dataset id + version + fingerprint"**,
and names two rows as its motivation. Both are still `kept_open`, verified by
reading their `status` rather than by copying an id out of that prose:

| row | backlog | status |
|---|---|---|
| `BL-20260810-SWEEP-VERDICTS-DO-NOT-RECORD-THEIR-DATASET` | health | `kept_open` |
| `BL-20260812-SWEEP-CORPUS-RECORDS-NO-FRAME-FINGERPRINT` | health | `kept_open` |
| `BL-20260912-U15S-OWN-CODE-NO-LONGER-REPRODUCES-U15S-OWN-PUBLISHED-TABLE-ONE-CONTROL-PACKAGE-FLIPPED-AND-NOBODY-WOULD-HAVE-KNOWN` | performance | `open` |

Three instances of one class, the third filed **16 days after** the contract that
forbids it. **That the earlier fix did not hold is the finding**, and it is why
this unit ships an executable instrument rather than a fourth restatement.

## 3. Why TWO digests — it is the whole point

One digest answers *did anything change*, which is already answerable by re-running
and seeing a different number. It cannot separate the row's two hypotheses. Two can:

| | rowset | order | verdict | maps to |
|---|---|---|---|---|
| identical | same | same | `reproduces` | — |
| **reordered** | same | differs | `reordered` | hypothesis **(a)** |
| **re-stamped** | differs | any | `content_changed`, rows **named** | hypothesis **(b)** |
| different fields hashed | — | — | `undecidable_field_sets_differ` | *we did not look at the same thing* |
| nothing declared | — | — | `no_declared_input` | **every memo in the repo today** |

⚠️ **A multiset, not a set.** Deduplicating per-row digests would make a pull that
lost a duplicate row hash identically to one that did not — silent, and in the
reassuring direction. `n_duplicate_ids` and `n_rows_without_id` ship beside it, and
a row with no id is counted rather than dropped.

⚠️ **`fields_covered` is compared FIRST.** A digest over a different field set is
not a smaller answer to the same question, it is an answer to a different one — so
it grades `undecidable_field_sets_differ`, never `content_changed`. A renamed
column lands there too, which is correct: it is not a data change.
`fields_present_not_covered` ships on every fingerprint, because *we did not hash
it* is not *it did not change*.

## 4. The live run — and a confound I nearly published

Two real pulls of `/api/diag/journal?table=trades&limit=1000`, **2026-09-12T16:16:36Z**
and **2026-09-12T23:39:50Z** (7.4h apart), 41 fields, `group_field=order_package_id`:

| | |
|---|---|
| `state` | `content_changed` |
| `rows_changed` (present in both, content differs) | **0** of 998 shared |
| `rows_added` / `rows_removed` | `5728, 5729` / `4728, 4729` |
| `also_reordered` | **True** |
| `shared_order_preserved` | **True** |
| `groups_changed` | **1** — `pkg-2f8c3a901ae84be1` |
| `n_groups` old → new | 791 → 792 (111 multi-row in the old pull) |

⚠️ **`also_reordered: True` here is NOT evidence of hypothesis (a), and my first
reading of it was that it was.** `order_digest` covers the whole sequence, so two
added and two removed rows move it even when every surviving row kept its place.
The 998 shared rows kept their **relative order exactly**, and the endpoint returns
rows strictly `id`-descending in both pulls.

**The instrument was changed rather than the memo written around it:**
`shared_order_preserved` now ships on every verdict — `True` / `False` / **`None`
when not comparable** (no shared ids, or a stanza that did not record the order).
Never `True` for "nothing to compare".

⚠️ **A stanza without `include_order=True` cannot answer it**, and returns `None`
rather than a reassuring `True`. That loss is named in `render_stanza`'s own
docstring; `id_first`/`id_last` still bound the window cheaply.

## 5. ⚠️ THE THIRD MECHANISM — observed, and named by neither hypothesis

`pkg-2f8c3a901ae84be1` was a **3-row** fan-out package at 16:16Z and a **2-row**
package at 23:39Z, because row `4729` fell off the 1000-row tail:

```
old members ['4729', '4730', '4731']
new members         ['4730', '4731']
```

**Nothing was re-stamped** (0 of 998 changed) and **nothing was reordered**
(relative order identical). **The window moved.** A package-level adjudication — or
any `rows[0]` selection — can flip on that alone, which is exactly the U15 symptom.

This is neither (a) nor (b). It is a third cause, and it is the one *most* likely
to explain a control-arm cell moving by one package between two tail pulls taken
days apart, because it needs no venue behaviour at all — only time.

**State the population:** one pair of pulls, 7.4h apart, one 1000-row tail, 111
multi-row packages in the older pull, **exactly one** membership change. That is an
existence proof of the mechanism, not a rate. It says nothing about how often it
happens over the multi-day gap U15's discrepancy actually spans, which is longer
and would drift further.

⚠️ **It still does not attribute the 30-vs-31** — U15's pull is gone, so which of
the three causes acted there remains unknown. What changed is that there are now
three candidates rather than two, and the next occurrence is decidable.

`group_field` is **off by default and never defaulted to a value**: a fingerprint
taken without it grades `not_declared` — *we did not look* — never "no group
changed". Three group states, never collapsed: `compared` · `not_declared` ·
`undecidable_group_fields_differ`.

## 6. Verification — 64 controls, and two of my own plants were ineffective

Fifteen defects planted; every one caught, **after two plants had to be re-armed**:

| planted defect | controls that fired |
|---|---|
| set instead of multiset | 28 |
| order dominates content | 12, 14, 15, 16 |
| no field-set guard | 17 (+ crash) |
| incomplete stanza counts as a declaration | 39, 40, 42 |
| bare `str()` canonicalisation (no type tags) | 29, 30, 31 |
| drop rows with no id | 27 |
| `no_declared_input` returns `[]` not `None` | 23 |
| `not_declared` returns `[]` not `None` | 25 |
| **group members unsorted** | 31, 32 |
| mismatched group fields compared anyway | 27, 30 |
| null group given a synthetic key | 31 |
| no shared rows returns `True` not `None` | 18 |
| `shared_order` skips the shared filter | 16 |
| stanza with no order asserts preserved | 20 |

⚠️ **Two plants tripped nothing on their first attempt, and in both cases the
PLANT was wrong, not the harness — but each exposed a real control gap:**

1. **`"\x00str:" → "\x00num:"`** did not create a collision, because
   `repr(float(10))` is `"10.0"` and the fixture string was `"10"`. The control was
   testing a string that could not collide. It now uses **`"10.0"`**, the string
   that actually does, and a bare-`str()` plant fires three controls.
2. **Unsorted group members** changed nothing, because every fixture happened to
   arrive already sorted. A control now delivers the same group in a **different
   row order** — without it, a package whose siblings merely swapped places would
   be reported as a membership change: a false positive on the third mechanism, in
   the alarming direction, which is the one that gets acted on.

**This is the same class of error as MI-278 U21's** (a plant that coincided with
the correct behaviour on the chosen fixture). A plant that fires nothing is a
statement about the plant first and the harness second, and both must be checked.
The plant runner greps the **whole** output and surfaces `Traceback` explicitly —
`tail -N | grep FAIL` is blind to a plant caught by crashing, which is the
truncation flaw this lane has now hit twice.

## 7. What is proposed, and what is deliberately not

**Proposed (Tier-1, no approval needed, and done here):** the instrument exists and
this memo carries the first stanza. A memo author runs one command.

**NOT proposed: a CI guard requiring the stanza.** 66 memos would fail it on day
one. The precedent is explicit — `diagnostic-provenance-guard` ran diff-scoped
while its residue stood at 52 findings for 26 days, and its ungated `--all` step
only became survivable *after* the residue was drained to 0. An ungated guard here
would be the same mistake with a fresh coat. A diff-scoped guard on **newly added**
memos is the natural next step once adoption exists; it is a separate unit and is
not smuggled in here.

**NOT proposed: retrofitting the 66.** Their pulls are gone. A stanza computed
today would fingerprint a pull the memo was not computed from — a fabricated
provenance, which is worse than none.

---

## Standing

`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-…` is `loud: true` and unchanged:
**17 days, −$38,851.81, onset 2026-08-27**, bounded below by the data. Nothing in
this unit addresses it.

<!-- input-provenance
source: /api/diag/journal?table=trades&limit=1000
pulled_at: 2026-09-12T23:39:50Z
n_rows: 1000
id_field: id
rowset_digest: sha256:7c1c4199c07a72c86f0e8061981aff04134b61a1e7d967e9d0e77312041cf63d
order_digest: sha256:1cd78ceddecde1b446d4d5a0ba81be6baae10072c431a172b54dd48ef4f7da0b
fields_covered: account_class,account_id,bias,broker_order_id,closed_at,cost_source,created_at,direction,entry_price,entry_reason,exit_price,exit_reason,fee_maker_usd,fee_taker_usd,funding_paid_usd,id,is_backtest,is_demo,killzone,notes,order_package_id,pnl,pnl_percent,position_size,protection_repair_first_at,protection_repair_last_at,protection_repair_last_kind,protection_repair_last_verified,protection_repairs,reconcile_status,setup_type,sl_order_id,status,stop_loss,strategy_name,symbol,take_profit_1,take_profit_2,take_profit_3,timestamp,tp_order_id
id_first: 5729
id_last: 4730
n_rows_without_id: 0
n_duplicate_ids: 0
-->
