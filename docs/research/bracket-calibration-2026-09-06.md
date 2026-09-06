# Are our brackets predictions? — the first calibration read, and what it says to build

**MI-148** · work object [`WO-20260906-THE-EXIT-GEOMETRY-REBUILD-WAS-SPECIFIED-AND-NEVER-DISPATCHED`](../claude/work/objects/WO-20260906-THE-EXIT-GEOMETRY-REBUILD-WAS-SPECIFIED-AND-NEVER-DISPATCHED.yaml) · branch `claude/exit-geometry-rebuild-20260906`

⚠️ **PROPOSE-ONLY on the geometry.** Per-leg take-profit values are Tier-3. Nothing in `config/strategies.yaml` is touched by this branch. What IS shipped is Tier-1 and observe-only: a pure grader, a manual-only report script, 30 tests, and this memo.

## Why this session exists, and what it is NOT

[`docs/research/EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md`](EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md) was written on 2026-08-23 and ends *"Paste this whole file as the opening message of a NEW session."* **Nobody ever did.** The diagnosis under it has been established at least **seven times** — 2026-08-20, 08-23 (34KB), 08-24, 08-26, 08-29, 08-31, and 09-06 (MI-146) — **and built zero times.**

So this memo does not re-derive it. §"Taken from priors" states what is inherited. The new work is one thing: **the falsifier E3.6 requires now has an instrument, and it has been run.**

## The thesis being served (operator, verbatim)

> *"Brackets ALWAYS represent our prediction of where the trade should end … The only solution here is to properly build out the active management infra, not layer on bandaids to a poorly constructed strategy."* (2026-08-23)

> *"if we're talking about a momentum strategy … the whole point is that we need to place the brackets where we think we know when momentum will run out … And then if we see, for example, that we're getting close to the take profit but the momentum is still strong, then we can adjust that during the trade. But there shouldn't be, like, an endless bracket."* (2026-09-06)

[`docs/design/exit-mechanism-construction-PROCESS.md`](../design/exit-mechanism-construction-PROCESS.md) § E3.6 turns that into a falsifier:

> *"a predictive bracket is a **claim about where the trade will exit**, so it is graded against realised exits — calibration first …, P&L second. A bracket that improves net R while being systematically wrong about *where* trades exit has not met this bar."*

⚠️ **Two corrections to the dispatch brief, recorded because the next reader will look for them.** (1) The brief cites *"PROCESS § 4"* as the thesis rule; **there is no § 4** — the document's top-level sections are 0, 1, 1.5, 2, 3. The thesis rule is **item 4 *inside* § E3.6** (line 344, *"Revision must be conditioned on the strategy's OWN thesis"*), and the falsifier is the closing paragraph of the same section. (2) The work object named in the brief **did not exist** on `origin/main` or on disk; this branch creates it.

---

## Taken from priors — inherited, not re-measured

| fact | source |
|---|---|
| 25 of 44 enabled live legs (56.8%) have no reachable take-profit — 15 declare a ≥20R sentinel, 10 declare no target key and inherit `tp_r = 50.0` | MI-146 [`exit-lever-wiring-audit-2026-09-06.md`](exit-lever-wiring-audit-2026-09-06.md) |
| `_base.monitor` has declared `{"tp": float}` since it was written; **no strategy has ever produced one** (AST-verified). 14 `return {"sl": …}` sites; `return {"tp": …}` exists nowhere | MI-146 + `target_extension_soak.py` docstring |
| The downstream `tp` channel is **fully plumbed** — `interpret_verdict` → `_apply_update` → `_send_modify_to_exchange` → `modify_open_order`. Only the producer was missing | same |
| `target_extension_soak` reads **100 of 100** rows `sentinel_no_expectation`, so it cannot distinguish *"the lever never fires"* from *"no trade ever had a target to approach"* | MI-146, read 2026-09-06T10:31Z |
| Directional book n=120 (08-27→09-06): bracket 29.2%, take-profit 5.8%, reconciliation/plumbing 63.3% | MI-146 |
| `/api/bot/performance`'s `expectancyR` is **sign-inverted on the live endpoint**; over the whole journal n=1287, 104 rows (8.1%) carry 96.6% of `totalR` | MI-144 (#11131) |

