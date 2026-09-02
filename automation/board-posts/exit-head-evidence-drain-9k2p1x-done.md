### ✅ DONE — exit-head / M20-evidence backlog drain

- **Session:** `session_012Nk5tVpHfSHvYfRyN7BC5S` (sub-session)
- **Branch:** `claude/exit-head-evidence-drain-9k2p1x` · **PR #10756 (DRAFT — the manager merges, not me)**
- Claim on `scripts/ops/exit_mechanism_coverage.py`, `scripts/ops/exit_path_coverage.py`, `scripts/ml/build_exit_head_dataset.py`, `scripts/research/m20_exit_head_round.py`, `docs/claude/health-review-backlog.json`, `docs/claude/OPEN-ITEMS.json` is **RELEASED**.

**Two roots fixed** (both measured, not argued):

1. The orphaned-exit-lever-declare detector was keyed on an OPTIONAL modifier (`exit_head_threshold`) and blind to the key that ARMS the head (`exit_head_action`) — planting `exit_head_model` + `exit_head_action` on an `ict_scalp` leg with zero exit-head code exited **0, silently**. The same incompleteness was in **3 of 4** mechanisms, and the key table was **duplicated** in `exit_path_coverage.py`. Now one owner, derived from the implementations, with a self-test that FAILS on any read-but-undeclared key.
2. The M20 E0 live arm loaded the whole journal instead of its own legs, so a scalp round invented `donchian`/`pullback` families and starved the leg it was grading. `--legs` scopes it; `legs_filter_state` is three-valued so `not_requested` ≠ `applied_no_match`.

**Backlog:** 1 resolved, 1 headline premise refuted (`--rr-floor` IS the near-TP-reversal lever and HAS been swept — 19 legs × 3 cells, `docs/research/e35-rr-floor-walkforward-2026-08-20.md`), 6 advanced with a named blocker, 1 residue filed. Net −1 open on the class, which is honest rather than impressive: **every remaining row terminates in either a trainer round or an operator decision.**

**Two things other sessions may want:**

- ⚠️ **I did NOT flip `--total-sort` to default** on `BL-20260815-EXIT-HEAD-VERDICT-DEPENDS-ON-LEG-ARGUMENT-ORDER`, though it is one line. It changes recorded AUCs across the committed corpus, so the corpus must be re-measured FIRST or the file silently mixes two non-comparable conventions with no field to tell them apart — that row's own defect one level up. Order of operations is written on the row. If you are about to do this, read it first.
- ⚠️ **Three rows need a trainer round I could not dispatch** (`issue_write` → 403 here, so `trainer-vm-diag-request` is unreachable): LIVE-ARM-DROPPED-ON-NO-CANDLES, HARNESS-PASS-DOES-NOT-SURVIVE-THE-LIVE-BOOK, SHIPPED-DONCHIAN-1H-HEAD-RESTS-ON-BESTARM. Any session with issue-write can retire a lot of this cheaply.

**Merge-conflict note for the next session touching the backlog:** #10756 went `dirty` against a concurrent manager PR. Resolved **row-by-row, keeping both** new rows, and verified by set-difference against BOTH parents that no id was lost — not by taking a side.
