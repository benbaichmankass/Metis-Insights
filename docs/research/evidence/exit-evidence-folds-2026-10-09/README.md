# EXIT-EVIDENCE-FOLDS — full-rule evidence records (2026-10-09)

> **Doc status:** `unknown` · category `evidence` · measured `2026-10-09` · lane EXIT-EVIDENCE-FOLDS (session_01VsnuLCmU96p2DknkgUcEdN) for manager session_01Uq7MrjPQDRWqbhCjwuMava

Question: do the e5-passing exit cells of RQ-20261007-040 / -042 / -043 clear their FULL
pre-registered rules once the missing walk-forward / attribution evidence is pulled?

**Source of every number:** the `exit-lever-sweep-<leg>` Actions artifacts of the runs named in
each record (`verdicts.json` copied verbatim to `raw/`). The corpus branch
`claude/m20-sweep-corpus` stops at the 2026-10-05 run, so these runs' fold rows were never
merged there; the artifacts (retained to 2027-01) are the only copy. Harness, split, and cost
stack (fee 7.5 bps + slippage 3.0 bps + funding 1.0 bps/window) are the sweep's, unchanged.
Nothing was re-run; nothing is re-derived.

| unit | leg | cell | n OOS | folds (raw / effective) | verdict |
|---|---|---|--:|---|---|
| 040 | ada_pullback_2h | vt_hot90_t2.5 | 49 | 4/6 / 4/6 | **PASS (all clauses)** |
| 042 | trend_donchian_eth_4h | adx20_k0_t2.5 | 49 | 4/6 / 4/6 | NEEDS_DATA (attribution) |
| 042 | trend_donchian_xrp_4h | adx20_k0_t2.5 | 31 | 6/6 / **2/6** | **FAIL** (folds: 4 of 6 are inert) |
| 042 | trend_donchian_xrp_4h | adx25_k0_t2.5 | 31 | 6/6 / 4/6 | NEEDS_DATA (attribution) |
| 042 | trend_donchian_eth_prop | adx25_k0_t1.8 | 49 | 4/6 / 4/6 | NEEDS_DATA (attribution) |
| 042 | trend_donchian_eth_prop | adx25_k3_t1.8 | 49 | 5/6 / 5/6 | NEEDS_DATA (attribution) |
| 043 | trend_donchian_eth_4h | ms12_a20 | 49 | 3/6 / 3/6 | **FAIL** (folds) |
| 043 | trend_donchian_eth_4h | ms12_a25 | 49 | 3/6 / 3/6 | **FAIL** (folds) |
| 043 | trend_donchian_eth_prop | ms8_a25 | 49 | 3/6 / 3/6 | **FAIL** (folds) |

Reading notes
- 040's rule asks for e5 pass, base OOS >= 25, the registered cell, and the fold count quoted: all hold; 4/6 quoted.
  The 2024 and 2025 folds are the losing ones (d_net_R -3.9976, -0.8186); 4/6 is a bare pass, not a strong one.
- 042's attribution clause (tightened exits must net non-negative) is **not readable** from any committed record:
  per-trade `trail_adx_tightened` exists only under `--emit-trades` and the frames sit on the trainer.
  Unread is not passed, so no 042 cell is proposed. Data task: PI-20261009-NKGUCEDN-0001.
- 042 xrp_4h adx25_k0_t2.5: folds 2022 and 2024 are inert (delta 0.0 / 0.0), so the 6/6 raw tally is 4/6 effective. Its IS gain
  (+2.482R) is almost all fold 2021 (+2.4689). Flag for the attribution read.
- 043 failed on folds before base-(b) (stale-swapped) became relevant; eth_prop ships stale12, so (b) would have applied there.
- Both dispatches of 042 and of 043 returned identical measurements (same fields in the e5 records), so a re-queue adds no information.
