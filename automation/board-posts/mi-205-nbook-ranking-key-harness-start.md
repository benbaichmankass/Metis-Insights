▶️ **START** — MI-205 · N-book ranking-key harness

**WO:** `WO-20260908-BUILD-THE-N-BOOK-RANKING-KEY-HARNESS` (registry key `pending-20260908T221047Z`)
**Authority:** operator-approved 2026-09-08 — `DEC-20260908-TRADE-PRIORITISATION-HARNESS` → `build_harness`, recorded in `docs/claude/work/objects/WO-20260908-DECISION-TRADE-PRIORITISATION-HARNESS.yaml`
**Branch:** `claude/mi-205-nbook-ranking-key-harness`
**Tier:** 1 (measurement only)

Posting through the `board-post` relay: `add_issue_comment` returned **403 Resource not accessible by integration** on board #11336 from this session.

**Paths I will touch:**
- `scripts/backtest_system.py` — N-book mode + a `--ranking-key` arm (the shape of its existing `--flip-policy`)
- `.github/workflows/` — one new dispatchable workflow firing the four arms on a **GitHub runner**, NOT the 1-core trainer VM (`docs/claude/vm-resource-management.md`)
- `scripts/research/` — the contested-subset per-arm reporter
- `tests/` — ranking-key arm + N-book accounting
- `docs/claude/work/objects/WO-20260908-BUILD-THE-N-BOOK-RANKING-KEY-HARNESS.yaml` — my work object (created this session)
- `docs/research/` — the result write-up
- `.github/pr-landing/`, `.github/pr-automerge-requests/`, `docs/claude/session-board.json` — landing declaration + merge slot

**Explicitly NOT touched — the Tier-3 boundary:** `src/runtime/intents.py`, `config/strategies.yaml`, and every order path. Measuring the question is not answering it; flipping the live ranking key would be Tier-3. If the build starts to require an order-path change I STOP and write the proposal instead.

**The reporting contract I am held to:**
- CONTESTED SUBSET ONLY — a ranking key cannot matter on a tick with one candidate, and pooling uncontested ticks would manufacture a null.
- STRATIFIED BY `decided_by` (half of contests have every contender at identical confidence).
- Against the **ACHIEVED** contested-tick count, never the declared 1,780. Population stated for every number.
- Per-arm net PnL, expectancy in R, maxDD.
- Gate is pooled-net **AND** fold-majority, never either alone — `be_floor_r=1.5` won a strict fold majority under all three panel members while LOSING 35R because 30–39% of its folds were INERT. Fixed fold panel; inertness IMPORTED from `m20_wf_effective.is_inert`, not restated.
- A NULL RESULT CLOSES THE QUESTION AND IS A SUCCESS. `random_tiebreak` is a declared arm and the floor the others must clear — M18's adjacent allocator already answered NO (edge −$7, ranker OOS AUC ~0.51).

This build also unblocks the adjacent question (global vs per-account arbitration), blocked on the same missing capability. Designing for both; answering only this one.

Will post ✅ DONE when I wrap.
