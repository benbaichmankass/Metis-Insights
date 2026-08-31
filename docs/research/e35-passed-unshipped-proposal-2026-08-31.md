# e35 `passed_unshipped` — the Tier-3 proposal set, as it stands on 2026-08-31

**Date:** 2026-08-31 · **Tier 1** (a proposal + records; no `src/`, no `config/`, no order path)
**Requested by the operator** 2026-08-31 ("bring me the per-leg proposal to review").
**Answers** `OI-20260829-E35-REVERSED-LEGS-ARE-A-TIER-3-PROPOSAL-SET-NOT-APPLIED`.

---

## 0. ⚠️ The set is 2 legs, not 10 — the OPEN-ITEMS row is STALE

The row this proposal answers says *"15 SHIPPABLE gate-passing cells across 10 live legs"*.
**That is no longer the state of the matrix and must not be quoted as current.**

Measured now against `docs/research/exit-refinement-coverage.json` (`updated_at 2026-08-30`,
52 rows):

| `bracket_geometry.status` | at the 08-29 re-check | **now** |
|---|---:|---:|
| `shipped` | 8 | **17** |
| `passed_unshipped` | 12 | **2** |

The 2026-08-30 Tier-3 approval (`OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED`)
shipped 9 legs, which accounts for the movement. **What remains unshipped is two legs:**
`gld_pullback_1h` and `spy_pullback_1h`. This document is that set, and nothing else.

---

## 1. Selection rule, stated before the numbers

Per `e35-matrix-recheck-2026-08-29.md` § 7, a proposal must state its rule in advance,
trace each cell to its corpus row, and carry the § 1 caveat. So:

