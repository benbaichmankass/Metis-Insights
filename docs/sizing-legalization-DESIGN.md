# Position-Sizing & Qty-Legalization Consolidation — DESIGN

**Status:** proposal, awaiting operator approval to kick off
**Tier:** 3 (touches the live order-sizing path) — phased, each phase separately reviewed
**Origin:** the recurring ETHUSDT/bybit_2 "below the exchange lot minimum after
step-alignment" error ping (surgical fix in PR #5700); the durable follow-up to
`BL-20260628-CRYPTO-INSTRUMENT-MIN-FLOOR`.

---

## 1. Problem

A single class of bug — "a quantity below the exchange's minimum lot reaches the
order path" — has recurred at least four times, each time fixed at the one site
that happened to page:

| # | Incident | Site the fix landed | Minimum it used |
|---|---|---|---|
| 1 | `BL-20260611-005` | `execute._submit_order` pre-flight (`execute.py:958`) | venue lot ✓ (but raises → error ping) |
| 2 | `BL-20260619-ETHMIN` | `coordinator.py:1500` sized-qty guard | venue lot ✓ |
| 3 | `BL-20260622-ALPACA-FRACTIONAL` / `BL-20260628` | `risk.py::position_size` whole-unit + sub-min refusal | account `min_qty` ✗ (0.001) |
| 4 | PR #5700 (this thread) | `coordinator.py:1900` intent-delta guard | was account `min_qty` ✗ → now venue ✓ |

The recurrence is not carelessness — it is structural. The logic that answers
**"is this quantity legal for this exchange, and if not, refuse"** has **no single
home**. It is re-implemented at every point that produces a quantity, and those
implementations resolve "the minimum" from **three different, disagreeing
sources**:

- the account-level `RiskManager.min_qty` (from `config/accounts.yaml::risk`,
  default `0.001` — BTC-shaped, **not** symbol-aware);
- the per-symbol Bybit lot rule (`precision.get_lot_rule` → live instruments-info
  → a small static map);
- the per-symbol `config/instruments.yaml` (`min_qty: 0.01` for ETHUSDT), loaded
  into `InstrumentProfile` — **which the sizing path does not consult today.**

For BTCUSDT all three agree (`0.001`), so the bug is invisible there. For any
higher-lot symbol they diverge, and whichever site still reads the account
`min_qty` lets a sub-lot quantity through.

## 2. The three concerns hiding inside "sizing"

Separating them is the key to the fix. "Sizing" is really three decisions:

| Concern | Question | Where it lives today | State |
|---|---|---|---|
| **A. Risk sizing** | how big *should* this trade be — risk %, confidence, margin, daily-loss budget? | `RiskManager.position_size` (`risk.py:600`) | **centralized** (S-026 G2 "the only function that decides size") |
| **B. Reconciliation** | given target X and I hold Y, what order do I send (delta X−Y)? | `compute_execution_delta_for_package` (`intents.py`) + delta orchestration (`coordinator.py:1670`+) | one home, but re-checks the minimum itself |
| **C. Legalization** | round to the venue's lot step / enforce its minimum / whole-unit — else refuse | **scattered:** `precision.py` (rules), `execute.py:101` (`venue_min_qty_for`), `execute.py:958` (pre-flight), `coordinator.py:1513` + `:1900` (two guards), and partly *inside* `risk.py:700` (whole-unit/futures) | **no single seam** |

**Concern A is not the problem** — it was already centralized on purpose.
**Concern C is the sprawl**, and its minimum source-of-truth is itself split.
`RiskManager` is constructed from only the `risk` sub-block and, by design,
"never sees the exchange" (hence `whole_units` is passed in as a flag) — so it
*structurally cannot* enforce a venue minimum, and every caller bolts one on
afterward, inconsistently.

## 3. Proposal

### 3.1 One legalization seam

Introduce a single function that owns concern C end to end:

```python
# src/units/accounts/qty_legalize.py  (new)

@dataclass(frozen=True)
class LegalizedQty:
    qty: float          # step-aligned, >= venue min, whole-unit if required
    ok: bool            # False => refuse this trade (per-trade refusal, Prime-Directive shape)
    reason: str         # "" when ok; a cause token when refused (e.g. "below_venue_min_qty")
    venue_min: float | None
    step: float | None

def legalize_qty(qty: float, *, account_cfg: dict, symbol: str,
                 client=None) -> LegalizedQty:
    """The ONLY place a raw quantity becomes an exchange-legal quantity.

    Resolves (step, min) from ONE resolver (see 3.2), floors to the step
    (never rounds up — realised risk must not exceed the sized cap), and
    returns ok=False when the floored qty is below the venue minimum.
    Rule unknown (no profile, non-lot venue) => passthrough (ok=True,
    qty unchanged) — byte-for-byte today's "rule unknown => submit
    unmodified" contract.
    """
```

Every quantity producer routes through it:

