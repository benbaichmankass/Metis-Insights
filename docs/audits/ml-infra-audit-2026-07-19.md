# ML / data / strategy-consumption infra audit — 2026-07-19

**Why:** operator directive ahead of the 07-20→07-26 research week — recurring
infra bug classes (version-pin silent reuse, dead-feature datasets, stale/missing
shards, schema drift, orchestrator interface drift) keep forcing task rework.
This audit sweeps the ML consumption chain (manifests → dataset builds →
datasets on disk → training cycle → registry → shadow serving → readiness
tooling) so the week runs clean.

**Method:** static repo sweep (manifest internal consistency, version-pin
inventory, family-column cross-check, orchestrator interface check — partly via
fan-out subagents) + live state pulls (trainer-vm-diag: dataset_audit flags,
dataset freshness, 7-day cycle outcomes, registry, quarantine; vm-diag: shadow
serving). Same-day fixes land in the session PR (#6917); everything else gets a
backlog item.

## Findings

### F1 — ETH 1h dataset version split-brain (FIXED in #6917)

All four ETH 1h manifests (`eth-regime-1h-lgbm-v1`, `-xasset-v1`,
`eth-direction-1h-lgbm-v1`, `-xasset-v1`) pinned `dataset.version: v001` — the
frozen one-shot side-build snapshot (stale since ~2026-06-17) — while the
nightly build refreshes ETH 1h `market_features` at **v002**. Consequences:
the MB-20260627-002 alt-staleness fix never reached these heads' TRAINING data
(they kept retraining nightly on the frozen June snapshot), and the new
cross-asset side-stream (BL-20260628-XA-TRAINING-ZERO fix, same PR) would have
landed at v002 while the xasset heads kept reading dead-xa v001. The BTC 1h
twin pins v002; the ETH v001 pin was a historical accident, not a
parameterization. **Fix: bumped all four to v002.** Class: version-pin silent
reuse (same class as the MB-20260716-BUILDPARAMS-IGNORED gate and the P1
pooled-run v001 reuse bug).

### F2 — 47/86 manifests pin side-built dataset versions (structural fragility, ACCEPTED with guard)

The version-pin inventory: 47 manifests reference dataset versions the nightly
does NOT build (v001/v003/v004/v011/v020/v513–v530/v901 + market_sequences /
corpus_panel / exit_candidates families). These are research side-builds that
must simply persist on the trainer disk; the nightly's `--overwrite` writes
different versions so it can't clobber them, and a missing pinned dataset skips
cleanly (exit-78 `manifest_skipped`). Residual risks: (a) a trainer
re-provision strands all 47 until their side-build orchestrators re-run; (b) a
pinned frozen snapshot silently ages (the F1 class — v001 looked alive because
training kept succeeding). Guard adopted: the 7-day cycle-outcome sweep (below)
is the detection surface — a pinned manifest that flips done→skipped signals a
lost side-build; a *nightly-refreshed* manifest must never pin a version the
nightly doesn't write (that's F1, now fixed for the only offending group).

### F3 — dataset-audit alarm fatigue: 62/86 manifests flagged nightly (OPEN, backlogged)

