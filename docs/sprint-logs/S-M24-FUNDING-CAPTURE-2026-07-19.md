# Sprint Log: S-M24-FUNDING-CAPTURE-2026-07-19

## Date Range
- Start: 2026-07-19
- End: 2026-07-19

## Objective
- Primary goal: Close the go-forward **funding-capture** gap in M24's cost data (the reason `funding_paid_usd=0` on the net-R re-grade) and correct the record on the Bybit-export "cost-capture unblocker".
- Secondary goals: Restore Telegram ping delivery; land a durable fix for the recurring trainer OOM D-state wedge; verify all three live end-to-end.

## Tier
- Tier 1 (docs, tooling) + Tier 2 (deploy: new systemd timer, trainer cgroup unit change).
- Justification: The funding timer is a new observability timer (read-only on the exchange, writes only the sidecar `exchange_fills.sqlite`, no order path) — Tier-2 deploy, operator-approved in chat. The trainer memory change is a trainer-VM cgroup tuning (autonomous territory). No `src/`, `config/strategies.yaml`, `config/accounts.yaml`, risk-cap, or order-path change.

## Starting Context
- Active roadmap items: M24 (net-R / cost-aware modeling — P1/P2 done, P3/P4 blocked on cost coverage); fc-pcv regime head readiness (oos_edge passed, RG4 soak pending).
- Prior sprint reference: M24 P1/P2 (#6806/#6808) + net-R re-grade findings (`M24-net-r-regrade-findings-2026-07-17.md`).
- Known risks at start: operator reported Telegram pings not delivering; trainer wedged ~24.8h on a recurring OOM.

## Repo State Checked
- Branch or commit reviewed: `main` @ `853a26e` (post-merge); dev branch `claude/ml-vol-regime-probe-21az61`.
- Deployment state reviewed: live trader `ict-bot-arm` @ HEAD `853a26e` (confirmed via `pull-and-deploy` issue #6902); funding-pull ran clean (issue #6903).
- Canonical docs reviewed: CLAUDE.md, CLAUDE-RULES-CANONICAL, ARCHITECTURE-CANONICAL, ROADMAP.md (doc-freshness sweep — corpus clean).

## Files and Systems Inspected
- Code files inspected: `src/runtime/net_r_label.py`, `scripts/research/net_r_regrade.py`, `src/runtime/exchange_fills_puller.py`, `scripts/pull_exchange_funding.py`, `src/runtime/exchange_fills_store.py`.
- Config/deploy files inspected: `deploy/ict-exchange-fills-pull.{service,timer}`, `scripts/ops/pull_exchange_{fills,funding}_action.sh`, `scripts/install_systemd_units.sh`, `.github/workflows/system-actions.yml`.
- Docs inspected: `comms/broker_truth_ledger.json`, `docs/audits/bybit2-broker-reconciliation-2026-07-13.md`, `docs/research/M24-net-r-*.md`.
- Services/timers inspected: `ict-exchange-fills-pull.timer` (daily 00:20 UTC, auto-enabled); confirmed NO funding timer existed.

## Work Completed
- **Telegram ping delivery (earlier in session):** root-caused a drainer path-split (drainers ran from before their DATA_DIR drop-in took effect → drained the stale repo-path inbox); fixed via a HEAD-advancing status_check diagnostic (PR #6871) + `pull-and-deploy` restarting both drainers. Left a permanent drainer/inbox diagnostic in `status_check.sh`.
- **Trainer OOM D-state wedge (earlier in session):** `deploy/training-vm-cloud-init.yaml` `ict-trainer.service`/`ict-trainer-catchup.service` set `MemorySwapMax=0` (PR #6892) + `MemoryHigh=infinity` (PR #6897). A manifest in the `[MemoryHigh, MemoryMax]` band swap-thrashed / reclaim-stalled in State D (SIGKILL can't reap D-state → per-manifest `timeout` couldn't kill it → whole trainer wedged). Verified live: the stalled manifest jumped to R-state/89% CPU the instant the throttle was removed; backlog then churned at 96-99% CPU. Logged `BL-20260719-TRAINER-FLOWHEAD-OOM-DSTATE-WEDGE`.
- **M24 funding timer (this thread):** added `deploy/ict-exchange-funding-pull.{service,timer}` (PR #6901, merged `853a26e`), mirroring the fills timer — daily 00:35 UTC, per-symbol bybit_2, DATA_DIR-pinned to the canonical store. Closes the "funding puller had a system-action but no timer" gap that produced `funding_paid_usd=0`.
- **M24 Bybit-export correction:** established that the operator's whole-period bybit_2 UM export from last week **was** captured (`broker_truth_ledger.json` + the 2026-07-13 reconciliation audit) — NOT lost. Per-trade broker-truth for the history is **structurally impossible** (spot+perp mix + a 2026-05-10 sub-account switch: perp TRADE legs net +$768 vs wallet-truth −$262.52; FIFO cycles never resolve), so no re-upload widens per-trade coverage.

## Validation Performed
- Tests run: `canonical-doc-coherence` checker (corpus clean — the only FAILs were a stray local git worktree CI never sees); PR #6901 CI all 11 checks green.
- Dry-runs / staging checks: `pull-and-deploy` (#6902) → live HEAD confirmed `853a26e`; `pull-exchange-funding` (#6903) → exit 0 to the canonical store `/data/bot-data/runtime_state/exchange_fills.sqlite`.
- Manual verification: funding puller default-path resolution reads `runtime_state_dir()` (DATA_DIR-anchored) when `--fills-db` omitted — matches the fills service.
- Gaps not yet verified: the daily funding timer's first *scheduled* fire (00:35 UTC) — but the mechanism is proven by the one-time pull. The first *actual funding rows* will only land when bybit_2 holds a perp position across an 8h settlement.

## Documentation Updated
- Roadmap updates: M24 row — appended the 2026-07-19 go-forward funding-capture + Bybit-export-clarification note.
- Subsystem doc updates: new `deploy/ict-exchange-funding-pull.{service,timer}` (self-documenting headers).
- Backlog: `BL-20260719-TRAINER-FLOWHEAD-OOM-DSTATE-WEDGE` (health-review backlog).
- This sprint log.

## Contradictions or Drift Found
- None in the canonical corpus. The doc-freshness mechanical scan's only hits were inside the stray `.claude/worktrees/agent-a19fd3fe57c5056c2/` worktree (historical sprint-log copies) — not tracked on `main`, invisible to CI. Noted below as a cleanup follow-up.

## Risks and Follow-Ups
- Remaining technical risks: the one-time funding pull returned **0 rows** — bybit_2 holds no perp position across an 8h funding settlement in the 30d window (consistent with the −$0.55 lifetime funding; funding is a negligible cost for this scalping account). The timer will capture funding *if/when* a position spans a settlement.
- Remaining product decisions (Tier-3): M24 P3 (cost-aware EV scorer) + P4 (within-tick net-R ranker) — still operator-gated, now waiting on ~1-2 wks of go-forward broker-truth **fee** accrual on the clean account (not operator data).
- Blockers: none requiring the operator.

## Deferred Items
- fc-pcv full gate-check (RG4 live-regime discrimination) — oos_edge PASSED (+0.282 OOS macro-F1); RG4 needs days of fresh-forecast shadow soak to accrue before scoring. Time-gated → next session.
- Stray local git worktree `.claude/worktrees/agent-a19fd3fe57c5056c2/` — cleanup candidate (trips the local doc-coherence scan; harmless to CI). Minor.
- Funding-timer per-symbol list is hardcoded (BTCUSDT/ETHUSDT/XRPUSDT/ADAUSDT) mirroring the action; deriving it from the account's live traded set is a refinement.

## Next Recommended Sprint
- Suggested next sprint: **fc-pcv RG4 promotion readiness** — when the RG4 soak has accrued (~days of post-2026-07-18T16:57 fresh-forecast data), re-run the full gate-check; if RG4 ≥ 0.55, present the exact Tier-3 promotion (`btc-regime-15m-lgbm-fc-pcv-v1` → advisory, demote `-v2` → shadow) for operator approval. Then **M24 P3/P4** once broker-truth fee coverage accrues on the clean account.
- Why next: fc-pcv is the nearest concrete gated step; M24 P3/P4 unblock M18 (allocator selects the capital-constrained subset).
- Required verification before starting: confirm the funding + fills timers are still enabled and accruing; confirm the trainer has had no new D-state wedge.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] No pipeline stage touched — `docs/TRADE-PIPELINE.md` not applicable (observability timer only, no order path).
- [x] Roadmap status was checked (M24 row updated).
- [x] Contradictions were recorded (none in the canonical corpus; stray worktree noted).
- [x] Remaining unknowns were stated clearly (funding 0-rows is expected, not a failure; RG4 soak time-gated).