- `coordinator.py:1500` sized-qty guard → `legalize_qty(sized_qty, …)`
- `coordinator.py:1900` intent-delta guard → `legalize_qty(delta.qty_delta, …)`
  (this is what PR #5700 does inline; it moves into the seam)
- `execute._submit_order` pre-flight → becomes a cheap **assertion** that the qty
  is already legal (defence in depth), not a fourth independent decision.

You cannot have divergent copies of a rule when there is one copy.

### 3.2 One minimum resolver — reuse `InstrumentProfile`

The authoritative per-symbol source **already exists** and is unused by the order
path: `InstrumentProfile` (`src/core/instrument_profile.py`), loaded from
`config/instruments.yaml` by `profile_loader.load_instrument_profiles()`. It
already carries `min_qty`, `qty_step`, `tick_size`, `contract_value_usd`, and a
`round_qty()` method — exactly the fields legalization needs.

Resolution order inside the seam (all fail-safe, degrading to today's behaviour):

1. `InstrumentProfile` for `symbol` from `instruments.yaml` (authoritative, offline);
2. live venue lot rule (`precision.get_lot_rule`) when a client is present and the
   symbol has no profile (keeps the Bybit-live path for un-profiled symbols);
3. account `min_qty` as the last-resort fallback (today's behaviour when nothing
   else resolves).

This collapses the three disagreeing sources into one ordered resolver with the
**symbol-aware** profile at the top. `RiskManager` still doesn't need to see the
exchange; the coordinator legalizes *after* risk-sizing, at the seam.

### 3.3 A CI guard that enforces the seam

Follow the pattern the repo already uses to make an invariant executable rather
than tribal (`canonical-db-resolver` forbids stray DB paths; `env-gate-guard`
forbids `*_ENABLED` flags). Add `qty-legalization-guard`:

- **fails CI** if a quantity is compared against a raw `min_qty` / numeric lot
  literal (`0.001`, `0.01`, …) **outside** `qty_legalize.py`; and/or
- **fails CI** if an order leg / `_submit_order` call is constructed with a qty
  that did not pass through `legalize_qty` (enforced by a lightweight marker or an
  allowlist of legal producers).

This is the layer that prevents a *fifth* recurrence: a future contributor adding
a new qty-producing path is stopped at CI unless they route through the seam.

## 4. Migration plan (phased — each phase a separate reviewed PR)

Because this is the live money-sizing path, ship it incrementally, verifying live
between phases — never one big-bang refactor.

- **Phase 0 (done):** PR #5700 — the surgical delta-guard fix (venue-aware),
  stopping the active error ping. Ships first, independent of this doc.
- **Phase 1:** introduce `qty_legalize.legalize_qty` + `LegalizedQty` and the
  `InstrumentProfile`-first resolver, with exhaustive unit tests. **No call sites
  switched yet** — pure addition, Tier-1-ish (no behaviour change on the live
  path). Prove parity against every existing guard's current output.
- **Phase 2:** switch the two coordinator guards (`:1500`, `:1900`) and the
  `_submit_order` pre-flight to the seam. Tier-3. Verify live: the next ETHUSDT/
  bybit_2 top-up logs a clean `below_venue_min_qty` / `intent_sub_min_qty_delta`
  noop, never a `bybit_place_order_failed` ping; BTC sizing unchanged.
- **Phase 3:** fold the `risk.py` whole-unit/sub-min refusals onto the same
  resolver (closes `BL-20260628`), so `risk.py` no longer carries its own copy of
  the minimum. Tier-3. Verify Alpaca whole-share + futures whole-contract parity.
- **Phase 4 (DONE, #5736):** added the `qty-legalization-guard` CI check
  (`scripts/check_qty_legalization_guard.py` + `.github/workflows/qty-legalization-guard.yml`)
  — an AST scan that fails the build if any `src/` file outside the seam
  (`src/units/accounts/qty_legalize.py`) *calls* a venue-lot primitive
  (`precision.get_lot_rule` / `quantize_qty`), the pattern that would seed a
  fifth private copy of the minimum; a genuine exception carries an inline
  `# qty-legalize-allow: <reason>`. Self-test: `tests/test_qty_legalization_guard.py`.
  Also removed the now-dead ad-hoc min read `execute.venue_min_qty_for` (no `src/`
  caller after Phase 2 migrated the coordinator guards to `legalize_qty`); its
  direct-resolver coverage now lives in `tests/test_qty_legalize.py`, and the
  live-path clean-refusal integration test is kept in
  `tests/test_venue_min_qty_refusal.py`. Tier-1.

Roll-back at every phase is a revert; the seam is fail-safe (rule unknown →
passthrough), so a resolver miss degrades to today's behaviour, never to a
blocked order path.

## 5. Test plan

- Unit: `legalize_qty` over the matrix {BTCUSDT 0.001, ETHUSDT 0.01, SOLUSDT 0.1,
  MES whole-contract, IWM/alpaca whole-share, un-profiled symbol → passthrough,
  non-Bybit → passthrough}; floor-never-round-up; refuse-below-min.
- Parity: for each existing guard, assert the seam returns the same
  legal/refuse verdict the guard produces today (regression harness before any
  call site is switched).
- Integration: the `coordinator.multi_account_execute` cases already covered by
  `tests/test_intent_delta_dispatch.py` + `tests/test_venue_min_qty_refusal.py`
  keep passing unchanged.
- CI-guard self-test: a deliberately-planted raw-`min_qty` comparison outside the
  seam fails the new guard.

## 6. Cross-coordination principle (why this is the durable fix)

The reason a fix at one site never reached its siblings is that the invariant
lived in **tribal knowledge and scattered comments**, which decay, rather than in
**one code path or a machine check**, which do not. The durable answer, in
priority order:

1. **One code path** (the seam) — nothing to keep in sync.
2. **A machine enforces it** (the CI guard) — "you can't merge otherwise,"
   exactly how the Prime Directive killed the `*_ENABLED` class.
3. **Sibling-sweep on class-bugs** — when a backlog item names a *class*
   ("the minimum is resolved from the wrong source"), the definition-of-done is
   to grep every call site of the pattern and fix-or-log each, and the backlog
   entry **enumerates the known siblings**. Had `BL-20260628` listed all four
   sites, PR #5700's incident would not have occurred. This belongs in the
   `/system-review` and `/full-system-audit` protocols.

The general rule: scattered logic is the symptom of a missing abstraction seam.
You regain coordination by giving the invariant one home, making a machine
enforce that everyone uses it, and — when you must patch in place — treating
"find the siblings" as part of the fix, not a follow-up.
