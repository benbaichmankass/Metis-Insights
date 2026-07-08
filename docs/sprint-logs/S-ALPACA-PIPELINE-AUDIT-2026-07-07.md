# Sprint Log: S-ALPACA-PIPELINE-AUDIT-2026-07-07

## Date Range
- Start: 2026-07-07 (operator flagged `alpaca_paper` creating a lot of orphaned trades)
- End: 2026-07-08 (final deploy-verification + backlog closeout)

## Objective
- Primary goal: root-cause and fix a NEW live-incident pattern — `alpaca_paper`
  generating orphaned trades distinct from previously-fixed bugs (the earlier
  reconciler mass-false-close defect) — without leaving the trading pipeline
  "running blind."
- Secondary goals: per explicit operator directive ("do a full audit of the
  entire Alpaca pipeline... don't want to keep fixing one bug just to create
  another" / "deep dive into issues before putting on bandaids"), audit the
  ENTIRE Alpaca integration end-to-end rather than patch the one symptom, then
  ship the full remediation ("finish the fix for the entry side as well — I
  want full pipeline stability").

## Tier
- Tier 2 (order-path / close-path / entry-path runtime changes on a live
  real-money-adjacent integration; each PR got explicit operator merge
  approval before shipping, per `CLAUDE.md` Permission Tiers).
- Justification: `AlpacaClient.close()`/`place()` are on the live order path
  for `alpaca_paper` and real-money `alpaca_live`; `order_monitor.py`'s
  re-adopt guard runs every reconciler tick.

## Starting Context
- Active roadmap items: M15 (Market & Platform Migration — Alpaca live
  wiring) is the owning milestone; this sprint hardens the execution path
  M15 built, it doesn't extend M15's strategy/account scope.
- Prior sprint reference: the earlier same-class defect
  (`BL-20260707-RECONCILER-MASS-FALSE-CLOSE`, fixed just before this sprint)
  was a truthiness bug in `account_open_positions`. This sprint's incident
  looked superficially similar (orphaned Alpaca trades) but the operator
  correctly flagged it as a DIFFERENT failure mode — confirmed true on
  investigation.
- Known risks at start: `alpaca_paper`'s balance/PnL visibility was already
  suspect from an earlier-session incident (a large negative equity reading);
  this sprint did not touch that (still needs a manual operator-side Alpaca
  console reset, out of Claude's tool access — communicated earlier, not
  revisited here).

## Repo State Checked
- Branch or commit reviewed: `claude/spy-dispatch-failures-ir2ktj`, rebased
  onto `main` repeatedly across the session as prior PRs merged (branch
  auto-deletes on merge; re-created from `origin/main` each time per the
  session's branch policy).
- Deployment state reviewed: live VM `git_sha` polled via the
  `[diag-request] version` issue relay after each merge to confirm
  `ict-git-sync.timer` actually rolled the fix forward (not just merged to
  `main`).
- Canonical docs reviewed: this file's own `CLAUDE.md` (permission tiers,
  VM authority split, "no default-off gate" Prime Directive — relevant to
  confirming the fixes below are baseline behaviour, not new flags).

## Files and Systems Inspected
- Code files inspected: `src/units/accounts/alpaca_client.py` (full file, incl.
  recent `git log -p` history), `src/units/accounts/execute.py`
  (`close_open_position`, `_submit_order`), `src/runtime/order_monitor.py`
  (`_recently_closed_adopted_orphan`, `_reconcile_orphan_exchange_positions`,
  `_close_unattributable_orphan` / `exit_coverage_resolver`,
  `position_snapshot_reconciler`), `src/units/accounts/oanda_client.py`
  (comparison read only, not modified).
- Config files inspected: none changed (no `config/*` Tier-3 files touched —
  this was infra/reliability hardening, not a strategy/account/risk change).
- Deployment files inspected: none (no systemd unit / workflow changes).
- Docs inspected: `docs/claude/health-review-backlog.json` (heavily edited —
  see Documentation Updated below).
- Services or timers inspected: `ict-web-api.service` (via `/api/diag/version`
  post-deploy polling only).
- GitHub Actions workflows inspected: none dispatched beyond the standard
  CI checks on each PR (`check`, `pytest-run`, `pytest-collect`, `ruff-lint`,
  `secret-scan`, `env-gate-guard`, `canonical-db-resolver`,
  `canonical-config-loaders`, `dry-run-guard`, `silent-empty-guard`,
  `repo-inventory`).

## Work Completed
- Item 1 — **Live-incident investigation**: traced a real SLV incident
  (2026-07-07 20:33:45 UTC) where `exit_coverage_resolver` phantom-closed an
  orphaned SLV short (1360sh @ $53.94) because `AlpacaClient.close()`
  treated Alpaca's HTTP-200 accept on `DELETE /v2/positions/SLV` as
  confirmed-flat with no fill verification. The position never actually
  flattened; 7 minutes later the reverse reconciler found it still open and
  bare-adopted it as a SECOND, fresh orphan row — two DB rows for one broker
  position, one carrying a fabricated mark-to-market PnL that's never
  corrected. Root-caused as structurally distinct from the earlier
  `account_open_positions` truthiness bug.
- Item 2 — **Full 4-agent parallel Alpaca-pipeline audit** (per explicit
  operator directive): independent read-only coverage of (a) order
  placement/sizing, (b) position/balance reads, (c) close/flatten paths, (d)
  reconciler/re-adopt-guard logic. Produced a prioritized, evidence-based fix
  plan instead of a one-off patch.
- Item 3 — **PR #5918** (merged, deployed): `AlpacaClient.close()` now
  cancels resting orders for the symbol BEFORE attempting the flatten (a
  resting order was blocking the close in the QQQ incident that first
  surfaced this class of bug).
- Item 4 — **PR #5923** (merged, deployed), three coupled fixes shipped
  together because the audit judged them tightly coupled:
  1. `AlpacaClient.close()` now polls `positions()` after the DELETE, bounded
     by new `ALPACA_CLOSE_CONFIRM_S` (default 6.0s, `<=0` restores legacy
     accept-is-success) — mirrors `IB_CLOSE_CONFIRM_S` exactly, including
     reusing IB's existing "not confirmed flat" retMsg substring so
     `order_monitor.py`'s cooldown/retry/consecutive-failure-alert machinery
     picks it up generically with zero other-file changes.
  2. `order_monitor._recently_closed_adopted_orphan` widened to also match a
     row closed via `exit_reason IN ('exchange_flat_reconciled',
     'exit_coverage_no_strategy')`, not just `setup_type='adopted_orphan'` —
     closes the re-adopt-guard gap that let the SLV phantom-close's
     re-adoption through with zero flap protection. Deliberately NOT
     widened to every close reason (a genuine broker-confirmed close like
     Bybit's `reconciler_filled` or a normal `sl_cross`/`tp_cross` exit must
     never suppress a legitimate new position).
  3. `AlpacaClient.balance()` / `buying_power()` truthiness bugs fixed — same
     bug SHAPE as the already-fixed `account_open_positions` bug, one layer
     up: a genuine `equity=0.0` / `regt_buying_power=0.0` was being treated
     as "could not read" and silently substituted with a less-authoritative
     fallback, which then made `Coordinator.multi_account_execute` size MORE
     permissively exactly when it should size MOST conservatively (real
     zero free margin).
- Item 5 — **PR #5924** (merged, deployed): entry-side counterpart. Scoped
  down from the full "entry accept-vs-fill gap" to the REJECTION half only
  (deliberately NOT the fill-price half — see Deferred Items).
  `AlpacaClient.place()` now polls the order by id after a successful POST,
  bounded by new `ALPACA_PLACE_CONFIRM_S` (default 3.0s, matching
  `IB_PLACE_CONFIRM_S`): a terminal rejected/canceled/expired status within
  the window surfaces as a failure so `_submit_order` raises and
  `_log_trade_to_journal` never writes a phantom `status='open'` row for an
  order the broker never actually executed.
- Item 6 — **PR #5930** (merged): closed out the health-review backlog
  record with deploy-verification notes on the two entries left `open`
  pending live confirmation (`BL-20260707-ALPACA-CLOSE-NOT-CONFIRMED-FLAT`,
  `BL-20260707-ALPACA-BALANCE-TRUTHINESS`) once the live VM's `git_sha`
  (`07f2b3fa`, PR #5924's merge sha) confirmed all fixes deployed.

## Validation Performed
- Tests run: `tests/test_alpaca_wiring.py` grew from ~21 to 34 tests across
  the three code PRs (bracket-order construction, confirm-poll rejection /
  still-pending / disabled-restores-legacy cases, balance/buying_power
  truthiness, close() idempotent-404, cancel-resting-orders, confirm-flat).
  `tests/test_reverse_reconciler.py` grew from 26 to 29 (widened re-adopt
  guard, incl. a new `_insert_closed_strategy_trade` helper). Full suite: 34/34
  in the Alpaca file; 323 across the broader order-management/reconciler
  surface, all green in CI on every PR.
- Dry-runs or staging checks: none beyond CI (Tier-2, not Tier-3 — no
  backtest-gate requirement for a bug fix that doesn't change strategy
  behaviour).
- Manual code verification: read `IBClient.close`/`place`'s existing
  `IB_CLOSE_CONFIRM_S`/`IB_PLACE_CONFIRM_S` poll-until-confirmed pattern as
  the design template (mirrored, not copy-pasted, since Alpaca's REST
  semantics differ from IB's async event-loop model).
- Gaps not yet verified: none of the four fixes has yet been observed live
  against its actual target failure condition (a close attempted
  near/after market hours, a genuinely rejected entry) — deploy-verification
  via `git_sha` is necessary but not sufficient proof of stability. Tracked
  as a new backlog watch item (see Documentation Updated).

## Documentation Updated
- Rules doc updates: none required (no contradiction found with
  `CLAUDE-RULES-CANONICAL.md` — all four fixes are baseline behaviour behind
  a tunable env var with a documented default, not a new default-off gate).
- Architecture doc updates: none required this sprint (no new subsystem, no
  contract change to the `/api/bot/*` surface).
- Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`): the doc-freshness
  sweep at session end found a real doc-vs-reality contradiction unrelated to
  this sprint's code changes — the doc's header, "How to update" step 3, and
  Stage 10 description all claimed the dashboard's **Trade Process** tab
  fetches this doc live at runtime, but that tab was never carried over in
  the 2026-05-12 Vercel→Streamlit migration (confirmed against the dashboard
  repo's own `CLAUDE.md` "Not (yet) ported" list). Fixed all three spots.
  Whether the doc's per-stage content should also gain explicit Alpaca
  close/entry-confirm detail is left open (`BL-20260708-TRADE-PIPELINE-DOC-ALPACA-GAP`).
- Roadmap updates: this entry + the `> Last Updated` header addendum on
  `ROADMAP.md` (M15's Alpaca-live execution path).
- GitHub Actions doc updates: none (no workflow changed).
- Subsystem doc updates: `docs/claude/health-review-backlog.json` — 4 entries
  from the audit marked `resolved` with deploy-verification notes
  (`BL-20260707-ALPACA-CLOSE-RESTING-ORDER-BLOCK`,
  `BL-20260707-ALPACA-CLOSE-NOT-CONFIRMED-FLAT`,
  `BL-20260707-ALPACA-BALANCE-TRUTHINESS`,
  `BL-20260707-ALPACA-ENTRY-FILL-CONFIRM-GAP`); 1 entry left `open` for minor
  audit follow-ups (`BL-20260707-ALPACA-PIPELINE-AUDIT-FOLLOWUPS` — OANDA
  close() parity, no client_order_id idempotency, naked-reprotect qty
  staleness, `place_protective()` DRY violation, a harmless dict leak, and a
  theoretical order-package resurrection risk); 1 NEW entry opened per
  operator directive this session
  (`BL-20260708-ALPACA-PIPELINE-VERIFICATION-WATCH` — keep checking for
  ~5-7 days until the fixes are proven under real conditions, not just
  deploy-verified).
- Historical docs marked superseded: none.

## Contradictions or Drift Found
- Contradiction 1: none found between `CLAUDE.md` / `CLAUDE-RULES-CANONICAL.md`
  / `ARCHITECTURE-CANONICAL.md` as a result of this sprint's changes.
- Contradiction 2: `docs/TRADE-PIPELINE.md` (header, "How to update" step 3,
  Stage 10 description) claimed the dashboard's Trade Process tab fetches
  this doc live — that tab was never carried over in the 2026-05-12
  Vercel→Streamlit dashboard migration. Found + fixed in the session-end
  doc-freshness sweep (unrelated to this sprint's Alpaca changes, caught
  incidentally while checking the sprint-log template's wrap-up item).
- Code/doc mismatch: this sprint itself IS the fix for a code/doc mismatch —
  `IB_CLOSE_CONFIRM_S`/`IB_PLACE_CONFIRM_S` were documented in `CLAUDE.md`'s
  Environment Variables table as the pattern to follow for a confirmed
  broker action; Alpaca's client lacked the analogous confirm step despite
  running the same conceptual order path. No further doc/code mismatch
  remains after this sprint's PRs; `CLAUDE.md`'s env-var table does not yet
  list `ALPACA_CLOSE_CONFIRM_S`/`ALPACA_PLACE_CONFIRM_S` — logged as a doc gap
  to the health-review backlog (see below) rather than hand-edited into the
  curated-subset table here, since that table is explicitly a curated subset
  and the new vars are self-documented at their call sites per that table's
  own stated convention.

## Risks and Follow-Ups
- Remaining technical risks: none of the four fixes has been live-fire tested
  against its real trigger condition yet (see Gaps not yet verified above).
- Remaining product decisions (Tier 3): none — all four fixes are Tier-2
  reliability/observability hardening of an existing execution path, no
  strategy/account/risk change proposed or made.
- Blockers: `alpaca_paper`'s earlier large-negative-equity reading (from a
  prior, unrelated incident) still needs a manual operator-side Alpaca
  console action — outside this sprint's scope, previously communicated.

## Deferred Items
- Deferred item 1: entry-side FILL-PRICE capture (as opposed to just
  rejection detection) — `_submit_order`'s return contract is a bare
  `str` (order_id) shared byte-for-byte across bybit/IB/alpaca/oanda;
  threading a richer fill-price payload through it is a materially larger,
  cross-exchange refactor and a separate design decision, not this sprint's
  bug fix. The decision-time entry price remains the documented, accepted
  convention for every exchange here (IB's own `IB_PLACE_CONFIRM_S` only
  checks non-rejection too, never fill price).
- Deferred item 2: the 6 minor audit findings bundled in
  `BL-20260707-ALPACA-PIPELINE-AUDIT-FOLLOWUPS` (OANDA close() parity before
  any OANDA account goes live; order idempotency; naked-reprotect qty
  staleness trace; `place_protective()` DRY cleanup; a harmless dict leak;
  order-package resurrection defensive exclusion).

## Next Recommended Sprint
- Suggested next sprint: none scheduled by name — the standing
  `BL-20260708-ALPACA-PIPELINE-VERIFICATION-WATCH` backlog item directs the
  next several `/health-review`/`/system-review` passes to explicitly check
  for Alpaca orphan/phantom-close/naked-position incidents until ~2026-07-15
  or a real close-not-confirmed-flat / entry-rejection event is observed
  resolving cleanly.
- Why next: deploy-verification alone doesn't prove the fix holds under the
  actual failure conditions (market-close timing, genuine broker rejection).
- Required verification before starting: none — this is a passive watch,
  not an active kickoff.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries (full
      reads of `alpaca_client.py`, `order_monitor.py`, `execute.py`, plus
      `git log -p` on the changed files).
- [x] Documentation was reviewed and updated as part of the sprint
      (health-review backlog; this sprint log; ROADMAP.md addendum).
- [x] If this sprint touched any pipeline stage, `docs/TRADE-PIPELINE.md` was
      updated and the dashboard's Trade Process tab was visually verified —
      **partially**: found and fixed a stale claim in the doc (the Trade
      Process tab it describes doesn't exist in the current Streamlit
      dashboard, so visual verification is now explicitly N/A per the doc
      itself, not skipped). Whether the doc's stage content needs explicit
      Alpaca detail remains open (`BL-20260708-TRADE-PIPELINE-DOC-ALPACA-GAP`).
- [x] Roadmap status was checked (M15 row reviewed; addendum added).
- [x] Contradictions were recorded (none found beyond the resolved
      doc/code mismatch above).
- [x] Remaining unknowns were stated clearly (live-fire verification gap,
      tracked in the new watch backlog item).
