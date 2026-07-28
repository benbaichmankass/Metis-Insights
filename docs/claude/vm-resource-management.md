# Cross-session resource management (binding, 2026-07-28)

> **Operator directive (2026-07-28):** *"this is exactly what we have the GitHub
> CPU platform and the GPU burst budget for — to ensure efficient and effective
> resource management … Cross-session resource optimization needs to be the core
> of this build, and it needs to resolve this issue once and for all. And when a
> run fails, there needs to be an immediate loud flag."*

This doc is the **contract every VM / relay / heavy-compute workflow is built on**.
Multiple Claude sessions run in parallel; they were **disrupting each other and
themselves** by piling heavy work onto the one scarce resource (the 1-OCPU trainer
VM) and then **waiting hours on runs that had already died**. This codifies the fix
so it does not regress:

1. **Route work to the cheapest sufficient resource** (below). The trainer VM is
   scarce; GitHub runners are abundant and free — most heavy work does not belong
   on the VM at all.
2. **Serialize the scarce resource with a FIFO queue** — nothing new starts on the
   trainer VM until the running job ends (running is never preempted), FIFO,
   visible on the board, with an explicit priority/resource override.
3. **Every run that dies flags LOUDLY and immediately** — so no session ever again
   waits hours on a run that failed minutes in.

## 1. The resource tiers (route to the cheapest that suffices)

| Resource | Capacity | Cost | Parallel-safe across sessions? | Use for |
|---|---|---|---|---|
| **GitHub-hosted runners** | 4 vCPU, up to ~5.5h/job (`timeout-minutes: 330`) | **$0** on this public repo, effectively unlimited | **Yes** — every run gets its own runner | **The DEFAULT for CPU-heavy work that needs NO VM-resident state**: a public-feed fetch + a backtest/k-fold over it, a research discovery build, a data-validation pass. |
| **Trainer VM** (`ict-trainer-vm`, 158.178.209.121) | **1 OCPU / 6 GB — a single core** | free but **scarce** | **No** — one core; concurrent SSH jobs starve each other | ONLY work that needs **VM-resident state**: the persistent dataset/parquet cache, the model registry, on-box services (`ict-trainer.service`), the training lifecycle, GPU-adjacent staging. Serialize via the FIFO lane (§ 3). |
| **Live VM** (`ict-bot-arm`, 141.145.193.91) | 2 OCPU / 12 GB, **money-at-risk** | — | reads only | **Read-only** diag via the `vm-diag-snapshot` relay (`/api/diag/*` + allowlisted Tier-1 GETs). Never run heavy compute here — it shares the box with the live trader. Mutations go through `system-actions` (tiered). |
| **GPU burst budget** | spot GPU, **$10/month cap** | metered, spend-gated | serialized by the burst workflow | GPU training bursts only. The workflow's spend-gate + `comms/gpu_spend_ledger.json` enforce the cap (`/api/bot/gpu/spend`). |

### The routing decision (ask this BEFORE dispatching heavy work)

```
Does the work need VM-RESIDENT state (the on-box dataset cache, the registry,
an on-box service, the trainer's GPU)?
│
├─ NO  → run it on a FREE GitHub-hosted runner (a workflow, the
│        research-exit-head-build.yml pattern). Fetch the feed from a public
│        archive (Binance-vision is keyless + reachable from US runners;
│        Dukascopy for FX/metals; Bybit geoblocks US runners). No VM, no
│        contention, parallel across sessions, $0. THIS IS THE DEFAULT.
│
└─ YES → is it a quick read (a log tail, a `cat`, `systemctl status`, a registry
         list — seconds)?
         │
         ├─ YES → just fire the trainer-vm-diag relay. Quick reads are
         │        parallel-safe and need no lane claim.
         │
         └─ NO (a heavy/long VM job) → CLAIM THE VM LANE (§ 3) first, and if it
                  runs longer than ~an hour, dispatch it DETACHED (nohup + a
                  sentinel file) and poll via a follow-up relay rather than
                  holding one SSH session.
```

