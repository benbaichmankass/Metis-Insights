# MI-278 U4 — Tier-3 proposals from the `ict_scalp` exit sweep

> **Doc status:** `live` · category `plan` · 2026-09-12 · MI-278 U4 ·
> object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`

**This document exists to be accepted or rejected, not read.** The queue's
instruction was explicit: *"A memo is not the deliverable; a proposal the
operator can accept or reject is."* Evidence lives in
[`m20-u3-scalp-target-sweepable-2026-09-12.md`](m20-u3-scalp-target-sweepable-2026-09-12.md);
this is the decision surface.

**Nothing here is enacted.** `config/strategies.yaml` is untouched by this PR
and by every PR in MI-278. Each proposal is Tier-3 and the operator decides.

---

## The short version

**One lever survived the full M20 gate out of everything MI-278 swept, and it
points the opposite way to the question that started the queue.**

| lever | population swept | cleared IS/OOS | cleared walk-forward |
|---|--:|--:|--:|
| `bracket_geometry` @ 24-bar timeout | 49 cells / 7 legs | 0 | — |
| `bracket_geometry` @ **live parity** | 49 cells / 7 legs | 3 (2 legs) | **see below** |
| `breakeven_ratchet` @ live parity | 7 cells / 7 legs | **0** | — |
| `stop_geometry` (the buffer) | **built, NOT RUN** | — | — |

MI-277 measured winner hold collapsing 5.17h → 1.90h and MI-278 is titled
*hold winners longer*. **No lever tested makes winners run longer and survives.**
The one that survives takes profit **sooner**, on one leg.

---

## PROPOSAL 1 — `ict_scalp_xrp_15m`: `tp_at_r` 1.5 → 1.25

**Tier-3. Recommendation: ACCEPT only if the operator is comfortable with a
single-leg change confirmed by four walk-forward folds, three of which pass.
I would not object to a REJECT, and §"Why I am not pushing harder" says why.**

### The exact change

```yaml
# config/strategies.yaml
ict_scalp_xrp_15m:
  tp_at_r: 1.25        # was 1.5
```

One field, one leg. No other leg changes. No code changes.

### The evidence

Run [`34684742080`](https://github.com/benbaichmankass/Metis-Insights/actions/runs/34684742080),
`m27_data/XRPUSDT_15m.csv`, split `2025-07-01`, config-exact base
(`--sim-breakeven`), **`timeout_bars=100000` (live parity)**.

```
tp1.25R  IS ΔR=+5.82 ΔDD=-2.53 (n198) | OOS ΔR=+5.93 ΔDD=-3.72 (n117)
         -> CANDIDATE  [timeout IS 0% OOS 0%]
yearly walk-forward: 3/4 usable folds (need 3) -> PASS
         [2021:skip 2022:skip 2023:PASS 2024:- 2025:PASS 2026:PASS]
