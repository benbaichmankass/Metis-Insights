# MI-278 U36 — expectancyR disagreeing with totalPnl is R working, not R broken

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Instrument: [`scripts/research/r_sign_split_basis.py`](../../scripts/research/r_sign_split_basis.py) ·
23 pytest controls in [`tests/test_r_sign_split_basis.py`](../../tests/test_r_sign_split_basis.py) ·
subject: `/api/bot/performance` (**`src/web/`, Tier-2, untouched**)

**Population:** the live endpoint, read **2026-09-13T04:0xZ** from
`https://ict-bot.duckdns.org/api/bot/performance?window=30d` — HTTP 200,
`window=30d`, `since 2026-08-14T04:02:16Z`, **41 real-money trades** (the route
defaults `demo=False`). Every figure below is from that one payload.

## The claim

`PB-20260906-R-CONTAMINATION-QUANTIFIED-AND-THE-HEADLINE-SIGN-IS-WRONG`
(severity **high**, open) makes two statements, and they are not the same:

1. **The mechanism** — `trades.stop_loss` holds the *current trailed* stop, so
   `|entry − stop|` collapses on a trade trailed to breakeven and `pnl/risk`
   explodes.
2. **The criterion** — resolution requires the endpoint to publish an
   `expectancyR` that **agrees in sign** with its own `totalPnl` and
   `profitFactor`.

## 1. The disagreement is REAL and LIVE — criterion (2) is not met

| field | live value |
|---|--:|
| `expectancyR` | **+0.1113** |
| `totalR` | +4.5617 |
| `totalPnl` | **−14.5552** |
| `profitFactor` | **0.8251** |
| `winRate` | 34.1% (14 / 41) |

R says the book made money per unit of risk; dollars and profit factor both say
it lost. ⚠️ The magnitude has fallen **8.8×** from the row's published +0.9818,
but the *sign* disagreement has not gone away.

## 2. But the MECHANISM the row names provably is not operating

The endpoint publishes `rBasis` — a count of **which risk each published R was
actually divided by**. On this payload:

```
rBasis: {"declaredInitial": 41, "storedStop": 0, "refusedWrongSide": 0, "noBasis": 0}
```

**All 41 of 41 rows used the declared initial risk. Zero used the stored stop.**
Verified per leg as well as in the headline — every one of the six legs reads
`storedStop: 0`, and the per-leg counts sum exactly to the headline.

`r_multiple_provenanced` (MI-144, landed 2026-09-06) is not merely deployed, it
is **fully binding on this window**: it prefers the signal-time `risk_per_unit`
from `order_packages.meta`, which no trailing amend can reach. So the collapse
the row root-caused cannot have touched any published R here.

⚠️ **`rProvenance.contaminated` still reads 11 of 41, and that is NOT a
contradiction.** The two fields answer different questions and the route's own
comment says so:

> *"WHICH risk each published R was divided by. A different question from
> `r_prov` above (which grades the stored stop) and never a renaming of it: a
> row graded `unverified` can still be computed on the `declared_initial` basis,
> because the declared record is independent of the stored stop."*

`classify_r` grades **whether the stored stop was trailed**. `rBasis` says
**what the R was divided by**. A row can be `contaminated` and have its R
computed on a basis the contamination never reached — which is the case for all
11.

⚠️ **CONSEQUENCE FOR THE ROW'S REMEDY: excluding `contaminated` rows today
would remove rows whose R the mechanism never touched.** The row's decomposition
("12 rows carry 117% of the R; remove them and the sign flips") was measured on
2026-09-06 against the *pre-MI-144* basis. Applied to today's payload it would
be excluding sound rows to force a number.

## 3. What actually explains it — and it is a finding, not a defect

`R` is `pnl ÷ the trade's own risk`. **Its whole purpose is to stop a
large-risk trade and a small-risk trade being compared on dollars.** So a book
whose winning legs are sized smaller than its losing legs *must* show positive
mean R and negative dollars. Measured per leg:

| leg | n | `totalPnl` | `totalR` | `expectancyR` | **$ per R** |
|---|--:|--:|--:|--:|--:|
| `ict_scalp_5m` | 19 | −26.332 | −6.941 | −0.365 | **3.793** |
| `xrp_pullback_2h` | 5 | −8.012 | −1.875 | −0.375 | **4.274** |
| `trend_donchian_xrp_4h` | 1 | −4.819 | −1.053 | −1.053 | **4.577** |
| `eth_pullback_2h` | 4 | −0.330 | +0.284 | +0.071 | *n/a — mixed sign* |
| `trend_donchian` | 6 | +7.992 | +2.699 | +0.450 | **2.961** |
| `trend_donchian_eth_4h` | 6 | +16.946 | +11.448 | +1.908 | **1.480** |

The legs sum **exactly** to the headline (4.5616 vs 4.5617; −14.5553 vs
−14.5552), so the decomposition is complete and nothing is hidden.

> **TOTAL SEPARATION.** Both positive-R legs sit at **1.480 and 2.961**; all
> three negative-R legs sit at **3.793, 4.274 and 4.577**. Winner max 2.961 <
> loser min 3.793 — **gap 0.832, spread 3.09×, no overlap.**

**Every profitable leg is sized smaller per unit of risk than every losing leg.**
That is the entire sign disagreement, and it is arithmetic, not contamination.

⚠️ **The separation test is threshold-free** — it asks only whether the two
ranges overlap, so it carries no tuned constant.

## 4. So criterion (2) is UNSOUND and should be withdrawn

Requiring `expectancyR` to agree in sign with `totalPnl` requires R to stop
being risk-normalised. A gate enforcing it would force `/api/bot/performance` to
publish a **wrong** number — and would do so on a route the operator reads. The
criterion should be replaced by something that tests the mechanism, which is
what `rBasis` already reports and what this module grades.

## 5. The finding the row actually reveals

**On real money over 30 days, dollars-of-risk per unit R spans 3.09× across legs
and is inversely aligned with edge.** `trend_donchian_eth_4h` earned +11.45R
from $16.95 — the best leg by R carries the *smallest* risk — while
`ict_scalp_5m` lost −6.94R for −$26.33 at 2.6× the risk per R. The book is
sized against its own edge.

⚠️ **That is Tier-3 and no change is proposed here.** It is also small-n: 41
trades, six legs, one of them n=1, over 30 days, and `$/R` is a ratio of leg
aggregates rather than a per-trade risk. Filed for a sizing unit to take up.

## What this does NOT do

- **It changes nothing.** `/api/bot/performance` is `src/web/` — Tier-2 — and is
  one of the three dashboard routes MI-278 U12 named as untouchable by a
  research unit.
- **It does not say the row was wrong when filed.** On 2026-09-06 the stored-stop
  basis was in use and the decomposition was correct then. MI-144 landed the
  same day. What is wrong is the criterion, and applying the remedy *now*.
- **It grades one window.** A payload where `storedStop > 0` would flip
  `mechanism_state` to `can_operate`, and the module reports that in preference
  to any benign explanation — a real available cause is not displaced by a
  convenient one.
- **It does not resolve `pnlCoverage: 0.7561`.** 10 of 41 rows carry `estimated`
  PnL, and R is computed on that PnL. `rCoverage: 1.0` reports full *R*
  coverage over a PnL that is 24% unmeasured — a separate asymmetry, not
  measured here.