- **Rule (B4's, unchanged):** highest `wf_wins_effective`, tie-break `d_net_r`, restricted
  to cells with **no timeout component** (a `to*` cell is unshippable by construction —
  no live trend/pullback/squeeze unit implements a bar-count exit,
  `BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES`).
- **Corpus:** `docs/research/e35-bracket-corpus.jsonl`, newest run per leg = **2026-08-31**
  (199 cells each; the 08-29 rows the matrix cites are superseded but retained).
- **§ 1 caveat carried:** the 08-29 CLEAN-leg control failed as specified and the operator
  accepted a fold-level substitute. Every verdict below inherits that.

⚠️ **This run is at `split_target_oos=60`, the matrix's cited run was at 50.** The
`measurement_key` carries the target, so these are *additional* measurements, not
replacements — but it also means the matrix's quoted numbers and mine are **not the same
measurement** and must not be diffed against each other casually.

---

## 2. THE HEADLINE NUMBER IN THE MATRIX IS THE FULL-SAMPLE ONE

The matrix names each leg's winner with a `d_net_r` — **+46.4640** for gld, **+28.1569**
for spy. Those are **full-sample** figures. The gate's own out-of-sample delta is a
different field, `gate_oos_d_net_r`, and it is much smaller:

| leg | named cell | `d_net_r` (full) | `gate_oos_d_net_r` (OOS) | ratio |
|---|---|---:|---:|---:|
| `gld_pullback_1h` | `sm1.5` | +47.66 | **+19.12** | 0.40 |
| `spy_pullback_1h` | `sm1.5` | +28.16 | **+4.46** | **0.16** |

Both are real fields and neither is wrong; quoting the first as the expected improvement
is what would be wrong. **spy retains 4.46/28.16 = 16% of its headline out of sample (n = 58 OOS trades)** —
population: the `sm1.5` cell of `spy_pullback_1h` on the 2026-08-31 run, achieved
`base_oos_trades` 58 against `base_is_trades` 78, at `split_target_oos` 60.
(gld's `sm1.5` on the same run: 59 OOS against 156 IS.)

---

## 3. ⚠️ NEITHER NAMED WINNER IS A CLEAN GATE PASS — BOTH FAIL ON DRAWDOWN

This is the finding that decides the proposal, and it is not visible from the matrix's
status string alone. Both named cells are **`path_b_wf_pass`** — Path B, the weaker route,
which asks only for net_R across folds. Reading the gate's own fields:

| leg | cell | `gate_is_passed` | `gate_oos_passed` | reason (both) | `d_max_dd` |
|---|---|---|---|---|---:|
| `gld_pullback_1h` | `sm1.5` | **False** | **False** | `maxdd_worse` | **+4.90** |
| `spy_pullback_1h` | `sm1.5` | **False** | **False** | `maxdd_worse` | **+3.64** |

**Both the in-sample and the out-of-sample gate REFUSED both cells, on exactly the same
ground: the drawdown gets worse.** Path B passed them on net_R alone. A positive
`d_max_dd` means a deeper trough, so what is on offer in both cases is *more net R bought
with more drawdown* — not a free improvement.

---

## 4. Per-leg

### 4.1 `gld_pullback_1h` (GLD 1h, `execution: live`) — a candidate, but NOT the named cell

| cell | verdict | path | IS | OOS | `d_net_r` | OOS `d_net_r` | `d_max_dd` | wf eff |
|---|---|---|---|---|---:|---:|---:|---:|
| `tp6_sm1.5_to24` | `path_b_wf_pass` | B | ✗ | ✗ | +66.07 | +17.80 | +2.27 | 6 |
| `sm1.5` *(matrix's named winner)* | `path_b_wf_pass` | B | ✗ | ✗ | +47.66 | +19.12 | **+4.90** | 5 |
| `sm2` | `path_b_wf_pass` | B | ✗ | ✗ | +20.95 | +4.46 | +0.34 | 4 |
| **`tp4`** | **`wf_pass`** | **A** | **✓** | **✓** | +9.00 | +4.34 | **0.00** | 4 |

- `tp6_sm1.5_to24` is the best number and is **unshippable** (`to24` timeout).
- `sm1.5`, the matrix's named winner, buys +19.12 OOS at **+4.90 worse drawdown**, and both
  gates refused it. Its 2021 fold is a loss (−1.04).
- **`tp4` is the only Path-A pass on either leg** — the strong gate, IS *and* OOS both
  passed, at **exactly zero drawdown cost** (`d_max_dd 0.00`). It is modest (+4.34 OOS) and
  its wf is 4/6 (2024 and 2026 lose), but it is the only cell here that is not buying
  return with risk.
- **The matrix does not name `tp4`.** Shipping "the winner" as the matrix records it would
  ship `sm1.5`, the drawdown-widening cell, and skip the clean one.

### 4.2 `spy_pullback_1h` (SPY 1h, `execution: live`) — recommend DECLINE

| cell | verdict | path | IS | OOS | `d_net_r` | OOS `d_net_r` | `d_max_dd` | wf eff |
|---|---|---|---|---|---:|---:|---:|---:|
| `sm1.5` *(named winner)* | `path_b_wf_pass` | B | ✗ | ✗ | +28.16 | **+4.46** | +3.64 | 5 |
| `sm1.5_to400` | `path_b_wf_pass` | B | ✗ | ✗ | +28.16 | +4.46 | +3.64 | 5 |
| `sm2` | `path_b_wf_pass` | B | ✓ | ✗ | +16.15 | **+0.60** | +1.95 | 5 |

Four independent reasons to decline, any one of which would be enough:

1. **OOS is +4.46 against a +28.16 headline** — 16% retention over n = 58 OOS trades
   (`base_oos_trades` 58 vs `base_is_trades` 78, 08-31 run, `split_target_oos` 60).
2. **A single fold carries it.** 2023 contributes **+22.36** of the +28.16; 2024 is
   **−12.33**. Remove 2023 and the cell is roughly flat-to-negative.
3. **The leg is flagged CONTAMINATED** in the matrix's own `timeout_binding` field: the
   harness's 200-bar force-close **bound on 17 of 39 graded geometry pairs**, so this
   verdict was measured under an exit production does not have.
4. **`sm2`'s OOS delta is +0.5977** — indistinguishable from zero, and it is the only cell
   whose IS gate passed.

⚠️ Note `sm1.5` and `sm1.5_to400` are **numerically identical on every field**. That is
itself evidence the 400-bar timeout never binds on this leg — which does *not* rescue the
leg, because the contamination finding above is about the harness's separate 200-bar
force-close, not the cell's own `to400`.

---

## 5. What I recommend, and what I am NOT claiming

| leg | recommendation |
|---|---|
| `spy_pullback_1h` | **DECLINE.** Record the four reasons in the matrix so `passed_unshipped` stops reading as a pending ship. |
| `gld_pullback_1h` | **Do not ship `sm1.5`.** If anything ships here it should be **`tp4`** — the only Path-A, zero-drawdown-cost pass — and only if you accept a +4.34 OOS improvement as worth a config change and a fresh soak. Declining the whole leg is also defensible. |

⚠️ **NOT CLAIMED:** that `tp4` will improve live PnL. It is one cell, on one leg, measured
on a harness whose § 1 control is unresolved, at wf 4/6. What I am claiming is narrower and
checkable: *of the cells on offer for these two legs, `tp4` is the only one both gates
accepted, and the only one that does not widen drawdown.*

⚠️ **`avax_pullback_2h` is NOT in this set.** § 7 of the re-check flagged it at +1.0080 as
"a candidate to drop, not ship". It is not `passed_unshipped` in the current matrix, so it
is out of scope here and is *not* silently endorsed by that absence.

---

## 6. If you approve anything

A ship requires, in one PR: the `config/strategies.yaml` change (Tier-3), the matrix row
flipped `passed_unshipped` → `shipped` with the cell id **in backticks** (`check_matrix_bracket_values.py`
reads only the backticked spelling, and these two refs are currently bold-only — § 7 item 3),
and a new OPEN-ITEMS monitoring row, since deployed is not proven.

*Populations: matrix 52 rows @ 2026-08-30, 2 `passed_unshipped`. Corpus rows for these two
legs: 206 each (7 @ 08-29, 199 @ 08-31). Passing cells on the newest run: gld 4 of 199,
spy 3 of 199. Reproduce: `python3 scripts/research/e35_matrix_recheck.py`.*
