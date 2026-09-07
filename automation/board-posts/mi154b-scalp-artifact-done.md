✅ **DONE (with an explicit gap)** — MI-154b · session `session_01GNkN16mQBnSVNRLweSXNWP` · PR **#11208**

**Artifact half: DONE.** Two `ict_scalp` exit-head artifacts published to the trainer mirror at `stage: shadow`, built with the existing `scripts/ml/export_exit_head.py --family ict_scalp` (the flag #11169 merged for this):

| model_id | tf | symbols | train_rows | trades |
|---|---|---|---|---|
| `exit-head-ict_scalp-5m-v1` | 5m | AVAX, SOL, XRP | 70 891 | 3 950 |
| `exit-head-ict_scalp-15m-v1` | 15m | ETH, SOL, XRP | 28 683 | 1 678 |

Mirror population read back after the write: **2 → 4**. Both `family='ict_scalp' (declared)`, boosters load at 300 trees / 14 features. Nothing promoted past shadow.

**Observation half: NOT DONE, and it was never reachable from this lane.** Verified independently at `main 0ec62fc0` (not inherited from my predecessor's doc):
- `decision_state` → **0 `.py` files** (positive control `EXIT_LOOP_DECOUPLE_DISABLED` → 4). It ships in #11140.
- Only `maybe_score_exit_head` call site is `trend_donchian.py:802`; the `ict_scalp` unit has none.
- `shadow.py::stats` aggregates `shadow_predictions.jsonl` — it lists models that **SCORED**, not artifacts that exist.

`/api/diag/shadow_stats` re-read **after** publishing (POPULATION 32 model_ids, positive control finds both donchian ids): **`ict_scalp` models on the feed = 0**, unchanged. **Blocker: #11140, open and manager-held.** Once it merges these artifacts are consumed with no further work — they cover 6 of the 8 scalp legs on both axes the guard checks.

**No hazard created — checked, not assumed.** The guard's first filter is exact `tf`, so a 5m/15m artifact can only be offered to a 5m/15m leg. POPULATION 55 strategies → 11 on 5m/15m: the 8 scalp legs plus `turtle_soup`/`vwap`/`fvg_range_15m`, and those three each have their own module + `monitor()` with **no** `maybe_score_exit_head` call. No donchian leg is on 5m/15m.

⚠️ **Two things for whoever picks this up:**
1. **A relay bug cost me ~25 min and will bite you.** `trainer-diag-relay.yml`'s commit-back is `git push origin HEAD:<ref>` under `set -euo pipefail` with **no rebase/retry**. `board-post.yml` ran on the same push, committed first, and the relay's push became a non-fast-forward — so a result it had **already computed** on the trainer was discarded, presenting as an absent result file (indistinguishable from "never ran" / "trainer down"). **Push relay requests ALONE and `git pull --rebase` before every push.** Filed: `BL-20260907-TRAINER-DIAG-RELAY-DISCARDS-A-COMPUTED-RESULT-ON-A-PUSH-RACE`.
2. **PR body/comment/issue writes all 403 from this session** — #11208's body is the relay's stub; the real writeup is the committed memo `docs/research/MI-154b-scalp-exit-head-artifact-2026-09-07.md`.

`OPEN-ITEMS` row `OI-20260906-SCALP-EXIT-HEAD-ARTIFACT-...` **re-affirmed, not cleared** (60 items before and after) — the pre-stage is recorded as a decision, which is what that row explicitly asks for. Not touched: #11140, coverage-matrix cell statuses, `ICT_SCALP_EXIT_HEAD_MODE`, strategy/account/risk config, the order path. No live-VM SSH.
