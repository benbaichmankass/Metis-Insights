✅ MI-222 HANDOFF — #11550 is GREEN AND CLEAN and ready for your merge; #11566 (Tier-2) armed and HELD

For the manager (`session_01HrmZ1RRNM4UnEUaFdrPEjj`). Session `session_01WmFdLwfq4U5aLLjFRDDy6b`. Posted from `claude/mi-222-board-notes`, a branch with **no PR**, so this relay's results commit cannot bury either PR's checks.

## #11550 — READY. Head `3c103412`.

| check | result |
|---|---|
| `guards` | ✅ success |
| `pytest-run` | ✅ success |
| `pytest-collect` | ✅ success |
| `repo-inventory` | ✅ success |
| **`mergeable_state`** | **`clean`** |

Both halves, per your #11555 lesson — I am not reporting green checks over a `dirty` tree. `landing: hold`, auto-merge NOT armed, no merge slot. **Yours to merge.**

⚠️ **ITS BODY IS STALE AND I CANNOT FIX IT — `update_pull_request` 403s for this session too.** Three claims in it are now wrong, all corrected **in the diff** (evidence doc § 6b), but a reviewer reads the body first:
1. It says the work object is **not** on `origin/main`. **It is** — it arrived with the main-merge.
2. Its "Proposed next action" (expose the raw `get_positions` payload) is **already built**: `clients.py:2036` + `diag.py:2890`, shipped on main while the branch was open. **I nearly repeated the exact error this PR exists to correct** — the lane's finding was that its brief named a mechanism that already existed, and its own proposal was a mechanism that already existed. Same fix for both: check `origin/main` before *proposing*, not only before building.
3. Its "the reader collapses states" claim is **narrowed**: MI-221 § 12 measured `row_count: 2`, both books `size_raw "0"`, so for that instance it is *zero-size rows returned*, not *no row*. Still true of `account_open_positions` — which every production sweep calls — and no longer of the system as a whole.

If you want the body corrected before merge, it needs someone whose MCP can PATCH.

## #11566 — Tier-2, armed, HELD for an operator merge decision

Branch `claude/mi-222-collapsed-read-observable`, cut from `07c2d12e`. CI **armed** (4 checks running) by an ordinary content push — a real test, not padding: it pins the soak's diag allowlist entry, the gap I shipped wrong on the first pass.

`landing: hold` · `tier_2_3_needs_approval` · auto-merge NOT armed · no merge slot. `check_pr_landing` → `declared_needs_approval`, correctly naming both Tier-2 paths (`clients.py`, `diag.py`).

The safety property is **asserted, not argued**: `TestReturnValueIsByteIdentical` runs the real function over the same venue payloads and pins the returned list key-by-key. If it is green the change cannot have altered what any caller receives.

⚠️ **I could not verify the operator's approval from any repo surface** — `MANAGER-CHECKLIST.json` on main carries no MI-222 entry, your record is still in unmerged #11547. Not treating it as suspect; it is why this declares `hold`. **The human merging should confirm the scope is what was granted: observability only.**

## The four clauses: I cleared NONE, and I am not close

Now that the object is readable:

- **(1)** ❌ — and **the object's own premise needs correcting**: the roster sweep it contemplates is *not* independent of `account_open_positions`, it **is** `account_open_positions` (`clients.py:1410`). The genuinely independent anchor is MI-221 § 11's wallet-margin reconstruction, which reads no position list at all. **Proposed, not built.**
- **(2)** ❌ nothing built, nothing scheduled.
- **(3)** ❌ and **unsatisfiable against the real ETH** — needs a planted control, which touches a real-money venue.
- **(4)** ❌ as the object means it (a *detector's* finding).

**MI-222's done_condition is NOT met. The object should stay `ready`, not `done`.** What exists is the correction, the characterization tests, and observability that is a *precondition* for a non-blind detector — not the detector.

## Three decisions that are yours, not mine

1. **Merge #11550** (Tier-1, green, clean) — and decide whether the stale body matters enough to fix first.
2. **#11566 needs the operator's merge OK** (Tier-2 on the module gating a real-money close).
3. **Clause (3) needs a planted control on a real-money venue. I will not do that without an explicit instruction**, and per MI-221 § 14 nothing in my work may be cited as evidence the operator's position does not exist.

## Two facts worth carrying into canon

- **`create_pull_request` AND `update_pull_request` both 403 for this session.** The documented prevention — "open the PR yourself first, then arm" — assumes a session that can create PRs, and a read-only-MCP session is the only kind that needs the relay at all. The prevention is unreachable for exactly its intended audience.
- **A main-merge re-triggers `claude-pr-automerge`** by touching other branches' arming files. I verified from source (line 150) that it resolves the slug from the *branch name*, finds no matching file, and no-ops — so the guard holds. Worth knowing, since every branch merges main.

No order was placed, modified or cancelled on any account. Every venue fact here is cited from MI-221's GETs or read from source.
