# N7 / D2 — the old-vs-new verdict diff, and why its control could never pass

**Tier 1, evidence only.** Item **N7** of `docs/claude/WORKPLAN-NIGHT-2026-08-29.md`.
⚠️ **Nothing here is applied to `config/strategies.yaml`.** Any verdict change is Tier-3
and operator-gated; the deliverable is the diff and its reading.

Reproduce: `python3 scripts/research/e35_resweep_verdict_diff.py --before a986ac3
--after c2641827 --clean-legs <timeout_binding_audit grades>`

---

## 1. The control, stated first — as § 0 requires — and it FAILS as written

`WORKPLAN-NIGHT-2026-08-29.md` § 0 is unambiguous: the 23 legs graded CLEAN on the
timeout axis *"must come back **numerically identical** … If they do not, the pin changed
something it should not have and **N7's diff is void** — that outcome is a finding, not a
nuisance."*

> **4,583 cells compared across the 23 CLEAN legs · 1,068 identical · 3,515 differing (76.7%) · 18 of 23 legs affected.**

**The control fails.** Reporting that first, before any other number, because that is the
order § 0 demands and because the temptation is to lead with the reassuring explanation.

## 2. ⚠️ But the control is UNSATISFIABLE, and that is the real finding

**The control cannot pass on any re-run taken on a different day, and the reason is not
the pin.** The sweep fetches a **trailing** window — `days=1830` ending at run time — so a
re-run three days later moves **both edges** of the data:

- the **right** edge grows: the current partial year gains bars;
- the **left** edge advances: the earliest year loses bars.

**Evidence, at fold level.** On `gld_pullback_1h|sm1.5` the walk-forward folds
**2021, 2022, 2023, 2024 and 2025 reproduce byte-identically** (`d_net_r` −2.2394 /
2.8491 / 2.3580 / 15.6317 / 14.9779 in both revisions) and **only 2026 moves**
(4.5483 → 4.0754). A computation change would not spare five closed folds.

Where a **closed** fold does move it is the **earliest** one — `avax_pullback_2h` at
2021 — which is the left edge dropping bars, not the pin.

| | cells |
|---|---|
| differing cells carrying fold data | **11** |
| — only the current (partial) fold moved | **8** |
| — a closed fold also moved (all `avax_pullback_2h`, all 2021 = left edge) | **3** |
| differing cells with **no** fold data (never carried into the gate) | **3,504** |

⚠️ **State that denominator.** Fold data exists only for the ~7 cells per leg carried into
the IS/OOS + walk-forward gate, so the fold evidence covers **11 of 3,515** differing
cells. It is decisive about *mechanism* and silent about the other 3,504. A stronger claim
than "consistent with a moving window, checked where checkable" is not supported.

**Nothing was lost:** 0 of 8,211 BEFORE keys are absent from the AFTER corpus
(8,211 → 8,289, +78 new keys, 8,159 rows = 41 legs × 199 cells from the new run).

## 3. ⚠️ `base == to400` is NOT the post-pin control either — and reading it as one manufactures a finding

The obvious substitute check is wrong, and it was drafted and discarded here rather than
published:

- **Before** the pin the base arm carried the harness default (48/200 bars), so
  base-vs-`to400` measured *does the default bind*.
- **After** the pin the base arm carries `NO_BAR_COUNT_EXIT` (1e9), so the same comparison
  measures *does 400 bind* — **a different question**, on which a long-hold leg differs
  **correctly**.

Divergence fell **439/1,588 (27.6%, 18 legs) → 182/1,569 (11.6%, 10 legs)**. Read flat,
that says *"the pin only half-worked"*. **That is a false finding.** Split by the leg's
prior grade it says the opposite:

| | |
|---|---|
| residual legs that were previously **CONTAMINATED** | **10 of 10** |
| residual legs that were previously **CLEAN** | **0** ← the load-bearing number |
| previously-contaminated legs now showing `base == to400` | **8** (the pin's intended effect) |

**Zero previously-clean legs regressed**, which is the check that matters: a leg with no
trade past 48/200 bars certainly has none past 400, so any clean leg appearing here would
be a real defect. The 10 residual legs are the long-hold ones — `trend_donchian*`,
`mgc_trend_1h`, `xauusd_trend_1h`, `sol_pullback_2h`, `xrp_pullback_2h` — where an
infinite timeout legitimately differs from a 400-bar one.

## 4. What this means for the plan, and what it does NOT license

**The pin is behaving as designed.** Closed folds reproduce exactly where checkable; no
clean leg regressed; 8 contaminated legs now measure against production's real (absent)
bar-count exit.

⚠️ **This does NOT license proceeding as though the control passed.** § 1 makes a failed
CLEAN-leg control a **stop-and-ask** condition, and the honest position is that the
control *as specified* failed and I have shown *why the specification cannot hold* — not
that the control passed. The operator's call is whether to accept the fold-level evidence
in its place. **No verdict change is applied here, and none should be until that is
settled.**

**The control needs re-specifying**, and the fix is cheap: pin the window (`--since`/
`--until`, or record the resolved candle span per row) so a re-run is a pure function of
code + data. Until then, *"identical"* is not a property this sweep can have across days,
and any future plan asserting it will produce the same false alarm.

## 5. Consequence for the 19 stale-ref matrix cells

The `bracket_geometry` refs citing the superseded 2026-08-20 run — including the two
asserting `passed_unshipped` on a winner the newer rows do not reproduce
(`eth_pullback_2h`, `eth_pullback_prop_2h`) — now have a **third** generation of
measurement behind them (08-20 → 08-24 → 08-29). Re-reading them is real work with a real
payoff and is **not** attempted here: it is gated on the § 4 decision, since the same
window question governs how much of any 08-24 → 08-29 movement is signal.

Evidence for the open `BL-20260820-BRACKET-GEOMETRY-COLUMN-HAS-NO-AGREEMENT-CHECK`.

---

*Populations: BEFORE `a986ac3` (8,211 rows / 8,211 unique `measurement_key`), AFTER
`c2641827` (8,289). Clean/contaminated grades from
`scripts/research/timeout_binding_audit.py` over the BEFORE corpus: 23 clean / 18
contaminated of 41 legs. Sweep run 33277532648, dispatched on `claude/e35-resweep-n2`,
completed 2026-08-29T22:44Z, conclusion success.*
