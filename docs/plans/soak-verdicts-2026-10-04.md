# Soak verdicts — 2026-10-04 (every ready / dead / overdue soak gets an outcome)

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · the register has no rule that can verify a decision pack; every figure below names its source

**Lane:** SOAK-WATCH, answering the manager's 17:52Z review.

**Sources:**
- Soak states: the **live** brief SOAKS section (`GET /api/bot/work/brief`, generated 2026-10-04T17:53:29Z by `soak_state.soak_states()` on the VM): 48 soaks, of which 23 ready, 15 accruing, 7 dead, 2 overdue and 1 unknown.
- Verdicts: `scripts/ops/mandate_resolver.py` run this session against main. Promotion is S1→S2 under `MD-PROMOTE-S1-S2`. Demotion is S1→OFF under `MD-DEMOTE-S1-OFF`, and S2→S1 under `MD-DEMOTE-S2-S1`.
- Stage-0 figures: `comms/strategy_evidence/<leg>.json`.

## Bottom line

- **No promotion fires.** Every ready Stage-1 leg is either already on S2, or refused on its Stage-0 record. No promotion PR is owed.
- **No demotion or cut can land under an existing mandate.**
  - `MD-DEMOTE-S1-OFF` fires only on an R3 `divergent` cost verdict. The newest R3 record (2026-09-25) grades every leg here `insufficient_n` or `consistent`.
  - `MD-DERISK-ONLY-ROSTER-CUT` covers **real-money** legs only.
  - So a leg that is mechanically proven on Stage 1 but fails Stage 0 offline has **no mandate that removes it**. That decision arrives in the same shape every time, so I propose it **becomes a mandate** (§ "Proposed mandate").
- **The soak contract and R3 measure different things.** `soak_states()` counts closed trades. R3 counts *package-referenced* fills per side. Legs with 17 to 63 closes still grade `insufficient_n` in R3. Until the contract counts what R3 counts, "ready" means "enough closes", not "R3 can decide". Filed as a finding (§ Filed).

## 1. READY (23) — one outcome each

| soak | closes / packages | promotion (S1→S2) | demotion / cut | outcome |
|---|---|---|---|---|
| bybit_1 `ada_pullback_2h` | 39/10 | REFUSE R-NO-OP (already on bybit_2) | S2→S1: NEEDS-DATA, no mirror-window record | **Soak complete**: mechanics proven. The Stage-1 copy duplicates the real-money one. Gate-2 demotion waits on the mirror-window record. |
| bybit_1 `trend_donchian_eth_4h` | 17/10 | REFUSE R-NO-OP | same | **Soak complete.** Remove the bybit_1 copy (S2 in `soak-decisions-2026-10-04.md`; Tier-3). |
| bybit_1 `xrp_pullback_2h` | 25/10 | REFUSE R-NO-OP | same | **Soak complete**, as for ada. |
| bybit_1 `eth_pullback_2h` | 38/10 | **REFUSE R-B1-C4**: Stage-0 fails net of cost (−0.028R, n=62, 1/4 folds) | S1→OFF: NEEDS-DATA (R3 `insufficient_n`) | **REFUSE.** No mandate cuts it; see the proposed mandate. |
| bybit_1 `ict_scalp_eth_15m` | 38/10 | **REFUSE R-B1-C4** | S1→OFF NEEDS-DATA | **REFUSE**, same. |
| bybit_1 `ict_scalp_sol_15m` | 49/10 | **REFUSE R-B1-C4** | S1→OFF NEEDS-DATA | **REFUSE**, same. |
| bybit_1 `ict_scalp_xrp_15m` | 37/10 | **REFUSE R-B1-C4** | S1→OFF NEEDS-DATA | **REFUSE**, same. |
| bybit_1 `ict_scalp_xrp_5m` | 42/10 | **REFUSE R-B1-C4** (−0.16R, n=169, 0/4 folds) | S1→OFF REFUSE: R3 cost `consistent` | **REFUSE promotion.** Cost fidelity is proven, so it stays as a cost instrument only if the operator wants that. |
| bybit_1 `squeeze_breakout_4h` | 63/10 | **REFUSE R-B1-C4** (−0.040R, n=10) | S1→OFF NEEDS-DATA | **REFUSE**, same. |
| bybit_1 `trend_donchian` | 38/10 | **REFUSE R-FOLDS** (2/4 folds) | S1→OFF NEEDS-DATA | **REFUSE.** S3: remove or reprioritise. |
| bybit_1 `trend_donchian_eth` | 20/10 | **REFUSE R-FOLDS** | S1→OFF NEEDS-DATA | **REFUSE.** |
| bybit_1 `trend_donchian_sol` | 10/10 | **REFUSE R-FOLDS** | S1→OFF NEEDS-DATA | **REFUSE.** |
| bybit_1 `sol_pullback_2h` | 27/10 | **NEEDS-DATA R-N**: Stage-0 n_trades_oos=18 < 30 | — | **NEEDS_DATA**, filed `PI-20261004-JC8KDKLF-0004` (re-run the harness over a longer window). |
| alpaca_paper `gld_pullback_1h` | 10/10 | **REFUSE R-AFFORD**: 0 of 54 setups size above zero on alpaca_live (equity $193.56) | — | **REFUSE.** It cannot trade the live book at its size, whatever its edge. |
| alpaca_paper `qqq_pullback_1h` | 15/10 | **REFUSE R-FOLDS** (2/4) | — | **REFUSE.** |
| alpaca_paper `uso_trend_1h` | 11/10 | **REFUSE R-FOLDS** (2/4) | — | **REFUSE.** |
| shadow `slv_trend_1h` | 22 pkgs | NEEDS-DATA R-EXECUTION-SHADOW. Stage-0 **passes**: +0.78R, n=68, 4/4 folds | — | **NEEDS_DATA**, filed `PI-20261004-JC8KDKLF-0010`: needs a real Stage-1 soak as `execution: live` on alpaca_paper. That is a Tier-3 flip, backed by a passing Stage-0 record. **Only this one is a promotion candidate.** |
| shadow `mgc_trend_1h` | 263 pkgs | NEEDS-DATA (shadow), but Stage-0 is 2/4 folds, so R-FOLDS would refuse it | — | **REFUSE** on folds. Auto-filed row `-0012` killed with that reason. |
| shadow `avax_pullback_2h` | 108 pkgs | Stage-0 **fails** (−0.025R, 2/4) | — | **KILL candidate.** Shadow can't establish edge and Stage 0 refuses. Auto-filed `-0005` killed. |
| shadow `fade_breakout_4h` | 43 pkgs | Stage-0 **fails** (−0.596R, 1/4) | — | **KILL candidate.** `-0006` killed. |
| shadow `htf_pullback_trend_2h` | 10 pkgs | Stage-0 **fails** (−0.057R, 1/4) | — | **KILL candidate.** `-0007` killed. |
| shadow `ict_scalp_avax_5m` | 27 pkgs | Stage-0 **fails** (−0.210R, 0/4) | — | **KILL candidate.** `-0008` killed. |
| shadow `ict_scalp_sol_5m` | 13 pkgs | Stage-0 **fails** (−0.120R, 0/4) | — | **KILL candidate.** `-0009` killed. |

