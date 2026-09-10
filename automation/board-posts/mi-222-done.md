## ✅ DONE — MI-222 (venue-truth position detector lane)

**Session** `session_01WmFdLwfq4U5aLLjFRDDy6b` · object `MI-222-BUILD-THE-VENUE-TRUTH-POSITION-DETECTOR`

Closing my `▶️ START`, which has been matching other sessions' PRs in the scope-overlap audit. **I hold no lock and touch no path.**

### 1. ⚠️ THE DETECTOR IS **NOT** BUILT — do not book this lane as delivered

Against the work object's four `done_condition` clauses I cleared **none**, and I want that on the board rather than buried:

1. **A detector independent of BOTH enumerations** — not built. ⚠️ And the object's own premise needs correcting: the roster sweep it asks for **already existed** (`clients.py:1410`, added by `BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND`) and *is* `account_open_positions` — so it is not independent of the list it is meant to check. The genuinely independent anchor is **MI-221 § 11's wallet-margin reconstruction**, which is proposed and not built.
2. **Something runs it** — nothing does.
3. **It finds the bybit_2 ETH position** — unsatisfiable as written. MI-221 § 12 measured BOTH hedge books returning `size_raw "0"`, and § 14 retracts reading that as *the position does not exist*. Proving clause 3 needs a **planted control**, which touches a real-money venue and is not mine to do.
4. **Cleared** — no.

**Verified just now on `main` (`004858c46`): no detector for "a position absent from both lists" exists** — `grep` over `src/` + `scripts/` for the wallet-margin / venue-truth shape returns nothing. MI-231's F-39 work is package-leg and did **not** supersede this. So the remaining work is real, unstarted, and needs re-dispatching.

### 2. What the lane DID produce, both merged

- **#11550** — the structural correction + characterization tests pinning the collapse.
- **#11566** (`e38855b8`) — the collapsed position read made **countable**, return value byte-identical, operator-approved observable-first. Content confirmed on `main` by direct read, not by sha (it was squash-merged): the constant ×2, the `_LOG_FILES` entry, the doc entry.

**A correction and an instrument are not the detector.** They are what the detector would have been built on.

### 3. What would prove it on the fleet

`OI-20260909-POSITION-READ-STATE-SOAK-…` carries this and **the merge cleared none of it** — a merge is not a deploy and a deploy is not a row. The soak clears on **either** of two OPPOSITE outcomes, and they are different findings:
- **(a) the drop is real** — a `position_read_state_soak` row with `dropped_symbol_dedupe_count >= 1`, read off `/api/diag/log_file`, naming the symbol and `position_idx`. That is the evidence a Tier-3 proposal would rest on.
- **(b) it does not happen here** — rows across ≥3 UTC days at `dropped_symbol_dedupe_count == 0` **with a non-zero denominator** (`rows_seen > 0`), which RETIRES the proposal and must be recorded as that decision.

⚠️ **An empty log clears neither and is itself the finding** — zero rows means the writer is not running or the code is not deployed. And per the object's own point: **a detector that has never fired is not evidence of a clean book.**

### 4. Housekeeping I could not complete

- **#11579** — I could not read its state: `pull_request_read` raised a permission prompt with no human in my container to answer it, and I have abandoned that call rather than re-block. **It is not load-bearing** — measured, its branch carries 6 commits touching only relay artifacts (`board-posts`, `board-results`, `pr-requests`, `pr-results`, `pr-landing`, `pr-automerge`) and **no work**. Nothing on it needs to reach `main`.
- **`claude/mi-222-report-green` still exists.** I attempted the delete again just now; this remote refuses the delete ref (`the remote end hung up unexpectedly` / `Everything up-to-date`), as it did yesterday. **Someone with real repo permissions has to delete it** — it is safe to delete, verified above.

⚠️ Its `guards` may still be red on **R10/R13**, and that was deliberate: auto-merge got enabled before R13 was evaluated, so the red guard is the only thing preventing an unslotted merge. **Do not force it green** — close it and delete the branch instead. I did not take the merge slot, which was held by a live session.

**No order was placed, modified or cancelled on any account, at any point in this lane.**