**I re-ran one of these as a control, not as a re-derivation.** `scripts/research/bracket_expectation_census.py` on the repo config returns, for the 44 enabled+live legs: **15 declared sentinels, 25 effective sentinels, 19 real** — reproducing MI-146's 15/10/19 split exactly by a different method. Two methods, same population, same numbers.

The census also surfaces something MI-146 did not, and it sharpens the picture: **9 of those 19 "real" targets would themselves be clamped** at the census's reference ATR/entry of 0.02 (`ada_pullback_2h` 4.0R vs cap 3.30, `mgc_pullback_1d` 6.0R vs 3.30, `trend_donchian_eth_prop` 6.0R vs 1.98, …). So at that reference, **34 of 44 enabled+live legs (77.3%)** rest a target set by the venue clamp rather than by a stated expectation — not 25. ⚠️ **0.02 is a stated reference, not a per-leg measurement**; ATR at entry is not knowable from config, which is exactly why the live views below matter more.

---

## NEW — the calibration instrument (clause 4)

MI-146 § Q1 measured that E3.6's falsifier *"is not measured anywhere … has no instrument, no artifact and no cell."* It has one now:

- **`src/runtime/bracket_calibration.py`** — pure grader (no I/O, never raises, no runtime caller).
- **`scripts/research/bracket_calibration_report.py`** — `--exits` and `--mfe`, plus `--selftest`.
- **`tests/test_bracket_calibration.py`** — 30 tests pinning the *distinctions*, not today's numbers.

### The load-bearing design choice: percent-of-entry, never R

The obvious basis is R. It is the wrong one, for two independent reasons that agree:

1. **The R denominator is contaminated, and the contamination is measured.** `trades.stop_loss` is the FINAL trailed stop and `order_packages.sl` is overwritten by the same `_apply_update` path, so both erase the level they replaced. MI-144 measured the consequence live. An instrument built on that denominator inherits it.
2. **The venue clamp is itself a percent of entry.** `TP_VENUE_CAP_PCT` is 9.9% *of entry*. In R the same clamp is a different number per trade — which is why `tp_venue_cap.py` warns that **no `tp_r` reproduces the clamp**. Percent-of-entry is the basis on which *prediction vs artefact* is directly decidable.

Entry price and exit price are the only two fields needed, and no monitor path rewrites either.

### Why `take_profit_1` is a trustworthy record of the entry-time target

It has exactly **one** writer — `order_monitor.py:1374`, `trade_sync["take_profit_1"] = updates["tp"]` — which fires only on a `tp` verdict that **no live strategy produces**. The single acting producer rolls `turtle_soup`'s `meta.tp2` forward, and `turtle_soup` is `execution: shadow`.

⚠️ **This is an argument from the fleet's current state, not an invariant, and it expires the moment clause 2 ships.** The grader therefore carries a `target_provenance` field with three values — `static_no_acting_producer` / `may_have_moved` / **`unknown` (*we did not look*)** — and a caller that has not established the acting set gets `unknown` rather than a silent assumption of pristineness.

---

## What the instrument says

### View 1 — `--exits`: where trades actually ended vs the target they declared

**POPULATION: closed, non-backtest, non-`pairs_*` rows of `/api/diag/journal?table=trades`, newest 1000 returned (the endpoint caps there), window 2026-08-09T22:50Z → 2026-09-06T10:03Z. n = 297 directional.** The pairs sleeve is excluded: its legs stop on the **spread**, so a per-leg bracket is the wrong yardstick — the same call MI-144 made.

Stratified by exit-price provenance, **never pooled**:

| stratum | n graded | reach_rate | clamp_bound_rate | declared target sits at quantile of realised exits |
|---|---:|---:|---:|---:|
| `measured` | 82 (of 84) | **0.1220** | 0.2561 | **0.9024** |
| `estimated` | 180 (of 180) | 0.1167 | 0.3944 | 0.8611 |
| `unverified` | 7 (of 33; 26 unreadable) | 0.0000 | 1.0000 | 1.0000 |

