# E46 — `PI-20260922-E41-0006` CONFIRMED: `qqq_trend_long_1d`'s 3.56R trail-decay arm is inert once the harness models the leg's take-profit

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> (The MEASUREMENT below was taken and cross-checked on 2026-09-22 by the session that wrote it; `unknown` is this register's statement about independent REVIEW, which is a different question and the same status every sibling under `docs/research/` carries.)

**The prediction was registered IN ADVANCE**, by the E41 lane, before this run
existed — `PI-20260922-E41-0006`, `clears_when`:

> *"The expected result, stated in advance so it is falsifiable: with the TP
> modelled the arm cannot fire, so removing it should be a NO-OP and the two
> should agree. If they do not, the inert-by-construction reasoning is wrong and
> the strip in PR #12737 needs re-opening."*

**It agrees exactly.** The delta is `0.0000` — not small, identical: same trade
count, same outcome mix, same pooled net-R.

## The 2×2, MEASURED 2026-09-22

Harness `scripts/backtest_trend.py`, invoked through
`regime_debt_matrix.build_harness_cmd` so every other parameter is the leg's own
live config. Feed: Yahoo `QQQ` 1d, 365 rows, fetched by
`regime_debt_matrix._fetch_csv`. Runs committed beside this file.

| arm | `trail_decay_arm_r` | TP modelled | n | `net_total_r` | outcomes |
|---|---|---|--:|--:|---|
| **A** | 3.56 | no | 8 | **2.0753** | trail_stop 2 · stop 5 · timeout 1 |
| **B** | *stripped* | no | 5 | **6.7671** | trail_stop 2 · stop 2 · timeout 1 |
| **C** | 3.56 | **yes** | 8 | **5.4666** | take_profit 3 · stop 3 · trail_stop 1 · timeout 1 |
| **D** | *stripped* | **yes** | 8 | **5.4666** | take_profit 3 · stop 3 · trail_stop 1 · timeout 1 |

* **A → B (no TP modelled): +4.6918R, and n falls 8 → 5.** This reproduces the
  pair `PI-20260922-E41-0006` reported (1.9389 → 6.6307, +4.69R) — same sign,
  same magnitude, same mechanism. The span differs by one day (this run's 365d
  window ends 2026-09-22), which is why n is 8 rather than 7.
* **C → D (TP modelled at the leg's declared `tp_r: 3.0`, with the 9.9% venue
  cap): 0.0000R.** Byte-identical outcome mix. The arm cannot fire.

**Why.** `tp_r_effective_median` is **3.0** across all 8 trades in C and D
(`tp_r_effective_n: 8`), so the binding ceiling on this leg is the 3.0R target,
not the ~9.9% clamp — and 3.0R < the 3.56R arm. Since-entry MFE can never reach
the arm, so arming it is unreachable and removing it changes nothing.

## What this settles, and what it does not

* **`PI-20260922-E41-0006`: CONFIRMED.** The *inert-by-construction* reasoning
  behind PR #12737's `qqq` strip holds on the harness, not only on the `cap_R`
  arithmetic the PR argued it from. **#12737 does not need re-opening on this
  evidence.** (It is still an open **draft**, `landing: hold`,
  `hold_reason: tier_2_3_needs_approval` — a Tier-3 config change, and nothing
  about this measurement changes who approves it. The arm is still on `main`,
  `config/strategies.yaml:1825`.)
* **The +4.69R in the evidence corpus is a harness-fidelity artifact, now
  measured rather than argued.** Arm A is the basis every committed
  trend/pullback record rests on: the harness passes no `--tp-cap-pct`, so it
  models no take-profit at all, and in that world MFE runs free and the 3.56R
  arm *does* fire, costing three stop-outs. Do not read +4.69R as a benefit of
  the strip.
* **It does NOT establish that 5.4666R is this leg's edge.** Arms C and D turn
  the TP on for *this comparison only*; the committed record is still measured
  on the uncapped basis, and re-basing the corpus is
  `PI-20260922-PJVBLXLA-0001` / `BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`,
  deliberately not done here. `n = 8` is also below
  `build_strategy_evidence.MIN_TRADES_FOR_POOLED` (10), so none of these four
  numbers is a basis for a verdict about the leg — the comparison **between**
  them is what this document measures.

## Reproduce

```bash
pip install yfinance                      # absent from a bare sandbox; the fetch needs it
python3 - <<'EOF'
import sys, os, json, subprocess, yaml
sys.path.insert(0, "scripts/research")
import regime_debt_matrix as rdm
WD = "/tmp/e46ab"; os.makedirs(WD, exist_ok=True)
live = dict(yaml.safe_load(open("config/strategies.yaml"))["strategies"]["qqq_trend_long_1d"])
csv = os.path.join(WD, "qqq.csv")
rdm._fetch_csv(rdm.resolve_feed("QQQ", "1d"), 365, csv)
no_arm = {k: v for k, v in live.items() if k != "trail_decay_arm_r"}
TP = ["--tp-cap-pct", "0.099", "--tp-r", "3.0"]
for label, cfg, extra in (("A_noTP_arm3.56", live, []), ("B_noTP_noarm", no_arm, []),
                          ("C_TP_arm3.56", live, TP),  ("D_TP_noarm", no_arm, TP)):
    jout = os.path.join(WD, f"{label}__bt.json")
    argv, *_ = rdm.build_harness_cmd("qqq_trend_long_1d", cfg, "trend", csv, "1d",
                                     os.path.join(WD, f"{label}__trades.jsonl"), jout)
    subprocess.run(argv + extra, check=True)
    d = json.load(open(jout))
    print(label, d["total_trades"], d["net_total_r"], d.get("by_outcome"),
          "tp_eff_median=", d.get("tp_r_effective_median"))
EOF
```

⚠️ **Strip the arm from the CONFIG, never by appending a second
`--trail-decay-arm-r`.** `build_harness_cmd` already forwards the config's value,
so an appended flag is a duplicate and both halves of the comparison keep the
arm. The first attempt at this A/B did exactly that and returned A == B and
C == D — a vacuous pass that looked like a confirmation. The assertion
`sum(1 for a in argv if a == "--trail-decay-arm-r") <= 1` is what caught it.