**The load-bearing lesson (2026-07-28).** The deep ~14-year XAU/MGC re-validation
(`fetch_dukascopy_ohlcv.py` + `run_symbol_p0.py` k-fold) was fired as a
**`trainer-vm-diag` SSH job** and kept dying at the 10-minute job cap — mislabelled
"preempted by a newer diag request" (issues #7815/#7816/#7829). It needed **no
VM-resident state at all**: only repo code + a public Dukascopy fetch. It belonged
on a **free GitHub runner** (the `research-exit-head-build.yml` shape), where it
would have had 4 cores, a 5.5h budget, $0 cost, and zero contention with any other
session. Routing it to the scarce 1-OCPU trainer with a 10-min cap was the actual
bug. **When a validation like this is CPU-only, build it as a runner workflow.**

## 2. The loud failure flag (no more waiting on dead runs)

Every Claude-driven VM / relay / heavy-compute run that concludes
**failure / cancelled / timed_out** raises an **immediate** flag, on two channels:

- **Telegram ping to the operator** — `.github/workflows/claude-run-failure-alert.yml`
  listens on `workflow_run: completed` for the watched workflows (the diag/action
  relays + the GH-runner research builds) and pings the moment one dies: *"a
  Claude-driven run just died — if a session is waiting, stop, it's dead."* This is
  the universal backstop so the operator never has to poke a session that has been
  blocked for hours on a run that failed minutes in.
- **Honest issue comment** — each workflow posts a truthful failure/cancelled
  comment on its own request issue (a polling session sees it). The message states
  the **real** cause (a `cancelled` relay run is the **job time budget** or a manual
  cancel — **not** a sibling preemption; per-issue concurrency cannot cancel a
  sibling) and the fix (route to a runner / detach), never a fabricated one.

**Any new Claude-driven workflow where a session BLOCKS on the result MUST** (a)
post an honest comment on **every** terminal conclusion — success AND
failure/cancelled — and (b) be added to the `claude-run-failure-alert.yml`
`workflows:` list. A run that can be waited on but fails silently is the exact
"waited hours on a dead run" defect this section exists to kill.

**Do not make it noisy.** The alert is scoped to workflows where a waiting-blind
failure is the real cost. Ordinary CI-check failures are visible on the PR and are
deliberately out of scope — an alarm that fires on everything is itself a P1 bug
(CLAUDE.md § "If you see something, say something").

## 3. The FIFO VM-lane queue (running is never preempted)

The scarce resource (the trainer VM's single core; likewise any exclusive heavy
live-VM action) is serialized with an **application-level FIFO queue on the
coordination board** (issue [#6927](https://github.com/benbaichmankass/ict-trading-bot/issues/6927)),
mirroring the merge queue. **It is a board protocol, not a GitHub concurrency
group, because GitHub concurrency keeps at most one PENDING run per group — a true
FIFO queue of depth > 1 is impossible at that layer** (that limitation is exactly
why a shared concurrency group silently dropped queued bursts, BL-20260611-002). So
per-issue GitHub concurrency stays (quick reads run parallel and never drop), and
the FIFO for heavy jobs lives on the board.

**When a claim is required:** before dispatching a **heavy or exclusive** VM job —
one that holds the VM for more than a few seconds (a backtest/sweep/k-fold that
must run on the VM, a dataset build, a training cycle, a validation pipeline, an
exclusive live-VM action). **Quick read-only pulls do NOT need a claim** (a log
tail, a `cat`, `systemctl status`, a registry list) — they are parallel-safe.

**The protocol (FIFO, running-never-preempted):**

1. **Read the board tail.** Look for an open `🔒 VM-LANE CLAIM` for the same VM
   (`trainer` / `live`) with no matching `🔓 VM-LANE RELEASE`. If one is open, the
   lane is **HELD**.
2. **Lane FREE →** post `🔒 VM-LANE CLAIM · <vm> · <session-id> · <task> · ETA <min>`
   and dispatch. You hold the lane until you release it. The running job is **never
   preempted** — nothing new starts on that VM until you release.
3. **Lane HELD →** do **not** dispatch. Post
   `🕓 VM-LANE QUEUED · <vm> · <session-id> · behind <holder> · <task>` and wait.
   **FIFO:** when the holder posts `🔓 RELEASE`, the earliest-queued session claims
   next (turns its `🕓` into a `🔒`). Newest never wins.
4. **Release promptly →** post `🔓 VM-LANE RELEASE · <vm> · <session-id>` the moment
   the job finishes or you abandon it. A held-but-abandoned claim blocks everyone;
   a claim whose holder is gone / ETA long past may be reclaimed by a queued session
   after a `⚠️` note.
5. **Priority / resource override (the one escape hatch) →** a genuinely
   higher-priority job (a live-money-adjacent infra fix) **or** a job that provably
   does **not** contend (a different VM, or a quick read-only pull) may proceed with
   an explicit `⚡ VM-LANE OVERRIDE · <vm> · <session-id> · reason: <…>` note. The
   override is for real priority or provable non-contention only — not a routine
   bypass.

**Before you claim the lane, ask the § 1 routing question first.** The best way to
avoid queuing on the scarce VM is to not need it: if the work is CPU-only, run it on
a free runner and no lane is involved at all.

## 4. Composition

- **`docs/claude/coordination-board.md`** — the board mechanics + the VM-lane and
  merge-slot claim formats.
- **`.claude/skills/session-coordination/SKILL.md`** — the binding workflow (the
  VM-lane claim is a per-heavy-job precondition, alongside the merge protocol).
- **`docs/claude/trainer-vm-mode.md` § 9–10** — the trainer relay + detach pattern.
- **`docs/CLAUDE-RULES-CANONICAL.md` § Multi-session coordination** — the binding
  one-liner + pointer here.
- Workflows: `claude-run-failure-alert.yml` (the loud flag), `trainer-vm-diag.yml`
  (relay + honest messaging + 60-min cap), `vm-diag-snapshot.yml` (live read relay),
  `research-exit-head-build.yml` / `research-panel-build.yml` (the free-runner
  heavy-compute pattern to copy).
