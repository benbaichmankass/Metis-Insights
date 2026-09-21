# VM topology and the authority split

> Extracted verbatim from `CLAUDE.md` on 2026-09-21 by the operating reset.
> Reference material — read on demand, not at session start.
> Registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md).


Two VMs, two trust contracts. A Claude session is acting on exactly
one of them at a time.

| VM | Role | Trust contract | Default posture |
|---|---|---|---|
| `ict-bot-arm` (`141.145.193.91`, Ampere A1.Flex 2 OCPU / 12 GB; migrated off the x86 micro `158.178.210.252` on 2026-06-14) | **Live trader** — runs `ict-trader-live.service`, holds money-at-risk | [`docs/claude/vm-operator-mode.md`](docs/claude/vm-operator-mode.md) | **Restricted.** Tier-1 read autonomous; Tier-2 mutations need operator ack (PM-side issue → `system-actions.yml`); Tier-3 paths (live order code, risk caps, key rotation) are hard-blocked. **Account-mode flips have a sanctioned wire: `set-account-mode` operator action; code paths that flip mode outside that action are Tier-3 violations.** |
| `ict-trainer-vm` (`VM.Standard.A1.Flex`, Ampere A1) | **Training center** — runs the ML lifecycle (datasets, training, registry, eval), no live trade authority of its own | [`docs/claude/trainer-vm-mode.md`](docs/claude/trainer-vm-mode.md) | **Autonomous.** Claude provisions, SSHes, installs, syncs read-only DB from live, runs training cycles, writes the registry up to `advisory` stage, terminates + re-provisions — all without operator-in-the-loop. |

The separation has two gates (2026-05-19 update; see
`docs/ARCHITECTURE-CANONICAL.md` § Change log for the
shadow-default-flip rollout):

1. **Stage gate** — autonomous-Claude on the trainer VM can write a
   model into the registry up to `advisory`, but only the `advisory`
   stage ever influences the order package. Models at `shadow` log
   predictions but never change order decisions; models at `candidate`
   are refused by the shadow factory. (Stage ladder collapsed 7→3 on
   2026-06-16 — canonical `candidate → shadow → advisory`; the legacy
   names `research_only`/`backtest_approved` alias to `candidate` and
   `limited_live`/`live_approved` to `advisory` via
   `ml.manifest.canonical_stage`, so old registry rows still resolve.)
2. **Promotion gate** — the `shadow → advisory` transition (and
   every step beyond) is the operator-approved gate. Promoting
   past shadow is the move that turns a model from "observing" to
   "influencing." This is the live-trading switch.

Since the default flip, models at `shadow` auto-wire onto every
strategy's predictor list when the strategy YAML omits
`shadow_model_ids` (or sets it to `None`). An explicit `[]` opts a
strategy out; an explicit list pins specific ids. This means
shadow-mode logging is enabled-by-default for any newly-trained
model — the operator's role is the promotion gate, not the YAML
wire-up. See trainer-vm-mode.md § 5 for the full lifecycle.

**Hard limits that survive the split** (apply on either VM):

- Never SSH into the **live** VM from a trainer-scoped session.
- Never merge a PR to `main` that touches `config/strategies.yaml`,
  `config/accounts.yaml`, `config/risk_caps.yaml`,
  `src/runtime/orders.py`, `src/runtime/risk_counters.py`, or any
  unit file the live VM consumes **without explicit operator
  approval** — these are Tier-3. The canonical gate is "explicit
  product approval required before merge" (see
  [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)
  § Permission Tiers). By default open the PR, mark it draft, and
  ping the operator; once the operator approves, you may merge and
  deploy.