**Resolver bug found on the way.** `mandate_resolver.py` checks `execution: shadow` before the decisive Stage-0 refusal. So for the five failing shadow legs it returned NEEDS-DATA "flip to live and accrue fills", and its `--file-needs-data` filed those Tier-3 flips as data tasks. I killed those rows with the reason, and filed the clause-order fix as `PI-20261004-JC8KDKLF-0013`.

## 2. DEAD (7)

| soak | cause (one line) | disposition |
|---|---|---|
| `trend_donchian_1h`, `turtle_soup`, `vwap` | Not running: `enabled: false`, or on no roster. | **Closed as not-a-soak.** `soak_states()` now omits `design: not_soaking`, while the contract keeps the decision on record. Retirement is a Tier-3 config proposal (S7). |
| `fvg_range_15m` (shadow) | `config/regime_policy.yaml` sets it `{ long: off, short: off }` everywhere ("−17 loser, keep off everywhere"), so the regime router drops every intent and it **can never emit a package**. Config, not wiring, and not patience. | **Not a soak.** Propose retiring it from bybit_1 and shadow (Tier-3). Stage-0 has no passing record either. |
| `spy_trend_long_1d` | **Not dead.** It holds an open long. The 09-26 R5 row shows `has_open_position: true` with zero new intents, and a daily trend leg makes no close while it holds. | Bug in my dead rule, **fixed here**: an open position counts as activity, so this reads `accruing`. Promotion is NEEDS-DATA R-N (Stage-0 n=11), filed `PI-20261004-JC8KDKLF-0011`. |
| `tqqq_trend_long_1d` | 308 actionable intents, **0 orders**, and no same-symbol contender (R5 09-26). An unexplained dispatch drop: a wiring bug. Stage-0 is ~0R (−0.009R, n=12). | **Wiring bug, owned by ORDER-AUDIT-2** (`PI-20260926-X3QEGPJL-0006`). Keep it on the roster until the trace names the drop; removing it would hide the bug. |
| `eth_pullback_prop_2h` (shadow) | 0 packages in 42 days against ~8 expected. Its config describes it as the Breakout prop variant, so its packages may never be written under this name on bybit_1. **Unverified.** Stage-0 fails (−0.050R, 2/4). | **KILL candidate on Stage 0 alone.** The zero-package cause is moot if it is retired. |

