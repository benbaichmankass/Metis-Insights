# Do we need a new data source for the roster work?

**2026-08-29 · operator:** *"if we need to find new sources of data, thats fine - as long as
it reliable for our needs"* · **Tier-1: reads + one catalogue probe. Nothing built, no config
touched, no map edited.**

---

## Short answer: probably not. The deep lane already exists and is already wired.

`scripts/ops/fetch_backtest_candles.py --source` accepts **five** lanes:
`auto` · `bybit` · `binance_vision` · `yfinance` · **`dukascopy`**.

**Dukascopy is the deep-history lane**, and it carries US ETF CFDs. That is the plausible
origin of the trainer's hourly files reaching back to **2017** (12,416–13,854 bars) when
yfinance caps at **724 days** — a gap this session first mis-diagnosed as a hard ceiling
(corrected in `alpaca-roster-phase1-data-depth-2026-08-29.md`).

## What is already adjudicated

`scripts/ops/dukascopy_instruments.py` is **the one owner** of "which Dukascopy instrument
serves which of our symbols" — and it is deliberately an **adjudication, not a lookup**. Its
own history is the reason: a raw substring probe matched **`MHG` → a Norwegian salmon farmer**
(`INSTRUMENT_NORWAY_MHG_NO_NOK`; ours is CME Micro Copper) and *missed* `XAUUSD` because an
underscore defeated the search. **Two errors in opposite directions out of eighteen.**

**15 symbols mapped**, and every current roster symbol is covered:

| relation | symbols |
|---|---|
| `same_ticker_cfd` | SPY · QQQ · GLD · TLT · IWM · IEF · SLV · USO · GDX |
| `proxy` | **SPLG**→SPY *(same index, different share price + expense ratio)* · **IAUM**→GLD *(same underlying, different vehicle)* · **SCHA**→IWM ⚠️ *(a **DIFFERENT INDEX**: DJ US Small-Cap vs Russell 2000)* · MES→S&P index · MGC→XAU spot |
| `relaxed` | XAUUSD |

The module carries `relation` alongside every instrument precisely so a consumer cannot
silently treat a **proxy** as the same thing — collapsing those is the semantic substitution
`diagnostic-provenance-guard` sub-class A exists to stop.

⚠️ **I described SCHA as an IWM proxy earlier today without the different-index qualifier.
The map is more careful than I was.**

## The leveraged question was already ruled on — and the ruling is NARROWER than it looks

`REFUSED` carries `QLD` and `TQQQ`:

> *"ProShares Ultra QQQ is 2x with a **DAILY LEVERAGE RESET**, so its path is not 2x the QQQ
> path"*

⚠️ **That refusal is about substituting QLD's data for QQQ's — NOT about trading QLD as its
own instrument.** Those are different questions and conflating them would be exactly the
substitution the module is built to prevent. The operator's position (2026-08-29) — *"Leverage
ETFs are fine as long as we have a strategy that we know we'll trade those things correctly…
what matters is having those symbols trading on strategies that effectively produce PNL"* —
is untouched by this entry. It means only that **QLD needs its own data, not QQQ's**, which is
also what the system's `signal symbol == order symbol` invariant already requires.

`REFUSED` is a first-class state, deliberately distinct from absence: *"'we decided not to' and
'we have no entry' are different facts and only the first one is a decision anybody made."*

## The actual gap: the inverse candidates are UNKNOWN, not refused

All nine resolve `state=unknown` — *"has not been adjudicated against the Dukascopy catalogue"*:

`SH` · `PSQ` · `RWM` · `DOG` · `TBF` · `TBX` · `DGZ` · `SEF` · `EUM`
(plus `VTWO` and `SCHX` on the long side).

**Nobody has looked.** And these are precisely the tickers where a substring probe is most
dangerous — `SH` and `DOG` are two and three characters and would match a great deal of a
1,388-line catalogue.

So the next step is an **adjudication, not a source hunt**. Dispatched
`dukascopy-coverage-probe.yml` with filter **`ETF_CFD_US`** — dumping the whole US-ETF-CFD
population rather than grepping per ticker, because the probe's own contract is that it *"dumps
the catalogue and refuses to declare coverage"*; the judgement is a human step on top.

## THE PROBE RAN. Dukascopy carries NONE of them. — measured 2026-08-29

Run [33248107794](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33248107794),
job 99088981314, ref `2f73c51`.

