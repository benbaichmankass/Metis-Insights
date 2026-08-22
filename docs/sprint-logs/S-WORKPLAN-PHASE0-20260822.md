# Sprint Log: S-WORKPLAN-PHASE0-20260822

## Date Range
- Start: 2026-08-21T~20:00Z (operator approval of the synthesized work plan)
- End: 2026-08-22T~07:00Z

## Objective
- **Primary goal:** synthesize the two divergent work plans into ONE plan of
  record, get operator approval, then run the approved autonomous lane —
  Phase 0 (the retirement pass) and the restored Phase 2 items.
- **Secondary goals:** ship the one Tier-3 item the operator had already
  approved (T.1), and verify it against the live trader rather than assuming it.

## Tier
- **Tier 1 throughout, plus ONE pre-approved Tier-3 (T.1).**
- Justification: every other change is docs, tests, guards, or a read-only diag
  surface. T.1 (`src/runtime/market_data.py`) was approved by the operator on
  2026-08-21T18:05Z on the evidence in
  [`exit-eval-fetch-attribution-2026-08-21`](../research/exit-eval-fetch-attribution-2026-08-21.md);
  it is not on the Tier-3 hard-limit file list but alters price freshness behind
  live orders on four IB legs, which is why it was gated at all.
- **No Tier-3 gate was flipped on my own reading.** The three conditional
  pre-approvals the operator granted (T.2 pairs hedge mode, T.3
  `slv_trend_1h` demote, T.4 ETH promotion) were **not** exercised — see
  *Deferred Items*.

## Starting Context
- Active roadmap items: [`WORKPLAN-2026-08-21`](../claude/WORKPLAN-2026-08-21.md)
  Phase 0 + the T-items; M20 exit loop.
- Prior sprint reference: `S-WORKPLAN-REPLAN-20260821`,
  `S-WAVE0-EXIT-FETCH-20260821`, `S-SYSTEM-REVIEW-20260821`.
- Known risks at start: two sessions had produced two divergent plans; seven
  items from the original plan had been silently dropped in the replan; the
  backlog's own "378 open" population was not reproducible.

## Repo State Checked
- Branch or commit reviewed: `main` at `01e2d1c` → `693452f` → `35d6bcf`.
- Deployment state reviewed: live trader via `scripts/ops/diag_fetch.sh` over
  the Caddy host (`/api/diag/tick_cost`, `/api/diag/version`,
  `/api/diag/log_file?name=exit_loop_health`, `/api/diag/timers`,
  `/api/diag/broker_account_status`).
- Canonical docs reviewed: `CLAUDE.md`, `CLAUDE-RULES-CANONICAL.md`,
  `ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`, `docs/api-tier-policy.md`.

## Files and Systems Inspected
- Code: `src/runtime/market_data.py`, `src/runtime/tick_cost.py`,
  `src/web/api/routers/diag.py`, `src/web/api/routers/training_center.py`,
  `scripts/ops/pull_and_deploy.sh`, `scripts/deploy_pull_restart.sh`,
  `scripts/ops/bybit_bracket_audit.py`, `scripts/check_claim_basis.py`,
  `ml/cli.py`, `scripts/ops/train_and_register_ws5_baselines.sh`.
- Config: `ruff.toml`, `requirements-dev.txt`.
- Docs: the four canonical docs + `docs/api-tier-policy.md` + the three
  review backlogs.
- Services/timers: all 16 `ict-*` timers, read through the new
  `/api/diag/timers`.

## Work Completed

**11 PRs merged, zero left open.**

- **T.1 — `#10114`** (`1b05353`, the pre-approved Tier-3). Removed
  `interactive_brokers` from the connector-memo exclusion in
  `_client_cache_key`, memoizing IB on its **resolved connection identity**
  (`_ib_connection_identity`) rather than on the settings dict, so the memo key
  cannot drift from what would be constructed. Adds **no new socket sharing**:
  `IBMarketData.__init__` already takes its client from `get_ib_client()`, a
  process-wide registry keyed on `(host, port, client_id)`.
- **`#10115`** — restored the **seven items the replan dropped** as Phase 2,
  corrected the backlog population, and added the **autonomous lane** (three
  lanes, ordered work list, and an explicit "what must NOT happen overnight").
- **`#10117` (item 2.7)** — `scripts/ops/pull_and_deploy.sh` **inferred** the
  restart from `PRE_HEAD != POST_HEAD` and printed *"service bounced"* when the
  deploy script had exited at "nothing to deploy". Now measures
  `ActiveEnterTimestampMonotonic` + `MainPID` and returns a three-state verdict
  (`bounced` / `not_bounced` / `unknown`).
- **`#10118` (item 2.6)** — gave the bybit bracket audit a **price axis**
  (`agree` / `diverged` / `ungradeable`, envelope `price_state`), and
  root-caused the R-vs-dollars sign split.
- **`#10119` (item 0.1 Step 0)** — collapsed the backlog `status` field from
  **41 free-text values to a 6-value enum**, enforced by extending the
  **existing** `claim-basis-guard` (Phase 0 is a retirement pass — no new guard).
