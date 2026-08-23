# Exit-geometry rebuild — active management — standalone session prompt

**Written:** 2026-08-23 by the full-system-audit session · **Paste this whole file as the opening message of a NEW session.**

Repo: `benbaichmankass/Metis-Insights`. Read `CLAUDE.md` and
`docs/CLAUDE-RULES-CANONICAL.md` first, then
[`docs/design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md)
— that is the scope of record for this thread and § 4 is the thesis rule you
will be applying.

## The operator's directive — this governs every decision below

> *"Brackets ALWAYS represent our prediction of where the trade should end …
> The only solution here is to properly build out the active management infra,
> not layer on bandaids to a poorly constructed strategy."*

Two consequences, both binding:

1. **A bracket must carry an expectation at entry.** A sentinel target is not an
   expectation; it is the absence of one.
2. **Do not reach for a clamp, a floor, or a refusal** when the honest answer is
   that the geometry was never constructed. Refusing trades instead of building
   the mechanism is the bandaid this thread exists to stop.

And the standing rule, so it does not have to be re-argued:
**TUNE BEFORE DEMOTE.** A losing leg gets a genuine multi-axis tuning attempt
before any demotion is proposed. It is canonical practice, not a per-case
favour.

## What is already true — measured, not remembered

Populations are stated because the same figure differs across three of them.
Measured against `config/strategies.yaml` on 2026-08-23:

| population | n | `tp_r >= 50` (sentinel) | real target |
|---|---|---|---|
| all declared | 55 | 30 | 25 |
| enabled, any `execution` | 52 | 28 | 24 |
| **enabled + `execution: live`** | **45** | **24** | **21** |

**A `tp_r` of 50 is the "far sentinel"** — there is no real target and the trail
is the only exit. `_TP_SENTINEL_CAP_PCT = 0.099` is the Bybit ErrCode 10001
boundary, not a chosen level.

**The venue ceiling is a function of the stop**, which is the trap in this area:
`cap_r = (tp_cap_pct × entry) / risk` with `risk = atr_stop_mult × ATR`, so
`cap_r` is **inversely proportional to `atr_stop_mult`**. Widening a stop lowers
the reachable target in R. Two of the three declared lever arms in
`position_telemetry` are already **unreachable under their own leg's ceiling**
(`xrp_pullback_2h` arm 4.49 vs cap 3.9233; `qqq_trend_long_1d` 3.56 vs cap
2.1258) — read `/api/diag/position_telemetry` and check `arm_reach` before
proposing any arm.

**The producer was the only missing piece of the extension lever.**
`_base.monitor` has declared `{"tp": float}` — move the take-profit — since it
was written, and **no strategy has ever produced one** (AST-verified).
Everything downstream is already live: `interpret_verdict` parses a `tp` delta
independently of `sl`, `_apply_update` routes it, `modify_open_order` amends the
resting leg.

**A soak now measures the decision, in ANNOTATE mode** (shipped 2026-08-23):
the donchian and pullback monitors evaluate the extension each tick and write a
row to `runtime_logs/target_extension_soak.jsonl` **instead of** returning a `tp`
verdict; a test pins the verdict byte-identical with and without the soak.
Read it at `/api/diag/log_file?name=target_extension_soak`.

⚠️ **Read `expectation_state` BESIDE `extension_state`.** The 24 sentinel legs
have no target to extend *from* and land `sentinel_no_expectation` /
`no_expectation_declared`. A soak read on `extension_state` alone scores that as
*"the lever never fires"* when it means *"there was never a target"*.
`thesis_unknown` **never extends** — "we did not check" must not become "it
holds".

## The exit-head question, and why it is not settled

Do not treat the shipped exit head as a working mechanism. Measured:

- Fleet edge **+1.217R at best-tau** vs **−0.341R causal**. The sign flips
  between the two, and only the causal number is deployable.
- The E1 gate is **~90% predicted by book size alone** (`n_oos >= 350`) — the
  gate may be measuring how much data a leg has, not whether the head works.
- Per-leg **AUC moves ±0.11 per day** against a 0.55 bar.
- **All 11 rounds on disk have ZERO TP exits.**
- The shipped donchian-1h head has fired **twice in a month** on real money.

⚠️ **The re-measure MUST run with `--total-sort` and the 0.099 cap, and MUST
record which convention it used** — the verdict depends on CLI leg order, which
is itself a finding and not a footnote.

## ⚠️ EVIDENCE CONTAMINATION — read this before calibrating anything on IB exits

Corrected 2026-08-23, and an earlier statement of mine was **too broad**: it is
NOT true that every IB bracket was stop-only. The two paths differ:

- **`IBClient.place()` — the ENTRY bracket — was FINE.** It builds a native IB
  bracket whose children are linked by `parentId`, so transmit-on-the-last-leg
  works as intended.
- **`IBClient.place_protective()` — the RE-ARM path — was the bug.** It mints a
  **parentless OCA** pair, where `transmit=False` on a leg means *held by IBKR
  forever*. So a position whose protection was ever re-armed (naked-autoprotect
  sweep, reconciler adopt / re-attach) **lost its take-profit** and could only
  ever stop out or run.

**The size of that population is UNMEASURED.** Two live `ib_paper` positions
were found target-naked and account-wide there were **zero** limit orders — but
that is 2 observations, not a rate. **Measure it before trusting any IB
live-parity exit evidence**, because for a contaminated trade "the target was
never hit" describes a mechanism that did not exist.

Fixed in **PR #10174** (`_locked_place_protective`, every leg now transmits;
plant-proven guard in `tests/test_ib_place_protective_transmits.py`). ⚠️ **At
the time of writing the fix is DEPLOYED-OR-PENDING and UNEXERCISED** — both
targets restored on 2026-08-23 went on retroactively via `place_target_in_group`,
a *different* method that always transmitted correctly. It is proven only when a
**new** bracket, born after the deploy, carries both a `STP` and a `LMT` in one
OCA group. Ask the concurrent session before assuming either way.

## Is this thread blocked on that verification? — NO

The research and backtesting are **not blocked**. The harness runs offline over
historical candles and reads no live broker state. The coupling is at
**deployment**, not at research: a conclusion that says *"carry a real target"*
cannot be trusted to actually rest at the broker until the transmit fix is
proven exercised — and that is weeks away from mattering. Proceed in full; just
carry the contamination caveat above into any live-parity comparison.

## Where to start

1. Read the PROCESS doc § 4 (the thesis rule: each family's OWN entry condition
   re-evaluated — donchian: is the channel still being pushed; pullback: does
   ADX still clear its declared `adx_min`).
2. Read the `target_extension_soak` rows that have accrued, with the
   `expectation_state` caveat above. If the file is thin, say so with the row
   count rather than reasoning over it.
3. Then the real question this thread exists for: **what expectation should a
   bracket carry at entry, per family**, and how is it constructed rather than
   clamped. The 24 sentinel legs are the population.

## Rules of engagement

- **Tier-3 is everything that touches strategy logic, params, risk caps, sizing,
  or a live promotion.** Propose the exact change and get an explicit operator
  OK. Tier-1/2 you ship yourself.
- **State the population on every number.** A figure without its denominator is
  not a finding.
- **Post `▶️ START` on coordination board issue #6927 before your first
  substantive tool call, and `✅ DONE` when you wrap.** ⚠️ **Another session is
  live on this repo** driving PR #10174 to merge + deploy and verifying IB
  trade adjustments at the venue reopen. Read the board tail first and prove you
  reached it — request `perPage=N` and treat only a SHORT page as proof of the
  end.

## Do NOT

- Do not touch PR #10174, the deploy, or the `ib_paper` positions — the other
  session owns them.
- Do not touch the armed routines (22:15Z MGC verification, 22:30Z verify-only).
- Do not change `PROP_SCREENSHOT_BACKEND` — it defaults to `local` (refuses)
  deliberately; the operator withdrew that driver on 2026-08-23 (ROADMAP M38).
- Do not demote a leg without a genuine tuning attempt first.
- Do not promote any model.
