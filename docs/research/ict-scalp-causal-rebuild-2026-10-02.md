# ict_scalp evidence rebuilt on the causal HTF harness — 2026-10-02

> **Doc status:** `live` · category `research` · lane ICT-EVIDENCE-REBUILD · rule `RULE-ICTSCALP-CAUSAL-REBUILD` (`research/queue/RQ-20261002-001.yaml`, registered before any run)

Population: 7 measured `ict_scalp_*` records, 365d Binance feed (window ends 2026-09-26..2026-10-01), 4 contiguous folds, cost stack fee 7.5 + slippage 3.0 + funding 1.0 bps, leg config as live. Bar: net_r_oos > 0, expectancy > 0, strict majority of folds positive, n >= 88 (d=0.3 at alpha 0.05 / power 0.8); n < 88 is UNDERPOWERED, escalated to 730d (pre-registered), never lowered.

| leg | old n / net R / folds+ | new n (365d) / net R / folds+ | 365d verdict | 730d escalation | mandate S1→S2 (bybit_2) |
|---|---|---|---|---|---|
| ict_scalp_xrp_15m | 129 / +4.08 / 3 of 4 | 76 / +4.07 / 2 of 4 | UNDERPOWERED | n=132, −4.60, 1 of 4: FAIL | REFUSE R-FOLDS |
| ict_scalp_eth_15m | 117 / +9.12 / 2 of 4 | 78 / +1.32 / 2 of 4 | UNDERPOWERED | n=151, +0.43, 2 of 4: FAIL | REFUSE R-FOLDS |
| ict_scalp_sol_15m | 132 / +4.86 / 2 of 4 | 84 / −5.57 / 2 of 4 | UNDERPOWERED | n=158, −8.64, 1 of 4: FAIL | REFUSE R-B1-C4 |
| ict_scalp_avax_5m | 257 / +6.49 / 2 of 4 | 177 / −37.12 / 0 of 4 | FAIL | n/a | NEEDS-DATA R-EXECUTION-SHADOW |
| ict_scalp_5m | 193 / −9.48 / 1 of 4 | 145 / −29.71 / 0 of 4 | FAIL | n/a | NEEDS-DATA R-EXECUTION-SHADOW |
| ict_scalp_sol_5m | 259 / −8.86 / 1 of 4 | 190 / −22.72 / 0 of 4 | FAIL | n/a | NEEDS-DATA R-EXECUTION-SHADOW |
| ict_scalp_xrp_5m | 243 / −0.92 / 1 of 4 | 169 / −27.00 / 0 of 4 | FAIL (fidelity: approximate) | n/a | REFUSE R-B1-C4 |

"Old verdict" under the same rule: xrp_15m would have been PASS-eligible on the evidence clauses (n=129, +4.08R, 3 of 4 folds); the other six already failed the fold or net clause.

Caveats, stated rather than implied:
- The three 15m escalation records are not the committed records; they sit in `comms/strategy_evidence/runs/2026-10-02-730d/` and the 365d record stays the superseding one. The 730d window includes the prior year, so those results are about a longer period, not a corrected one.
- avax_5m / 5m / sol_5m resolver verdicts stop at the shadow gate before the record is read; their rebuilt records fail on net and folds regardless.
- `param_selection_provenance` remains `not_established` on every record.
- mgc_15m (`harness_failed`, Yahoo 15m window) was not among the 7 and was not touched.
- Old records: `comms/strategy_evidence/superseded/<leg>__pre-causal-htf.json`.
