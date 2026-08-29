# `timeout_bars`: the harness force-closes, live never does — how much does it matter?

**Date:** 2026-08-29 · **Lane B / B9** of [`../claude/WORKPLAN-2026-08-29.md`](../claude/WORKPLAN-2026-08-29.md)
· Backlog row `BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES`

---

## The question this answers

The 08-29 workplan opens B9 with a defect and an explicit gap:

> `scripts/backtest_{trend,pullback}.py` force-close every trade at
> `entry_i + timeout_bars` (default 200); no live code path does. **Blast radius is
> NOT established**: how many non-e35 trend/pullback verdicts were measured under
> the default 200 needs a measurement, not an assumption.

**MEASURED** — this document supplies that measurement. Source:
[`e35-bracket-corpus.jsonl`](e35-bracket-corpus.jsonl) (8,211 rows, committed, so
unlike the 3,781 surface cells of `BL-20260823-E35-SWEEP-EVIDENCE-HAS-NO-DURABLE-PATH`
it is durably retrievable). Reproduce with
`python3 scripts/research/timeout_binding_audit.py`.

---

## 1. The code half, verified this session

| claim | how checked | result |
|---|---|---|
| The trend + pullback harnesses force-close on bar count | read `backtest_trend.py:982`, `backtest_pullback.py:961` | `--timeout-bars`, **default 200** on both; `backtest_squeeze.py:545` is **48** |
| The force-close is unconditional | read the exit loop, `backtest_pullback.py:496`/`:660-671` | `exit_idx = min(i + timeout_bars, n-1)`; `exit_reason` **defaults to** `"timeout"` |
| No live unit implements one | `grep -rn timeout_bars src/` | only `fvg_range_15m.py` and `fade_breakout_4h.py`, each from its **own** `_DEFAULTS`. **No generic reader.** |
| Live's effective timeout | follows from the above | **infinite** |

⚠️ **Two config keys declare a value nothing reads.** `mgc_pullback_1d` and
`mhg_pullback_1d` carry `timeout_bars: 200` in `config/strategies.yaml`
(lines 1540, 1581). The pullback unit has no reader, so both are decorative — a
reader would reasonably conclude those two legs carry a 200-bar backstop in
production. They do not.

⚠️ **`exit_reason: "timeout"` is a collapsed state.** Because it is the loop's
default, it is emitted both when the bar-count exit fired AND when the trade ran
off the end of the data (`n-1`). A census that counts `timeout` rows therefore
cannot separate "the lever bound" from "the backtest ended", and the sweep's own
premise comment (below) was written off exactly such a census.

---

## 2. The data half — does the default ever BIND?

*That the harness models an exit live lacks is a statement about code. Whether it
changed any verdict is a statement about data, and only the second sizes the problem.*

The `e35_bracket_geometry_sweep` grid contains its own control: it sweeps
`timeout_bars` over (24, 48, 96, **400**) beside a base arm at the harness default.
At otherwise-identical geometry, **base vs `to400`** is a direct test.

- `net_total_r` **differs** ⇒ the default **bound**; at least one trade was
  force-closed at 200 that would still have been open at 400. Verdicts on that leg
  are measured under an exit production does not have.
- `net_total_r` **identical** ⇒ no trade in that configuration reached 200, so the
  base arm **is** live-parity on this dimension.

⚠️ **The identical case is not trusted on its own.** A negative needs a denominator,
so the audit also reports the longest grid timeout that *does* move the leg — which
bounds max trade duration from below and proves the probe finds a positive there. No
leg is graded `clean` off silence alone; a leg where nothing moves would be graded
`no_power`, and none was.

### Result — population: 41 legs, 1,588 graded base-vs-`to400` geometry pairs

| | |
|---|---:|
| pairs where relaxing the default changes the result | **439 / 1,588 (27.6%)** |
| legs **CONTAMINATED** (the default bound at least once) | **18 / 41** |
| legs **CLEAN** (default provably inert ⇒ base arm *is* live-parity) | **23 / 41** |
| legs `no_power` | **0** |
| rows excluded as `inert_equals_base` (null, never zeroed) | 11 |

**This refutes the sweep's own stated premise.** `e35_bracket_geometry_sweep.py:117-120`
says the grid reaches below the default because *"the census records timeout as a
near-empty exit bucket (5 of 284 on the E0 leg), i.e. the current value is far outside
the binding region."* That is **one leg** generalised to the fleet, off the collapsed
`exit_reason` bucket described above. Measured across the corpus the default binds on
**18 of the 41 legs (43.9%)**.

The split is structural, not random — it tracks how long a leg's trades run:

