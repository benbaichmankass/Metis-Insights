# M20 U42 — PB-20260821 names the wrong leg: its own justification is gone, and the leg whose headline looks worst has a measured record that is a GAIN

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U42** · RESEARCH lane · Tier-1 · works `PB-20260821-SLV-TREND-1H-ZERO-WINS-IN-13`, the oldest open `high` row in [`docs/claude/performance-review-backlog.json`](../claude/performance-review-backlog.json) bearing on trade management.

Instrument: [`scripts/research/kill_case_admissibility.py`](../../scripts/research/kill_case_admissibility.py)
(27 self-test controls; 14 pytest controls in
[`tests/test_kill_case_admissibility.py`](../../tests/test_kill_case_admissibility.py);
10 planted defects, 10 caught by a named control).

---

## One sentence

The row is filed on two claims about `slv_trend_1h` — *0 wins in 13* and *"the only large loser whose loss is well MEASURED (coverage 0.77)"* — and **both are false today**; meanwhile `htf_pullback_trend_2h`, one of the other three legs the row names, has a headline paper loss of **−$4,459.45** whose **admissible part is a +$2,130.53 GAIN**.

## Populations, stated

Two pulls from the live API (`https://ict-bot.duckdns.org`, trader `git_sha f2335be98`), read 2026-09-13T06:34Z:

- **30d** — `/api/bot/performance?window=30d`, since `2026-08-14T06:34:28Z`.
- **lifetime** — `?window=all`. ⚠️ **A `90d` request returns this same window.** `_WINDOWS` holds only `24h/7d/30d/all` and `performance.py:1202` normalizes anything else to `all`. The response *echoes* the normalized token, so a reader who checks is not misled — but a reader who assumes 90d is quoting lifetime.

Positive control on every block, so an absent leg cannot be confused with a dead reader: 30d returned **6 / 35 / 35 / 14** strategies for real-money(top) / demo / paper / paperPortfolio; lifetime returned **12 / 46 / 46 / 14**. `demo` and `paper` are the same numbers under two names.

Journal detail from `/api/diag/journal?table=trades&limit=1000` (a **tail**, not the lifetime).

## What "admissible" means, and why it is the right filter for a kill

`/api/bot/performance` publishes `totalPnl` beside `totalPnlMeasured`, and the second sums only **MEASURED + ESTIMATED** rows — `src/web/api/routers/performance.py:490`, verified in code, not inferred from the name. Fabricated and unverified rows are in the first and not the second.

So a leg can carry a large headline loss whose defensible part is a gain. **A kill argued off such a headline is argued off rows that are not measurements.** The gate grades exactly that and nothing else — it never proposes a disposition, because retiring a leg is Tier-3.

Its states are not collapsed: `no_admissible_rows` is *we could not look* and is deliberately **not** folded into `loss_inverts`, which would assert the loss is unreal; `absent_from_block` is *0 closes* and is never rendered as a 0.0 PnL. The floor is **5 admissible rows** to carry a sign — a chosen number, not a tuned one, and a parameter so a reader can see what the verdict rests on.

## Result

**30d — almost nothing is gradeable, which is itself the finding.**

| leg | block | verdict | n / admissible | headline | admissible |
|---|---|---|---|---|---|
| `slv_trend_1h` | paper | `insufficient_admissible_n` | 4 / **2** | −735.11 | **+49.56** |
| `slv_trend_1h` | real money | `absent_from_block` | — | — | — |
| `htf_pullback_trend_2h` | **every block** | `absent_from_block` | — | — | — |
| `tlt_pullback_1h` | paper | `insufficient_admissible_n` | 3 / 3 | +26.93 | +26.93 |
| `tlt_pullback_1h` | real money | `absent_from_block` | — | — | — |
| `eth_pullback_2h` | real money | `insufficient_admissible_n` | 4 / 4 | **−0.33** | −0.33 |
| `eth_pullback_2h` | paper | `loss_survives` | 8 / 8 | −386.90 | −386.90 |

**Lifetime — three losses survive, one inverts.**