Live pull (trainer-vm-diag #6922): `dataset_audit.jsonl` holds 401 flagged rows
across **62 of 86 manifests** — the observe-only audit fires on most of the
fleet every night, so the signal it was built for (a genuinely dead xa_*-class
block) drowns. Degenerate-label flags are doing their job (prop-mission-policy,
TCN heads on absent datasets, MES journal-backed families with 0/near-0 rows);
the dead-feature side needs a look at which columns actually trip it (the
per-column names weren't extracted this pass — `FeatureAudit.name`). Action →
backlog: tune the audit (per-manifest expected-dead whitelist or
threshold), THEN consider flipping the FLAGGED branch to enforce. An audit
nobody reads is the same as no audit.

### F4 — orchestrator interface drift (subagent sweep; 2 active, 2 fragile)

- **F4a (FIXED in #6917)** — `record_harness_trades.py` let a row's
  self-reported `strategy` win over the orchestrator's explicit
  `--trades-jsonl PATH=STRATEGY` override. `backtest_squeeze.py` hardcodes
  `strategy: "squeeze_breakout"` (live name: `squeeze_breakout_4h`), so pooled
  M23 rows were silently mislabeled and the override was a no-op. Precedence
  flipped (override wins) + regression test. P1 science unaffected materially
  (strategy was never a feature; symbol-scoped joins unaffected).
- **F4b (FIXED in #6917)** — promotion-readiness reports
  (`runtime_logs/trainer_mirror/promotion_readiness/<date>/`) were written by
  `run_promotion_readiness.sh` + the `ml promotion-readiness` CLI, whose docs
  both claim "the existing publish_trainer_mirror.sh rsync picks it up" — but
  the publish script had no push block for that dir: every readiness report was
  stranded trainer-local. Push block added (small JSON/MD only). A live-side
  reader/API surface is a follow-up (the M25 week-plan workstream consumes the
  mirrored files directly meanwhile).
- **F4c (fragile, backlogged)** — every dataset family's `iter_rows` ends in
  `**_: Any` and the CLI forwards `key=value` args verbatim, so a **misspelled
  build param is silently swallowed** (the builder quietly uses its default) —
  the exact class behind the buildparams/version bugs. Proposal: warn/fail on
  unknown family kwargs.
- **F4d (fragile, noted on MB-20260716-PROMOREADY-EXITHEAD-SCHEMA)** — the
  shadow-record loader now derives `row_keys` from `feature_row` (exit/entry
  heads parse), but a future writer emitting neither still gets silently
  dropped per-line, and derived `row_keys` for exit/entry heads is the logged
  context keys, not the trained feature list.

### F5 — dead-feature manifests beyond ETH xa (subagent sweep)

`market_features` optional side-stream groups: `cross_asset_path`→`xa_*` (13),
`macro_path`→VIX/DXY/UST (7), `funding_oi_path`→`funding_*`/`oi_*` (5),
`microstructure_path`→`ofi`/`vpin`/… (6), `embedding_path`→`tsfm_emb_*` (32),
`forecast_path`→`fc_*` (6), `corpus_embedding_path`→`corpus_emb_*` (16).
Nightly coverage after this session: ETH 1h gets cross_asset; MES gets macro;
everything else side-built at pinned versions the nightly never clobbers
(verified clean). Remaining dead-column instances, all BTC `research_only`:

- **`btc-regime-5m-lgbm-flow-v1`** — microstructure cols dead
  **unconditionally**: no nightly join path exists at all. Already known
  data-blocked (MB-20260613-002, forward L2 capture accruing); when capture
  data suffices, the nightly needs a `microstructure_path` join wired — noted
  on that backlog item.
- **`btc-regime-1h-lgbm-funding-v1` / `-svble-v1`** — funding/OI cols dead
  unless `ICT_BUILD_FUNDING_OI=1` (was default-off; the funding_oi side-stream
  sat 45d stale). **FIXED**: flag enabled on the trainer units (cloud-init +
  on-box), so the nightly refreshes the side-stream and joins it.

### F6b — live shadow-serving chain VERIFIED healthy

vm-diag #6923: `shadow_predictions.jsonl` fresh to the minute; 15+ heads
actively scoring at `shadow` (per-bar regime scorers for BTC/ETH/SOL 5m+15m,
MES fleet, the ETH 1h base + xasset heads). The live consumption half of the
chain needs no fixes.

### F7 — `ict-trainer-catchup.timer` inactive on the box (FIXED on-box)

The checkpoint/resume design depends on the catch-up run; the 7-day cycle
outcomes show 274 'pending' manifest rows (cycles cut short by the OOM wedges
never resumed same-day). The timer read `inactive` on the box (the unit
predates this box or was never enabled). Enabled via the trainer relay;
cloud-init already carries it for re-provisions.

### F8 — datasets on disk: core healthy, research tail aging (ACCEPTED, inventoried)

All nightly v002 shards fresh (0.2–0.3d) incl. MES-yfinance and the new ETH/SOL
intraday shards. Aging-but-deliberate: side-built research versions (v003…v901,
xsym setup_candidates v001 @ 42d — its daily rebuild is opt-in `ICT_BUILD_XSYM`,
default off), the MES v001 deep-history snapshots (IBKR pull is its own item,
BL-20260626-MES-BASE-STALE), funding_oi v001 (now refreshing via F5 fix). The
freshness sweep (diag section B) is the cheap recurring detection surface —
fold it into /ml-review's routine pulls.

### F6 — relay ergonomics (fixed operationally)

Two relay-usage footguns hit during this audit, worth recording so sessions
stop re-deriving them: (1) `trainer-vm-diag` `cmd:` blocks mangle INDENTED
heredocs (the block's 2-space indent means an indented delimiter never matches
— `warning: here-document delimited by end-of-file`); ship multi-line python
via `echo <base64> | base64 -d > /tmp/x.py` instead. (2) `vm-diag-request`
issues parse the BODY as additional diag paths — explanatory prose in the body
fails the run (`Rejected diag path`); keep the body empty or paths-only.

## Fixes landed this session (PR #6917)

- F1 version-pin bump (4 ETH 1h manifests v001→v002).
- Load-time column projection (the 5m OOM class) + audit-subprocess projection.
- ETH 1h cross-asset side-stream in the nightly build (xa-zero class) + loud
  fail-open.
- F4a `record_harness_trades` override precedence + regression test.
- F4b promotion-readiness mirror push block.
- F5 `ICT_BUILD_FUNDING_OI=1` on the trainer units (cloud-init; on-box via relay).
- F7 catch-up timer enabled on the box (relay).
- s012 canonical unit set registered the funding-pull + trainer-git-sync units
  (pre-existing main-red CI).

## Backlog items raised / updated

- NEW: dataset-audit alarm fatigue (F3) — tune thresholds/whitelist, then
  consider enforcement.
- NEW: unknown family build-param kwargs silently swallowed (F4c) — warn/fail.
- UPDATE MB-20260716-PROMOREADY-EXITHEAD-SCHEMA: loader fixed; residuals F4d.
- UPDATE MB-20260613-002 (flow head): nightly `microstructure_path` join still
  unwired — required before the head can ever A/B.

## The five recurring bug classes, named (for the week's sessions)

1. **Version-pin silent reuse** — a manifest pins a dataset version the
   orchestrator isn't building; training silently reuses the stale/frozen pin.
   Detection: version-pin inventory + cycle-outcome flips; F1 fixed the live case.
2. **Dead side-stream columns** — feature lists include optional-side-stream
   columns the build didn't join (xa/funding/microstructure). Detection: the
   dataset audit — once F3 de-noises it.
3. **Interface drift between orchestrators and CLIs** — overrides that don't
   override, kwargs that silently drop, docs asserting wiring that isn't there.
4. **Consumption-chain stranding** — an artifact written where no
   publisher/reader picks it up (F4b).
5. **Single-process resource blowups** — the 5m OOM class; contained by
   projection + the memcg backstop + quarantine.
