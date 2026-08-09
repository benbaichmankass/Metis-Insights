# Gross-exposure governance — what we are actually trying to accomplish

**Status:** design investigation, 2026-08-08. No code change proposed in this
document's first phase beyond an observability seam; the ceiling VALUES remain
Tier-3 and operator-owned.

**Origin:** `BL-20260807-ALPACA-PAPER-ZERO-BUYING-POWER-REFUSES-ALL`, shipped as phase 1 in
`bb5203ae` (#8600). Phase 2 — declaring values — was explicitly deferred
("proposed separately, not shipped here") and never happened. This document is
that proposal, plus a structural finding that explains *why* it never happened.

---

## 1. The goal, stated precisely

Not "limit leverage for safety". The originating incident was an **availability
failure**:

> `alpaca_paper` sat at **2.03× gross leverage against a 2.00× Reg-T limit**
> with `available_usd 0.00`, **refusing every new signal**.

The account was bricked. Not by losses, not by stuck positions — by walking
into the broker's margin wall and having nowhere left to go.

**So the goal is: never let an account arrive at a state where it cannot
trade.** Loss control is already owned elsewhere (§2). This is about capacity.

The commit names three defects in delegating capacity to the broker, and they
are the actual requirements:

| # | Defect of delegating to `available_usd` | Requirement it implies |
|---|---|---|
| 1 | adopts the **venue's** risk appetite as ours | the ceiling must be **ours**, expressed in our own terms |
| 2 | a **wall**, not a gradient — everything, then nothing | must **govern on the way up**, not only stop at the end |
| 3 | venue-non-uniform; **silently moves** when a balance fetch fails | must be **uniform across venues** and not depend on a live fetch |

Requirement 2 is the one most easily lost. A ceiling that only refuses is a
second wall. The shipped design already honours it: `evaluate()` refuses *at*
the ceiling, `position_size()` **downsizes into remaining headroom** below it.

## 2. What already exists — and where the real gap is

`RiskManager` controls, in `evaluate()` order:

| Control | Bounds | Basis |
|---|---|---|
| `dry_run` | account gate | mode |
| `DAILY_LOSS_CAP` | daily realised loss | fraction of equity (or abs USD) |
| `INTRADAY_DRAWDOWN` (`max_dd_pct`, 5%) | peak-to-trough today | fraction of equity |
| `GROSS_EXPOSURE_CAP` | **capacity** | fraction of equity — **inert, undeclared** |
| `position_size()` | per-trade risk | `risk_pct` (1.5%) × SL distance |
| margin pre-flight | venue capacity | broker `available_usd` |
| netting guard | one position per strategy | baseline, unconditional |

**There is deliberately NO position-notional cap** (`POSITION_SIZE_CAP`,
removed 2026-06-24) because it "would gate a correctly risk-sized trade on a
number unrelated to the account's actual capacity."

**The gap is real and specific.** Every loss control is *per-trade* or
*realised*. Nothing bounds **accumulated concurrent capacity**. Ten
correlated positions each risking 1.5% is a 15% simultaneous exposure that:

- `risk_pct` permits (it is per-trade),
- `max_dd_pct` catches only **after** the loss is realised in equity,
- and the broker catches only **at its wall** — the 2.03× incident.

So the feature is not redundant. The question was only ever *what value*.

## 3. Why phase 2 never happened — the structural finding

**Exposure is unobservable until a ceiling is already declared.**

```python
def gross_exposure(self):
    if self.max_gross_exposure_pct <= 0:
        return None          # <-- "no policy" and "no data" are the SAME answer
```

`report()["exposure"]` is gated identically. So an operator asked to pick a
multiple has **no way to see what the account currently runs at** — they would
have to declare an arbitrary ceiling to find out whether that ceiling is right.

That is sufficient to explain the deferral. It is also a familiar defect class
in this repo: **conflating "no policy" with "no measurement."** The same shape
as `no_fill_in_window` vs `fills_present_but_qty_unreconciled`, as
`FillsWindowUnavailable` vs an empty book, and as `exit_anchor`'s three-way
`anchored` / `deferred` / `no_anchor` — which already documents the rule:
*collapsing any two of those reintroduces a defect.*

### 3.1 Why the obvious fix is dangerous — two measured halt vectors

Ungating the measurement alone (attempted and **reverted**, 2026-08-08) is a
fleet-wide trading halt, by two independent paths:

| | Path | Mechanism at the default `0.0` |
|---|---|---|
| **1** | `evaluate()` | `state[2] >= 0.0` is true for **any** exposure, including `0.0` — every trade refused, even on a flat account |
| **2** | `position_size()` | `exposure_headroom_usd()` = `max(0, 0.0×equity − notional)` = **0.0** — every position clamped to nothing |

Trap 1 was guarded on the first attempt; **trap 2 was missed entirely** and was
caught by the pre-existing `tests/test_risk_gross_exposure.py`. Two halt
vectors in ten minutes on the money path is the signal that this needs a
structural fix, not a guard.

`test_unset_ceiling_is_a_noop` asserts the current behaviour **deliberately**
("no declared ceiling => no exposure state, no gate, no clamp"). Any change
must argue with that contract, not edit around it.

## 4. The structural fix: separate observation from policy from verdict

One function serving both measurement and enforcement is the defect. Split it
into three, where the dangerous states are unreachable **by construction**
rather than by remembering to guard:

```
observe_exposure()  -> Observation
      status ∈ { measured, unmeasurable }
      notional, equity, multiple      (only when measured)
      # NEVER consults policy. Cannot gate anything. Safe to call anywhere.

exposure_policy()   -> float | None
      # the declared ceiling, or None. A pure config read.

exposure_verdict(observation, policy) -> Verdict
      policy is None           -> ALLOW          (no policy = no action, ever)
      status is unmeasurable   -> ALLOW          (we did not look ≠ a breach)
      multiple >= policy       -> REFUSE
      else                     -> CLAMP(headroom = policy*equity - notional)
```

Properties that matter:

- **Both halt vectors become unreachable.** `policy is None → ALLOW` is checked
  before any arithmetic, so `0.0` can never be compared against and never
  becomes a headroom.
- **Observability is free and safe.** `observe_exposure()` has no path to a
  refusal, so it can be surfaced on `report()`, a diag route, or a dashboard
  panel with no trading risk — which unblocks phase 2.
- **`unmeasurable` stays distinct from `flat`.** Preserves the existing
  `measured: false` contract, which the current `report()` already gets right.
- **The existing test contract is preserved,** not argued with: with no policy
  declared there is still no gate and no clamp. Only the *measurement* becomes
  available, and it is on a path enforcement never reads.

This is the same remedy the repo already applied in `exit_anchor.py`. It is not
a novel invention; it is applying an established local pattern to a place that
predates it.

## 5. Choosing the values — three options

Phase 2 proper. **All are Tier-3.**

### Option A — operator declares per-account, from observed data
Ship §4, surface the multiple, soak, then set each account from its observed
peak with headroom.
- ✅ evidence-based; no guessing
- ✅ per-account, respects different venues/strategies
- ❌ needs a soak before any protection exists
- ❌ N numbers to maintain; drift risk as the roster grows

### Option B — derive from the venue's own limit, minus a buffer
`ceiling = venue_portfolio_limit × safety_fraction` (e.g. Reg-T 2.00 × 0.85 =
1.70 for Alpaca).
- ✅ **directly prevents the originating incident** — 2.03× could never occur
- ✅ zero operator guesswork; self-correcting per venue
- ✅ still a fraction of our own equity, satisfying the 2026-06-24 directive
- ❌ **the quantities are not the same across venues** — Reg-T 2× is a
  *portfolio* limit; Bybit's `leverage: 3` is *per-position*. Deriving one from
  the other silently compares different things — precisely the semantic
  substitution (sub-class A) this repo names as a defect class.
- ❌ re-imports venue appetite, which requirement 1 exists to reject

### Option C — a declared default, per-account override (recommended)
One conservative fleet default, per-account overrides where evidence justifies.
- ✅ protection exists immediately, without waiting for a soak
- ✅ one number to reason about, not eleven
- ✅ overrides are evidence-driven as data arrives
- ❌ a fleet default is by definition not tuned to any account
- ❌ must be set loose enough not to bind in normal operation (§6)

**Recommendation: §4 + Option C**, in that order, as two separate PRs. §4 is
safe and unblocks everything; C without §4 is guessing.

## 6. The binding constraint on any value

**A ceiling that binds in normal operation is worse than none.** It would
silently clamp correctly-risk-sized trades — the exact reasoning that removed
`POSITION_SIZE_CAP`. The ceiling exists to stop the account arriving at the
*broker's* wall, so it must sit **below the venue limit and above normal
operation**, and the gap between those is an empirical question §4 answers.

Corollary: the first value should be deliberately **loose**. A too-loose
ceiling still prevents the 2.03× incident; a too-tight one causes the silent
throttling this system has repeatedly been bitten by.

## 7. What is NOT proposed

- ❌ Removing the venue margin pre-flight. It is the real hard boundary; this
  governs approach to it.
- ❌ Correlation-aware exposure. Real, and much larger; gross notional first.
- ❌ Any value shipped without §4's observation soak behind it.
