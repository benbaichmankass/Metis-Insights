# B6, split — the eight `passed_unshipped` lever cells

**Status: Tier-1 PREP. Nothing is proposed for merge into `config/strategies.yaml` here,
and no sweep has been dispatched.** Item **N6** of
`docs/claude/WORKPLAN-NIGHT-2026-08-29.md`.

---

## 1. The split is confirmed against the matrix, not taken on trust

`WORKPLAN-2026-08-29.md` row **B6** describes "the 8 remaining `passed_unshipped` cells"
and splits them 6 + 2. Read directly from `docs/research/exit-refinement-coverage.json`
(52 rows) there are **12** `passed_unshipped` cells — but four of them are in the
`bracket_geometry` column, which is **B4's** territory and not B6's
(`eth_pullback_2h`, `eth_pullback_prop_2h`, `gld_pullback_1h`, `spy_pullback_1h`).

Excluding that column leaves **exactly the 8 B6 cells**, and they split 6 + 2 exactly as
the row says. The arithmetic reconciles, so the split can be used as written.

## 2. ⚠️ The B9 precondition, applied — the result is better than feared

B9 added a precondition B6 did not originally have: **a leg whose recorded verdict was
measured against a harness that force-closes on bar count may be describing a strategy
production does not run.** Applying `scripts/research/timeout_binding_audit.py` per
candidate leg:

| # | cell | leg | timeout-axis status |
|---|---|---|---|
| 1 | `vol_trail` | `qqq_trend_long_1d` | ✅ **clean** (0 binding pairs) |
| 2 | `stale_stop` | `mhg_pullback_1d` | ✅ **clean** |
| 3 | `exit_head_ml` | `trend_donchian_eth_prop` | ✅ **clean** |
| 4 | `exit_ladder` | `ict_scalp_sol_15m` | ⚠️ **ABSENT from the e35 corpus — UNGRADED, which is not the same as clean** |
| 5 | `stale_stop` | shadow fleet (roll-up row) | ⚠️ **ABSENT — ungraded** |
| 6 | `trail_decay` | shadow fleet (roll-up row) | ⚠️ **ABSENT — ungraded** |
| 7 | `trail_geometry` (trail3) | `tlt_pullback_1h` | ✅ **clean** |
| 8 | `trail_geometry` (trail3) | `uso_trend_1h` | ✅ **clean** |

Five of the six named legs are clean; `trend_donchian_eth_prop`'s status was **not** stated
in the plan and is measured here for the first time (clean).

**`ict_scalp_sol_15m` and the two shadow-fleet roll-up rows have no corpus rows at all**
(membership checked directly against `e35-bracket-corpus.jsonl`, independently of the audit
script). That is **ungraded on the timeout axis, and must not be recorded or read as
clean** — the distinction this whole arc exists to preserve. Their exit-ladder /
stale_stop / trail_decay evidence comes from other sweeps and may be perfectly sound; what
cannot be said is that B9's question has been asked of them.

## 3. The two trail3 cells — why they cannot be proposed yet, and why a re-sweep is valid

`tlt_pullback_1h` and `uso_trend_1h` both had their `atr_stop_mult` changed by **#10419**
(2.5 → 2.0 on each; cells `sm2` and `tp4_sm2` respectively, verified by resolving each
annotation line to its owning strategy block). Their trail3 pass was validated at the
**old** stop, so shipping it now would apply a validated-at-2.5 trail on top of a live 2.0
stop and the walk-forward would not transfer.

**What N6 adds:** both legs are **CLEAN on the timeout axis**, so a re-sweep at the new
stop is a valid measurement rather than one confounded by the harness default. That was
not known when B6 was written, and it is the precondition that makes the re-sweep worth
running at all.

## 4. ⚠️ The re-sweep is deliberately NOT dispatched tonight

Two reasons, both stated so the omission is a decision rather than an oversight:

1. **The plan scopes N6 to "Tier 1 prep only".** Dispatching a sweep is running work.
2. **Runner contention is measured, not assumed.** The N2 41-leg re-sweep
   ([run 33274767713](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33274767713))
   sat **queued for ~35 minutes** before its first job started tonight. A second sweep
   dispatched into that would queue behind it and return no sooner.

**Corpus safety was checked and is NOT the blocker:** `e35-bracket-sweep.yml` is the only
workflow that writes `docs/research/e35-bracket-corpus.jsonl` (grep over all workflows and
scripts), so a trail re-sweep could not corrupt the corpus N2 is currently rebuilding. The
hold is about runner time and plan scope, not about a data race — worth stating precisely,
because "it might race the corpus" would have been a plausible-sounding wrong reason.

## 5. What the next session should do, in order

1. **Wait for N2 to land**, then run N7's clean-leg control (23 legs must reproduce
   identically).
2. **Re-sweep trail3 on `tlt_pullback_1h` and `uso_trend_1h` at the new
   `atr_stop_mult: 2.0`.** Only then can cells 7–8 be proposed at all.
3. **Grade cells 4–6 on the timeout axis before proposing them**, or state explicitly in
   the proposal that they are ungraded on it. Do not write "clean".
4. **Cells 1–3 are ready to assemble** into a Tier-3 proposal packet: clean on the timeout
   axis, on legs #10419 did not touch, no B4 interaction.

**Everything in § 5 that changes `config/strategies.yaml` is Tier-3 and operator-gated.**

---

*Population note: matrix = `docs/research/exit-refinement-coverage.json`, 52 rows.
Timeout grades = `scripts/research/timeout_binding_audit.py` over
`docs/research/e35-bracket-corpus.jsonl` at commit `6497148` (8,211 rows, 41 legs;
23 clean / 18 contaminated fleet-wide). Corpus membership checked by leg-set
intersection, independently of the audit script.*