## 3. OVERDUE (2) — a verdict on the data in hand, no extension

| soak | data in hand | verdict |
|---|---|---|
| `spy_pullback_1h` (alpaca_paper) | 8/10 closes, end 2026-08-09. Mechanics flowing but thin: R5 09-26 shows 88 intents in 8 episodes, 1 order received, a position open, and 8 closes over the soak. | **Close it, no extension.** Promotion is REFUSED R-B1-C4 (Stage-0 fails net of cost), so the last 2 closes cannot change the outcome. Same class as § 1's REFUSE rows. |
| `tlt_pullback_1h` (alpaca_paper) | 4/10 closes, end 2026-07-30. Starved in the R5 window against its 1d twin. Stage-0 **negative**: −0.072R, n=94. Already pulled off alpaca_live (A6). | **Close it, no extension.** Promotion is REFUSED R-B1-C4. Recommend cutting it from alpaca_paper (Tier-3); "tune before demote" applies if the operator wants a retune first. |

## 4. ACCRUING to 2027–2029: the design-wrong cases (for the operator)

| soak | progress | end date | recommended redesign |
|---|---|---|---|
| `ief_pullback_1d` | 3/10 | 2029-10-19 | **Stop the per-leg soak.** Mechanics are already proven on the same venue by `qqq_pullback_1h` and `uso_trend_1h` (orders flow). Measure cost at the venue level (pooled alpaca fills), not per leg. |
| `iaum_pullback_1d` | 2/10 | 2027-12-11 | Stop the per-leg soak, with one exception: it is also **real-money** on alpaca_live. Check that leg's order flow first (`PI-20261004-JC8KDKLF-0001`). |
| `scha_trend_long_1d` | 1/10 | 2027-12-11 | Stop the per-leg soak; use the venue pool. |
| `gld_pullback_1d` | 2/10 | 2027-11-15 | Stop; venue pool. Contention with its 1h twin is still unresolved (`X3QEGPJL-0003`). |
| `iwm_trend_long_1d` | 5/10 | 2027-09-20 | Stop; venue pool. |
| `tlt_pullback_1d` | 4/10 | 2027-09-20 | Stop; venue pool. |
| `qqq_trend_long_1d` | 2/10 | 2027-09-11 | Stop; venue pool. |
| `qld_trend_long_1d` | 1/10 | 2027-08-11 | Stop; venue pool. |
| `gdx_pullback_1d` | 2/10 | 2027-06-27 | Stop; venue pool. |
| `slv_pullback_1d` | 4/10 | 2027-05-25 | Stop. It is also on alpaca_live, so it is measured there. |
| `trend_donchian_xrp_4h` (bybit_1) | 8/10 | 2027-02-02 | **Keep.** It is 2 closes from n, and the date is a rate artefact. Re-read it at the next weekly grade. |
| `trend_donchian_sol_4h` / `_avax_4h` / `_ada_4h` (shadow) | 0/10 | 2026-12 to 2027-01 | **Verify by replay/parity**: run the harness over the live candles and compare it to logged packages, instead of waiting months for packages. |

The common fix, as one decision: **Stage 1 measures mechanics per leg, which takes a handful of intents, and cost per venue (pooled), never cost per leg.** That turns every row above into days, not years. Detail: `soak-decisions-2026-10-04.md` § "Soak contracts, backfilled".

## Proposed mandate (operator grants; a session only proposes)

`MD-S1-CUT-ON-STAGE0-FAIL` (derisk_only):
- **Fires when:** a Stage-1 leg's committed Stage-0 record FAILS RULE-D1 net of the full cost stack, or is positive in a fold minority, **and** its Stage-1 soak is complete (contract n reached, or overdue).
- **Effect:** remove the leg from its Stage-1 roster.
- **Why:** this would resolve 13 of the rows above without a popup. The question has arrived in this exact shape for every ready leg today.
- **Never:** never an addition, and never for a leg that is also on a real-money roster (that path is `MD-DEMOTE-S2-S1`).

## Filed

- **NEEDS_DATA rows,** auto-filed by `mandate_resolver --file-needs-data`:
  - `PI-20261004-JC8KDKLF-0004`: `sol_pullback_2h` Stage-0 n.
  - `-0010`: `slv_trend_1h` real Stage-1 soak (Tier-3 flip).
  - `-0011`: `spy_trend_long_1d` Stage-0 n.
- **Killed with reason:** `-0005` to `-0009` and `-0012` (flip-to-live tasks for legs whose Stage-0 refuses promotion).
- `-0013`: the resolver clause-order bug.
- The contract-vs-R3 metric mismatch: `PI-20261004-JC8KDKLF-0014`.