| leg | block | verdict | n / admissible | headline | admissible | carried by unmeasured |
|---|---|---|---|---|---|---|
| `slv_trend_1h` | paper | `loss_survives` | 23 / 12 | −6,107.00 | −4,214.86 | −1,892.15 |
| `eth_pullback_2h` | **real money** | `loss_survives` | 15 / 15 | **−15.49** | −15.49 | 0.00 |
| `eth_pullback_2h` | paper | `loss_survives` | 20 / 17 | −4,289.49 | −6,225.94 | **+1,936.44** |
| `tlt_pullback_1h` | paper | `loss_survives` | 11 / 7 | −2,998.00 | −1,654.96 | −1,343.04 |
| **`htf_pullback_trend_2h`** | **paper** | **`loss_inverts`** | 23 / **6** | **−4,459.45** | **+2,130.53** | **−6,589.98** |
| `htf_pullback_trend_2h` | real money | `loss_survives` | 6 / 5 | −4.83 | −4.03 | −0.80 |

## The four legs as they actually stand

Read from `config/strategies.yaml` and `config/accounts.yaml` at HEAD, with the flips verified by reading the file **at the commit** rather than taking the commit subject's word:

| leg | execution | accounts | note |
|---|---|---|---|
| `slv_trend_1h` | **shadow** since **2026-08-24** (`6f7c14f8`, `live`→`shadow` confirmed at `^` and at the commit) | 3 paper | never on real money |
| `htf_pullback_trend_2h` | **shadow** since **2026-08-23** (`9544ced3`, same check) | `bybit_1` paper | its lifetime real-money rows predate `04f66d0da`, **2026-07-16**, which demoted it off `bybit_2` |
| `eth_pullback_2h` | **live** | `bybit_1`, **`bybit_2` REAL MONEY**, `bybit_portfolio` | the only leg of the four actually trading real money |
| `tlt_pullback_1h` | **live** | `alpaca_paper`, `alpaca_portfolio`, **`alpaca_live` REAL MONEY** | see below |

**`tlt_pullback_1h` is "live on real money" in name only.** Of its 57 rows in the journal tail, **19 are on `alpaca_live` and all 19 are `rejected`** — zero closed, spanning 2026-08-18 → 2026-09-11. That is U39's finding (`alpaca_live` books every dispatch dry) reproduced from the other side.

⚠️ **The non-closed rows on the shadow legs are `status: rejected`, which is the execution gate WORKING** — 31 of slv's 35 and 8 of 8 for htf. I checked this before reporting it, because 31 un-closed rows on a shadow leg reads like a phantom-position defect and is not one.

## Why the row's own justification is gone

The row singles out `slv_trend_1h` because it was *"the one whose loss we can actually stand behind"* at `pnlCoverage 0.77`. Today that leg reads **coverage 0.00 at 30d** and **0.26 at lifetime**, and at 30d the part of its PnL anyone can stand behind is **+$49.56** — a gain — against a −$735.11 headline carried by 2 unmeasured rows.

Its lifetime loss **does** survive (−$4,214.86 over 12 admissible rows), so the row's *instinct* was not wrong. But the **stated reason** for preferring it over the other big losers no longer holds, and the row has been re-measured by hand three times without that being noticed.

⚠️ **And the row's premise now points away from the evidence.** `htf_pullback_trend_2h` is the leg whose headline looks worst *and* whose admissible record is positive *and* which U34 found carries the best offline number of the four (`net_r_oos +2.7261` over n=79 at FAITHFUL fidelity). Two independent lines now say the same thing about it. **This is not a promotion proposal** — that is Tier-3 and is not made here — it is a warning that a kill argued on this row's premise would be arguing against the only positive evidence in the set.

## What is actually owed

`PB-20260821`'s `resolution_criteria` wants *a current M7 packet AND an operator decision per leg*. U34 supplied the packet half and **proved the badge cannot arrive**: `decide()` gates KILL/DEMOTE on ≥20 **live** closes, and both shadow legs grade `structurally_ungradeable` with `days_to_floor` NULL — *"waiting cannot grade it."*

So the decision cannot come from the M7 gate, and waiting will not produce it. It is recorded as `DEC-20260913-FOUR-LEG-DISPOSITION` on
[`WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER`](../claude/work/objects/WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER.yaml).

**No parameter, roster or execution value is changed by this unit.**

## Reproduce

```
curl -sS "https://ict-bot.duckdns.org/api/bot/performance?window=30d" -o perf.json
python3 scripts/research/kill_case_admissibility.py --performance perf.json \
    --legs slv_trend_1h,eth_pullback_2h,htf_pullback_trend_2h,tlt_pullback_1h
```
