# Sprint Log: S-E35-SHIP-REVERSED-GEOMETRY-2026-08-30

## Date Range
- Start: 2026-08-30T05:40Z
- End: 2026-08-30T07:55Z

## Objective
- Ship the e35 bracket geometry the operator approved: the gate-passing cells found by the
  2026-08-29 matrix re-check, on the 10 legs whose verdict reversed.
- **Outcome: 9 shipped, 1 HELD.** `trend_donchian_eth_prop` was stopped by a test that was right —
  shipping it would have invalidated the prop EV/survival gate that graduated it to live, on the one
  account that can be permanently disabled.

## Tier
- **Tier 3.** `config/strategies.yaml`, three legs on **real money** (`bybit_2`).
- **Operator-approved in conversation, 2026-08-30** — "approve the 15 cells, ship them",
  given after reading my written recommendation to drop `avax_pullback_2h`.

## Starting Context
- #10444 merged (`e8bbc91c`): matrix re-checked, 10 legs flipped to `passed_unshipped`,
  15 shippable cells identified, nothing applied.
- The prior unit deliberately stopped at the record; this one is the config flip.

## Repo State Checked
- `origin/main` at `e8bbc91c`; branch `claude/e35-ship-reversed-geometry`.
- Board tail proven by a SHORT page (`perPage=10` page 165 → 5 items); slot free at START.

## Files and Systems Inspected
- `config/strategies.yaml` (the 10 leg blocks) · `config/accounts.yaml` (routing, read-only)
- `docs/research/e35-bracket-corpus.jsonl` (the winning rows)
- Every test that reads the live YAML and names one of the 10 legs (9 files)

## Work Completed
- **10 field edits across 9 legs** (verified by counting the inline annotations: 10), each carrying
  its cell id, gate verdict, `wf_wins_effective` and `d_net_r` — the B4 convention.
- **ONE LEG OF THE APPROVED TEN WAS HELD BACK** — `trend_donchian_eth_prop`; see below.
- **Matrix flipped to `shipped` on the 9**, which ARMS `matrix-bracket-values` on them: the guard
  now checks **17** shipped cells against config and passes. The 10th is recorded
  `blocked:prop_ev_gate_would_be_invalidated` with its three reasons and its unblock condition.
- **`test_m15_eth_pullback_wiring` pin updated per leg**, not loosened.

## Validation Performed
- **The selection rule was stated BEFORE the values were read** — highest
  `wf_wins_effective`, tie-break `d_net_r`, over shippable cells only. Applied by script,
  not by hand.
- **15 candidates → 10 legs → 11 edits, of which 9 legs / 10 edits SHIPPED.** Both apparent ties (`ada_pullback_2h` `tp4` vs
  `tp4_sm1.5`; `trend_donchian_eth_prop` `sm1.5` vs `tp6_sm1.5`) are **illusory**: identical
  `d_net_r` because the leg already declares the other axis. Self-verifying.
- **Post-edit the YAML was re-parsed and every value compared against the plan** — 11/11
  match — **and the set of legs whose parsed config changed was compared to the intended
  set: exactly equal, zero unexpected changes.** That check is what catches a regex that
  matched in the wrong block.
- **The one test failure was predicted before it ran** (`eth_pullback_2h atr_stop_mult
  drifted from the BTC leg`), and nothing else failed: 130 passed, 1 failed.
- `run_guards.py --all` → **PASS 59 · FAIL 0**.
- **Gaps:** the 3 `ccxt` collection errors are a sandbox dependency gap, confirmed
  reproducing on a stashed clean tree — not this change. Nothing on the live fleet has
  been observed; the config has not been seen on the running trader.

## Contradictions or Drift Found
- **A test coupling that would have broken silently.** `test_m15_eth_pullback_wiring`
  asserts `eth_pullback_2h`'s params EQUAL `htf_pullback_trend_2h`'s. Shipping `sm3`
  de-couples them. The file already carried this exact precedent for `trail_mult`, so
  `atr_stop_mult` was pinned per leg with its own evidence — `eth_pullback_2h` stays at
  2.5 because its own e35 verdict is `blocked:no_live_bar_count_exit` (zero shippable
  cells). **Never loosened to make the two agree.**
- **My board START said "12 field edits"; it is 11** — `ada_pullback_2h.atr_stop_mult` was
  already 1.5, so the `tp4` cell needed no stop edit. Corrected here and in the PR.

## Risks and Follow-Ups
- ⚠️ **ONE OF THE TEN APPROVED LEGS WAS NOT SHIPPED — `trend_donchian_eth_prop`.** Its `sm1.5`
  cell is real (path_b_wf_pass, wf 5/6, +13.4198) but three independent reasons block it: it is a
  config-only EXIT variant whose design premise is entry-identity with `trend_donchian_eth`
  (`atr_stop_mult` is an ENTRY param there, deliberately); it was graduated to `execution: live` on a
  prop EV/survival gate (relay #8975, `run_ev_montecarlo` under `breakout.yaml`, +$883 @ P=0.8477 over
  1,110 trades) measured on THAT construction, which a stop change invalidates; and `breakout_1` can
  be PERMANENTLY DISABLED by a drawdown breach, so the mandatory per-account compatibility rule wants
  a prop-ruleset evaluation the e35 sweep does not provide. Its base leg has no shippable cell, so
  shipping the variant alone would de-couple the pair. **Unblocks when `run_ev_montecarlo` is re-run
  on the sm1.5 geometry under `breakout.yaml` and the operator approves on that evidence.** The
  operator approved the set without this information; it is surfaced rather than absorbed.
- ⚠️ **Two changes alter strategy SHAPE, not stop width, and one is real money.**
  `ada_pullback_2h` and `trend_donchian_xrp_4h` move `tp_r` **50.0 → 4.0 / 3.0** —
  introducing a take-profit where the leg had none. Capping a TREND leg at 3R truncates
  the rare large winners trend-following depends on. Passed its walk-forward; flagged
  because it is the highest-variance change in the set.
- ⚠️ **`avax_pullback_2h` (+1.0084) shipped against my own recommendation to drop it.**
  Operator reaffirmed after reading it. `execution: shadow`, so nothing live moves.
- ⚠️ **Every cell rests on a run whose CLEAN-leg control FAILED**, with a fold-level
  substitute accepted on 2026-08-29 — a weaker base than B4 had.
- `OI-20260830-E35-GEOMETRY-SHIPPED-TO-10-LEGS-NOT-YET-LIVE-VERIFIED` (`loud`,
  monitoring, 2-day cadence).

## Deferred Items
- **12 matrix legs still carry no corpus rows** — ungraded, not clean. Untouched.
- The `matrix-bracket-values` backtick-only regex (`BL-20260829-…-BACKTICKED-CELL-SPELLING`).
- Re-specifying the sweep window so a re-run is reproducible across days.

## Next Recommended Sprint
- **Verify on the fleet**, per the OPEN-ITEMS row — read `/api/bot/config`, then the first
  close on a real-money leg. Then Lane P (P1/P2) per N-D4; Lane A Monday.

## Wrap-Up Check
- [x] Code inspected directly, not inferred from summaries.
- [x] Documentation reviewed and updated.
- [x] No pipeline stage touched; `docs/TRADE-PIPELINE.md` unchanged.
- [x] Roadmap M20 updated.
- [x] Contradictions recorded.
- [x] Remaining unknowns stated — chiefly that this is deployed, not proven.
