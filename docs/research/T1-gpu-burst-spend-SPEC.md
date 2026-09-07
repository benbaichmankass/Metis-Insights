# M19 Tier-1 — $10/mo spot-GPU burst tier — spend SPEC (proposal, no spend)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> **Status:** ✅ **SPEND APPROVED (operator, 2026-07-02)**, with the explicit
> directive to **build the cost-tracking UI first** so each training session's cost
> is visible. Originally a proposal / decision memo (2026-07-01, "spec the exact
> spend first, decide later"). **Tracking rails SHIPPED before any spend:** the
> committed `comms/gpu_spend_ledger.json` (this doc's ledger-gate), the
> `src/runtime/gpu_spend.py` helper (`summarize_spend` / `would_exceed_budget` /
> `record_run`), the `GET /api/bot/gpu/spend` endpoint, and a dashboard GPU-spend
> panel. **Remaining human hand-off (the one real step):** add the RunPod API key to
> Actions secrets + fund a small prepaid balance; then the burst workflow below is
> built and the first ~1-GPU-hr T1.1 bake-off runs, writing its cost to the ledger.
> Roadmap: [`ai-model-strategy-roadmap-2026-07-01.md`](ai-model-strategy-roadmap-2026-07-01.md)
> § Tier 1. Live-serve contract reused from
> [`T0.4-live-parity-spike-DESIGN.md`](T0.4-live-parity-spike-DESIGN.md).

## Why a GPU tier at all (and why now)

The M19 map after the Tier-0 sweep: the class-weighted LightGBM regime head over
hand-engineered + **forecast** features is the thing to beat, and every *frozen
off-the-shelf* model class (T0.1 embedding, T0.2 HMM) **matches-or-loses** to it.
The two levers that could actually beat it both need more than the free 1-OCPU
trainer:

- **Deep sequence models** (small TCN / lightweight Transformer over raw bars) —
  the head-to-head test vs LightGBM the roadmap's T1.1 calls for.
- **An in-house self-supervised encoder** (T1.2) — the "reads-everything" model,
  trained on the wide multi-asset corpus, that produces a *task-tailored*
  embedding (unlike the frozen Chronos one that only helped the vol niche).

Both are **GPU-train, CPU-serve**: train in short bursts on a rented spot GPU,
export inference to CPU/ONNX, serve on the free VMs via the existing per-bar
scoring machinery. Nothing about the *live* box changes — the GPU is a
**training-only, ephemeral** resource. **The binding constraint remains labels,
not compute** (only ~350 real trades), so this tier is aimed at the *label-free*
representation work (T1.2 SSL) and the *offline-metric* head bake-off (T1.1),
never at label-hungry RL (Tier-2).

## Cost math (the $10/mo envelope)

| Item | Figure |
|---|---|
| Spot GPU price (RunPod / Vast community, RTX 3090/4090-class, CPU-export target) | ~$0.20–0.40 / GPU-hr |
| Monthly budget | **$10** |
| ⇒ GPU-hours / month at $0.40/hr | **~25 hr** |
| ⇒ at $0.20/hr | ~50 hr |
| A T1.1 head train (≤50M params, our dataset sizes) | ~0.3–1.0 hr/run |
| A T1.2 SSL encoder v0 pretrain (masked-reconstruction, small) | ~2–6 hr/run |

So $10/mo buys **~25 GPU-hours** — comfortably a weekly deep-head train + a
monthly SSL-encoder pretrain with checkpoint/resume, with headroom. The cap is a
**hard monthly ceiling**, not a rate: the burst workflow refuses to launch if the
month-to-date spend (tracked in a ledger file) would exceed the budget.

## Provider comparison

| Provider | Spot $/hr (24GB-class) | API for CI launch/teardown | Notes |
|---|---|---|---|
| **RunPod** (community cloud) | ~$0.20–0.34 | Yes — REST API + `runpodctl`; per-second billing; can auto-terminate | Cleanest CI story; pods stop on command; recommended primary |
| **Vast.ai** | ~$0.15–0.30 (varies) | Yes — CLI/API; interruptible | Cheapest but more variance/interruption; good fallback |
| Lambda / others on-demand | ~$0.50–1.10 | Yes | Over budget for a burst cadence; not for this tier |

**Recommendation: RunPod community spot as primary, Vast as fallback** — both
per-second-ish billed with a scriptable stop, which is what makes a
"burst-train-export-teardown" safe on a fixed budget.

## The burst workflow SHAPE (a spec, NOT a live workflow)

This is the *shape* to build **only after operator go** — it is described here,
not committed as an executable workflow, so nothing can spend by accident. A
GitHub Actions workflow, **issue-label-triggered** (the same Claude-drivable
pattern as the diag relays), operator-gated:

```
name: gpu-burst-train   (NOT YET SHIPPED — spec only)
trigger: issues.opened, label: gpu-burst-train   (operator or Claude-with-approval)
guard 0: read comms/gpu_spend_ledger.json; ABORT if month-to-date + est_run_cost > $10
step 1: provider API → launch a spot pod (pinned image w/ torch+cuda), capture pod-id
step 2: rsync the pinned training corpus + manifest to the pod (read-only data)
step 3: run the train (checkpoint every N steps to a resumable volume)
step 4: EXPORT inference to CPU: torch→ONNX (or torch-CPU state_dict), validate
        numerically vs a GPU-side reference batch (parity gate)
step 5: publish the exported CPU artifact to the model registry mirror
        (runtime_logs/trainer_mirror/ — the same channel models already arrive on)
step 6: provider API → TERMINATE the pod (in a `finally`, always runs)
step 7: append actual cost to comms/gpu_spend_ledger.json; comment cost + metrics
alarms: a hard wall-clock kill (max pod lifetime) + a teardown-on-any-failure trap
```

Key safety properties the spec mandates:
- **Ledger-gated:** a committed `comms/gpu_spend_ledger.json` is the source of
  truth; the workflow refuses to launch past the monthly cap. Claude reads it
  before ever proposing a run.
- **Teardown-guaranteed:** pod termination is in a `finally`/trap so a crashed run
  can't leak a running (billing) pod. A separate scheduled "reaper" lists+kills
  any pod older than the max lifetime as a backstop.
- **Data one-way:** only the read-only training corpus goes *to* the pod; only the
  exported CPU artifact comes *back*. No secrets, no money-DB, no live creds ever
  touch the rented box.
- **CPU-serve contract:** the deliverable is always a **CPU artifact** (ONNX via
  `onnxruntime`, or a torch-CPU `state_dict` loaded without CUDA), served by the
  existing per-bar scorer under its fetch-gate + wall-clock budget — identical to
  the T0.4 live-parity contract. **No torch/CUDA ever on the money-box.**

## On-pod exec design (steps 2–5 made concrete, 2026-07-02)

The workflow *shape* above left the **mechanism** of "get data onto the pod → run
the train → pull the artifact back" unspecified. After the launch/verify/teardown
adapter (`runpod_burst.py`) was proven live (issue #5439: launch → RUNNING →
teardown, ~$0.0001), the mechanism is now fixed as follows — chosen to add **zero
new operator steps** and keep the data-one-way rule airtight.

**Transport: DIRECT public-IP SSH + an EPHEMERAL per-run keypair (landed 2026-07-02).**
The original plan was RunPod *proxy* SSH (`ssh <pod-id>@ssh.runpod.io`), but the
proxy only authenticates **account-registered** keys — not a per-pod key — so an
ephemeral per-run key can't use it without an operator adding a `RUNPOD_SSH_KEY`
secret (the very step we're avoiding). The landed path instead launches the pod
with a **public IP + exposed port 22** (`support_public_ip=True, ports="22/tcp"`)
and hands the runner's throwaway ed25519 public half to the pod via
`env={"PUBLIC_KEY": <ephemeral pub>}`. The **official `runpod/pytorch` image's own
start-script** installs `PUBLIC_KEY` into `authorized_keys` and runs `sshd` on 22
— so no custom `docker_args` is needed (and must be avoided: RunPod interpolates
`docker_args` raw into its GraphQL mutation, where a `%` or `$` aborts the launch —
issues #5447/#5449). The runner then connects `ssh -i <ephemeral priv> root@<ip> -p <port>`.
**No account SSH key, no new GitHub secret, no operator action** — the keypair is
born and dies with the run. Verified live: probe #5451 (launch → public-IP SSH →
env check → teardown, ~$0.002).

**Data onto the pod: the pod builds its own dataset from PUBLIC sources.** The
`ict-trading-bot` repo is **public**, so the pod `git clone`s it directly (no
token). The T1.1 dataset (`market_features` for a BTC regime head) is built from
**public candle data + the committed corpus** (`runtime_logs/trainer_mirror/corpus`,
the C1b FRED store) — it does **not** touch `trade_journal.db`. So the pod builds
its own training input with `python -m ml build-dataset …`; nothing from the money
box is ever uploaded. (Label-hungry heads that DO need the journal are out of
scope for the burst tier by construction — that's the Tier-2 line, and the
constraint is labels, not compute.)

**Artifact back: gzip|base64 over the SSH channel.** The pod `gzip`s the trained
artifact and emits it **base64 on the SSH command's stdout** (framed between
`---ICT-ARTIFACT-BEGIN/END---` markers); the runner decodes it straight into the
registry mirror. Deterministic, dependency-free, no relay/one-time-code
coordination. (`runpodctl send/receive` is the documented scale-up path for the
larger T1.2 encoder, where a multi-hundred-MB artifact outgrows an SSH stdout —
noted, not built for v1.)

**What the artifact IS (landed 2026-07-02): the trained `model_state` bundle, not
ONNX.** The first driver increment trains the existing **LightGBM** regime/direction
heads, whose artifact is the JSON-embedded booster in `model_state.json` — there is
**no ONNX export and no numeric parity gate in the codebase** (`ml train` has no
such flags; verified against `run_training_cycle.sh`). So the pod returns a single
JSON **bundle** = `{model_state, metrics, manifest}` for the freshest experiment
run. The **ONNX-export + CPU/GPU parity gate is deferred to the first *deep* (torch)
manifest** — it only becomes necessary when a model is trained on CUDA and must be
served on the CPU money-box; a LightGBM booster is already CPU-native, so it needs
neither. Building that export+parity path is a prerequisite of the T1.1 deep-model
increment, tracked there — not faked here.

**The exact per-run sequence (all driven from the runner over direct public-IP SSH):**

1. `create_pod(..., support_public_ip=True, ports="22/tcp", env={"PUBLIC_KEY": <ephemeral pub>})`
   → wait RUNNING → wait for the public 22 endpoint + sshd to accept the key
   (existing adapter, with the capacity fallback).
2. SSH: `git clone` the public repo at the pinned SHA + `pip install` the train deps.
3. SSH: `ICT_OFFVM_BUILD_HOST=1 python -m ml build-dataset market_raw …` (Bybit public
   klines) → `build-dataset market_features …` — public candle data only, no journal.
4. SSH: `python -m ml train <manifest> --datasets-root … --registry-root …` → writes
   `model_state.json` + `metrics.json` under `ml/experiments-runs/<model_id>/<run_id>/`.
5. SSH: the pod bundles `{model_state, metrics, manifest}` → `gzip | base64 -w0` →
   runner decodes to `runtime_logs/trainer_mirror/gpu_burst/<manifest>.bundle.json`
   (uploaded as a workflow artifact — no multi-MB blob committed to git).
6. `terminate_pod` in the `finally` (unchanged guarantee); append the **actual**
   billed cost to `comms/gpu_spend_ledger.json`; the trainer VM ingests the mirror
   artifact into the registry at **`candidate`** stage (operator-gated to go past
   shadow, unchanged).

**Safety deltas this introduces:** (a) the ephemeral key means a leaked pod can't
be re-entered after the run (key is gone); (b) still **no secret / money-DB / live
cred** touches the pod — it only ever holds public code + public market data + the
model it trains; (c) the first heads are CPU-native LightGBM (booster JSON), so
"trained on the pod" and "served on the CPU money box" are already identical — the
GPU/CPU numeric parity gate becomes load-bearing only for the deferred deep-torch
increment, where it gates ONNX export before ingest; (d) teardown stays in
the `finally`, and the ledger still hard-gates the monthly cap before launch. The
model still lands at `candidate` — **the shadow→advisory promotion remains the
operator-gated live switch**, untouched by this tier.

## The concrete FIRST experiment (what the first ~1 GPU-hr buys)

**T1.1 — a small deep sequence regime head vs the LightGBM head, head-to-head on
the SAME offline gate.** A lightweight TCN (or a 2–4-layer, ≤5M-param Transformer)
over the raw BTC-15m bar sequence, target `regime_label`, trained on the existing
`market_features` dataset, evaluated under the *same* purged-CV split the whole
M19 line uses, vs `btc-regime-15m-lgbm-base-pcv`. Success = it **beats** the tree
head's macro_f1 / f1_volatile by more than run-noise (the tree is the incumbent
none of the frozen models beat). Exported to ONNX, parity-validated, and — only
if it wins — carried into the same live-parity/shadow path as T0.4.

- **Why this first (not the SSL encoder):** it's the cheapest, most falsifiable GPU
  test — one ≤1-hr run answers "does a deep model over raw bars beat the boosted
  tree on our data at all?" If **no** (likely, given the label/data regime), that's
  a decisive, cheap negative that says the corpus + SSL (T1.2) is the only path —
  and we haven't spent the month's budget finding out. If **yes**, it's the first
  model to actually beat the incumbent and justifies the tier immediately.
- **T1.2 (SSL encoder)** is the *second* experiment, gated on the corpus workstream
  (design pending) being far enough along to feed it — it's the bigger,
  multi-GPU-hour bet and should follow the corpus, not precede it.

## The exact go/no-go the operator approves

Before **any** paid resource exists, the operator approves this bundle:

1. **The $10/mo hard cap** + the ledger-gated, teardown-guaranteed workflow shape
   above (built as a real workflow *only* on this approval).
2. **The provider** (RunPod primary) + adding the provider API key to Actions
   secrets — a genuine human-only secret step (the one real hand-off).
3. **The first experiment** = the T1.1 deep-head bake-off (~1 GPU-hr), reporting
   back cost + metrics before any second run.

Until all three are given, this stays a **paper spec**: no workflow, no key, no
pod, $0. If at any point a step would incur spend before that approval, it stops
and asks. **No paid compute is incurred by this document.**

## Open questions for the operator

- **Budget cadence** — is $10/mo a hard ceiling (workflow refuses past it) or a
  soft target (alert + continue)? Spec assumes **hard ceiling**.
- **Who triggers a burst** — operator-only, or Claude-with-in-chat-approval (the
  system-actions Tier-2 pattern)? Spec assumes the latter, ledger-gated.
- **Provider account** — new dedicated RunPod account funded with a small prepaid
  balance (so the ceiling is enforced by the *balance*, not just the ledger) is
  the safest belt-and-suspenders; confirm before key creation.
