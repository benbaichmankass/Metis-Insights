# Does one `pullback_frac` generalise across the fleet?

**No.** Neither live value generalises, and neither does any alternative.

**Date:** 2026-08-24 · **Evidence:** run
[`32767426410`](https://github.com/benbaichmankass/Metis-Insights/actions/runs/32767426410)
on `main` @ `1bf1ced`, **19 of 19 legs reported**.
**Tier-1, observe-only. `config/strategies.yaml` untouched — nothing applied.**

## The criterion, which was fixed before any result existed

`VERDICT_RULE` and `MIN_LEGS_FOR_A_CROSS_LEG_CLAIM` are **constants in
`pullback_frac_cross_leg_sweep.py`**, not a paragraph written after the numbers
came back — the donchian § 6.0b lesson that a shortlist chosen after its
candidates are measured is a shortlist chosen by the argmax.

> A value **GENERALISES** within a stratum iff it is the argmax `net_R` on
> **> 50%** of that stratum's legs. At or below that the stratum is **SPLIT**.
> A stratum with fewer than **5** legs is **UNDERPOWERED** and gets no verdict.

Grid `{0.33, 0.5, 0.618, 0.75}`; fees **7.5 bps roundtrip** charged on every
run (`pullback_frac` is an ENTRY gate, so it changes the trade population and
turnover — the per-exit fee does not cancel between arms as it does in a lever
replay).

## Result

| stratum | legs | argmax votes | verdict | median spread | max spread | flat legs |
|---|---|---|---|---|---|---|
| `full` | 15 | `0.33`:2 · `0.5`:3 · `0.618`:4 · **`0.75`:6** | **SPLIT** | 18.14 R | 56.37 R | **0/15** |
| `capped_730d` | 4 | `0.33`:3 · `0.75`:1 | **UNDERPOWERED** | 19.09 R | 43.22 R | **0/4** |

⚠️ **The strata are not combined**, and there is no input that makes them
combine. A 730 d hourly leg and a decade-long daily leg do not share a
denominator.

## What this says

1. **No value generalises on the powered stratum.** The leader, `0.75`, takes
   **6 of 15 = 40.0%** — below the bar. This is precisely the case the
   fixed criterion exists for: `0.75` is the plurality, and an argmax-chaser
   would have called it the winner.

2. **The two values the fleet actually ships take 7 of 15 between them**
   (`0.5`:3, `0.618`:4) — under half. So the live config is *not* supported as
   a universal choice. It is also **not refuted as a per-leg choice**: this
   sweep measures whether ONE value fits ALL legs, not whether each leg's own
   value fits that leg.

3. **`0.75` is the most frequent single argmax and NO leg declares it.** Worth
   noting, and **not** worth acting on from this evidence — 40% is the number
   that decides, not the ranking.

4. **The surface is not flat anywhere — 0 of 19 legs.** Median spread
   **18.14 R**, max **56.37 R**. So the parameter *matters* per leg, materially;
   there is simply no universal best. That combination — a live axis with real
   per-leg effect and no cross-leg winner — is the substantive finding.

## What this does NOT say

- ⚠️ **It is not a per-leg tuning result.** Each leg's argmax here is one number
  from one full-history run with **no walk-forward and no IS/OOS split**. A
  per-leg `pullback_frac` change is **Tier-3** and would need the usual
  walk-forward evidence, which this sweep does not produce.
- It says nothing about interactions with any other parameter; only
  `pullback_frac` moved.
- **2 of 19 legs are measured on a PROXY series** (`mgc_pullback_1d` → `GC_F`,
  `mhg_pullback_1d` → `HG_F`), which is the fleet-wide convention because the
  proxy is the deeper series. Reported per leg as `proxy: true` so a cross-leg
  count is never read as if every leg were measured on its own instrument.

## Provenance note — the first two runs were wrong, and how

Recorded because the failure was silent both times:

- **Run 1** (`32762934189`) produced a correct answer that **could not be read**:
  the aggregate wrote only to `$GITHUB_STEP_SUMMARY`, which is not exposed
  through the Actions API. Fixed with `tee -a` (#10231).
- **Run 2** (`32765331857`) reported **17 of 19 legs** — `mgc_pullback_1d` and
  `mhg_pullback_1d` dropped out on **green** jobs, because the fetch wrote
  `data/MGC_1d.csv` while `resolve_data` (proxy-first by default) wanted
  `data/GC_F_1d.csv`. Fixed by planning the proxy spelling (#10233).

Both are why this file quotes run 3 and no earlier number.