| bound on max trade duration | legs | examples |
|---|---:|---|
| `(24, 48]` bars | 6 | `gdx`/`gld`/`iaum`/`mhg`/`slv_pullback_1d`, `tlt_pullback_1h` |
| `(48, 96]` bars | 10 | every 1d equity trend leg (`spy`/`qqq`/`iwm`/`scha`/`splg`/`mes_trend_long_1d`) plus `mgc_pullback_1d`, `tlt_pullback_1d`, `trend_donchian_avax_4h`, `trend_donchian_eth_prop` |
| `(96, 200]` bars | 7 | `slv_trend_1h`, `uso_trend_1h`, `gld_pullback_1h`, `ief_pullback_1d`, `avax_pullback_2h`, `eth_pullback_2h`, `trend_donchian_sol_prop` |
| **> 200 (CONTAMINATED)** | **18** | the 1h/4h crypto donchian legs, `htf_pullback_trend_2h`, `qqq`/`spy_pullback_1h`, `mgc`/`xauusd_trend_1h`, `squeeze_breakout_4h` |

---

## 3. What it means for what is SHIPPABLE

Applying the B4 selection rule (highest `wf_wins_effective`, tie-break `d_net_r`)
over the 51 `wf_pass` / `path_b_wf_pass` cells in the 08-26 corpus plus the 133 gate
rows of the 08-20 run, for the **27 legs that have a validated winner**:

| category | legs | meaning |
|---|---:|---|
| **A — shippable** | **11** | best cell carries no timeout component **and** the leg is CLEAN. The measurement matches production. |
| **B — verdict contaminated** | 4 | best cell is config-expressible, but the leg is CONTAMINATED: it was graded under a binding 200 live does not apply. |
| **C — blocked, tightening** | **10** | best cell prescribes a **24/48/96**-bar exit. **No config change can deliver it** — there is no live reader. |
| **E — unverified vs live** | 2 | best cell is `to400` on a CONTAMINATED leg, so it is untested against live's infinity. Both have a no-timeout fallback at the same `wf_wins_effective`. |

**Of the 10 in C, four have NO shippable alternative at all** — every cell that passed
their gate prescribes a timeout: `mes_trend_long_1d`, `tlt_pullback_1d`,
`trend_donchian_1h`, `squeeze_breakout_4h`. The other six retain a weaker
non-timeout winner (e.g. `gld_pullback_1h` `tp6_sm1.5_to24` at 6/6 is blocked, but
`sm1.5` stands at 5/6).

### ✅ The 8 legs B4 shipped to real money this morning are all category A

Checked because it is the only part of this with money on it. Each shipped cell was
re-derived independently from the corpus and matched the inline annotation in
`config/strategies.yaml` **on all 8**:

| leg | shipped cell | prescribes a timeout? | leg |
|---|---|---|---|
| `spy_trend_long_1d` | `tp2_sm1.5` | no | CLEAN |
| `qqq_trend_long_1d` | `tp3_sm2` | no | CLEAN |
| `iwm_trend_long_1d` | `tp3_sm2` | no | CLEAN |
| `scha_trend_long_1d` | `tp1.5_sm3` | no | CLEAN |
| `mgc_pullback_1d` | `tp6_sm1.5` | no | CLEAN |
| `uso_trend_1h` | `tp4_sm2` | no | CLEAN |
| `tlt_pullback_1h` | `sm2` | no | CLEAN |
| `slv_trend_1h` | `sm1.5` | no | CLEAN |

**PR #10419 is not affected by this defect.** `slv_trend_1h` also has a winning
`sm1.5_to400` cell whose `d_net_r` is **identical** to `sm1.5` (46.1961) — the leg is
CLEAN, so 200 ≡ 400 ≡ ∞ there and the timeout term is a provable no-op.

### The workplan's own estimate, corrected

B9 estimated *"4–6 cells"* and named six legs. The measurement moves it in both
directions:

- **`spy_pullback_1h` is not blocked** — its best cell `sm1.5_to400` ties its
  no-timeout sibling `sm1.5` at `wf 5/6` (`d_net_r` 29.96 vs 29.87). It is category E.
- **`gld_pullback_1h`, `eth_pullback_2h`, `eth_pullback_prop_2h` are only partly
  blocked** — their *best* cell is, a weaker one is not.
- **Five legs were missed**: `ada_pullback_2h`, `sol_pullback_2h`,
  `trend_donchian_1h`, `trend_donchian_sol_4h`, and **`squeeze_breakout_4h` — a third
  family the backlog row's scope (trend + pullback) does not cover at all.**

The count of legs whose best validated cell is undeliverable is **10, not 4–6**. The
count of *matrix cells* that must move to `blocked` is **2** (§ 5).

---

## 4. The recommendation

**The harness should stop modelling a bar-count exit by default; live should not gain
one on this evidence.** Reasons, in order:

1. **The base arm is meant to be live-parity and on 18 legs it is not.** The fix that
   makes it so is a research-tooling default, not an order-path change.
2. **The blast radius of the fix is bounded and already known**: on the 23 CLEAN legs
   a non-binding default is a provable no-op, so the change cannot move 56% of the
   fleet's verdicts. Only the 18 contaminated legs need re-running.
3. **Adding a time stop to production is a new order-path exit mechanism** — Tier-3,
   on a system whose Prime Directive is that the trader never switches itself off.

