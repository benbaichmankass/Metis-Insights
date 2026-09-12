# MI-278 U17 — the arbitration fallback wins instead of losing: two detectors armed, and the remedy is not what the finding implies

> **Doc status:** `unknown` · category `research` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> **Unit:** MI-278 U17 · object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` · cycle `CY-20260906-TRADING-TRUTH`
> **Ships part 1 of** `BL-20260909-UNKNOWN-STRATEGY-PRIORITY-NOW-BEATS-45-OF-50-DECLARED-LEGS-AND-THE-CONTENTION-IS-LIVE` (the two CI detectors, Tier-1) **and proposes part 2** (the `src/runtime/intents.py` change, Tier-3) rather than taking it.

## 1. Population

Everything below is measured **2026-09-12** by AST over `src/runtime/intents.py` and by YAML load of
`config/strategies.yaml` at `origin/main` `4a91b834e`. Nothing is transcribed from the
2026-09-09 audit; the audit's figures are quoted only where this pass **reproduces** them.

- `DEFAULT_PRIORITIES`: **n = 50**, module-level at line 198.
- Histogram: `{0: 41, 1: 1, 2: 1, 3: 1, 5: 1, 10: 1, 20: 1, 30: 1, 40: 1, 50: 1}`. min **0**, max **50**.
- `_UNKNOWN_STRATEGY_PRIORITY` = **10**, line 375.
- Legs strictly **below** the fallback: **45 of 50**.
- `config/strategies.yaml`: **55** declared, **45** `execution: live`, **44** of those `enabled`
  (the one live-but-disabled leg is `xauusd_trend_1h`, which **is** mapped, at 0 — so the
  enabled filter changes nothing about the finding).
- Live legs absent from the map: **5** — `gdx_pullback_1d`, `iaum_pullback_1d`,
  `scha_trend_long_1d`, `slv_pullback_1d`, `splg_trend_long_1d`. All five `enabled`.
- Priority entries with no declared strategy: **0**.

Every figure reproduces the audit's F-29 exactly, three days on. The inversion has not moved.

## 2. What ships here (Tier-1)

`scripts/check_strategy_coverage.py` gains the two invariants the row's `resolution_criteria`
names, plus the register that supports them in `config/regime_coverage_exemptions.yaml`.

**Invariant 4 — priority coverage.** Every `execution: live` + `enabled` strategy must have a
`DEFAULT_PRIORITIES` entry or a dated `priority_exempt` entry carrying `reason`, `tracking_id`
and `added`. `priority_exempt` is a **debt** register, not an excuse: `priority_exempt_ceiling`
only ratchets down, and an entry for a leg that *has* since been mapped is refused as **stale**,
so the register cannot quietly accumulate cover for the next real gap.

**Invariant 5 — the distribution assertion.** `_UNKNOWN_STRATEGY_PRIORITY` must be **strictly
below** `min(DEFAULT_PRIORITIES.values())`. Equality is refused too, and has its own control: at
`constant == min` an unlisted leg *ties* the lowest mapped leg and the downstream tiebreak
decides, which is not "loses".

### 2.1 Both invariants fail on `main` today, and that is the design problem

Arming either one hard on 2026-09-12 would have turned **every open PR in the repo red** for a
condition none of them caused. The two dishonest ways out — reporting without failing, or a
presence-only marker — are both the "guard cheaper to lie to than to satisfy" shape this repo
has already paid for. What ships instead:

- the five absent legs are entered in `priority_exempt` under the ratchet, so invariant 4 passes
  today **and refuses the sixth**;
- the inversion is waived by a single `priority_inversion_waiver` block that is **pinned to the
  measured pair** (`observed_constant: 10`, `observed_min: 0`).

The pin is the whole point. The waiver excuses **the state it was measured against**, not the
invariant: if either term moves, the guard fires again — which is exactly the failure class the
row names, *a default whose fail-safety depends on a distribution that has since moved*. And the
guard **fails if the waiver is still present once the constant is fixed**, so deleting it is
strictly cheaper than keeping it. A waiver that outlives its condition is how the *next*
inversion gets waived by accident.

### 2.2 Controls

The self-test goes from 8 controls to **38**, all passing. Every new refusal path is planted in
isolation and each has a negative control beside it, so a probe firing on the wrong thing is
caught rather than credited: an unmapped leg refuses **and the same leg under a dated exemption
passes**; an inverted constant refuses **and the same fixture with the constant below the minimum
passes**; the pinned waiver passes **and the same waiver refuses when either pinned number moves**.

Two are worth naming because they were written against real traps:

- **`read_priority_map` is exercised against the real `src/runtime/intents.py`.** Both names are
  module-level `AnnAssign` (`DEFAULT_PRIORITIES: Dict[str, int] = {...}`), which a walker handling
  only `ast.Assign` misses entirely — returning `None`, which reads exactly like *the map is gone*.
  This author's first probe made precisely that error this session. Every other control stages the
  loader, so without this one the real reader would never execute at all.
- **Seven never-collapsed read states, and every one of them REFUSES.** `no_intents_file`,
  `no_priority_map`, `no_unknown_constant`, `empty_priority_map`, `non_literal`, `unparseable`. If
  this guard cannot find the constant it is meant to be watching, the honest report is that it is
  **not watching it** — not a pass. Without these a rename in `intents.py` would turn the whole
  arbitration half into a no-op that keeps printing OK.

One real defect was found by the controls rather than by reading: `evaluate_arbitration` called
`min()` on a map that could be empty. A guard that **crashes** reports "the guard is broken", not
"the invariant is violated", and the two get triaged very differently. Refused explicitly now.

## 3. The Tier-3 proposal — and the audit's remedy is not safe as stated

The operator answered this on 2026-09-09 (`WO-20260909-DECISION-UNKNOWN-STRATEGY-PRIORITY-IS-INVERTED`,
`chosen: fix_constant_and_map_legs`). That authorises the change; it does not say **what value**
each of the five legs gets, and measuring that turned up something the audit did not state.

**Every sibling of all five absent legs sits at 0, unanimously** — all 5 `*_pullback_1d`, all 6
`*_trend_long_1d`, all 4 `*_trend_1h`, all 4 `*_pullback_1h`. So `0` is not an invented number.

**Contention, measured over every `enabled` leg — live AND `shadow`, because `execution: shadow`
does not exclude a leg from the election** (`src/runtime/intents.py:182-187`, which states this in
terms: the shadow gate is downstream in `multi_account_execute`, so "a shadow, data-only leg can
win a symbol and silence every live leg on it for that tick"):

| leg | symbol | rival | rival execution | rival priority |
|---|---|---|---|---|
| `gdx_pullback_1d` | GDX | none | — | — |
| `iaum_pullback_1d` | IAUM | none | — | — |
| `scha_trend_long_1d` | SCHA | none | — | — |
| `splg_trend_long_1d` | SPLG | none | — | — |
| `slv_pullback_1d` | SLV | `slv_trend_1h` | **shadow** | 0 |

⚠️ **The row's evidence says the SLV rival is a live contention and does not say the rival is
`execution: shadow`, and that omission inverts how the remedy reads.** `slv_pullback_1d` is
`execution: live`; `slv_trend_1h` is `execution: shadow`. So on SLV *today* the inversion is
producing the outcome one would want — the **live** leg (unmapped → 10) beats the **data-only**
leg (0). Mapping `slv_pullback_1d` at 0 like its siblings creates a **tie**, decided by the next
terms of the election key (`-_election_confidence`, `_track_record_rank`, `timestamp`, then name),
which can hand SLV to the shadow leg and silence the live one for that tick. That is not
hypothetical: the same file records `eth_pullback_prop_2h` (shadow) beating `trend_donchian_eth`
(live) head-to-head today.

**Proposed change to `src/runtime/intents.py` — NOT applied here:**

1. `_UNKNOWN_STRATEGY_PRIORITY: int = 10` → `-1`. **This changes no live routing at all.** After
   step 2 the map holds all 55 declared strategies, so no declared leg resolves to the constant and
   it becomes what its own comment says it is: a fail-safe floor for an *undeclared* strategy.
2. `gdx_pullback_1d`, `iaum_pullback_1d`, `scha_trend_long_1d`, `splg_trend_long_1d` → **0**.
   Each runs **alone on its symbol**, so its priority is inert today; this is hygiene against a
   future rival, not a routing change.
3. `slv_pullback_1d` → **open question, and the only part of this with a live consequence.**
   `0` is sibling-consistent and defers to confidence; `1` keeps the live leg deterministically
   ahead of a data-only rival on its own symbol. **Recommendation: 1**, because a shadow leg
   winning a symbol is a documented live starvation mode and the tie buys nothing here. The
   operator decides.

The waiver block must be deleted in the same PR — the guard shipped here fails if it is not.

## 4. What this does not establish

- **No misrouting has been observed.** The row says so and it is still true: the arbitration
  outcome is not journalled per-tick in a form that would show a starved leg, so this rests on the
  election key and the config, not on an observed loss. `BL-20260831-CONFIDENCE-IS-CARRIED-TO-THE-ELECTION-AND-READ-BY-NEITHER-SORT-KEY`
  is open and bears on which tiebreak term actually decides step 3.
- **The four inert legs are inert *today*.** "No rival on this symbol" is a property of the current
  roster, not of the legs.
- **The detectors prove nothing about the fleet.** They grade the repo at merge time. Nothing here
  was deployed and nothing was enacted.