**Read the `measured` row.** 12.2% of trades reached their declared target (n=82), and the declared target sits at the **0.90 quantile** of the realised exit distribution. Those two numbers are the same fact arriving twice, which is the instrument's internal consistency check.

**The per-leg table splits the fleet into two regimes that share nothing.** POPULATION for both: the 264 rows whose exit provenance is `measured` or `estimated` (both anchor the exit to a real close; reported together only to reach per-leg n, and labelled as such).

| regime | legs | n | clamp_bound_rate | median declared target | reach_rate |
|---|---:|---:|---|---|---|
| **`ict_scalp` family** | 8 | 162 | **0.0000 on every leg** | 0.85%–1.90% | 0.077–0.308 |
| **donchian / pullback** | 20 | 102 | **0.80–1.00** | **0.099 — exactly `TP_VENUE_CAP_PCT`** | 0.000 on 13 of 16 legs with n≥2 |

**On 12 donchian/pullback legs the target sits at quantile 1.0000 of realised exits** — not one trade in the window ended above it. That is the sharpest available statement of the thesis failure: the declared target is not a prediction that is sometimes wrong, it is a level nothing ever reached.

⚠️ **And note the inversion, which MI-146 also flagged:** the family that already implements the operator's thesis is `ict_scalp` — fixed `tp_at_r: 1.5`, zero clamp-binding, targets landing at quantile 0.69–0.92 of its own outcomes. **It is the fleet's existence proof that a calibrated bracket is achievable here**, and it is the family the coverage matrix marks `n/a` on all four trailing levers.

### View 2 — `--mfe`: how far the trades ever GOT

`--exits` is **censored by the current geometry** — these trades exited at a trail, so a realised exit is a *floor* on how far the move went. It can falsify a target ("nothing ever got there") but must not be used to set one. The uncensored basis is `position_telemetry.peak_r`.

**POPULATION: 102 of 168 `position_telemetry` rows with `peak_gradeable: True` and readable `peak_r`/`cap_r`.** ⚠️ **`peak_provenance` is `estimated` on 168 of 168 and `peak_r_is_lower_bound` is `True` on 168 of 168** — so every figure below is a LOWER BOUND on the excursion. That is the safe direction for falsifying a too-far target and the unsafe one for justifying a too-near one, and it is why nothing here proposes a number.

**Only 4 of 102 trades (3.9%) ever reached — even at the lower bound — the 9.9% level their target sits at.**

### This answers an open question `tp_venue_cap.py` explicitly declared open

That module's own docstring warns the 0.099 is *"named for a Bybit boundary and is applied to every symbol, including legs that touch no Bybit account"*, and says plainly: *"whether `0.099` is right, too tight, or too loose for a non-Bybit leg is an **OPEN QUESTION**."*

| group | n | MFE p50 | MFE p75 | MFE p90 | ever reached 9.9% |
|---|---:|---:|---:|---:|---:|
| crypto (Bybit-traded) | 63 | 1.39% | 6.15% | **9.70%** | 4/63 = 0.063 |
| **non-crypto (touches NO Bybit account)** | 39 | 1.19% | 3.00% | **3.42%** | **0/39 = 0.000** |

- **On Bybit-traded crypto the cap is, by accident, about right**: 9.9% against a p90 lower-bound MFE of 9.70% — a ratio of **1.0×** (n=63). It was never chosen as a prediction; it happens to land near one.
- **Off Bybit it is too loose by ~2.9× at p90**, and **0 of 39** trades ever reached it (n=39).

⚠️ **State the limits before quoting this.** n=39 for the non-crypto arm; per-leg n runs 1–8; the MFE is estimated and lower-bounded, which biases *against* the finding on the crypto arm (the true p90 may exceed 9.9%) and *toward* it on the non-crypto arm. The direction is solid; the ratio is not a tuned number.

---

## What this says to BUILD — the proposal

### Clause 1 — give the sentinel legs a stated, reachable expectation · **Tier-3, PROPOSED, NOT APPLIED**

