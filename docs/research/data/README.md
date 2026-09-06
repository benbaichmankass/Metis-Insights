# Working scripts for `real-money-0for13-attribution-2026-09-06.md`

Read-only analysis scripts. Journal + order_packages pulled live from
`/api/bot/db/table/*`; venue candles obtained through the `trainer-vm-diag`
relay (issues #11107–#11110) because Bybit's API is geo-blocked from a
Claude sandbox (HTTP 403, CloudFront country block).

- `measure.py` — R-provenance contamination over the whole journal (Deliverable 1)
- `win.py` — windowed `bybit_2` performance + exit-path split
- `base.py` / `base2.py` — lifetime base rates, with and without the retired `vwap` leg
- `streakdate.py` — dated losing streaks; trade-by-trade view of the current one
- `regime.py` — directional-efficiency comparison across the two windows
- `validate.py` — the proxy-validation pass that REJECTED Coinbase spot as an
  adjudicator (fails 4 of 11 known stop-outs). Kept because the negative result
  is the reason the venue relay was used.
- `replay.py` / `cf.py` — the rejected proxy counterfactual (superseded)
- `relay11108.txt` — MAE/MFE from Bybit's own perp candles (the used result)
