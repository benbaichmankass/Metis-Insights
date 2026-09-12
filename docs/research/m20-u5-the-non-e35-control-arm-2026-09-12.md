# MI-278 U5 — the excursion measurement had no control arm; built it, and it acquits e35 of the winner-size collapse

> **Doc status:** `unknown` · category `research` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U5 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Serves the MFE half of** `OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED`'s `clears_when`, and bears on `BL-20260911-MFE-OVER-MAE-IN-ATR-UNITS-IS-A-LEADING-INDICATOR-NOBODY-COMPUTES`.
> **Tier-1.** One new `scripts/research/` module, read-only over the journal and a public candle feed. No `src/`, no config, no order path, nothing enacted. **⚠️ This does NOT clear the row** — see §6.

---

## 0. The answer, in eight sentences

The row's `clears_when` says in terms that *"'THE MARKET CHOPPED' IS NOT A VERDICT WITHOUT THE NON-e35 CONTROL"*, and the excursion measurement that would settle it **already existed and had no control arm**: `stop_width_counterfactual_2026_09_11.py` (MI-275) computes MAE/MFE in ATR units per era, and `build_units` filters to `group_of(...) == "e35"` and then drops anything `stop_change()` does not recognise, so a non-e35 leg is excluded twice over by construction. **So the missing piece was never an instrument — it was an arm**, and this unit supplies it by importing MI-275's `excursion_regime` and `fixed_window_excursion` **unmodified** and running them on `untouched_control` (60 pre / 66 post packages against e35's 18 / 17). **The result separates the two candidate mechanisms.** Adverse excursion in ATR units grew in **both** arms by similar amounts (post/pre **1.09–1.45×**), so a common, market-wide effect is real. **Favourable excursion collapsed only in the treated arm** — at a fixed 48h window from entry, e35 MFE fell to **0.195×** while the control's moved to **0.945×** — and it survives symbol matching, where on the same symbols over the same period the control is 0.88–1.23× on SOL/ETH/AVAX/XRP against 0.03–0.33× for e35. **That acquits e35 of the winner-size collapse rather than convicting it**, because a fixed window measured forward from entry ignores the exit entirely, and e35 changed only the stop multiplier — a stop cannot reach back and shorten a window that does not depend on it. What the measurement points at instead is **entry quality on the trend-following family**, which is a different question with a different remedy, and which nothing in this repo currently owns.

---

## 1. Population, stated first

| | |
|---|---|
| **Source** | `/api/diag/journal` `trades` + `order_packages`, `limit=1000` each, pulled 2026-09-12T16:0xZ |
| **Unit** | **order package**, never the trade row — account fan-out shares one price path (MI-275 §F(ii)) |
| **Population rule** | `bleed_attribution_2026_09_11.population` — **imported, not restated** |
| **Split** | `created_at` vs `E35_DEPLOY_UTC` = `2026-08-30T08:53:19+00:00`, imported from MI-275 |
| **Treated arm** | `group_of == "e35"` → **18 pre / 17 post** packages (26 / 25 rows), 7 legs, all `trend_donchian_*` + `ada_pullback_2h` |
| **Control arm** | `group_of == "untouched_control"` → **60 pre / 66 post** packages (80 / 86 rows), 7 legs, all `ict_scalp_*` |
| **Price basis** | OKX `*-USDT-SWAP` 1m via MI-275's `fetch_candles` / `INST` — inherited, not re-argued |
| **Ungraded** | control **7 × `no_candle_source`** (all `ict_scalp_mgc_15m`; MGC is an IB future and is genuinely outside this basis). e35: **0**. |

Ungraded units are counted and named, never dropped — a control arm that quietly shrinks to the units that happened to work is not a control.

---

## 2. Three instrument findings, all caught by controls rather than by reading

These are reported first because two of them produced **plausible wrong numbers**, and the third is a live fragility in an instrument whose verdict is load-bearing.

### 2.1 `extreme()` accepts any side string and silently takes the max-high branch

`CF.extreme(candles, side)` is `min(low) if side == "below" else max(high)`. The vocabulary is `"below"`/`"above"`. This file's first draft passed `"low"`/`"high"`, which is not an error — **it falls to the `else` and returns `max(high)` for BOTH the adverse and the favourable side.**

The output was **MAE exactly equal to MFE in all eight fixed-window cells**, with entirely plausible magnitudes. Nothing raised, nothing warned. It was caught only because the builder positive control (§2.4) put the numbers beside MI-275's, and an equality that clean is not something a reader looking at one arm would question. Filed.

### 2.2 The observation window is `rows[0]`'s close, and `rows[0]` is iteration order

`excursion_regime` windows each unit on `[opened_ms, rows[0]["closed_ms"]]`. On a fan-out package the sibling rows **do not close together**: measured on the treated arm, **5 of 35 packages have a close spread over an hour and the widest is 30.9h** — `pkg-19c87bcdb911419a` closed 12:12:27 on `bybit_1` and 21:25:24 on `bybit_portfolio`.

Which row is `rows[0]` is whatever order the input happened to arrive in. Before this file sorted them, four shared packages disagreed with MI-275 by up to **2.503 vs 0.850 ATR — a 2.9× swing on the same package and the same data.**

⚠️ **This is a fragility, NOT a claim that MI-275's published numbers are wrong.** With the rows sorted ascending the two agree exactly (§2.4), so today's ordering resolves the same way. What is true is that the window is **undefined**, and 5 packages are sensitive to it. This file declares the choice — **earliest close**, which is deterministic and the *shortest* window, so it can only understate an excursion and never manufacture one — and ships `closed_spread_h` on every unit so the sensitive packages are visible rather than inferred. Filed.

### 2.3 The OKX proxy basis is fit for the trend legs and marginal for the scalps

MI-275 validated its basis on e35 legs and recorded `fit` (0 unfit of 31). Run on the control arm, its **own** PC1 grades **`unfit`: 5 of 126, max deviation 120.5bp against a 25bp tolerance** — four SOLUSDT and one BTCUSDT `ict_scalp_*` entries whose journal entry price sits *outside* the OKX bar covering the fill minute.

That is 4.0% of the arm, and it means **the control arm's basis is weaker than the treated arm's**, which is the uncomfortable direction: the arm being introduced to test the other one is the less well-founded of the two. It is reported rather than tuned away, and §5 states what it does and does not permit. Filed.

### 2.4 The builder positive control, which is why the arm is admissible at all

A control arm built by a *different* builder from the treated arm is not a control. So `--group e35` rebuilds the **treated** arm through this file's own builder and compares it against MI-275's recorded output, package by package.

**Result: 0 disagreements across all 31 shared packages.** The arithmetic is identical. The remaining difference is **unit selection only** — this builder admits 35 packages where MI-275 admits 31, because it deliberately does not apply the counterfactual-geometry filters (`stop_change`, the era-vs-declared multiplier assertion), which have no meaning for a leg whose multiplier never changed.

It failed three times before it passed, once per finding above. **That is the control working, not incidental friction** — every one of those three would have shipped a confident wrong number.

---

## 3. The comparable basis, and why the obvious comparison is refused

`excursion_regime` measures over each trade's **own** `[open, close]` window. The control is entirely `ict_scalp_*` (5m/15m) and the treated arm entirely `trend_donchian_*`/`ada_pullback_2h` (2h/4h): holding periods differ by an order of magnitude, so ATR-unit excursions over the trade's own life differ between the arms for reasons that have nothing to do with e35.

**This file therefore refuses to difference it**, reports it per arm, and stamps `cross_arm_comparable: false` with the reason into the output — rather than leaving a tempting pair of numbers side by side.

`fixed_window_excursion` measures a fixed number of hours **from entry, ignoring the exit**, so the window depends on neither the geometry under test nor the holding period. **It is the only basis on which a difference is reported here.**

---

## 4. The result

### 4.1 Adverse excursion grew in BOTH arms — the common effect is real

Median MAE in ATR units, post ÷ pre:

| window | e35 | control |
|---|---|---|
| 4h | 1.118 | 1.366 |
| 12h | 1.092 | 1.170 |
| 24h | 1.351 | 1.447 |
| 48h | 1.371 | 1.451 |

Both arms, every window, same direction and similar size — **the control moves slightly MORE**. Price is travelling further against entries per unit of the volatility that sized the stops, across legs e35 never touched. *"The market chopped"* is supported for the adverse half, and it tightens every stop in the book including the untouched ones.

### 4.2 Favourable excursion collapsed only in the treated arm

Median MFE in ATR units, post ÷ pre:

| window | e35 | control |
|---|---|---|
| 4h | 0.641 | 0.721 |
| 12h | **0.370** | 0.818 |
| 24h | **0.438** | 0.551 |
| 48h | **0.195** | 0.945 |

At 4h the arms are close. **From 12h out they separate, and by 48h the treated arm has lost 80% of its forward favourable excursion while the control has lost 5%.**

### 4.3 It survives symbol matching, with one honest exception

The arms do not trade the same symbol mix, so a pooled difference can be a difference in *which* symbols were traded. Median MFE at 48h, post ÷ pre, per symbol:

| symbol | e35 | control |
|---|---|---|
| ETHUSDT | **0.03×** (n 3→2) | 0.96× (n 6→10) |
| SOLUSDT | **0.10×** (n 4→3) | 0.88× (n 20→22) |
| AVAXUSDT | **0.33×** (n 3→5) | 1.06× (n 10→18) |
| BTCUSDT | 0.36× (n 3→2) | **0.36×** (n 13→7) |
| XRPUSDT | — (no pre observation) | 1.23× (n 11→9) |
| ADAUSDT | 0.83× (n 5→4) | not traded |

On four of five shared symbols the separation holds on the same symbol over the same period. **BTCUSDT is the exception and both arms fell identically (0.36×)** — on BTC the collapse *is* market-wide, and it is named rather than averaged away.

---

## 5. What this establishes, and the load-bearing inference

**A fixed window measured forward from entry does not depend on the exit.** e35 changed `atr_stop_mult` — where the stop sits. A stop cannot reach backwards and shorten a window that ignores it. So whatever collapsed the treated arm's forward MFE, **it is not the e35 bracket geometry**, and it is not market-wide either, because the control legs trading the same symbols over the same days barely moved.

What is left is the property the two arms actually differ in besides geometry: **the entry**. `trend_donchian_*` enters on a breakout and needs continuation; `ict_scalp_*` does not. A market that stops extending guts the first and leaves the second roughly intact — which is exactly the shape here, and which is consistent with MI-271's independent finding that average win fell ~72% while the control's stop rate held.

**This is a statement about entries, not exits.** MI-275 said so of its own single-arm reading; the control arm is what makes it a comparison rather than an assertion.

⚠️ **It does not exonerate e35 of everything.** e35 raised its own legs' stop-out rate far more than the control's — measured here on the package unit: e35 **11.1% → 47.1%** (+35.9pp) against the control's **50.0% → 64.8%** (+14.8pp), a difference-in-differences of **+21.2pp**. Both moved; e35 moved more. So the honest split is: **e35 is a real second effect on the STOP side, and is not the cause of the winner-size collapse.**

---

## 6. What this does NOT establish

- **It does not clear `OI-20260911`.** That row wants *per-leg* stop-out rate and MFE-at-stop. **Per-leg is unreachable**: the largest e35 leg-era cell is 5 packages and several are 1–3, so a per-leg rate is a number without a denominator worth quoting. What is delivered is the **group-level** contrast the row's own reasoning depends on. Say which was measured.
- **It does not establish the cause.** "Entry quality on the trend family" is where the measurement points; it is not a verdict, and this unit ran no test of it.
- **The control arm's basis is graded `unfit`** (§2.3). Four of the five unfit rows are SOLUSDT, and SOL carries the control's second-largest cell. The direction of the finding does not depend on SOL alone — ETH and AVAX carry it independently — but the SOL numbers specifically should not be quoted without this caveat.
- **n is small on the treated side**: 2–5 packages per symbol-era cell. The pooled 18/17 is what the significance rests on, not the per-symbol table.
- **`fixed_window_excursion` drops windows running past the data end** — e35 post n falls 17→15 at 48h, control 66→56. The 48h row is the most affected and the most quoted; read it beside its n.
- **Nothing about real money.** Both arms are dominated by paper accounts.
- **No standing surface.** `BL-20260911-MFE-OVER-MAE-IN-ATR-UNITS-IS-A-LEADING-INDICATOR-NOBODY-COMPUTES` wants this *per leg per week on a durable surface* and a session observed reading it. This is a one-off with a `manual-only` wiring marker, deliberately: a scheduled runner would re-answer a dated question against a moving population. ⚠️ **That row's TITLE — "no surface computes it" — is false as stated**, and was before this unit: MI-275 computes it. Its *criterion* is right; the title is not, and the row is updated rather than closed.

---

## 7. Reproducing

```
python3 scripts/research/excursion_control_arm.py --self-test           # 13 controls, no network
python3 scripts/research/excursion_control_arm.py --group e35 \
    --trades trades.json --packages packages.json --out e35.json        # the builder positive control
python3 scripts/research/excursion_control_arm.py --group untouched_control \
    --trades trades.json --packages packages.json --out control.json     # the arm
```

⚠️ **A probe written with Python `urllib` will conclude the price basis is unreachable when it is not.** OKX returns **403 to `urllib`** and **200 to `curl`** from the same container at the same moment — a User-Agent artifact, not a venue block. (`api.bybit.com` genuinely *is* CloudFront country-blocked, with an explicit country message, and `api.binance.com` returns 451, so MI-275's choice of a proxy instrument stands and was re-verified here.) This cost one wrong conclusion in this unit before the script was simply run.
