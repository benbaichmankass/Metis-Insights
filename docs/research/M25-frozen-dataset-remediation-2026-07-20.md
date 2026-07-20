# M25 — Frozen-dataset per-head remediation memo (2026-07-20, overnight WS-4)

**Status: DECISION PACKET — prepared overnight for the operator's morning
review. No execution has happened from this memo.** Parent backlog item:
`MB-20260720-FCPCV-RETRAIN-NOOP` (docs/claude/ml-review-backlog.json).

## Background (one paragraph)

`scripts/ops/build_trainer_datasets.sh` rebuilds ONLY the `v002` dataset
family nightly (`--overwrite`). Experiment-pinned dataset versions
(`v5xx`/`v9xx`/`v001`) freeze at their build date, yet their manifests sat in
the nightly train roster — so those heads "retrained" nightly on identical
bytes, producing byte-identical artifacts (16 consecutive no-op runs on BTC
fc-pcv) and a fake-perfect `cross_run_stability` reading. The blast-radius
sweep (relay #7077) scoped this to **6 of 29** shadow/advisory models. Two
mitigations already landed 2026-07-20: the **frozen-dataset retrain skip
guard** (`scripts/ops/dataset_unchanged_check.py`, PR #7082) stops the
wasted no-op retrains, and the ETH xa rebuild (relay #7186, running tonight)
addresses the one head whose frozen dataset was also *defective*. What
remains is a per-head product decision: **refresh (unpin/rebuild + retrain +
re-soak) vs accept-frozen (the artifact keeps its live track record)**.

## Decision principle

Under the M25 gate reframe, edge is proven OFFLINE (purged walk-forward
`oos_edge`) and the live soak proves serving MECHANICS. A frozen artifact
with a long clean soak is *internally consistent* evidence — refreshing the
dataset resets the mechanics clock (new artifact ⇒ new soak) and only pays
off if data recency materially moves the edge. So the default is
**accept-frozen for heads with a live track record; refresh via a PARALLEL
sibling** (train on refreshed data under a new manifest id, soak in shadow
alongside, swap only when the sibling matures and beats the incumbent) —
never an in-place unpin of a serving advisory head.

## Per-head decisions proposed

| # | Head (stage) | Pinned ds (staleness) | Proposal | Why |
|---|---|---|---|---|
| 1 | `btc-regime-15m-lgbm-fc-pcv-v1` (**advisory** — drives the live BTC vol gate since 20:01Z) | v520 (~Jul 1, 18.6d) | **Accept-frozen + parallel refresh sibling** (Tier-3 to swap later): build a refreshed fc dataset (v521), train `btc-regime-15m-lgbm-fc-pcv-v2`, let it soak at shadow ≥7d under the M25 gates, swap only on a winning gate-check. | The v520 artifact owns the 16.8d live RG4 0.627 track record and every required gate. An in-place retrain would put an *unsoaked* artifact in the live gate path — strictly worse than the certified one. |
| 2 | `sol-regime-15m-lgbm-fc-pcv-v1` (**advisory** — no authored SOL cells yet, so not yet live-effective) | v530 (~Jul 6, 14.0d) | Same shape: **accept-frozen + parallel sibling** (v531 → `…-fc-pcv-v2`). Lower urgency than BTC — no SOL OFF-cells exist yet (WS-2 packet is the prerequisite). | Same track-record logic; swapping before cells are authored buys nothing. |
| 3 | `eth-regime-15m-lgbm-xasset-v1` (shadow) | v901 (3.1d; **xa_peer2 columns constant/zero — defective, not just stale**) | **Refresh IS the fix — already running tonight** (relay #7186: rebuild cross_asset v001 under current code → rebuild v901 → verify xa cols nonzero → retrain). Restarts its soak clock; matures ~07-27+. | Its frozen dataset was defective (dead SOL-peer block), so accept-frozen is not an option — the artifact's features never matched the live serving computation. |
| 4 | `eth-regime-15m-lgbm-selfonly-v901ctrl` (shadow) | v901 (3.1d) | **Retire to candidate.** It is the experiment CONTROL for the xasset head (self-only features), with weak oos (+0.015). Once the rebuilt xasset head is soaking, the control has served its comparative purpose. | Keeping a weak control soaking spends compute for no decision value (same rationale as tonight's sol-v1 demotion). |
| 5 | `execution-quality-baseline-v0` (shadow) | v001 (55.4d) | **Accept-frozen, explicitly.** Mark the pin intentional (constant baseline; the skip guard now suppresses its no-op retrains). Optionally drop from the nightly roster. | Deliberate constant baselines — staleness is by design, stakes ~zero. |
| 6 | `setup-quality-audit-baseline-v0` (shadow) | v001 (55.4d) | Same as #5. | Same. |

**WATCH list (fresh dataset but last-2-run metrics identical — verify, no
action proposed yet):** `eth-regime-5m-lgbm-v1`, `sol-regime-5m-lgbm-v1`
(and `sol-regime-15m-lgbm-v1`, now demoted to candidate tonight — moot).
Next /ml-review should confirm whether that identity is eval-window rounding
or a genuine skip.

## What executing this would take (if approved)

- **#1/#2 siblings (Tier-1 trainer work + Tier-3 only at swap time):** add a
  refreshed fc-dataset build (the forecast side-stream + market_features at a
  new version pin) to the trainer, two new manifests, nightly training via
  the normal roster. No live change until a future gate-checked swap.
- **#3:** no further action — tonight's rebuild either passes its verify
  gate and retrains, or reports STILL_DEAD for code-level investigation.
- **#4:** one `promote-stage` action (shadow → candidate). Tier-3-adjacent
  (registry stage change) — bundled for morning approval.
- **#5/#6:** backlog note + optional roster edit. Tier-1.

## Recommendation (single line for the morning ping)

Accept-frozen for both live fc-pcv heads + build refresh siblings in
parallel; retire the ETH control head; mark the two baselines
intentionally-frozen; ETH xasset already refreshing tonight.
