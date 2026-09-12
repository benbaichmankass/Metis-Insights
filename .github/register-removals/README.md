# Declared removals from a shared register

`register-field-loss-guard` (`scripts/ci/check_register_field_loss.py`) FAILS a
PR that deletes a **field**, a **row**, or a **top-level key** from one of the
shared registers, because that is the signature of a merge that silently
reverted somebody else's write-back — and a **union-by-id proof cannot see it**,
since the id sets still match exactly.

Nearly always the right response is **not** a file in here. It is to redo the
merge **by field**: take the base's version of the register and re-apply only
the keys your branch owns. `scripts/ops/install_merge_driver.sh` makes git do
that for you, and ⚠️ **it is NOT installed in a fresh clone** — `git config
--get merge.jsonregister.driver` returning nothing means every register merge in
that clone is a plain line-level merge, which is the failing case.

Use this directory only when a removal is **deliberate** — pruning a settled
`OPEN-ITEMS.json` row at review, say.

## Shape

One file per PR, named `<branch-slug>.json` (the branch with `claude/` stripped
and any remaining `/` replaced by `-`), matching every sibling relay here:

```json
{
  "why": "one sentence a reviewer can check, naming who decided",
  "removals": [
    {"file": "docs/claude/OPEN-ITEMS.json", "id": "OI-...", "keys": []},
    {"file": "docs/claude/work/SESSIONS.json", "id": "session_...",
     "keys": ["some_key"]}
  ]
}
```

* `keys: []` declares the **whole row** is gone. A keyed declaration does **not**
  excuse a whole-row loss, and there is a test for that.
* For a top-level key, omit `id` and put the key in `keys`.

## ⚠️ DELETE THE FILE ONCE ITS PR HAS MERGED

A declaration has to be **in** the diff that performs the removal, or the guard
cannot excuse it — so it merges to `main` alongside that removal, and from the
**next** diff onward the row it names is gone from the base too and it matches
nothing.

**The first ever use of this directory proved what that costs.** `#11915`
landed one file on 2026-09-12; `main` itself then returned **rc=1** with
`PHANTOM declaration`, and every open PR went red with it. The mechanism
guaranteed a repo-wide red after every single use.

`check_register_field_loss.py` now grades that case **`spent`** rather than
`phantom` — a true statement about a *past* diff, not a false one about this
diff — so it no longer fails a run. It is a receipt, not a silencer.

⚠️ **`spent` fails CLOSED and is not a loophole.** It requires positive evidence
that the declared row is absent from the **base**. With no base map, or a
declaration naming a file the map does not cover, the verdict stays `phantom`:
*we could not look* is never promoted to *it was already gone*. A declaration
naming a row that **is** on the base and was **not** removed here is still a
phantom, and still fails.

**So: delete your file in the next PR you open.** The guard prints the
instruction beside the spent line. Leaving it costs nothing today and is noise
tomorrow.

## ⚠️ The declaration is VERIFIED, not presence-only

**A declared removal that did not actually happen FAILS the run** — it is
reported as a `PHANTOM declaration`. So this file cannot be used as a blanket
silencer: it is a statement about *this diff* that has to be true, and it stops
being true the moment the diff changes.

That is the `new-table-wiring-guard` lesson, stated in `CLAUDE.md`: *a guard
cheaper to lie to than to satisfy is worse than no guard.* A malformed file is
**ignored** rather than trusted, so a broken override silences nothing.
