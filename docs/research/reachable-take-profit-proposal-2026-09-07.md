# Reachable take-profit — the per-leg repair for the 25 enabled+live legs that have none

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-156** · branch `claude/reachable-tp-proposal-20260907` · PR [#11225](https://github.com/benbaichmankass/Metis-Insights/pull/11225) · coordination-board START [#6927 comment](https://github.com/benbaichmankass/Metis-Insights/issues/6927#issuecomment-5567982440)

⚠️ **TIER-3 PROPOSAL. NOTHING IS APPLIED.** `config/strategies.yaml` was opened **read-only**. The
diff in § 7 is a fenced block, not a change. No account mode was flipped, no exit head was armed,
no `pr-automerge-requests` file exists on this branch.

---

## 0. The one rule this memo is written under

> **This repairs REACHABILITY, not calibration.**

A target the venue accepts is strictly better than one it silently clamps away. That is the whole
argument, and **it needs no fidelity evidence.** It does **NOT** establish that the new level is
where momentum runs out.

Every row in every table below therefore carries two columns that are never merged: **what the
number rests on**, and **what it does not rest on**. MI-155 is the reason. It graded all 44
enabled+live legs `insufficient_n` — the abstain floor is `n_live ≥ 30`, the maximum per-leg live n
is **8**, `n_bt = 0` on 41 of 44, and `peak_r_is_lower_bound` is **true on 171 of 171** rows, so
every live excursion figure in this repo is a **lower bound**. A proposal that quietly implied these
levels were evidenced would be worse than no proposal.

**Consequence, stated up front:** on **24 of the 25** legs the honest repair is a *declaration*, not
a *number*. Exactly **one** leg carries pre-existing, gate-passing, live-expressible take-profit
evidence, and it is the only leg for which a value is proposed.

### The contract this was written against — and a discrepancy

This session was dispatched against work object
`docs/claude/work/objects/WO-20260907-25-OF-44-ENABLED-LIVE-LEGS-HAVE.yaml`. **That file does not
exist on `origin/main`** (`383a4f7`). Positive control for the probe: the same `git ls-tree` over
`docs/claude/work/objects/` returns **72** `WO-*.yaml` files, and the only 2026-09-07 object present
is `WO-20260907-PER-LEG-BACKTEST-TO-LIVE-EXIT-LOCATION.yaml` (MI-155's). So this is a real absence,
not a quiet probe. **The done-condition was read from the dispatch prose, not from a committed
object** — recorded here because a future reader will look for that object and should not conclude
it was deleted. This is the second time in this thread a dispatch has named a non-existent work
object; MI-148 records the same for its own brief.

---

## 1. Taken from priors — inherited, not re-measured

| fact | source |
|---|---|
| 25 of 44 enabled+live legs have no reachable take-profit: 15 declare `tp_r ≥ 20`, 10 declare no target key | MI-146 [`exit-lever-wiring-audit-2026-09-06.md`](exit-lever-wiring-audit-2026-09-06.md) |
| All 44 grade `insufficient_n`; floor 30; max `n_live` 8; `peak_r_is_lower_bound` true 171/171 | MI-155 [`exit-location-fidelity-2026-09-07.md`](exit-location-fidelity-2026-09-07.md) |
| Percent-of-entry, never R: `trades.stop_loss` is the FINAL trailed stop; `order_packages.sl` is overwritten by the same `_apply_update` path | MI-148 [`bracket-calibration-2026-09-06.md`](bracket-calibration-2026-09-06.md) |
| **No `tp_r` reproduces the clamp** — `cap_r = TP_VENUE_CAP_PCT × entry / risk` is a percent-of-entry against a multiple-of-risk | `src/runtime/tp_venue_cap.py` (the declared constant's own docstring) |

**Re-run as a control, not a re-derivation.** `scripts/research/bracket_expectation_census.py`
against `config/strategies.yaml` at `origin/main` `383a4f7` returns, for the 44 enabled+live legs:
**15 declared sentinels · 10 sentinel-by-inheritance · 19 real** — reproducing MI-146's split
exactly. `src/runtime/bracket_calibration.py` is **imported** by the instruments used here
(`quantile` is not re-derived), per the dispatch.

---

## 2. The mechanism, restated precisely — because the repair depends on it

Every clamping unit (`trend_donchian`, `htf_pullback_trend_2h`, `squeeze_breakout_4h`,
`fade_breakout_4h`) computes, for a long:

```python
tp = min(entry * (1 + TP_VENUE_CAP_PCT), entry + tp_r * risk)     # TP_VENUE_CAP_PCT = 0.099
```

So the **nearer** of the two wins. Expressed in R, the declaration binds **iff `tp_r < cap_r`**,
where `cap_r = 0.099 × entry / risk` and `risk = atr_stop_mult × ATR`.

Two consequences that shape the whole proposal:

1. **A leg with no `tp_r` key is not target-free.** It inherits the unit `_DEFAULTS`
   (`"tp_r": 50.0`), so its runtime behaviour is **byte-identical** to an explicit sentinel. The
   census names this precisely: *"These are NOT visible to a `grep tp_r` of the YAML. Same runtime
   behaviour as an explicit sentinel, different remedy."*
2. **`cap_r` is a per-trade quantity, not a per-leg constant.** The census's `cap_r@ref` column
   assumes ATR/entry = 0.02 and is explicitly *"a stated reference, not a per-leg measurement"*.
   That reference is crypto-shaped and **materially understates `cap_r` on the low-volatility
   equity/ETF legs** — see § 3, where it is measured instead.

---

## 3. What the venue actually rests — measured, not referenced

**POPULATION: `/api/diag/position_telemetry?limit=1000` over Caddy, read 2026-09-07, 171 rows
returned; the 35 legs with at least one row carrying a readable positive `cap_r`.** `cap_r` is
*exact* given entry and risk (unlike `peak_r`, it is not a lower bound) — but **per-leg n is 1–8**,
so the *distribution* is thin even though each value is sound.

| leg | n | `cap_r` median | `cap_r` min | `cap_r@ref` (0.02) said |
|---|---:|---:|---:|---:|
| `spy_pullback_1h` | 5 | 16.04 | 14.35 | 1.98 |
| `tlt_pullback_1h` | 4 | 16.41 | 12.12 | 2.48 |
| `ief_pullback_1d` | 1 | 11.41 | 11.41 | 1.98 |
| `qqq_pullback_1h` | 5 | 8.78 | 7.79 | 1.98 |
| **`gld_pullback_1h`** | **4** | **8.24** | **7.94** | 1.98 |
| `tlt_pullback_1d` | 2 | 5.92 | 4.71 | 1.98 |
| `trend_donchian` | 4 | 7.30 | 4.12 | 2.48 |
| `trend_donchian_eth` | 5 | 4.85 | 4.41 | 1.98 |
| `trend_donchian_avax_4h` | 7 | 3.99 | 1.32 | 3.30 |
| `eth_pullback_2h` | 7 | 3.24 | 1.76 | 1.98 |
| `trend_donchian_eth_4h` | 5 | 2.83 | 1.40 | 2.48 |
| `trend_donchian_sol_4h` | 8 | 2.69 | 1.55 | 3.30 |
| `trend_donchian_ada_4h` | 6 | 2.11 | 1.06 | 2.48 |
| `gdx_pullback_1d` | 1 | 1.28 | 1.28 | 2.48 |
| `splg_trend_long_1d` · `tqqq_trend_long_1d` · `squeeze_breakout_4h` | **0** | — | — | — | 

*(Legs above with no row are **NOT MEASURED** — we did not look — never "cap_r is unknown to the
system".)*

**This is the reachability finding, and it is arithmetic rather than evidence:** the low-volatility
equity/ETF legs sit at `cap_r` **8–16**, so a finite `tp_r` in the 2–6 range would genuinely bind on
them. The crypto 4h legs sit at `cap_r` **2.1–4.0**, so on those legs almost any finite `tp_r` a
sweep would produce is *still* clamped and the declaration would remain inert. **The two families
are not in the same position and must not be given the same remedy.**

---

## 4. Is there any leg-specific take-profit evidence at all? — the decisive query

The repo's committed sweep corpus is the only per-leg take-profit evidence that exists.
**POPULATION: `docs/research/e35-bracket-corpus.jsonl`, 8,520 rows, 41 legs, sweeps
2026-08-23 → 2026-08-31; per leg, only its own latest sweep date.** A cell is usable here only if it
**passes the gate** (`gate_verdict ∈ {wf_pass, path_b_wf_pass}`), **carries a take-profit**
(`tp_r` non-null), **and carries no timeout** (`timeout` null) — because no live unit implements a
bar-count exit (`timeout_bars` is read only by `fvg_range_15m.py` and `fade_breakout_4h.py`, each
from its own `_DEFAULTS`; `BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES`).

**POSITIVE CONTROL — the probe is not dead.** Across the whole corpus it finds **14 such cells on 10
legs**: `ada_pullback_2h`, `gld_pullback_1h`, `iwm_trend_long_1d`, `mgc_pullback_1d`,
`qqq_trend_long_1d`, `scha_trend_long_1d`, `spy_trend_long_1d`, `trend_donchian_eth_prop`,
`trend_donchian_xrp_4h`, `uso_trend_1h`.

**And here is the sharpest thing in this memo.** Nine of those ten legs **have already shipped
exactly that cell**, each carrying its cell id inline in `config/strategies.yaml`:

| leg | winning cell | shipped `tp_r` |
|---|---|---|
| `ada_pullback_2h` | `tp4` | `4` |
| `uso_trend_1h` | `tp4_sm2` | `4.0` |
| `mgc_pullback_1d` | `tp6_sm1.5` | `6.0` |
| `qqq_trend_long_1d` | `tp3_sm2` | `3.0` |
| `scha_trend_long_1d` | `tp1.5_sm3` | `1.5` |
| `spy_trend_long_1d` | `tp2_sm1.5` | `2.0` |
| `iwm_trend_long_1d` | `tp3_sm2` | `3.0` |
| `trend_donchian_xrp_4h` | `tp3_sm2` | `3` |
| `trend_donchian_eth_prop` | `tp6_sm1.5` | `6.0` |

> **The pipeline already converted every leg for which it produced a live-expressible take-profit,
> and left exactly one behind: `gld_pullback_1h`.** The other 24 of the 25 are not an oversight —
> they are an **absence of evidence**. That distinction is the finding, and it is what makes 24 of
> the 25 a declaration problem rather than a number problem.

---

## 5. Sub-population A — the 15 clamped legs (`tp_r ≥ 20`)

**The declared intent here is genuinely "run forever", and it is written down** — not in config, but
in the unit docstrings, which is why nobody found it in the YAML:

- `trend_donchian.py:25` — *"There is no fixed profit target — the trail is the sole profit-exit, so
  `tp` is placed `tp_r × risk` away (a **deliberately far sentinel**; matches the backtest, which
  has no TP)."*
- `htf_pullback_trend_2h.py` § Exit — *"let the continuation run; **NOT a tight target** (the
  program's iron law: every tight-target strategy died on BTC fees). Far ~50R `tp` sentinel."*
- `squeeze_breakout_4h.py` — its `monitor()` trail logic is *"identical to trend_donchian"*.

So for these legs the sentinel is **a decision that was made and recorded in the wrong place**, not
an omission. The repair is to move it somewhere a reader and an instrument can see it.

| # | leg | tf | sym | current declaration | what the venue actually rests | proposed | basis | what it does **NOT** establish |
|---|---|---|---|---|---|---|---|---|
| 1 | `trend_donchian` | 1h | BTCUSDT | `tp_r: 50.0` | clamp; measured `cap_r` med **7.30** (n=4) | **explicit `none`** | unit docstring states deliberate unbracketing; **0** TP-bearing timeout-free passing cells | that no target would help; only that none is evidenced |
| 2 | `trend_donchian_eth` | 1h | ETHUSDT | `tp_r: 50.0` | clamp; `cap_r` med 4.85 (n=5) | **explicit `none`** | same; matrix `honest_negative` | — |
| 3 | `trend_donchian_sol` | 1h | SOLUSDT | `tp_r: 50.0` | clamp; `cap_r` med 6.20 (n=3) | **explicit `none`** | same; 1 passing cell, **0** TP-bearing | — |
| 4 | `trend_donchian_ada_4h` | 4h | ADAUSDT | `tp_r: 50.0` | clamp; `cap_r` med **2.11** (n=6) | **explicit `none`** | same; matrix `shipped` (`sm2` — a **stop-mult** cell) | — |
| 5 | `trend_donchian_avax_4h` | 4h | AVAXUSDT | `tp_r: 50.0` | clamp; `cap_r` med 3.99 (n=7) | **explicit `none`** | same; matrix `shipped` (`sm1.5`) | — |
| 6 | `trend_donchian_eth_4h` | 4h | ETHUSDT | `tp_r: 50.0` | clamp; `cap_r` med 2.83 (n=5) | **explicit `none`** | same; matrix `shipped` (`sm2`) | — |
| 7 | `trend_donchian_sol_4h` | 4h | SOLUSDT | `tp_r: 50.0` | clamp; `cap_r` med 2.69 (n=8) | **explicit `none`** | same; matrix `shipped` (`sm1.5`) | — |
| 8 | `mes_trend_long_1d` | 1d | MES | `tp_r: 50.0` | clamp; `cap_r` 2.99 (n=1) | **explicit `none`**, flagged `blocked` | its **only** passing TP cell is `tp1_sm2_to24` — **timeout-bound, live cannot express it** | that no target exists — one may, behind a Tier-3 order-path change |
| 9 | `qld_trend_long_1d` | 1d | QLD | `tp_r: 50.0` | clamp; `cap_r` 1.31 (n=1) | **`unexamined`** | **never swept** — absent from both corpora | anything at all. **We did not look.** |
| 10 | `splg_trend_long_1d` | 1d | SPLG | `tp_r: 50.0` | **NOT MEASURED** (0 telemetry rows) | **explicit `none`** | matrix `honest_negative`; **0** TP-bearing cells | — |
| 11 | `tqqq_trend_long_1d` | 1d | TQQQ | `tp_r: 50.0` | clamp; **NOT MEASURED** | **`unexamined`** | **never swept** — absent from both corpora | anything at all. **We did not look.** |
| 12 | `eth_pullback_2h` | 2h | ETHUSDT | `tp_r: 50.0` | clamp; `cap_r` med 3.24 (n=7) | **explicit `none`** | pullback docstring's "NOT a tight target"; **0** TP-bearing cells | — |
| 13 | `sol_pullback_2h` | 2h | SOLUSDT | `tp_r: 50.0` | clamp; `cap_r` med 2.94 (n=4) | **explicit `none`** | same; matrix `blocked:no_dispersion_band` | — |
| 14 | `mhg_pullback_1d` | 1d | MHG | `tp_r: 50.0` | clamp; `cap_r` med 2.42 (n=2) | **explicit `none`** | same; matrix `honest_negative` | — |
| 15 | `squeeze_breakout_4h` | 4h | BTCUSDT | `tp_r: 50.0` | **NOT MEASURED** (0 telemetry rows) | **explicit `none`** | same; matrix `blocked:dispersion_refused` | — |

**Verdict on sub-population A: 13 `none`, 2 `unexamined`, 0 finite targets.** Proposing a finite
`tp_r` on any of these would be inventing a number — and on the seven crypto legs whose measured
`cap_r` is 2.1–4.0, it would additionally be *inert*, because the clamp would still bind.

⚠️ **`qld_trend_long_1d` and `tqqq_trend_long_1d` are the third state and must stay separable.**
They are not "deliberately unbracketed" and they are not "evidenced as having no target" — **their
target question has never been asked.** Both are absent from `e35-bracket-corpus.jsonl` (41 legs)
*and* from `e35-bracket-corpus-history.jsonl` (27 legs), against a positive control
(`splg_trend_long_1d` present in both). The matrix says why: `blocked:no_free_lane_candle_feed`.
Folding them into `none` would launder *we did not look* into *we decided*, which is precisely the
collapse `docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states" exists to prevent.

---

## 6. Sub-population B — the 10 legs with no target key at all

**Absence is not a clamped target, and the first question is whether a target was intended and lost.**

**MEASURED: it was not. None of the 10 has ever carried a `tp_r` key.** Probe: every one of the
**110** revisions of `config/strategies.yaml` in the full repo history (`2026-04-29` → `2026-09-06`;
history fetched complete — `git rev-list --count origin/main` = 4,306, no shallow graft), block-scoped
per leg.

**POSITIVE CONTROL — the probe finds positives and even catches value changes:**

| control leg | result |
|---|---|
| `eth_pullback_2h` | `tp_r` found, present in 59/110 revs, introduced 2026-06-11 |
| `sol_pullback_2h` | `tp_r` found, 52/110, introduced 2026-06-18 |
| `ada_pullback_2h` | `tp_r` found — **and the change caught**: `50.0` → `4  # M20 e35 re-check` |
| `xrp_pullback_2h` | `tp_r` found — **change caught**: `50.0` → `3.0` |

Against that control, all ten target legs return `ever_tp_r = False` across every revision in which
the leg exists, back to its own introduction commit. So sub-population B is uniformly
**never declared** — *not* intended-and-lost.

| # | leg | tf | sym | current declaration | what the venue actually rests | proposed | basis | what it does **NOT** establish |
|---|---|---|---|---|---|---|---|---|
| 1 | **`gld_pullback_1h`** | 1h | GLD | **no key** → inherits `tp_r: 50.0` | clamp; measured `cap_r` med **8.24**, **min 7.94** (n=4) | **`tp_r: 4.0`** | cell **`tp4`**, sweep 2026-08-31: `wf_pass`, `wf_wins_effective` **4/6**, **0 inert**, `d_net_r` **+9.00**. Reachability: 4.0 < 7.94 ⇒ the declaration would have **bound on 4 of 4** observed trades | **that 4.0R is where momentum runs out.** No live fidelity backs it — MI-155 grades this leg `insufficient_n` (`n_live` 3, floor 30). The 4/6 walk-forward is about **net R**, not exit **location**, and 2 folds lost (2024 −2.00, 2026 −2.26) |
| 2 | `spy_pullback_1h` | 1h | SPY | no key → `50.0` | clamp; `cap_r` med **16.04** (n=5) | **explicit `none`** | matrix `passed_unshipped`, but its 3 passing cells are **stop-mult only — 0 TP-bearing** | — |
| 3 | `tlt_pullback_1d` | 1d | TLT | no key → `50.0` | clamp; `cap_r` med 5.92 (n=2) | **explicit `none`**, flagged `blocked` | its only passing TP cell `tp2_sm1.5_to24` is **timeout-bound** | that no target exists — one may, behind a Tier-3 order-path change |
| 4 | `tlt_pullback_1h` | 1h | TLT | no key → `50.0` | clamp; `cap_r` med **16.41** (n=4) | **explicit `none`** | matrix `shipped` — but as `sm2`, a **stop-mult** cell; **0** TP-bearing passing cells | — |
| 5 | `qqq_pullback_1h` | 1h | QQQ | no key → `50.0` | clamp; `cap_r` med 8.78 (n=5) | **explicit `none`** | matrix `honest_negative`; 0 passing cells | — |
| 6 | `gdx_pullback_1d` | 1d | GDX | no key → `50.0` | clamp; `cap_r` 1.28 (n=1) | **explicit `none`** | matrix `honest_negative`; 0 passing cells | — |
| 7 | `gld_pullback_1d` | 1d | GLD | no key → `50.0` | clamp; `cap_r` 2.55 (n=1) | **explicit `none`** | matrix `honest_negative`; 0 passing cells | — |
| 8 | `iaum_pullback_1d` | 1d | IAUM | no key → `50.0` | clamp; `cap_r` 2.61 (n=1) | **explicit `none`** | matrix `honest_negative`; 0 passing cells | — |
| 9 | `ief_pullback_1d` | 1d | IEF | no key → `50.0` | clamp; `cap_r` **11.41** (n=1) | **explicit `none`** | matrix `honest_negative`; 0 passing cells | — |
| 10 | `slv_pullback_1d` | 1d | SLV | no key → `50.0` | clamp; `cap_r` 1.67 (n=1) | **explicit `none`** | matrix `honest_negative`; 0 passing cells | — |

**Verdict on sub-population B: 1 finite target, 9 `none`, 0 `unexamined`** (all ten were swept).

⚠️ **On `gld_pullback_1h`, this proposal deliberately departs from the matrix's own "winner", and
says so.** The matrix ref names the *shippable winner* as cell `sm1.5` (`d_net_r` +46.46) — selected
by `d_net_r`, and it carries **no take-profit**. `tp4` is a **lower-ranked** but gate-passing,
live-expressible cell that *does* carry one (`d_net_r` +9.00). **Selecting `tp4` is selecting for
reachability, not for `d_net_r`,** which is a different criterion and is the operator's to accept or
reject. The two are not mutually exclusive — `sm1.5` is a stop change and `tp4` a target change —
but **this proposal does not evaluate them jointly, and no cell in the corpus evaluates the pair.**

---

## 7. The proposed diff — **NOT APPLIED**

Two mechanically different changes. Read them separately.

**(a) The one value change.** Uses the existing schema key, so it is effective on the next config
reload with no new reader. This is the only line in this memo that changes a resting order.

**(b) The 24 declaration changes.** These add an explicit `tp_intent` block **and** make the
inherited sentinel explicit. They are **runtime-inert by construction**: `tp_r: 50.0` written out is
byte-identical to the inherited default, and nothing reads `tp_intent` today. What they buy is that
the sentinel becomes visible to `grep tp_r` (the census records that today it is not), and that
*deliberately unbracketed*, *blocked*, and *never examined* stop being byte-identical.

⚠️ **`tp_intent` has no consumer, and by this repo's own standard that is a defect, not a
feature** — *"a signal that is written and never read is worse than a missing one"*
(`provenance-consumer-guard`). It is proposed anyway because the operator's own gate condition
(MI-146 § gate 1) asks for exactly this declaration, but **the follow-on Tier-1 work of teaching
`bracket_expectation_census.py` and `src/runtime/target_expectation.py` to read it should land in
the same cycle**, or this key will rot exactly as `exit_price_source` did.

```diff
--- a/config/strategies.yaml
+++ b/config/strategies.yaml
@@ gld_pullback_1h — (a) THE ONLY VALUE CHANGE IN THIS PROPOSAL
     atr_period: 14
     atr_stop_mult: 2.5
     trail_mult: 4.0
+    # M20 e3.5 bracket geometry: cell `tp4` (wf_pass, wf_wins_effective 4/6, 0 inert,
+    # d_net_r +8.9997; sweep 2026-08-31, corpus docs/research/e35-bracket-corpus.jsonl).
+    # REACHABILITY (this is what the number rests on): measured cap_r on this leg is
+    # min 7.944 / median 8.243 over n=4 live rows (position_telemetry, 2026-09-07), so
+    # 4.0 < cap_r on 4 of 4 — the declaration BINDS instead of the 9.9% venue clamp.
+    # ⚠️ THIS DOES NOT ESTABLISH THAT 4.0R IS WHERE MOMENTUM RUNS OUT. MI-155 grades
+    # this leg `insufficient_n` (n_live 3 vs a floor of 30); the walk-forward is about
+    # net R, not exit LOCATION, and 2 of 6 folds lost. It is a reachable target, not a
+    # calibrated one. Selected for reachability over the matrix's d_net_r winner `sm1.5`,
+    # which carries no take-profit at all.
+    tp_r: 4.0
+    tp_intent:
+      mode: r_multiple
+      basis: e35_cell_tp4
+      calibrated: false          # reachable, NOT calibrated — see MI-156 § 6
     min_confidence: 0.0
     shadow_model_ids: []

@@ THE 13 SUB-POPULATION-A LEGS THAT ARE DELIBERATELY UNBRACKETED
@@ trend_donchian, trend_donchian_eth, trend_donchian_sol, trend_donchian_ada_4h,
@@ trend_donchian_avax_4h, trend_donchian_eth_4h, trend_donchian_sol_4h,
@@ splg_trend_long_1d, eth_pullback_2h, sol_pullback_2h, mhg_pullback_1d,
@@ squeeze_breakout_4h   (+ mes_trend_long_1d, below, with a different reason)
     tp_r: 50.0
+    tp_intent:
+      mode: none
+      # DELIBERATE: the trail is the sole profit-exit. Stated in the unit docstring
+      # (trend_donchian.py:25 / htf_pullback_trend_2h.py "NOT a tight target"), never
+      # in config — which is why 50.0 read as an omission rather than as a decision.
+      # The 50R value is retained ONLY so the venue rests an exchange-valid order at
+      # 9.9%; it is NOT a prediction and must never be read as one.
+      reason: trail_is_the_profit_exit
+      evidence: no_gate_passing_timeout_free_tp_cell   # e35 corpus, leg's latest sweep

@@ mes_trend_long_1d AND tlt_pullback_1d — a target MAY exist but live cannot express it
     tp_r: 50.0
+    tp_intent:
+      mode: none
+      reason: blocked_no_live_bar_count_exit
+      # ⚠️ NOT the same as the legs above. This leg's only gate-passing take-profit cell
+      # (mes: `tp1_sm2_to24`; tlt_1d: `tp2_sm1.5_to24`) prescribes a bar-count timeout
+      # that NO live unit implements, so no config change can deliver it. Giving live a
+      # bar-count exit is a separate Tier-3 order-path change
+      # (BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES).
+      revisit_when: live_gains_a_bar_count_exit

@@ qld_trend_long_1d AND tqqq_trend_long_1d — WE DID NOT LOOK
     tp_r: 50.0
+    tp_intent:
+      mode: unexamined
+      # ⚠️ THIS IS NOT A DECISION AND MUST NOT BE READ AS ONE. These two legs are absent
+      # from BOTH e35-bracket-corpus.jsonl (41 legs) and -history.jsonl (27 legs) — their
+      # target question has never been asked. Matrix: blocked:no_free_lane_candle_feed.
+      reason: never_swept_no_free_lane_candle_feed
+      revisit_when: a_candle_substrate_exists_for_this_symbol

@@ THE 9 SUB-POPULATION-B LEGS THAT NEVER DECLARED A TARGET
@@ spy_pullback_1h, tlt_pullback_1h, qqq_pullback_1h, gdx_pullback_1d, gld_pullback_1d,
@@ iaum_pullback_1d, ief_pullback_1d, slv_pullback_1d   (tlt_pullback_1d is above)
+    # Was absent, therefore inherited htf_pullback_trend_2h._DEFAULTS["tp_r"] = 50.0.
+    # Written out so the sentinel is visible to `grep tp_r`. RUNTIME-IDENTICAL.
+    tp_r: 50.0
+    tp_intent:
+      mode: none
+      reason: trail_is_the_profit_exit
+      # MEASURED: this leg has NEVER carried a tp_r key, across all 110 revisions of this
+      # file (2026-04-29 -> 2026-09-06). Never declared — NOT intended-and-lost.
+      evidence: no_gate_passing_timeout_free_tp_cell
```

**Not in this diff, deliberately:** no change to `TP_VENUE_CAP_PCT` (tightening it would bind
*more*, not less — MI-148 names this as off the table); no `tp_r` "lowered to an equivalent figure"
on any leg (`tp_venue_cap.py`: *no `tp_r` reproduces the clamp*, and doing so tightens the real
target on every trade the clamp was never binding for); no change to the `ict_scalp` family, which
already declares a real expectation and must not be harmonised into the sentinel idiom.

---

## 8. Honest limits — what this proposal could NOT establish

- **It calibrates nothing.** 24 of 25 legs get a declaration, not a number, and the one number is
  reachable rather than calibrated. **Do not quote `gld_pullback_1h: tp_r 4.0` as an evidenced
  target.**
- **`cap_r` is measured but thin.** Per-leg n is 1–8 (§ 3). Each value is exact; the *distribution*
  is not. `gld_pullback_1h`'s "binds on 4 of 4" is a statement about four trades.
- **Three legs have no `cap_r` measurement at all** — `splg_trend_long_1d`, `tqqq_trend_long_1d`,
  `squeeze_breakout_4h` returned zero telemetry rows. Their § 5 rows rest on corpus evidence only.
- **The `ict_scalp` telemetry blindness that MI-155 filed is unfixed and bounds this too** — 0 of
  171 telemetry rows carry an `ict_scalp*` strategy, so the fleet's one calibrated family
  contributes no `cap_r` evidence to the comparison in § 3.
- **I did not re-derive the sweep gate.** The `wf_pass` / `path_b_wf_pass` verdicts and
  `wf_wins_effective` counts are read from the committed corpus as recorded; I checked only that
  cells are timeout-free and gate-passing, not that the gate was correctly applied.
- **`spy_pullback_1h`'s corpus verdict is flagged CONTAMINATED by the matrix itself** (the harness's
  200-bar force-close bound on 17 of 39 graded pairs). I propose `none` for it, which does not
  depend on that verdict — but its `passed_unshipped` status should not be read as clean.
- **No P&L claim is made anywhere in this memo**, per E3.6's ordering.
- **This session had no GitHub API write access** — `add_issue_comment` and `create_pull_request` both returned `403 Resource not accessible by integration`, against a positive control (`issue_read` on the same issue #6927 succeeded in the same minute). The board START went through `board-post.yml` and this PR through `pr-opener.yml`, both pushed on a **separate** branch so their results commits could not bury this PR's checks. MI-155 records the identical token scope one day earlier, so the board understates concurrent activity by an unknown amount.
- **The `tp_intent` key has no reader** (§ 7b). Until one ships, these declarations are readable by
  humans and instruments, not by the runtime.

---

## 9. What this says to do next

1. **The operator decides three things**, and they are separable: (a) accept `tp_r: 4.0` on
   `gld_pullback_1h` as a *reachability* repair; (b) accept the `tp_intent` vocabulary
   (`r_multiple` / `none` / `unexamined`); (c) accept that `unexamined` is a legitimate published
   state for two live legs.
2. **Land a reader for `tp_intent` in the same cycle** (Tier-1) or do not land the key.
3. **`qld_trend_long_1d` and `tqqq_trend_long_1d` need a candle substrate** before their target
   question can even be asked. That is the only work in this memo that unblocks new evidence rather
   than recording its absence.
4. **MI-155's two remedies still stand and are not superseded** — a timeframe-matched harness run,
   and live soak depth. **Nothing here sets a target from the MFE-quantile route**, which remains
   correctly blocked at `insufficient_n` on 44 of 44.

---

*Measured 2026-09-07 against `config/strategies.yaml` and both e3.5 corpora at `origin/main`
`383a4f7`, the full 110-revision history of that file, and the live trader over Caddy
(`/api/diag/position_telemetry?limit=1000`, 171 rows). `src/runtime/bracket_calibration.py` is
imported by the instruments used, never re-derived. `config/strategies.yaml` was not modified.*