```

It is **the only cell in the entire workstream** to improve both net_R and
maxDD in both windows, and it then cleared the yearly walk-forward.
`n_OOS = 117` clears the skill's `MIN_OOS_TRADES = 25` floor with room —
⚠️ **though checked by hand, not by the sweep: the M27 sweep does NOT implement
that floor** (only `m20_fleet_exit_sweep.py` does). Measured over the 77
cell-OOS observations in this workstream the minimum is **95**, so the missing
floor **binds on nothing here** — but it is a latent gap that would pass a thin
leg silently, and it is recorded rather than left implied. The
<!-- population-ok: not a claim — a verbatim quote of the sweep's own per-cell
     output stamp, whose two percentages are the timeout share of that cell's
     own IS and OOS trade counts (n=198 and n=117, in the block above). -->
`[timeout IS 0% OOS 0%]` stamp is the fidelity readout confirming it ran at
parity rather than under the harness's 24-bar force-close.

### Why I am not pushing harder for it

1. **It passes by exactly the minimum** — 3 of 4 usable folds against a `need`
   of 3, and **2021/2022 are skipped** because those years are empty on
   `m27_data`. One fold moving flips this to a fail.
2. **PATH A ONLY.** The gate has two qualifying paths and the sweep graded one;
   Path B's evidence was not recorded when this ran. A Path-A survivor is not a
   full-gate survivor.
3. **The same narrowing fails on other legs** — `btc_5m` at parity is +31.42 R
   IS at 0.75R and **−10.71 OOS**. There is no family-level claim here.
4. **`tp_at_r` multiplies a STRUCTURAL risk** (the stop is
   `sweep_extreme ± 0.20×ATR`), so this is not a fixed-distance move; it
   rescales against a per-trade denominator.
5. **`xrp_15m` is a modest book**: base OOS total_R 6.48 over 116 trades. A
   +5.93 R OOS improvement on that base is large in relative terms and small in
   absolute ones.

### I tried to cross-check it against the LIVE journal and could not — for a reason this workstream itself established

Worth stating, because *"the backtest says so"* invites the question and the
answer is not *"I didn't look"*.

`ict_scalp_xrp_15m` has **10 closed rows with a pnl** in the 1000-row journal
tail (ids 4720–5719, all `bybit_1`). Two things make them unusable as a check:

1. **n = 10.** The backtest verdict rests on n=198 IS / 117 OOS. Ten rows
   cannot confirm or refute it, and the naive read
   (3/10 reached ≥1.25R vs 2/10 reached ≥1.5R) is directionally consistent
   with the proposal and means nothing at that size.
2. **THREE OF THE TEN ARE CONTAMINATED BY U2b's OWN FINDING.** Rows 4941,
   5112 and 5141 sit at `risk/entry == 0.00150000` **exactly** — the
   break-even ratchet's `be_offset_bps: 15` signature. Their `stop_loss` is a
   *ratcheted* stop, not the entry risk, so an achieved-R computed from it is
   not achieved-R: row 5112 reads **+11.79 R** and 5141 **+4.82 R** against a
   1.5R target, which is the arithmetic of a shrunken denominator rather than a
   trade that ran 8× its target.

So the live journal **cannot** validate a target verdict without U2b's
entry-risk recovery applied first, and even then not at n=10. **A future
cross-check is a real unit** — it needs U2b's recovery over a wider window —
and it is named here rather than left as an implied "someone should".

### If ACCEPTED

Tier-3 path: a PR touching only that one field, `landing: hold`, merged by a
human, then `/api/bot/config` read back to confirm `ict-git-sync` carried it to
the running trader — a merge is not a deploy. Then an OPEN-ITEMS row, because
**deployed is not observed**: the row should require a closed `xrp_15m` trade
that reached the new 1.25R target, read against broker truth.

### If REJECTED

Nothing is owed. The evidence stays in U3's memo and the coverage cell records
`passed_unshipped`, which is the honest state: measured, cleared, not shipped.

---

## PROPOSAL 2 — `ict_scalp_avax_5m`: pending its walk-forward

`tp3R` and `tp4R` cleared IS/OOS at parity and are **still walking forward** at
the time of writing. They are **widenings** — the opposite direction to
Proposal 1 — and their IS/OOS pass rests substantially on a max-drawdown
improvement (~83 R → ~25 R) that is **n=1 in EPISODES**: four wide cells
improving by ~60 R each are four views of the same drawdown episode, not four
observations. The walk-forward is precisely the test for that.

**No proposal is made here until that verdict lands.** If it survives, the
family has two confirmed levers pointing in opposite directions on different
legs — which is a finding about the family, not a fix for it.

---

## NOT PROPOSED, and why

- **The break-even ratchet.** 0 of 7 legs at parity. Disarming it costs R
  in-sample (Σ −40.79), gains out-of-sample (Σ +31.57), and **raises in-sample
  max-drawdown on 6 of 7 legs** — it is buying the protection it is meant to
  buy. §4c's hypothesis that it was "probably the lever" is refuted.
- **Widening the target family-wide.** 5 of 7 legs at parity are negative in
  BOTH windows, several decisively.
- **The stop buffer.** Built this unit (`--atr-sl-buffer-mult` +
  `stop_geometry` cells) and **not run**. No claim.
- **Anything from the 24-bar screening arm.** That arm's negatives are not
  full-strength evidence and its positives are artifacts; see U3 §4e.

---

## The one thing I would ask for above any of these

**A decision on whether `ict_scalp` backtest numbers may be quoted without a
parity stamp.** U3 measured the harness's 24-bar force-close at up to **99% of
a leg-window's entire reported profit** (`sol_15m` OOS: +20.46 R → +0.20 R at
parity), and it changes verdicts — it flipped `xrp_15m` `tp1.25R` from
non-candidate to the survivor above. **Every prior M27 `ict_scalp` verdict in
the coverage matrix was measured without it**, and 0 of 24 `ict_scalp` tested
cells declare the axis. That is worth more than any single leg's target.