- Never copy production secrets to the trainer.
- Never provision past the OCI Always Free 4-OCPU / 24-GB Ampere tenancy
  ceiling. **Topology as of 2026-06-14 (live→Ampere cutover COMPLETE):**
  - **Live trader** — `VM.Standard.A1.Flex` **2 OCPU / 12 GB** (Ampere, aarch64;
    `ict-bot-arm`, `141.145.193.91`). Migrated off the x86 micro on 2026-06-14
    via `.github/workflows/cutover-live.yml`. `/data/bot-data` is a directory on
    the 45 GB boot volume (NOT a separate block-volume mount), so its units take
    the env-only `data-dir-nomount.conf` drop-in, auto-selected by
    `scripts/install_systemd_units.sh` — see
    [`docs/runbooks/live-vm-migration-ampere.md`](docs/runbooks/live-vm-migration-ampere.md).
  - **Trainer** — `VM.Standard.A1.Flex` 1 OCPU / 6 GB (Ampere; `158.178.209.121`).
  - **IB Gateway** — `VM.Standard.A1.Flex` 1 OCPU / 6 GB (Ampere; `ict-ib-gateway`,
    private IP `10.0.0.251`) — its own dedicated box. **Ampere usage: trainer 1 +
    gateway 1 + live 2 = 4 of 4 OCPU (12+6+6 = 24 of 24 GB) — the Always-Free
    Ampere pool is now full.** The retired x86 micro `158.178.210.252` was a
    *separate* AMD Always-Free allocation (retiring it frees/costs no Ampere
    budget); it was **terminated 2026-06-16** via `terminate-instance` (by OCID,
    display_name `ict-bot`) after a clean soak — no longer a rollback target.

  The 2026-06-10 wedge cascade root cause was the **heavy IB-Gateway
  (Java/Xvfb/IBC) sharing the 1 GB micro** with the trader → swap-thrash. The
  fix was to **move the gateway off the money box onto its own Ampere VM**
  (gateway isolation); the trader reaches it over the private subnet
  (`config/accounts.yaml::ib_paper.ib_host = 10.0.0.251`). Recovery is one
  deterministic daily `docker restart` (`ict-ib-gateway-reset.timer`,
  **06:05 UTC** — retimed 2026-07-02 from 05:30, which was actually inside
  IBKR's own ~03:45–05:45 UTC reset window and so raced the outage it existed
  to fix, BL-20260623-002) on the gateway VM, **plus** the reactive ~5-min
  `ict-ib-gateway-watchdog.timer` (re-armed 2026-06-22, BL-20260622-GATEWAY-MIDDAY-WEDGE
  — catches a mid-day wedge the daily reset alone would miss; it now also
  carries a `--suppress-window-utc 03:45-05:45` flag so it never burns a
  restart attempt on a wedge it can't actually fix). Full topology + rationale:
  [`docs/runbooks/ib-integration.md`](docs/runbooks/ib-integration.md) §
  "Gateway isolation redesign".

  The **live→Ampere migration COMPLETED 2026-06-14.** Rationale (still valid):
  with the gateway isolated, the 2-vCPU / 1-GB x86 micro held the trader on CPU
  fine (loadavg ~1.2 on 2 cores) but hit 90%+ memory with `kswapd` active — 1 GB
  was too small for the grown stack. Free-tier ceiling math: the Ampere pool is
  4 OCPU / 24 GB; trainer (1/6) + gateway (1/6) leave exactly **2 OCPU / 12 GB**
  for live, which is the verified shape of the candidate (`ict-bot-arm`,
  filling the pool to 4/24, $0). The x86 micro is a *separate* AMD Always-Free
  allocation, so retiring it costs no Ampere budget. **Post-cutover follow-ups**
  (most closed 2026-06-14: ✅ `ict-git-sync` re-enabled — the candidate
  auto-deploys from `main`; ✅ `ib_insync` confirmed already present in the trader
  venv — MES/MGC/MHG trade live; remaining: optional dedicated `/data` block
  volume; ✅ micro decommissioned 2026-06-16 via `terminate-instance` by OCID) are tracked in
  [`docs/runbooks/live-vm-migration-ampere.md`](docs/runbooks/live-vm-migration-ampere.md).
  Migration tooling (`provision-live-vm`, `cutover-live`, `terminate-instance`)
  remains for rollback / future moves.

When in doubt about scope, default to the **live-VM** rules and ask.

