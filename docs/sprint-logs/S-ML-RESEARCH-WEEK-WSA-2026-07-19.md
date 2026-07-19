# Sprint Log: S-ML-RESEARCH-WEEK-WSA-2026-07-19

## Date Range
- Start: 2026-07-19
- End: 2026-07-19

## Objective
- Primary goal: **WS-A of the research week — the M25 promotion harvest.** Run
  the powered RG4 sweep across the maturing shadow soaks on a fresh live
  mirror, produce per-head TRUSTWORTHY×POWERED verdicts, execute the safe (T1)
  demotes with operator approval, and tee up the Tier-3 promotion packets
  (MES; the ~07-24 BTC swap).
- Secondary goals: same-day nightly-equivalent acceptance checks (cycle /
  readiness / WS-B shards / MES base — operator: "why wait for tomorrow?");
  the operator-directed soak-workflow reframe (soak = mechanics, not edge)
  landing as the M25 parity gate + ml-review soak-audit mandate; M26 P0
  (conflict-bleed quantification) run in parallel per the operator's
  "immediately, very high priority" directive.

## Tier
- Tier 1 for everything executed here (research reads, docs, backlog, demotes
  approved by the operator in chat, trainer-unit env fix). The promotion
  proposals themselves (MES packet, BTC fc-pcv swap) are **Tier-3
  propose-only** — packets go to the operator; nothing live-influencing was
  changed by this sprint.
- Justification: demote shadow→candidate is the M25 P4 "demote is safe" leg
  (removes a head from live scoring; influences no order). Operator approval
  was obtained anyway ("ok on the demote") and is recorded in the registry
  `stage_history`.

## Starting Context
- Active roadmap items: M25 (promotion consolidation), M23 P2 close-out
  (predecessor session, PR #6934), M26 (opened mid-sprint by operator
  directive), research-week plan WS-A/WS-B/WS-C.
- Prior sprint reference: `S-ML-TIER1-OFFLINE-2026-07-19.md` (same-day
  predecessor: trainer mem-fix verification, xa retrain, M23 P2 honest
  negative).
- Known risks at start: trainer is a 6 GB box — heavy jobs must queue on the
  flock; the promotion-readiness sweep had produced no report on 07-18/07-19;
  MES candle base frozen ~44 days (GIGO-blinds MES RG4).