**The shape, not the numbers.** Per-leg target values are Tier-3 and must not be fitted off n=1–8. What the evidence supports today:

1. **The `ict_scalp` family needs no change.** It already declares an expectation, its targets do not clamp, and they sit at a defensible quantile of its own outcomes. **TUNE BEFORE DEMOTE applies here in reverse: do not "harmonise" the one calibrated family into the sentinel idiom.**
2. **The non-crypto legs are the priority, and the case is strongest there** — 0 of 39 ever reached the cap, and the cap is a boundary imported from a venue they do not trade on. A leg whose target is 2.9× the p90 of its own best excursion is not making a claim.
3. **The construction must be per-leg MFE-quantile conditioned on the family's thesis**, per E3.6(4) — donchian: is the channel still being pushed; pullback: does ADX still clear its declared `adx_min`. **Not** a lowered `tp_r`: `tp_venue_cap.py` states that *no `tp_r` reproduces the clamp*, so an "equivalent" figure tightens the real target on every trade the clamp was never binding for.
4. **A leg that genuinely wants no target must DECLARE that** — an explicit key — rather than carry a 50R sentinel. That is the difference between a decision and its absence, and today they are byte-identical.

⚠️ **What is NOT yet available, and must be before per-leg numbers are set:** an MFE distribution at proper n. The live telemetry gives 1–8 rows per leg. The un-circular source is the offline harness over historical candles, which reads no live broker state and is **not blocked** on anything in this thread. **That is the next unit of work, and it is the honest gate on clause 1** — proposing per-leg targets off n=1 would be the fitted-threshold failure this repo already pays for.

### Clauses 2 and 3 — status, and why they are correctly blocked

**Clause 2 (a producer that can extend) is ~90% built and I did not rebuild it.** `target_expectation.evaluate_extension` is the decision, `target_extension_soak.annotate_from_monitor` is the observe-only producer, and it already runs on 41 legs from `trend_donchian.monitor()` and `htf_pullback_trend_2h.monitor()`. What remains is the annotate→act flip — **Tier-3**, and the same shape as the M20 stale-stop rollout.

**Clause 3 is a consequence of clause 1, not separate work.** The soak reads 100/100 `sentinel_no_expectation` because there has never been a target to approach. It starts producing evaluable rows the moment any leg carries a real one. **Clause 1 is a hard prerequisite for clauses 2 and 3** — the ordering MI-146 also reached, independently.

### What NOT to do — and the operator named this specifically

> *"Do not reach for a clamp, a floor, or a refusal when the honest answer is that the geometry was never constructed."*

Concretely, off the table: lowering `tp_r` to an "equivalent" figure (it is not equivalent); tightening `TP_VENUE_CAP_PCT` (it would bind *more*, not less); refusing trades on legs whose targets are unreachable; and demoting a leg on evidence gathered under a sentinel target — which is most of the `giveback_stop` and `regime_flip_exit` negatives, per `BL-20260814-THREE-SIBLING-SWEEPS-STILL-BUILD-NO-TAKE-PROFIT-BOOKS-AND-STAMP-NOTHING`.

---

## Honest limits of this memo

- **The `--exits` view is censored** and can only falsify a target, never set one. Stated at every use.
- **The `--mfe` view is n=102, estimated, and lower-bounded.** Per-leg n is 1–8 — too thin for per-leg numbers, which is why none are proposed.
- **The journal endpoint caps at 1000 rows**, so the window is 2026-08-09→09-06 and not the lifetime book. A longer window needs the offline path.
- **`take_profit_1`'s cleanliness is an argument from the fleet's current state**, and expires when clause 2 ships. The grader carries `target_provenance` so it cannot silently rot.
- **No P&L claim is made anywhere here.** Calibration is a *location* read; per E3.6 it comes first, and `expectancyR` is sign-inverted on the live endpoint until MI-144's #11131 deploys.

*Measured 2026-09-06 against the live trader over Caddy (`/api/diag/journal`, `/api/diag/position_telemetry`, `/api/bot/config` — `as_of` and window stated per table), repo `origin/main` b5ccd91e.*
