# Crypto fine-tune proposals (Tier-3, for operator review) — 2026-07-30

Output of the crypto-book edge audit (`crypto-edge-audit-2026-07-30.md`). Each
proposal carries the **diagnosis block** the performance-review skill now
requires. **Nothing here is applied** — all Tier-3, operator-gated. These are
*fine-tunes* of legs with real edge, never demotions (no audited leg is dead).

---

## A. `sol_pullback_2h` — gate out the long side

- **diagnosis.reidentified_edge_R:** 2yr WF (exact live params, net-of-fee):
  blended **−5.43R**, but **long −12.39R / short +6.96R**. The short side is a
  real edge; the long side is the whole loss.
- **diagnosis.cause:** `persistent-directional` (NOT regime-transient) — the long
  side is negative across the sample, a single-symbol (SOL) effect, not a
  pullback-family property (eth/xrp pullback longs are net-positive).
- **diagnosis.why_finetune_insufficient (for a demote):** demoting the whole leg
  throws away the +6.96R short edge. The fix is to keep shorts, drop longs.
- **proposed change:** run `sol_pullback_2h` **short-only**.
- **validation:** short-only net-R +6.96 vs blended −5.43 (WF). maxDD + per-fold
  short-only stability — **running (relay #7965)**; gate = short-only beats
  blended on net-R AND maxDD across folds before merge.
- **config-capability gap:** there is **no `short_only` option** today (the
  pullback builder has no direction gate; `trend_donchian` has only `long_only`).
  So this needs a **small Tier-1 code add** — a `side_filter: long|short|both`
  (or `short_only: true`) on the pullback/trend builders — then the Tier-3 enable
  per leg. Ship the capability + validate, then flip.

## B. `trend_donchian_xrp_4h` — gate out the long side

- **diagnosis.reidentified_edge_R:** 2yr WF: blended **+5.29R**, **long −2.05R
  (stable_drag, negative 4/6 folds) / short +7.34R**. Short carries it; long is a
  persistent drag.
- **diagnosis.cause:** `persistent-directional` — same shape as (A); the
  alt-crypto long side is a bearish-regime drag across the book.
- **proposed change:** run `trend_donchian_xrp_4h` **short-only** (or `long_only:
  false` → a `short_only` equivalent).
- **validation:** short-only +7.34 vs blended +5.29 (WF) — improves net-R and
  removes the −2.05 drag. maxDD + per-fold **running (#7965)**.
- **config-capability gap:** `trend_donchian` supports `long_only` but not
  `short_only` — same small capability add as (A).

## C. `eth_pullback_2h` — KEEP as-is; regime-gate is a validation task, not a proposal

- **diagnosis:** +18.43R durable edge; negative only in the recent folds (regime).
  The 2yr WF **refuted a directional gate** for the eth/xrp pullback longs (they
  are net-positive) — so NOT a long-gate.
- **disposition:** **KEEP unchanged.** A vol/chop regime guard (skip the adverse
  regime) is a *hypothesis*; per the discipline it may not be proposed as a config
  change until a regime-gate sweep shows it beats baseline on net-R AND maxDD
  IS+OOS. Optional next validation; no live change now (the edge is real, the
  regime will turn).

## D. `trend_donchian` (BTC 1h) — WATCH; regime-gate is a validation task

- **diagnosis:** +1.12R thin base (2025 +9.6 / 2026 −9.3); the live **ML exit
  head** (`exit-head-donchian-1h-v1`, M20-shipped precisely because this leg's
  base exits are bad) lifts the live version above the base number.
- **disposition:** **WATCH.** Same as (C): a regime/vol guard needs a validation
  sweep before it's a proposal. No live change now.

---

## Summary for the operator

- **Ready to build+validate (A, B):** add a `side_filter`/`short_only` capability
  (Tier-1), validate short-only on both legs (#7965), then Tier-3-enable per leg.
  These fix the two clearest bleeders by *keeping the edge and dropping the drag*.
- **Keep + optionally sweep (C, D):** no live change; a regime-gate sweep is the
  next validation if we want to reduce the adverse-regime drawdown on the real
  edges — but only if it validates.
- **Cross-cutting:** the alt-crypto **long side** is the common thread (A + B). If
  the short-only validation holds, a general **`side_filter`** is the reusable fix
  for the bearish-alt-regime drag, not per-leg hacks.