- **`#10120` + `#10121` (item 0.6)** — two read surfaces: `/api/diag/timers`
  (a timer's SCHEDULE, not just its state) and bybit account **identity**
  (`uid_groups` / `shared_uid_groups` / `unread_bybit_accounts`).
- **`#10122`** — added the `fetchvenue.<venue>` cut to `tick_cost`.
- **`#10123` (item 2.3)** — root-caused the registry `status` field.
- **`#10124` (T.2 STEP 0)** — answered by measurement.
- **`#10125` (items 2.1/2.2)** — tested the trainer-disk → training-refusals
  causal link.

## Validation Performed

- **T.1 live falsifier — PASSED.** Deployment confirmed by **ancestry**, not by
  a SHA string. Population: ONE process, `23:08:50Z → 04:48:04Z` (5 h 39 m),
  **n = 676 passes**; before = n = 433.

  | | before | after | |
  |---|---|---|---|
  | off-loop `fetch.15m` per pass | 1.002 | **0.494** | −50.7% |
  | mean exit pass | 42.3 s | **4.89 s** | **8.6×** |
  | `requirement_state` breaches | 28.9% | **5 / 675 = 0.74%** | ~39× fewer |

  `EXIT_LOOP_INTERVAL_SECONDS` is **no longer inert** (observed interval 30.2 s
  against the configured 30 s).
- **Falsifiers that FIRED before their fix** (the row's own required control):
  `test_pull_and_deploy_bounce_claim.py` 4 failed → 4 passed;
  `test_bybit_bracket_audit_rollup.py` 7 failed → 22 passed;
  `check_claim_basis.py` exit 1 planted → exit 0 restored, verified **without a
  pipe masking the code**.
- **Live reads:** `fetchvenue` reconciles against `fetch`/`fetchby` at
  **0.00%** (n = 106/106/106); IB is **18.9% of fetches but 87.5% of fetch
  seconds** (30× slower per call); `/api/diag/timers` returned 16 units with
  `could_not_look: 0`.

- **Gaps not yet verified:**
  - **The 60 s requirement is STILL BREACHED at the tail** — `max_interval_ms`
    **88.76 s**. Residual cause is
    `BL-20260816-IB-QUEUE-TIMEOUT-EXCEEDS-EXIT-BUDGET` (workplan item **1.0**).
    The 8.6× does **not** close it.
  - The **28.9% pre-figure's own process was not recorded beside it** and these
    counters are per-process, so the two rates are not guaranteed to share a
    denominator. Direction and order of magnitude are solid; the ~39× is
    approximate. Stated as such in all three surfaces that quote it.

## Documentation Updated
- Rules doc updates: `CLAUDE.md` — the `CANDLE_CACHE_TTL_*` row (see
  *Contradictions*); the `/api/diag/timers` + bybit-identity surfaces.
- Roadmap updates: this sprint's ledger row.
- Subsystem doc updates: `docs/api-tier-policy.md` (new route row; corrected
  total 96 → **97 of 97 routes documented**).
- Historical docs marked superseded:
  `docs/research/exit-eval-fetch-attribution-2026-08-21.md` carries an
  **OUTCOME** block; its proposal body is preserved **verbatim** as the record.

## Contradictions or Drift Found

Found by the closing `doc-freshness` pass. **All three were drift I introduced
by shipping T.1** and left standing for a day:

1. **`src/runtime/market_data.py:54`** — the function's OWN docstring still read
   *"**IB is deliberately excluded.** An `IBMarketData` holds a live socket on a
   specific clientId"*, contradicting the `interactive_brokers` branch **40
   lines below it in the same function**. False on both counts. **Fixed.**
2. **`CLAUDE.md:1068`** — *"The exclusion itself is UNCHANGED in code and its
   removal is a **Tier-3 proposal**, not applied."* **Fixed.**
3. **`docs/research/exit-eval-fetch-attribution-2026-08-21.md`** — opened *"This
   is a PROPOSAL. Nothing in `src/` was changed."* **Stamped, body preserved.**

Also corrected in-session: the **backlog population was not reproducible** —
"378 open" could not be derived; measured **121 strict / 241 `kept_open` / 410
non-terminal** across 41 distinct free-text `status` values. The old figure
reconciles as `open + kept_open` (396–397). Enum + guard shipped in `#10119`.

## Risks and Follow-Ups
- **Trainer disk is degrading**: 3.15 → 3.09 → **2.87 GB free (94.0% used)**,
  manifest-pin bound (111 of 115 versions pinned by 41 manifests). Needs an
  operator decision on remediation.
- **`BL-20260816-IB-QUEUE-TIMEOUT-EXCEEDS-EXIT-BUDGET`** is now the *only* thing
  between this system and the 60 s requirement — promoted to workplan item **1.0**.
- **Remaining product decisions (Tier 3):** T.2's pre-approval condition
  ("confirmed demo from a read") may be **unsatisfiable as written** — needs the
  operator to restate it or drop it.

## Deferred Items
- **T.2 / T.3 / T.4 were NOT exercised** despite conditional pre-approval. The
  operator's binding constraint is *"a condition I am unsure about resolves to
  NO"*, and T.2's condition is the one I am unsure about.
- Phase 0 **0.2** (~150 runner-less scripts), **0.3** (promote
  `check_unwired_artifacts.py` to blocking), **0.4** (collapse duplicated
  facts), **0.5** (guard-set review); Phase 2 **2.5** (auto-merge relay).

## Next Recommended Sprint
Item **1.0** — bound the IB fetch tail. It is the last step of work already
done, not a new front, and it is what the 60 s requirement now turns on.