## Repo State Checked
- Branch or commit reviewed: work on `claude/ict-ml-continuation-7zp8by`
  (PR #6953); merges landed to `main` through 82243f8 (see-something rule),
  c04dd9f (parity gate, PR #6966), d068089 (M26, PR #6954).
- Deployment state reviewed: trainer services via trainer-diag relays
  (#6949/#6950/#6951/#6978/#6979/#6981); live VM untouched.
- Canonical docs reviewed: CLAUDE-RULES-CANONICAL, M25 design,
  regime-head-soak-to-advisory runbook, session-board/merge protocol.

## Files and Systems Inspected
- Code files inspected: `ml/promotion/gates.py`, `ml/promotion/cli.py`,
  `ml/promotion/stage_guard.py`, `scripts/ml/rg4_targeted.sh`,
  `scripts/ops/_trainer_heavy_lock.sh`, `scripts/ops/run_promotion_readiness.sh`.
- Config files inspected: registry manifests for the regime-head fleet
  (`ml/registry-store/*/manifest.json`, via trainer relay).
- Deployment files inspected: `deploy/trainer/ict-promotion-readiness.service`.
- Docs inspected: `docs/research/M25-promotion-consolidation-DESIGN.md`,
  ml-review skill, ROADMAP.
- Services or timers inspected (trainer): `ict-trainer.service`,
  `ict-promotion-readiness.{service,timer}`, heavy-lock holder state.
- GitHub Actions workflows inspected: trainer-vm-diag relay, system-actions
  send-ping, CI check-runs on #6953/#6954/#6966.

## Work Completed
- **Powered RG4 sweep** on a fresh mirror →
  `docs/research/M25-rg4-sweep-2026-07-19.md`. Headline verdicts: both ETH 15m
  heads **POWERED NO_EDGE** (`eth-regime-15m-lgbm-v1` 0.496 @ 46 vol bars /
  9 episodes; `eth-regime-15m-lgbm-fc-pcv-v1` 0.476) — the ETH vol-gate path
  needs a better head, not more soak; SOL near-powered (35/40), dated hold
  ~2026-08-01; live BTC advisory head 0.165 ANTI_PREDICTIVE but unpowered
  (corroborates `MB-20260718-BTCREGIME-V2-DRIFT-DEMOTE`; swap decision ripens
  ~07-24); `mes-regime-5m-lgbm-v2` 0.714 TRUSTWORTHY but labeling-blocked on
  the stale MES base.
- **P4 demote sweep executed**: operator approved in chat; both ETH heads
  demoted shadow→candidate on the trainer at 14:56:37Z (rc=0), approval + RG4
  evidence recorded in each registry `stage_history`.
- **Same-day acceptance checks** (operator: don't wait for the nightly):
  manual full cycle ran clean under the memory fix (finished 12:36Z,
  success); MES/5m/v002 base **rebuilt** at 11:51Z (yfinance fallback),
  breaking the 44-day freeze; WS-B dataset build chained and running.
- **Readiness-starvation root cause + fix**: the 07-18/07-19 report gap was
  the sweep starving on the default 1h heavy-lock wait behind the grown
  nightly cycle → `TRAINER_HEAVY_LOCK_WAIT_S=14400` added to
  `deploy/trainer/ict-promotion-readiness.service` (this PR); on-box apply
  post-merge.
- **Readiness sweep kill decision (14:55Z)**: today's manually-chained sweep
  ran 2h18m in D-state memory thrash (4.9 GB RSS on the 5.9 GB box, outputs
  0 bytes) on pre-parity-gate code — killed it; the WS-B build's flock waiter
  grabbed the heavy lock seconds before its 2h wait expired. Readiness
  re-runs on the new parity code.
- **Backlog dispositions** (`docs/claude/ml-review-backlog.json`):
  `MB-20260626-003` RESOLVED (gate re-target verified already implemented);
  evidence updates on `MB-20260628-REGIME-SOAK-READINESS` (now SOL-only),
  `MB-20260718-BTCREGIME-V2-DRIFT-DEMOTE`, `MB-20260705-FC-ADVISORY-READINESS`;
  alarm-fatigue item priority raised; new `MB-20260719-M26-TRANSITION-CONFLICT`
  + `MB-20260719-M23-HARNESS-DEBUG-QUERY`.
- **Adjacent merges shepherded by this session** (own PRs, slot-protocol):
  #6934 (M23 P2 close-out), #6956 (see-something-say-something rule), #6966
  (M25 parity gate: `live_parity` + `labels_accruing` required,
  `live_regime_discrimination` → advisory; 968 tests), #6954 (M26 milestone +
  P0 bleed findings), ml-review skill soak-audit mandate (this PR's sibling
  scope on the parity branch).

## Validation Performed
- Tests run: full pytest suite green on every merged PR's synced head (CI);
  968 tests incl. the new parity-gate unit tests on #6966.
- Dry-runs or staging checks: RG4 sweep on the fresh mirror (mirror-age gate
  respected); demote CLI output + registry `stage_history` re-read after
  execution (relay #6981) — both heads read `stage: candidate`.
- Manual code verification: `gates.py` regime profile verified on disk before
  resolving `MB-20260626-003`; heavy-lock holder file + `ps`/`free` evidence
  for the starvation and thrash diagnoses.
- Gaps not yet verified: MES RG4 re-read on the refreshed base (launched,
  pending); tomorrow's 04:00Z timer-fired readiness under the 4h queue wait +
  parity code; WS-B shard completeness.

## Documentation Updated
- Rules doc updates: none in this PR (the see-something rule landed via
  #6956).
- Architecture doc updates: none.
- Roadmap updates: M26 row landed via #6954; WS-C row landed earlier.
- Subsystem doc updates: `docs/research/M25-rg4-sweep-2026-07-19.md` (new);
  M25 design gate-reframe section landed with #6966 (single-owner to avoid a
  cross-PR conflict — the duplicate here was reverted).
- Historical docs marked superseded: the M25 design's original gate section
  is explicitly marked SUPERSEDED by the reframed section.

## Contradictions or Drift Found
- `MB-20260626-003` was stale — the gate re-target it asked for was already
  implemented and operational; resolved with on-disk evidence.
- The WS-A chain script's `systemctl is-active --quiet` loop treats an
  `activating` oneshot as inactive (premature "readiness finished" log lines);
  correctness was preserved by the blocking `systemctl start` + flock, and
  later scripts use the explicit `activating|active` check.

## Risks and Follow-Ups
- Remaining technical risks: the readiness sweep has **no cgroup memory cap**
  (only `ict-trainer.service` does) — today's 4.9 GB thrash argues for a
  `MemoryMax` on the readiness unit; noted for the trainer-unit hardening
  follow-up. Readiness must also be re-run on parity-gate code to produce
  today's report.
- Remaining product decisions (Tier 3): MES promotion packet (pending the
  RG4 re-read); the ~07-24 BTC fc-pcv swap packet; both go to the operator
  via Telegram.
- Blockers: none — WS-B build running; MES RG4 launched.

## Deferred Items
- M26 P0 full-coverage miner rerun once WS-B shards land (MES/equities
  episodes currently unmeasured).
- SOL powered RG4 re-check ~2026-08-01 (dated hold).
- Readiness-unit `MemoryMax` hardening.

## Next Recommended Sprint
- Complete WS-A tail in this PR (MES RG4 re-read → packet), then WS-B/WS-C
  per the research-week plan; M26 P1 (TF-ratio taxonomy) is the next M26
  step after the P0 findings.
