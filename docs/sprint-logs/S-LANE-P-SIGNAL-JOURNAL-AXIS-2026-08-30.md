# Sprint Log: S-LANE-P-SIGNAL-JOURNAL-AXIS-2026-08-30

## Date Range
2026-08-30 (single session, `01HYXKHpDQeWv3u4rjWWoL2J`).

## Objective
Work Lane P (P1/P2) per `WORKPLAN-2026-08-29.md` § "Recommended order" item 3,
and carry the loud `OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED`.

## Tier
**Tier 1 throughout.** Read-only observability + docs. No order path, no
`config/`, no VM mutation, no service restart. One free-runner workflow
dispatch (`gld-compat-matrix`, read-only by its own header).

## Starting Context
`origin/main` at `b0c3f468`. A concurrent `/system-review` session
(`claude/full-system-review-v68vcm`, PR #10482) was live and **unannounced on
the board**, holding `CLAUDE.md`, `docs/claude/OPEN-ITEMS.json` and
`docs/claude/health-review-backlog.json`. The session brief's one DUE item
(`OI-20260830-BYBIT-HEDGE-MODE-ARMED-BUT-UNEXERCISED`) was that session's own
subject, so it was **left to them rather than duplicated**.

## Repo State Checked
- `origin/main` `b0c3f468` (the handoff named `7c47ecb9`; #10461 had landed since).
- Board issue #6927 read before the first substantive call; `▶️ START` posted
  naming the claimed files and the deconfliction.
- Merge slot free (last `🔓 RELEASE` 09:15:10Z).

## Files and Systems Inspected
- `src/prop/account_rulesets.py::_standard_ruleset`, `src/prop/standard_account_size.py`
- `scripts/prop/account_compat_matrix.py`, `.github/workflows/gld-compat-matrix.yml`
- `src/runtime/dead_leg.py`, `scripts/ops/dead_leg_audit.py`, `src/runtime/silent_refusal_alert.py`
- Live: `/api/bot/config`, `/api/bot/positions`, `/api/bot/trades/closed`,
  `/api/bot/db/{tables,table/*}`, `/api/diag/{version,audit_query}`
- GH Actions run `33306014754` (job `99242607803`)

## Work Completed

### 1. P1 — already done, and the workplan said otherwise for three days
**No code was written for P1.** Reading `_standard_ruleset` before claiming the
work showed it already builds `drawdown_type="intraday_high"` +
`drawdown_breach="refusal"` with the synthetic `$10,000` gone (#10364, 08-27),
and its residual `BL-20260829-GLD-COMPAT-SUMMARY-CALLS-UNGRADED-A-REJECTION-AND-ITS-STANDARD-ARM-IS-INERT-ON-A-RUNNER` already fixed in #10393
(`c1f50fc`, 08-29) — a dedicated `UNGRADED` branch plus a real balance source.

The workplan row read *"Unchanged from 08-26/08-27 and not re-verified this
session"* and sat at **position 3 of Recommended order**, ahead of B6/B5. A
session following the plan would have spent a unit rediscovering a merged fix.
Row corrected in place; *field beats comment*.

### 2. P1 — verified by EXERCISING it, which had never been done
Both defects were fixed on 08-29 and **never observed working**; the row was
still `open`/`high`. Dispatched `gld-compat-matrix` on `main` (free runner).

| | 2026-08-29 (filed) | run `33306014754`, 08-30 |
|---|---|---|
| standard accounts graded | **0 of 10** | **9 of 10** |
| size basis | none → `unreadable` | `size=measured`, `source=db`, 11 accounts |
| the one refusal | — | `ib_live`, balance genuinely `None`/`api_ok=false` |

The refusal is **discriminating, not blanket**, and no synthetic size returned —
which is what makes this fixed rather than merely quiet. Row marked `resolved`
with the measurement; **one residual filed separately rather than swept in**
(`ib_live` reads `UNGRADED` with no stated reason — the workflow renders its own
table with no size column, though the *script* already renders `— (size_state)`).

### 3. P2 — the "signals, journals nothing" detector (the session's code)
Motivating claim **verified first, and it is worse than recorded**:
`trend_donchian_sol` is enabled/live and routed to `bybit_1`; **144** actionable
buy signals since 08-01 (workplan said 120); newest journal row of any kind
**2026-06-29**; all 7 trade rows on `breakout_1`; **zero rows on `bybit_1`,
ever**. Nothing alerted for two months.

Shipped a **third axis** in `src/runtime/dead_leg.py` — the module that exists so
the offline report and the live alert cannot disagree about a row, so not a new
module and not a second copy of the rule:
`signal_journal_state_for(actionable_signals, journal_rows, *, table_present)` →
`journaling · signals_never_journaled · no_actionable_signals · unknown`, plus
the audit consumer + render, sourced from `signals` rather than `legs`.

## Validation Performed
- `tests/test_dead_leg_audit.py`: **29 passed** (18 pre-existing + 11 new).
  Existing 18 still green after the fixture-helper change, which was written so
  their meaning is unchanged (`side` defaults to `None`).
- `run_guards.py --base-ref main`: **PASS 38 · FAIL 1**, the failure being
  `layer-guard` exit **127** (`lint-imports` absent locally); after installing
  import-linter it reports *"Contracts: 6 kept, 0 broken"*. **39/39 relevant pass.**
- `ruff` **clean** at the CI-pinned version. ⚠️ My first measurement said "45
  errors vs 30 baseline" and was **wrong** — it used unpinned ruff 0.16, whose
  default-ruleset expansion `requirements-dev.txt` explicitly pins against. At
  `0.15.22` both baseline and branch are clean.
- Negative controls hold for the two likeliest false-positive classes: a
  refusing leg (has rows, has an owner) and a packages-only leg.

## Documentation Updated
- `docs/claude/WORKPLAN-2026-08-29.md` — Lane P table + Recommended order.
- `docs/claude/health-review-backlog.json` — 1 resolved, 3 filed (all via
  `backlog_append`; the resolve was a `detect_format` round-trip, 7/1 numstat in
  a 1013-row file, no reformat churn).
- This log.

## Contradictions or Drift Found
1. **The workplan listed a completed item as open for three days** (P1), at
   position 3 of the recommended order. Corrected.
2. **A `high`-severity backlog row stayed `open` after both its defects were
   fixed in the same overnight PR that followed its filing.** Resolved with
   evidence. The pattern — file in the evening, fix in the same PR, never close —
   is worth noticing beyond this instance.
3. **Two guards caught real defects in this session's own work**, which is the
   system working: `timestamp-comparison-guard` (below), and the guard runner
   refusing to scan a stale `/tmp/pr.diff` rather than report a green having
   checked nothing.

## Risks and Follow-Ups
- `BL-20260830-TREND-DONCHIAN-SOL-SIGNALS-144-TIMES-AND-JOURNALS-NOTHING-ON-BYBIT-1` (**high**) —
  the detector makes it visible; **the cause is undiagnosed and the leg is still
  signalling into a void**. Leading hypothesis is a per-SYMBOL arbitration loss
  (`aggregate_intents`, Lane P/P3 — `bybit_1` also runs `trend_donchian_sol_4h`
  and `sol_pullback_2h` on SOLUSDT), which would make the silence *expected but
  unlogged*; the alternative is a wiring bug. **Opposite remedies — establish
  which before changing anything.**
- `BL-20260830-SIGNAL-JOURNAL-AXIS-HAS-NO-LIVE-ALERT-ONLY-AN-OFFLINE-AUDIT`
  (medium) — the blind spot is narrowed, not closed.
- `BL-20260830-COMPAT-TABLE-OMITS-THE-SIZE-BASIS-SO-UNGRADED-DOES-NOT-SAY-WHY` (low).
- ⚠️ `alpaca_live` graded **ROUTE** on the compat run. That is a 0-bps harness
  verdict and **must not** be read as unblocking the go-live —
  `OI-20260829-ALPACA-GOLIVE-BLOCKED-ON-T1-SETTLEMENT-MODEL` is loud and open,
  and a compat verdict is not a settlement model.

### A bug this session wrote, and what caught it
The first draft compared `order_packages.created_at >= datetime('now', ?)` raw.
That column is ISO-8601 with a `T`; `datetime('now')` is space-separated;
compared as strings they agree on the date and disagree at character 11
(`T` 0x54 vs space 0x20), so every package on the boundary **date** sorted as
in-window. The direction is the dangerous one — an over-counted package makes a
leg that journalled nothing look like it journalled something, **suppressing the
very finding the axis exists to raise**. `timestamp-comparison-guard` caught it.

⚠️ **The first regression test for it was VACUOUS and I only found that by
mutation-testing it.** It used a 90-day-old package, which the buggy string
compare also excludes, so it passed with the bug reintroduced. It now places the
row one second before the boundary instant, read back out of SQLite, and fails
when the bug returns. **Noted because the sibling test
`test_window_boundary_does_not_swallow_a_day_on_the_iso_separator` uses a
30-day-old fixture for the same claim and looks like it has the same problem —
unverified, not fixed here.**

## Deferred Items
- **P3** (Tier-3, arbitration scope) — untouched.
- The live alert for the new axis — filed, deliberately not half-built.
- Registering `signal_journal.state` with `collapsed-state-guard` — **not done
  on purpose.** The guard requires every state to be branched on by a real
  consumer; registering today would either fail or invite the decorative branch
  the guard exists to prevent. The four states *are* reported distinctly
  (`signal_journal_state_counts`), which is the honest precondition, but
  `unknown` still has no per-state consumer branch.
- `OI-20260830-E35-…` observation — see Wrap-Up; the register file is held by a
  concurrent session.

## Next Recommended Sprint
Diagnose `trend_donchian_sol` (read the 144 audit rows' `reason` + the
`regime_hard_gate` rows for the same ticks) — it is the one open item with a
live leg behind it. Then Lane A on Monday's US open, which is calendar-blocked
and cannot move.

## Wrap-Up Check
- [x] Board `▶️ START` posted before the first substantive tool call; `✅ DONE` at wrap.
- [x] Tests + guards green; lint clean at the pinned version.
- [x] Backlog rows filed through `backlog_append`, no reformat churn.
- [ ] `OPEN-ITEMS.json` **NOT updated — deliberately.** PR #10482 (concurrent,
      live) holds that file. The e35 observation below is recorded here and on
      the board instead of racing it.

### `OI-20260830-E35-GEOMETRY-SHIPPED-TO-9-LEGS-NOT-YET-LIVE-VERIFIED` (loud) — carried, half-cleared
- **(a) config read-back — SATISFIED.** All **11 field values across 10 legs**
  read back from the live `/api/bot/config`, held leg included
  (`trend_donchian_eth_prop` still 2.5). `/api/diag/version` shows the web-api
  process running `git_sha 892c9a2c` — the e35 shipping commit itself.
- **(b) a closed real-money trade under the new geometry — NOT satisfied.**
  There IS a closed real-money row on an e35 leg since the deploy
  (`trend_donchian_eth_4h`, trade 4904, `bybit_2`, closed 09:48:15Z) and **it
  does not count**: `openedAt 2026-08-21T21:54:54Z`, nine days *before* the
  deploy, so it carries the OLD geometry. It also fails all three axes
  `PB-20260830-XRP-4H-FIRST-CLOSE-…` warns about — `closeReason: reconciler`,
  `pnlProvenance: estimated`, `journalTrust: known_divergent`.
- **Why nothing has opened yet:** the donchian legs are inside their channels
  (SOL close 104.64 within [100.53, 110.58]); every recent audit row is `_eval`
  with `side: none`.
- ⚠️ **The `clears_when` wording is a trap and should be sharpened.** *"at least
  one CLOSED trade exists on a real-money leg under the new geometry"* reads as
  satisfiable by trade 4904 — it is a closed trade, on a real-money e35 leg,
  after the deploy. What is required is a trade **OPENED** after the deploy.
  Recommend the owning session amend it to say so.


---

## Addendum — unit 2: the detector's finding, DIAGNOSED (2026-08-30, same session)

Unit 1 shipped a detector and filed `high` that the cause was unknown. It is now known,
and it is an **arbitration loss, not a wiring bug** — the first of the two candidate
causes, which had opposite remedies.

**The decisive evidence** (`/api/bot/allocator/soak?symbol=SOLUSDT`, row 2026-08-29T14:55:37Z):

| candidate | entry | sl | confidence | `ev_net_r` score | routed |
|---|---|---|---|---|---|
| `trend_donchian_sol` (bybit_1) | 105.33 | 103.82107143 | 0.9941 | **6.811619** | ✗ |
| `trend_donchian_sol_prop` (breakout_1) | 105.33 | 103.82107143 | 0.9941 | 5.906347 | ✓ |

`executed_strategy_id: trend_donchian_sol_prop` · `allocator_choice: trend_donchian_sol` ·
`agree: false` · `regret_score: 0.905272`.

Identical entry, SL and confidence: these are the **same 1h Donchian strategy on SOLUSDT
routed to two accounts**. `aggregate_intents` picks ONE winner per SYMBOL **globally,
before account fan-out**, so they collide and the prop twin wins — which is why the
`bybit_1` leg never reaches an order package. Over the SOLUSDT soak population:
`total_scanned 47, disagree 42, disagree_pct 89.4, mean_regret 0.778926`.

**⚠️ The routed leg is the lower-scoring one, by the system's own scorer.** And this is
not a lone reading: **Lane R's R2** independently recorded the same inversion from the
backtest side (`trend_donchian_sol` EV +$1,162 / P 0.9137 vs the routed
`trend_donchian_sol_prop` +$611 / 0.7603). R2 saw the outcome; this is the mechanism.
The two are **the same defect from two ends** and the workplan now says so, so they are
not worked twice.

### Two causes REFUTED, recorded so they are not re-tested
- **Not the loaded-strategy set.** 52 strategies loaded incl. `trend_donchian_sol`
  (`running: true`, live on `bybit_1`, trader ticking at 1.6s age); all six SOL legs
  resolve an intent builder and carry `symbols=['SOLUSDT']`.
- **Not a regime OFF-cell.** `regime_hard_gate` **and** `regime_shadow_gate` on SOLUSDT
  over three days return **zero** rows — consistent with the standing record that no SOL
  `trend_vol` cell is authored and the 2026-07-06 walk-forward says none should be.

### A second defect found while tracing it
`pipeline_result` for SOLUSDT at 14:59:04 reads `reason='no_signal'` — **0.9s after two
legs logged `side=buy`**. The information dies in three stages: `intents.py` reaches
`_flat_position(reason='no_intents_for_symbol')` *after* the regime gate has filtered
candidates (so one string covers "nothing signalled", "all gated", and "intents lost");
`_desired_to_pipeline_signal` correctly carries it into `meta`; then `pipeline.py:614`
overwrites it with the generic literal. Filed as
`BL-20260830-PIPELINE-RESULT-REPORTS-NO-SIGNAL-WHEN-TWO-LEGS-SIGNALLED-AND-LOST-ARBITRATION`.

**That is the audit-side twin of the two-month blind spot**: the journal had no row *and*
the audit affirmatively said nothing signalled, so both surfaces agreed on a false
negative. The true state existed only in `allocator_soak` — observe-only, and read by no
health check.

### What is NOT done, and why
**The remedy is Tier-3 and is not mine to push.** It is Lane P/**P3** — decide whether a
prop leg and a paper/real leg of the *same* strategy on the *same* symbol should compete
in one global arbitration at all, or fan out per account. That is order routing.
⚠️ **Do not de-route either leg on this evidence**: the allocator's live score and R2's
backtest EV agree the `bybit_1` leg is the *better* twin, so the naive fix removes the
wrong one.


---

## Session close — 2026-08-30

### Shipped
| PR | sha | what |
|---|---|---|
| **#10485** | `ea6e25a` (merged) | P1 verified by exercising it · P2 detector (`dead_leg.signal_journal_state_for` + audit consumer) · the arbitration-loss diagnosis · 4 backlog rows + 1 resolved · workplan P1 correction |
| **#10495** | open, auto-merge armed | the `no_signal` audit fix (both halves) · the e35 `clears_when` sharpening |

### Docs sweep
`canonical-doc-coherence` passes; instruction hierarchy mirrors; no removed gate
described as live; no 7-stage ladder in the catalog.

**One real drift found and fixed, pre-existing and not from this session:**
`docs/workplan.md` § "Required pre-filled values" instructed Claude to *use*
`VM_HOST = "158.178.210.252"` — the x86 micro **terminated 2026-06-16**. The
file's superseded banner mitigated it but the block still prescribed an action,
and its *"any notebook or operator-run script"* framing predates the autonomy
contract. Flagged as dead in place, values kept commented as the record, and
pointed at the single source (`ARCHITECTURE-CANONICAL` § "VM topology").

**Decision-landing:** the arbitration finding landed in **ROADMAP.md M18** — the
correct home, because M18's own premise is *"money is never stranded on a worse
trade when a better one exists"* and this shows it is, 82.5% of the time, for a
reason no allocator can fix. The row now warns that the soak's headline
`mean_regret` is **inflated by the twin artifact** and that the 2026-06-30
"EV-scorer selection does not beat dumb priority" finding was measured on the
**un-separated** population.

### What is NOT done, stated plainly
- **P3 is not built.** `trend_donchian_sol` is still signalling into a void
  right now. Direction chosen, evidence gathered, nothing shipped.
- **The live alert for the signal-journal axis is not built** — the detector
  only fires when someone runs the audit.
- **The compat-table size column is not done.**
- **e35 half (b) is still unmet** — nothing has opened on those legs yet.

### Two corrections this session made to its own claims
1. **"The prop twin always wins" — WRONG as stated.** True of both donchian
   pairs, false on ETH pullback where the non-prop leg won. The arbitration is
   account-**blind**.
2. **"45 ruff errors vs a 30 baseline" — WRONG.** Measured with unpinned ruff
   0.16, whose default-ruleset expansion `requirements-dev.txt` explicitly pins
   against. At the CI-pinned `0.15.22`, baseline and branch are both clean.

### Process notes worth carrying
- **Merge starvation is real here.** #10485 conflicted **twice** on
  `health-review-backlog.json`; `main` moved five times inside the ~13-minute CI
  windows. All four checks went green on one head at 12:35:33Z and auto-merge
  still could not fire because a conflict had appeared meanwhile. Claiming the
  slot did **not** stop another session merging through it (#10490 — harmless,
  it touched only a queue YAML, verified rather than assumed).
- **File backlog rows as the LAST commit before pushing.** That array is the
  single most collision-prone file in the repo
  (`BL-20260821-BACKLOG-JSON-IS-A-SHARED-MUTABLE-ARRAY`).
- **Every PR event this session arrived for a superseded head.** Three of three.
  Acting on any without a fresh fetch would have produced a confident wrong
  conclusion about readiness.

---

## Addendum — unit 3: P3, the measurement half (2026-08-30, same session)

Operator directed that the silent leg be wrapped up before a new session. The
Tier-1 half shipped; **the Tier-3 remedy did not, and the leg is still
signalling into a void.**

**Shipped:** `src/runtime/arbitration_fanout.py` — a PURE assessment (no I/O, no
audit emission, no order path, so the policy is arguable in tests rather than
against a live position) plus `arbitration_fanout_soak.py` at
`ARBITRATION_FANOUT_MODE=annotate`, wired observe-only beside the existing M18
allocator soak in `intent_multiplexer`, with its
`/api/diag/log_file?name=arbitration_fanout_soak` entry **in the same commit as
the writer**.

**Verified against the live case before wiring anything:** fed the real
`accounts.yaml` roster with `[trend_donchian_sol, trend_donchian_sol_prop]` and
winner `trend_donchian_sol_prop`, it returns `starved: ['bybit_1']`,
`breakout_1: routed`, `accounts_graded: 2`.

### A design constraint that shaped the whole thing
The obvious implementation — re-run `aggregate_intents` on each account's subset
to elect a per-account winner — **re-enters `_hard_regime_gate` and would re-emit
a `regime_hard_gate` audit row per account per tick**, corrupting the one signal
that cleanly partitions "would have gated" from "did gate". That is the evidence
this whole lane depends on. So the soak measures **starvation** instead:
side-effect-free, no second copy of the winner rule, and sufficient to size the
change (an account never starved gains nothing from fanning out).

⚠️ **The cost of that choice is stated in the module, the row and the workplan:
a `starved` row does NOT mean "this account would have traded."** Whether its
candidate survives its own gate and conflict resolution is unmeasured.

### Gate polarity — deliberately opposite to two siblings
`ARBITRATION_FANOUT_ACCOUNTS` **empty means NONE**.
`CONVICTION_SIZING_ACCOUNTS` and `NETTING_ATTRIBUTION_ACCOUNTS` read empty as
ALL — which `CLAUDE.md` itself calls *"not a safe default, it is the widest
one"*. This one would arm a change to **which account an order routes to**, so
it copies `PROTECTION_REASSERT_ACCOUNTS`. A test asserts it and says in its own
docstring that harmonising it to match the siblings would be the bug, not the
fix. The allowlist scopes the **binding, never the measurement**.

`apply` is **not implemented** and does not pretend to be — refused back to
`annotate`, with `apply_implemented: false` beside the effective `mode` and the
requested `global_mode` on every row.

### Tests: 19, and one I caught being vacuous
`test_the_soak_call_cannot_alter_the_routed_signal` as first written set a flag
and asserted the flag — it proved nothing. Replaced with a **structural** check
that parses `intent_multiplexer`, locates the soak block and asserts it contains
no assignment to `signal`, no subscript write and no `return`.
**Mutation-checked:** injecting `signal["_fanout"] = True` into the block makes
it fail. That is the claim the PR rests on — at `annotate` the live path is
unchanged — proven rather than asserted from the diff.

## Addendum — unit 4: the reasoning that produced the bug (2026-08-30, same session)

Unit 3 shipped the measurement half of the fan-out. This unit went after the
remedy, took a different one than planned, and found the cause written down in
prose.

### The planned fix was evaluated and REJECTED, on evidence

The operator's chosen remedy was **"set explicit priorities instead"** — leave
the tiebreak comparison alone and give the colliding legs real `priority:`
values so the tiebreak never reaches the name comparison. It does not work
here, and shipping it would have traded one silent failure for another.

Two verified facts kill it:

1. **The twins route to DISJOINT accounts.** `trend_donchian_sol` →
   `{bybit_1}`, `trend_donchian_sol_prop` → `{breakout_1}`; same shape for the
   ETH pair. They share entries by design (the prop variant differs only in
   exits), which is why they collide on **every** bar rather than
   intermittently. So the correct outcome is that **both** trade.
   `aggregate_intents` elects exactly one winner per symbol, so any ordering
   starves one of them — and raising the base leg above its twin would starve a
   **Tier-3 operator-approved live prop leg**, on the account where a breach is
   terminal.

2. **A two-leg fix would not even settle the symbol.** SOLUSDT is contested by
   **six** enabled legs and ETHUSDT by six. `trend_donchian_sol` and
   `trend_donchian_eth` lose to **all five** of their rivals, in **both**
   tiebreak branches — four others would still outrank the base leg after the
   twin was ordered.

Filed as `BL-20260830-PRIORITY-CANNOT-RESOLVE-A-DISJOINT-ACCOUNT-TWIN-PAIR`
(tier 3) rather than half-built. **The fan-out remains the only remedy that
expresses the intended behaviour.**

### What was actually wrong: the map's own reasoning

Nearly every row of `DEFAULT_PRIORITIES` justified its value of `0` with some
form of *"this leg runs ALONE on its (symbol, account), so priority is moot —
it never arbitrates against another strategy."*

`aggregate_intents` elects one winner **per SYMBOL, globally, before** the
per-account fan-out in `Coordinator.multi_account_execute` — it never sees an
account. The justification describes a scope the aggregator does not use, and
the values were chosen on it. **Measured against the live config: 12 symbols
are contested by more than one enabled leg** — BTCUSDT 7, SOLUSDT 6, ETHUSDT 6,
XRPUSDT 4, MGC 3, AVAXUSDT 3, and six more at 2 — every contesting leg at `0`,
so winners are decided entirely by name spelling.

The two branches also disagree with each other by construction: the same-side
branch maximises a negated-ord tuple (a strict **prefix always loses**, so the
longer name wins), while the opposing-side branch sorts ascending (the
**shorter** name wins). The same two legs get opposite winners depending on
whether they agree on side.

### Second finding: `execution: shadow` does not keep a leg out of the election

That gate is the `execution_mode(...) == "shadow"` fold into `effective_dry` at
`coordinator.py:1279`, inside `multi_account_execute` — **downstream of this
election**. A shadow, data-only leg can therefore win a symbol and silence
every live leg on it for that tick. `eth_pullback_prop_2h` (shadow) beats
`trend_donchian_eth` (live) head-to-head today, and **6 of the 12 contested
symbols carry at least one shadow leg** (BTCUSDT: 4 of 7). Six comment blocks
asserting *"execution:shadow … so its priority never arbitrates a real order"*
were corrected: it never **places** a real order; it can certainly **suppress**
one. The safety-floor reasoning for the low values is still sound and the
values are unchanged — only the never-arbitrates claim is withdrawn.

### What shipped (PR #10507)

Comments and a test. **No priority VALUE changed — behaviour byte-identical.**
Six mutation-verified tests in `tests/test_priority_arbitration_scope.py`
pinning the contest sets, the disjoint-account routing, the spelling-decides
property, the all-five-rivals loss and the shadow-beats-live case. They read the
**real config on purpose** — a fixture would let the config drift away from the
assertion, which is the failure the file exists to prevent.

### A test I caught being vacuous — the second this session

The `priority is moot` guard first excluded any line within six lines of a
`CORRECTED` marker. That was maskable: an unrelated correction elsewhere in the
block hid an injected bare claim and **the mutation passed**. Rewritten to key
on the retraction *quotation*, per-line, which a neighbour cannot mask. The
failure mode is written into the test's own docstring so the weaker form is not
reintroduced.

### CI caught a third thing, and it was right

`pytest-run` on #10501 failed on
`test_every_allowlisted_log_file_is_documented`: `arbitration_fanout_soak` was
added to the diag allowlist **in the same commit as its writer**, as that PR
claims — but `CLAUDE.md`'s `log_file` enumeration was never updated, so a
session reading `CLAUDE.md` could not know to ask for it. Exactly the
`BL-20260825-ALERT-AND-CADENCE-STATE-FILES-SHIP-WITHOUT-A-READ-SURFACE`
discipline the PR invokes, half-applied. Fixed and mutation-verified. **1
failed / 13,593 passed — a real failure, not flake.**

### e35 open item: half (b) still unmet, and a second near-miss recorded

Re-read the live surfaces. `trend_donchian_eth_4h` on **bybit_portfolio**
closed 2026-08-30T14:01:17Z at −$350.75 — after the deploy, on an e35 leg — and
is disqualified on **both** of the criterion's own tests: opened
2026-08-21T21:54:56Z (nine days pre-deploy, so it carries the old `2.5`
`atr_stop_mult`), and `bybit_portfolio` is **paper**, not real money. Its
`pnlProvenance` is `estimated`, which the criterion also excludes. State the
population: across all 40 rows of `/api/bot/trades/closed`, every e35-leg row
was opened 2026-08-21 or 2026-08-22, and the live `/api/bot/positions` (9 open)
carries no e35 real-money leg at all. **Nothing has opened on any of the ten
legs since the deploy** — this is "no trade yet", not "the geometry is not
applying". Recorded in `OPEN-ITEMS.json` so the next session cannot misread it.

### One probe of mine that was wrong, recorded because it nearly cost a step

I checked whether a backlog id existed on `main` with a grep whose character
class was `[A-Z-]*` — no digits. The id I was looking for
(`…-TREND-DONCHIAN-SOL-SIGNALS-144-TIMES-…`) contains `144`, so the pattern
matched nothing and I briefly concluded the row lived only on an unmerged
branch. It was on `main` all along. **A search returning nothing is not proof of absence**; the probe needed
a positive control it never got.

## Addendum — unit 5: the soak I shipped had the defect it exists to catch (2026-08-30, same session)

Units 1–4 closed with all four PRs merged. This unit is what turned up while
doing the ordinary session-close check that the P3 soak was actually writing
rows — and it is a finding against **my own code from unit 3**.

### The soak IS live and IS capturing the real signal

`/api/diag/log_file?name=arbitration_fanout_soak` — `present: true`, first rows
at 2026-08-30T14:25Z. So the writer is exercised, not merely deployed, and the
"shipped but unexercised" concern that applies to two other open items does not
apply here.

Better: **the lane's thesis is confirmed live.** Two rows show `bybit_1`
**starved** while `breakout_1` **routed**, winner `trend_donchian_eth_prop` —
the exact disjoint-account twin pair from
`BL-20260830-PRIORITY-CANNOT-RESOLVE-A-DISJOINT-ACCOUNT-TWIN-PAIR`. The defect
this lane spent four PRs characterising is now observable in production data.

### And the headline metric is wrong

`fanout_state_for` grades an account **`starved`** on a tick where **no strategy
won the symbol at all**. The docstring literally holds — `starved` is "held a
candidate and did not get the winner", and with no winner the account did not
get one — but it collapses two conditions that mean opposite things:

- **(a)** another **account** got the winner and this one was starved of it —
  the defect the soak exists to measure;
- **(b)** nobody won, so nothing was routed anywhere and no account lost
  anything.

**STATE THE POPULATION.** First 9 rows, 14:25Z–19:03Z (BTCUSDT 7 / ETHUSDT 2),
**13 account-gradings**:

| population | gradings |
|---|---|
| no winner elected | `bybit_1` ×7, `bybit_2` ×2, `bybit_portfolio` ×2 = **11** |
| a winner elected | `bybit_1` starved ×2, `breakout_1` routed ×2 = **2 genuine** |

So `starved_count` **overstates real starvation ~5.5× on this sample** — in the
sole evidence base for the Tier-3 per-account fan-out decision. n=9 over ~4.6h
on one process is small and BTCUSDT-dominated; 11 vs 2 is not a sampling
artefact, but the precise ratio is not load-bearing.

### Why the tests did not catch it

**All 19 unit tests pass.** They assert the definition *as written* rather than
whether the two populations are *separable*. That is the whole lesson of this
unit: unit 3's PR argued at length for four never-collapsed states and shipped a
fifth condition collapsed into one of them — and the collapse was invisible from
the code and the suite, and obvious from ten minutes of production rows.

This is the same class the repo already names (`collapsed-state-guard`,
§ "Collapsed states"), applied to code written the same day by the session that
was invoking that discipline. Filed as
`BL-20260830-FANOUT-SOAK-GRADES-A-NO-WINNER-TICK-AS-STARVED` (severity high,
tier 1) rather than hot-fixed at session end; the fix is the next session's
first work item, because the soak's numbers gate a Tier-3 routing change and
should not accrue further under a misleading label.

### Two process corrections recorded against myself

1. **I reported the coordination-board `✅ DONE` as posted when it was not.** I
   posted the `▶️ START` and then asserted the DONE in a summary without making
   the call. Caught by checking rather than trusting the transcript; posted for
   real at session close.
2. **I wrote the handoff prompt while #10513 was still in flight**, which the
   `session-handoff` skill's own gate forbids when a downstream session is
   pointed at that branch. Corrected by driving #10513 to green and amending the
   prompt so the next session starts from `main` with no dependency on it.

### A repo-level CI failure re-observed, not new

#10513's `pull_request` checks **never attached** — zero check runs, and marking
it ready-for-review did not fire them either despite `ready_for_review` being in
the workflow's own `types` list. This is `BL-20260730-PR-CI-NOT-ATTACHING`
recurring. All four required workflows were dispatched manually
(`workflow_dispatch`), which is the remedy those files exist to provide, and
they **did** attach as PR checks and satisfy the required contexts. Worth
knowing for any session that sees an empty check list: the file's own comment
puts it best — *"the failure mode is not 'a check went red', it is 'a check
silently did not run', which renders identically to green."*

---

## Addendum — unit 6: the conflation FIXED, on the live rows (2026-08-30, next session)

**Objective.** Fix `src/runtime/arbitration_fanout.py::fanout_state_for`, which
graded an account `starved` on a tick where **no strategy won the symbol at
all** — the defect unit 5 filed as
`BL-20260830-FANOUT-SOAK-GRADES-A-NO-WINNER-TICK-AS-STARVED` rather than
hot-fixing at session end. **Tier 1** (observe-only soak, no order path, no
config, no VM).

### The measurement, re-taken first — not the test suite

The handoff was explicit that all 19 unit tests passed and that the suite was
therefore not the place to start. Re-read the live file before touching
anything: `/api/diag/log_file?name=arbitration_fanout_soak&lines=1000` returned
**9 rows** — the COMPLETE file, not a tail — spanning
**2026-08-30T14:25:15Z → 19:03:56Z**, carrying **15 account-gradings**.

| population | gradings |
|---|---|
| graded `starved` in the live file | **13** |
| …of which genuine starvation (a winner existed, elsewhere) | **2** |
| …of which no-winner ticks (`winning_strategy: null`) | **11** |
| graded `routed` | 2 |

The 2 genuine ones are the lane's own thesis live and identical:
`trend_donchian_eth_prop` (`breakout_1`) taking ETHUSDT from `bybit_1`, at
16:16:24Z and 17:00:18Z. The other 7 rows are BTCUSDT with no winner —
`htf_pullback_trend_2h` (1 account) ×5 and `trend_donchian` (3 accounts) ×2.

**One correction to the filed row, in the direction that matters.** It says the
headline overstates *"by ~5.5x"*. 5.5 is `11/2` — no-winner gradings per genuine
one. The factor by which **`starved_count` itself** overstates the finding is
`13/2 = **6.5×**`, because the old count included the 2 genuine gradings too.
Same rows, same counts; the row's arithmetic picked the wrong pair. Recorded in
the row rather than silently corrected.

### Why "no winner ⇒ starved" is wrong even though the docstring held

The old definition was internally consistent — *"held a candidate and did not
get the winner"*, and with no winner nobody got one. It is wrong for what this
soak is FOR. Starvation here means **another account took the winner from me**;
that is the only condition a per-account fan-out can fix. A no-winner tick has
no other account to have lost to, and its cause lives upstream (every candidate
held, gated, or flat). Counting it inflates the case for a Tier-3 routing change
with rows that change is not the remedy for.

### What shipped

- **Two new states**, six in all: `no_winner`, and `winner_unattributed` — a
  winner that resolves to **no account** in the roster. The second is a roster
  gap rather than a routing loss, it graded `starved` before too, and it is
  cross-checked by `unattributed_strategies`, which independently names the
  unmapped winner. **Never observed live**, and the row says so rather than
  implying it was found.
- **`winner_scope_for`** grades the TICK once (`attributed` / `no_winner` /
  `unattributed`) and every account on that tick is graded against the same
  reading. An unrecognised scope returns **`unknown`**, never `attributed` —
  defaulting the other way would silently promote an unreadable tick INTO the
  finding, which is the exact direction being corrected. Same discipline as
  *"a count we cannot read is not a count of zero"*, one field over.
- **The three populations are published side by side** — `starved_count`,
  `no_winner_count`, `winner_unattributed_count` — and together with `routed`
  they sum to `accounts_graded` by construction, so the partition is checkable
  rather than trusted.
- **A `no_winner` row is still WRITTEN.** It is the DENOMINATOR. Dropping it
  would have left a reader the finding with no way to see how often the symbol
  had contenders and still routed nothing, and would ALSO have made the row rate
  collapse mid-file when only the definition changed.
- **`fanout_schema: 2`** marks a post-split row. A row with no `fanout_schema`
  key is pre-split and its `starved_accounts` conflates both, so pooling them
  without saying so re-creates the overstatement in the analysis instead of the
  code. `apply_scope` stays keyed on the **starved** set only — a no-winner
  account is not one a fan-out would rebind.

### Validation

- **26 tests pass** in `tests/test_arbitration_fanout.py` (19 before). The test
  that asserted *"a flat tick starves every account that wanted to trade"* is
  **inverted**, carrying a note that restoring it is the bug.
- **The live 9 rows are pinned as a test** (`test_the_live_nine_rows_split_eleven_two`)
  with the roster read off the rows' own `per_account[*].candidates` rather than
  from `accounts.yaml`, so the fixture cannot drift from the rows it claims to
  replay; it asserts the per-row graded counts `[1,1,1,3,2,3,2,1,1]` reproduce.
- **Independently re-derived** by a second script that rebuilds the roster from
  the raw JSONL and asserts each row's own `accounts_graded` reproduces:
  `13 → 2 starved + 11 no_winner + 0 winner_unattributed`, 15 gradings, 6.5×.
  Two derivations rather than one, because the first was mine.
- **168 intent-layer tests pass** (`test_aggregate_intents_*`,
  `test_multi_strategy_intents`, `test_intent_*`) — routing is untouched.
- **Guards: 39 pass, 0 real failures.** `layer-guard` exits 127 in this sandbox
  (`lint-imports` absent); installed it and ran it directly — **6 contracts
  kept, 0 broken**. The first guard run was rejected for the right reason: the
  work was uncommitted, and guard relevance is computed from a commit range, so
  it reported *"this is NOT a clean bill of health for your change"*. Committed
  and re-ran.

### Deliberately NOT done

- **The per-account fan-out itself.** Still Tier-3 and unbuilt. Fixing the
  measurement is the whole of this unit; the corrected soak now has to accrue
  before anything is built on it.
- **Not registered with `collapsed-state-guard`.** The guard requires every
  state be branched on by a real consumer, and `routed` / `no_candidates` have
  none — registering today would either fail it or invite the decorative branch
  it exists to prevent. This is the same reading `CLAUDE.md` records for
  `BYBIT_HEDGE_MODE_SYMBOLS`, and it is stated here so a later session does not
  read the absence as an oversight.
- **No new backlog row filed.** The finding was already filed; the row is
  updated with the fix, the corrected ratio, and why it stays `open` — its
  `resolution_criteria` requires re-reading the LIVE log, which needs the merge
  to reach the trader first.

### What is unproven, stated plainly

The fix is verified against a **replay of the old rows**. Nobody has yet seen
the corrected writer produce a row. Tracked as
`OI-20260830-FANOUT-SOAK-SPLIT-SHIPPED-NOT-YET-READ-LIVE` (loud, 2-day cadence),
whose `clears_when` needs BOTH a live row carrying `fanout_schema: 2` — proving
the deployed trader runs the split, not merely that the PR merged — AND a
post-split file where `starved_count` and `no_winner_count` are both non-zero.
⚠️ The second half is the load-bearing one: only 2 of 15 gradings were the
finding, so the corrected soak may read near-zero for a while, and *"the split
works"* and *"the collision stopped happening"* render **identically** on a
`starved_count` of 0.

### The transferable lesson

A suite of 19 tests passed continuously while the headline number in the sole
evidence base for a Tier-3 change was 6.5× wrong. They asserted the definition
**as written** — including one that asserted the defect explicitly, in a
docstring that argued for it. No test asked whether the two populations a reader
would act on were separable at all. The defect was found by reading nine live
rows, and the fix is pinned by a test built from those same rows rather than
from the definition.

---

## Addendum — unit 7: making the pairs sleeve's hedge-mode question gradeable (2026-08-30, operator-directed)

**Objective.** Operator asked, after unit 6's incidental finding, what needs fixing
for the pairs-sleeve measurements. **Tier 1**, except one additive touch to an
order-path file (below), which the operator approved in-conversation after
reading the exact change.

### The diagnosis changed once I read the code instead of the log

Unit 6 filed "`pairs_soak` records no `position_idx`" from the *log*. Tracing the
code first — which the fix required anyway — inverted the framing:

**The order path was never broken.** `_place_pair` → `execute_pkg`
(`execute.py:118`) → `_submit_order` (called at `:567`, its **sole** call site) →
`apply_position_idx` (`:1538`). A pairs leg on an armed symbol **did** carry a
`positionIdx` all along. `apply_position_idx` *returns* `PositionIdx(idx, state,
reason)`, and `_submit_order` called it **bare** — discarding the answer one line
after computing it — while `execute_pkg` returns only a `trade_id`. So the single
place that knew which venue book an order was sent against threw it away.

That distinction is worth stating plainly because the backlog row, read quickly,
could be taken as "hedge mode is broken for pairs". It is not, and the row now
says so.

### The second gap mattered more than the one I filed

`OI-20260830-BYBIT-HEDGE-MODE-ARMED-BUT-UNEXERCISED` needs **three** things, and
I had only chased the first. Criterion (3) requires a **concurrent directional
position** on the same symbol — because under one-way netting a pairs leg only
strands when there is a directional book to net against. **That was equally
unrecorded**, so stamping `position_idx` alone would have left the row exactly as
unsatisfiable as before. A pair that opened cleanly having never faced a
directional position is not evidence of anything.

### What shipped

- **`execute.py` — additive only, and this is the order-path file.** An optional
  `observed` out-dict on `_submit_order` + `execute_pkg`, default `None`. Every
  pre-existing caller is byte-for-byte unchanged; **no wire-payload change, no
  control-flow change**. It is documented as write-only, and an AST test asserts
  the wire path never *branches* on it — an observability out-param that could
  alter a live order is the one way this could have been dangerous, so it is
  pinned structurally rather than asserted in prose.
- **`_directional_open_state(account_id, symbol, db_path)`** — three states,
  never collapsed: `present` / `absent` / **`unreadable`** (*we could not look*,
  emphatically not `absent` — only the second makes a clean open meaningful).
  One read-only SELECT on the journal the trader has already written; no socket.
  It **imports** `order_monitor._is_pairs_sleeve_row` rather than re-deriving the
  predicate, because that function's own docstring says two copies could drift
  into disagreeing about who owns a row — the seam its alarm came from. A test
  asserts the import and that no `startswith` re-implementation crept in.
- **`leg_placement` on `open`/`open_failed` rows** — per leg: `position_idx`,
  `position_idx_state`, `directional_open`, `placed`, `trade_id`. Kept
  **separate from `legs`** on purpose: `legs` is the pure decision's INTENT, and
  folding a plan into an outcome would give a `shadow_open` leg placement fields
  describing no order.
- **Ordering is load-bearing, not stylistic:** the directional read happens
  *before* the leg is placed. Afterwards it would partly measure our own pair —
  this leg is excluded as a pairs row, but the sibling leg on the other symbol is
  not.

### Two honesty constraints written into the field itself

1. **`position_idx` is what we SENT, never a venue read-back.** The criterion's
   own wording says *"the venue reports"*, and `/api/diag/bybit_open_orders` is
   that surface. Recording our sent value and letting it be read as venue
   confirmation would be precisely the semantic substitution unit 6 was filed
   about. The docstring, the CLAUDE.md row and the OPEN-ITEMS `clears_when` all
   say so.
2. **`directional_open: absent` on both legs does NOT clear the row.** Written
   into `clears_when`, because a clean open on a pair that faced nothing is the
   most likely-looking false positive available.

### Validation

- **15 tests** in `tests/test_pairs_leg_placement.py`, including the end-to-end
  wiring test that fails if anyone drops `observed=observed` from the
  `execute_pkg` call — a regression that is otherwise **silent**, since the soak
  keeps writing rows that have merely lost the field.
- **189 pass / 1 skipped** across every `test_pairs*`, `*execute*` and the
  fan-out suite — the order path is untouched in behaviour.
- Guards clean; `canonical-doc-coherence` and `stated-population-guard` pass on
  the CLAUDE.md edits.

### Not registered with `collapsed-state-guard`, and the honest reason

`bybit_position_mode`'s own docstring predicts it "becomes registrable in the
same change that first makes the allowlist non-empty". The allowlist is armed and
`CLAUDE.md` already records that prediction as **wrong**. This change does not
make it right either: the soak **records** `unresolved`, and recording is not
branching — `apply_position_idx` still leaves kwargs untouched on both `one_way`
and `unresolved`. Registering today would still invite the decorative branch the
guard exists to prevent. Stated here so a later session does not read the absence
as an oversight. (The module docstring still carries the stale prediction; left
for whoever owns that file, noted rather than swept.)

### Still unproven

Deployed, not observed. The clearing evidence needs a real pair to open after the
merge reaches the trader, with `directional_open: present` on at least one leg.
Tracked by the existing loud row rather than a new one.
