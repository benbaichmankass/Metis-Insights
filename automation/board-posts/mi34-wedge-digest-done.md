✅ **DONE** — MI-34 · session_01XbigCVRcy2bnVpm1tKNPno · **DRAFT PR #10758** · branch `claude/mi34-wedge-page-to-digest`

A close failure whose cause is **confirmed unclearable by any bot-side lever** now routes to the rolled-up digest instead of the pager. Routing only — no close path, no classifier, no order path, and not the live GLD wedge (that is #10679's and needs operator/venue action).

**The trigger is evidence, never repetition.** It keys on `share_hold == broker_cancel_wedged`, which `classify_share_hold()` determines by reading the residual order's own `status` from the broker (#10679, already on `main`). There is no retry count in the new module and an AST test pins that structurally. A close failing for an *unknown* reason pages exactly as before — five parametrised tests assert it for every other reading, `residual_unreadable` and `not_classified` included.

**Five transitions, one quiet.** `newly_wedged` / `evidence_changed` / `cleared_confirmed` / `vanished_unattributed` all PAGE; only `still_standing` is carried. `LOUD_TRANSITIONS` is the *complement* of the quiet state, so a state added later is loud by default. `vanished_unattributed` is its own state because a position that simply disappears is not a clean clear — the `PROTECTION_REASSERT_MODE` precedent.

**Silence is not reachable, four independent ways.** The floor (`CLOSE_WEDGE_REPAGE_HOURS`, 24h default) makes this ~24 pages/day → 1, not → 0; suppression is conditional on the ledger write actually succeeding; the digest renders all three read states and none is empty; and the floor does not depend on the digest working at all.

**On the seam:** `_append_operator_alert` is right for the RECORD and insufficient for the DELIVERY, so both halves are done. The ring row is written on **both** routes carrying `route` — it is the only surface a page rate is recoverable from, and dropping it with the ping would make "downgraded" and "never fired" identical there. Delivery needed a new VM→runner path, because the digest runs on a GitHub runner and `runtime_logs/` is `.gitignore`d and VM-local: the ledger is allowlisted on diag in the same commit as its writer, and `work-digest.yml` fetches it.

**A defect the tests caught rather than shipped:** `sweep_vanished` persisted its clock only when it retired something, so every quiet pass threw away the evidence that we had been watching — pinning `last_sweep_at` at the last re-arm and making `vanished_unattributed` **unreachable in production** while every retirement unit test passed. Fixed and pinned.

**Two corrections to existing records, in their own commits.** (1) `OI-20260901-ALPACA-SHARE-HOLD-CLASSIFIER-SHIPPED-NOT-YET-OBSERVED` read "NOT MERGED AND NOT OBSERVED"; the first half is false — 839247cc IS on `origin/main`, verified with `git merge-base --is-ancestor` and a grep of `origin/main`, not by reading a PR page. The NOT-OBSERVED half stands and is what the row is now for. (2) Three of my own citations of `OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON` went stale when #10753 cleared it mid-branch; corrected, and the argument they support is *better* evidenced by the correction (a cron that slips ~4h50m is a worse carrier for a standing alarm than one never tried).

**Populations, stated:**
- Guards: **53 of 53** relevant passed (`run_guards.py --base main`); 17 skipped as not relevant to the diff. `collapsed-state-guard` clean at **27 contracts**, up from 25 (`share_hold.state` + `close_wedge.transition` registered).
- Tests: **28 new**; **1189 passed / 0 failed / 7 skipped** over the touched surfaces (`-k "close or diag_unit or operator_alert or alpaca or notification or collapsed"`, 13055 deselected).
- Discriminating controls: **4 surgical breaks injected into the digest renderer, 4 caught**; the sweep-clock fix fails its test when reverted.
- Import layering: **6 contracts kept, 0 broken** (`run_guards` reported `layer-guard` 127 — `lint-imports` absent from my container, not a violation; I installed it and ran it rather than assuming).

⚠️ **NOT OBSERVED ON THE FLEET.** No live wedge has been seen flowing through this and no digest run has been observed carrying one. Both are deployment facts, not test facts. The floor exists precisely because neither has happened yet, and the docstring says when it may be removed: **when a digest run is observed carrying a standing wedge** — not when this merges, not when it is green.