⚠️ **But do not read that as "the timeout evidence is worthless", because part of it
is uncontaminated and it is not weak.** Four legs are **CLEAN *and* category C** —
`mes_trend_long_1d`, `tlt_pullback_1d`, `gld_pullback_1h`, `eth_pullback_2h`. On those
the base arm *was* live-parity, and a **shorter** hold still beat it at the gate
(`gld_pullback_1h` `tp6_sm1.5_to24` walks forward **6/6**). That is a real, measured
claim that production is missing a lever, and it is the honest case for a Tier-3
time-stop proposal — separate work, properly scoped, not folded into this.

**Do NOT simply raise the default and re-grade.** Re-running the 18 contaminated legs
changes their verdicts, and several are `shipped` cells on live legs. That is a
re-measurement of live configuration and belongs behind the same gate as any other.

---

## 5. Changes landed with this document

1. **`scripts/research/timeout_binding_audit.py`** — the measurement, reproducible,
   with a `--self-test` asserting the null-handling (below), the contaminated case,
   the clean-with-power case, and that all-identical grades `no_power`, never `clean`.
2. **`docs/research/exit-refinement-coverage.json`**
   - the **8 B4 legs'** `bracket_geometry` cells `passed_unshipped` → **`shipped`**
     (PR #10419, `91de68b9`) — the matrix was stale by one Tier-3 shipment;
   - **`mes_trend_long_1d`** and **`tlt_pullback_1d`** `bracket_geometry`
     `passed_unshipped` → **`blocked`**, reason `no_live_bar_count_exit`. These are
     the only two matrix cells where **every** passing cell prescribes a timeout;
   - a `timeout_binding` note on the cells whose *best* cell is blocked but which
     keep a shippable fallback, and on the contaminated legs;
   - this defect added to `known_caveats.conditions_verdicts`.
3. **`scripts/check_harness_lever_coupling.py`** — a corrected premise (§ 6).

---

## 6. A false premise found in a guard registry, and why the obvious fix is wrong

`check_harness_lever_coupling.py` lists `timeout_bars` in `_TREND_UNMODELLED` and
`_PB_UNMODELLED` — registries defined as *"keys the family's harness does NOT model"* —
under a comment stating *"the squeeze harness models giveback_\* / timeout_bars"*,
implying the other two do not.

**MEASURED, by reading the three parsers: all three harnesses model it, via the same
flag.** `--timeout-bars` exists in `backtest_trend.py:982`, `backtest_pullback.py:961`
and `backtest_squeeze.py:545`; `regime_debt_matrix._SQZ_LEVER_FLAG` maps it, and the
trend/pullback `LEVER_FLAG` maps do not. The stated premise is false.

⚠️ **The obvious correction would make things worse.** Moving `timeout_bars` into
`_TREND_LEVER_FLAG` / `_PB_LEVER_FLAG` — which the code plainly justifies — would stop
`regime_debt_matrix` naming it in `omitted_levers`, **upgrading `mgc_pullback_1d` and
`mhg_pullback_1d` from `approximate` to full fidelity for a key the live unit never
reads.** The current classification reaches a defensible *outcome* (flag the row as
lower-fidelity) through a false *premise*, which is the `diagnostic-provenance` class:
the label does not describe what was computed, and here it is load-bearing in the
direction that keeps the answer safe.

So the registry entry **stays**, and only its reason is corrected: the key is excluded
not because the harness cannot model it, but because the harness models an exit the
LIVE unit does not have — so replaying the config key faithfully would make the
backtest *less* like production, not more.

---

## 7. Caveats on this document's own measurement

- **Population.** All figures are over `e35-bracket-corpus.jsonl` (41 legs / 8,211
  rows, sweeps of 2026-08-20 and 2026-08-26) plus `e35-bracket-gate-corpus.jsonl`
  (133 gate rows, 2026-08-20). **It does not cover trend/pullback verdicts produced by
  any other harness run** — the `m20_fleet` sweeps, `trail_geometry`, `stale_stop` and
  the rest were also run under the same default and are **not** measured here. The
  workplan's "how many non-e35 verdicts" question is therefore **still open** for
  those; what is settled is that the answer is not "none".
- **`to400` is a proxy for infinity, not infinity.** A CLEAN verdict proves no trade
  reached 200; it does not prove none would reach 400. For the CLEAN legs that is
  sufficient, since 200 is the arm actually used. For the two category-E legs it is not.
- **The corpus, not a re-run.** Nothing here re-executed a harness; it compares arms
  the 08-20/08-26 sweeps already produced. A re-run would also pick up any drift in
  the data since.
- **My own first pass was wrong and the corrected number is the one above.** It
  coerced `inert_equals_base`'s `net_total_r: null` to `0.0`, comparing a real number
  against a manufactured one; that reported 450/1,599 and invented one spurious
  finding on each of the 8 legs carrying such a row. The 11 nulls are now excluded and
  counted, and the script's self-test pins that behaviour.
