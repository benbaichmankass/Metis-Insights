# MI-278 U25 — the wrong-sign expectancyR is still live six days on, and it is driven by contamination SHARE, not count

> **Doc status:** `unknown` · category `research` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U25 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Advances, and cannot close,** `PB-20260906-R-CONTAMINATION-QUANTIFIED-AND-THE-HEADLINE-SIGN-IS-WRONG` and its sibling `PB-20260821-R-AND-DOLLARS-DISAGREE-IN-SIGN`.
> **Tier-1.** One new `scripts/research/` module; the only VM contact is an unauthenticated `GET /api/bot/performance`. No `src/`, no config, nothing enacted.

---

## 0. The answer, in five sentences

`PB-20260906` measured on **2026-09-06** that `/api/bot/performance` publishes an `expectancyR` whose sign contradicts its own PnL. **Read live on 2026-09-12 it still does**: on the `30d` window `totalPnl −14.5552`, `expectancy −0.355` and `profitFactor 0.8251` all say losing while **`expectancyR` reads +0.1113**. ⚠️ **And `rCoverage` reads `1.0` on the same payload**, so the surface asserts full R coverage beside the contradicted number, with 11 of 41 rows graded `contaminated` by the endpoint's own `rProvenance` block. ⚠️ **The flip is driven by contamination SHARE, not count, and the row's framing by count points the wrong way** — the `all` window carries *more* contaminated rows (14) than `30d` (11) and its sign **agrees**; what separates them is 26.8% against 3.3%. So the damage concentrates in the **short window an operator is most likely to open**, not in the long ones.

---

## 1. Why this unit exists rather than a fix

`PB-20260906`'s criterion (1) is *"publishes an expectancyR that AGREES IN SIGN with its own totalPnl and profitFactor — verified by reading the LIVE endpoint, not a test."* The fix is in `src/web/api/routers/performance.py`: **Tier-2**, not mine to land.

What was missing is that the criterion had no runnable form, so checking it meant re-deriving the whole decomposition against the journal. This unit makes it one command, so the next session can tell in seconds whether the Tier-2 change landed **and worked**.

---

## 2. The live reading

**Population:** the live `/api/bot/performance` on **2026-09-12T23:0xZ**, read unauthenticated over the Caddy host. The endpoint defaults to `demo=False` — **real money only** — and applies its own `exclude_reconciler` / `exclude_superseded` / `exclude_reset_flat`.

| window | n | totalPnl | expectancyR | profitFactor | contaminated | share | rCoverage | verdict |
|---|---|---|---|---|---|---|---|---|
| `7d` | 5 | −12.4015 | −0.6904 | 0.0 | 0 | 0.0% | 1.0 | agrees |
| **`30d`** | **41** | **−14.5552** | **+0.1113** | **0.8251** | **11** | **26.8%** | **1.0** | **DISAGREES** |
| `all` | 429 | −81.9330 | −0.3197 | 0.6958 | 14 | 3.3% | 1.0 | agrees |

Reproduce:

```
curl -sS 'https://ict-bot.duckdns.org/api/bot/performance?window=30d' > perf30.json
python3 scripts/research/performance_sign_agreement.py --payload perf30.json
```

⚠️ **THREE distinct windows, not four.** I requested `7d`, `30d`, `90d` and `365d`; the endpoint served `all` for both `90d` and `365d`. **It is honest about that** — the payload echoes `window: "all"`, and a `bogus` value does the same, so an unrecognised window is *self-declaring* rather than silently substituted. That is the correct behaviour and is recorded as a credit, not a finding. A caller who trusts their *request* rather than the *echo* would still be wrong.

---

## 3. What the row gets wrong, and it matters for where to look

`PB-20260906` frames the exposure by **count** — *"124 contaminated rows journal-wide"*, *"12 rows = 30.8% of the window"*. A reader taking the count reasonably expects the long windows to be the worst.

**They are the safest.** `all` holds 14 contaminated rows against `30d`'s 11 and its sign agrees, because 14 in 429 is 3.3% while 11 in 41 is 26.8%. The probe reports `count_predicts_the_flip: False` as an explicit control rather than leaving it to be inferred.

**The practical consequence:** the corrupted figure is on the window an operator opens by habit, and the reassuring one is on the window they open when they want the full picture.

---

## 4. ⚠️ Agreement is NOT evidence of a fix

`7d` agrees while carrying **zero** contaminated rows and `profitFactor: 0.0`; `all` agrees because the contamination is diluted 8×. Neither says the aggregate stopped counting contaminated R — only that nothing flipped the sign on that population.

The probe therefore ships `agreement_is_not_evidence_of_a_fix: True` on every graded window and a summary `caveat` saying so. Criterion (2) of the row — that contaminated rows are demonstrably excluded, shown by the `|R|>10` control — is **not** tested here and is not claimed.

---

## 5. What this unit does NOT do

- **It closes neither row.** Both need the Tier-2 change in `performance.py`.
- **It tests criterion (1) only.** Criterion (2) needs the `|R|>10` control over the journal, which this does not run.
- **It proposes no `src/` change.** The natural remedy — publish `expectancyR` over non-contaminated rows, or publish both beside `rProvenance` — is a Tier-2 decision and belongs in a proposal, not here.
- **`7d` is n=5.** Its agreement is worth nothing as evidence either way.
- **One read, one instant.** These windows move; the verdict is dated, which is why the probe exists rather than a number in a memo.

---

## 6. Verification

`python3 scripts/research/performance_sign_agreement.py --self-test` → **21 controls, 0 failures**, no network.

Five defects planted, **all five caught**:

| planted defect | controls that fired |
|---|---|
| collapse a zero term into `agrees` | **5** |
| collapse an absent term into `not_gradeable_zero` | **6, 7, 8** |
| claim the contaminated COUNT predicts the flip | **16** |
| return `False` rather than `None` when there is nothing to compare | **18** |
| divide by a zero denominator for the share | **crash** |

⚠️ **The first run reported D1 as tripping nothing, and that was MY MEASUREMENT, not the harness.** I piped the self-test through `tail -20 | grep FAIL`, and control 5's failure line was above the cut — **the same truncation flaw I had found and written up one unit earlier in U24, repeated immediately.** The corrected runner greps the whole output and reports a crash explicitly. Recorded because a plant that "tripped nothing" is indistinguishable from a weak control unless the runner is trusted, and mine was not.
