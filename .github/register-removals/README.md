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

## ⚠️ The declaration is VERIFIED, not presence-only

**A declared removal that did not actually happen FAILS the run** — it is
reported as a `PHANTOM declaration`. So this file cannot be used as a blanket
silencer: it is a statement about *this diff* that has to be true, and it stops
being true the moment the diff changes.

That is the `new-table-wiring-guard` lesson, stated in `CLAUDE.md`: *a guard
cheaper to lie to than to satisfy is worse than no guard.* A malformed file is
**ignored** rather than trusted, so a broken override silences nothing.
