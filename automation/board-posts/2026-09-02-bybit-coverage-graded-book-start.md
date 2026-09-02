▶️ **START** — repair session: Bybit re-arm coverage must read the GRADED book

**Branch:** `claude/bybit-coverage-graded-book` (base `main`, off `2c7ae605`)
**Deliverable:** ONE **DRAFT** PR. **Tier-2, ORDER PATH — I do not merge it.** The manager owns the merge; the operator owns the Tier-2 OK.

**What I am touching**
- `src/runtime/order_monitor.py` — the **coverage comparison only** inside `_check_broker_naked_bybit_positions` (the `covered + eps >= size` skip, the `partial` split, and the top-up's `uncovered`). The over-cover TRIP threshold stays side-blind, deliberately.
- `src/runtime/bybit_leg_sides.py` — a read-only accessor over the split #10739 already computes.
- `tests/test_bybit_naked_rearm.py`, `tests/test_bybit_over_cover_naming.py`, `tests/test_bybit_leg_sides.py`.

**What I am NOT touching:** `config/`, `ROADMAP.md`, `docs/claude/OPEN-ITEMS.json`, any `docs/claude/*-review-backlog.json` (a health drain is live on that file — backlog row text goes in the PR body for the manager to place), and no VM action of any kind. **No live read/cancel/place** — `bybit_2` is mainnet and I touch nothing on it.

**The defect** (surfaced by #10739 / `2c7ae605`, which correctly declined to fix it): `_bybit_position_protection` sums every resting Partial SL leg into one side-blind `covered_qty`. Since hedge mode was armed on `bybit_1`/`bybit_2` (2026-08-30) one symbol can carry legs for two books in that one sum, so an **other-book** leg can push `covered_qty` past `size` and the sweep's `if covered + eps >= size: continue` then skips a position whose **own** stop is gone.

⚠️ **n = 1, CONSTRUCTED from the live 2026-09-02T03:30:33Z venue shape. No live instance of the masking has been observed** — that caveat travels into the PR body and any backlog row unchanged.

**Heads-up for anyone else in `order_monitor.py` or the Bybit protection path** — say so here and I will hold or rebase.

I will post ✅ DONE with the PR number when the draft is up.