```
probing 16 symbols (from input): SH, PSQ, RWM, DOG, TBF, TBX, DGZ, SEF, EUM,
                                 SCHX, VTWO, SPLG, IAUM, SCHA, QLD, TQQQ
probed=16  exact=1  relaxed=0  unmatched=15
```

**Population: all 16 candidates, against the WHOLE Dukascopy catalogue.**

| bucket | symbols |
|---|---|
| same-ticker US ETF CFD | **(none)** |
| hit, adjudicated a COINCIDENCE | `SH` |
| no match in either form | `PSQ` `RWM` `DOG` `TBF` `TBX` `DGZ` `SEF` `EUM` `SCHX` `VTWO` `SPLG` `IAUM` `SCHA` `QLD` `TQQQ` |

`SH`'s six hits are Sherwin-Williams (`INSTRUMENT_US_SHW_US_USD`), Cognizant
(`INSTRUMENT_US_CTSH_US_USD`), a UK equity (`INSTRUMENT_UK_SHP_GB_GBX`), a
French/British ETF (`INSTRUMENT_ETF_CFD_CSH2_*`) and a crypto
(`INSTRUMENT_VCCY_DSH_USD`). The probe flagged every one `NEEDS_ADJUDICATION`
and printed *"SH has NO same-ticker ETF CFD"* on its own. This is the
two-letter-substring hazard the probe was rebuilt around, behaving as designed.

### The denominator, because absence is the claim being made

I said I would not declare absence without establishing what was searched, so:
**these six hits ARE the positive control.** They span four different instrument
families — `ETF_CFD_*`, `UK_*`, `US_*`, `VCCY_*` — which demonstrates the
per-symbol probe searches the entire catalogue and is not scoped to
`ETF_CFD_US`. So `unmatched` here means *absent from the whole venue*, not
*absent from one family*.

⚠️ **My earlier worry was itself misdirected, and in a way worth recording.** I
believed the `ETF_CFD_US` filter had narrowed the probe. It had not — `filter`
only ever scoped the catalogue **dump**, and the per-symbol probe was always
catalogue-wide. But the earlier run was still not evidence about these tickers,
for a different reason: it never probed them at all. Two wrong readings in
opposite directions of the same log, which is what a job with two steps and one
misleadingly-named input produces. Fixed at the source in `2f73c51`: the probed
set is now an input and is echoed in the report.

## So: yes, this became a genuine new-source question — and the answer is that we do NOT need one

Dukascopy is out. But the constraint that sent us looking is **narrower than
"no data"**, and collapsing the two would be an expensive mistake:

- The blocker is the yfinance **~730 d intraday** cap. It binds on **1h** legs.
- **yfinance daily is uncapped**, and all 16 candidates are **already mapped**
  in `ml/datasets/adapters/yf_symbols.py::_DEFAULT_TICKER_MAP` (74 entries) —
  no map edit, no new vendor, no new lane.

So an inverse-ETF or small-share **daily** leg is backtestable on the free lane
we already have. Only an **hourly** one on these tickers has no free deep lane.

⚠️ **"Mapped" is not "usable", and the lane already has a scar proving it** —
`SPLG` returns `UNSOUND(thin 1b; stale 43d)`. Span and soundness are separate
facts from coverage, so they are being MEASURED (`yfinance-lane-proof`, D,
3650 d) rather than assumed. No roster claim rests on this section until that
run reports.

## What would make this a genuine new-source question

*(Written before the probe ran; the trigger it names has now FIRED — Dukascopy does not carry
them. Kept as the standing test rather than rewritten, so the criterion is visibly the one
that was set in advance.)*

The escape hatch it anticipated turned out not to be needed: the free daily lane covers the
candidates, so **no vendor decision is due**. A vendor question only returns if the roster
work later requires an **hourly** inverse/small-share leg — and at that point the honest
options are a paid intraday vendor, or dropping the hourly variant of that leg. **I would
bring both rather than pick one**, since a data vendor is a standing dependency, not a
research detail.

⚠️ Note what a new source would have to satisfy, from the existing lanes' own scars: a declared
**relation** to what we actually trade (not just a matching ticker), a measurable **span** (the
`dukascopy_span_probe` exists because depth is a separate fact from coverage), and a
**soundness** check (the yfinance lane already returns `UNSOUND(thin 1b; stale 43d)` for SPLG —
a source that answers is not a source that is usable).
