# Running the automated training pipeline off the trainer VM (Kaggle / runners) — design, 2026-10-13

> **Doc status:** `unknown` · category `plan` · last verified `2026-10-13` (cycle cost and manifest counts MEASURED this session; Kaggle limits other than the GPU/TPU quota are NOT verified, they are marked) · lane `ML-DATA-OFF-TRAINER` · checklist row `ML-DATA-OFF-TRAINER` · requested by the manager (operator, ~15:55Z) for the 2026-10-14 weekly planning. **A design, not a build. Nothing here is Tier-1 to start: the registry write-back, any timer change and arming `ict-offload-drain` are Tier 2.**

## 0. Short answer

1. **Most of the scheduled training can leave the VM; the part that needs OUR data and the registry cannot.** Of the 73 manifests in `ml/configs/` (MEASURED by reading each manifest's dataset family): **63 (86%)** train on market data (43 + 5 + 5 `market_features`, 10 `setup_candidates` = synthetic labels over public bars). **10 (14%)** need OUR data: `conviction_meta` 3, `execution_quality` 2, `account_context` 1, `setup_labels` 1, `setup_labels_audit` 1, `trade_outcomes` 1, and `exit_candidates` 1 (unverified, treated as ours).
2. **Kaggle is the wrong first target for the CPU work; a GitHub runner is simpler and already built.** The cycle is LightGBM/HMM on CPU. `kaggle quota` meters GPU and TPU only (rows observed 2026-10-13: GPU 30 h/week, TPU 20 h/week), so a CPU job costs none of the 30 GPU-h. A runner offers 4 vCPU / 16 GB for up to 6 h (`trainer-offload-train.yml` already trains a manifest there and was built because the trainer OOMs at 6 GB). Kaggle's distinct value is a **GPU** (the TCN/deep heads) and a second pool of free hours. Recommendation: CPU manifests → runner; GPU heads → Kaggle; RunPod as the ledger-capped fallback (already wired in this lane).
3. **The hard part is not compute; it is the return path.** The registry is trainer-local and untracked (96 entries on disk, 0 tracked), the shadow factory loads from it, and promotion readiness reads live shadow rows from the journal. Models must come back through a single writer on the trainer.
4. **The bigger consumer is not the nightly cycle.** `ict-drift-retrain` (hourly) is 63.7% of the trainer's service CPU; the cycle + catch-up are 31.7%. It needs fresh bars every hour, which a daily export cannot give (§1, §6).

## 1. Measured cycle cost (the thing to compare with a quota)

Source: `docs/research/trainer-workload-2026-10-13.md`, systemd journal, 7 days to 2026-10-13 16:00Z, 1 OCPU Ampere. Not measured: peak memory per manifest (docs claim ~5 GB under `MemoryMax=5G`).

| piece | cadence | CPU-h/week | CPU-h/day | wall-h/day | note |
|---|---|---|---|---|---|
| `ict-trainer` (dataset rebuild + primary training) | 00:05Z | 8.82 | 1.26 | 1.8 | 67–69 manifests trained per cycle (`/api/bot/ml/cycle`, 2026-10-09→13) |
| `ict-trainer-catchup` | 05:00Z | 6.29 | 0.90 | 1.6 | resumes manifests the primary run did not finish (checkpoint file per UTC date) |
| **scheduled cycle total** | | **15.11** | **2.16** | **3.4** | per-day sum of manifest wall time alone: 28–64 min (same endpoint) |
| `ict-drift-retrain` | hourly | 30.45 | 4.35 | 8.0 → 10.0 and rising | 167 runs in 7 d |
| `ict-trainer-forecast` (frozen Chronos forecasts) | 15 min | 1.44 | 0.21 | 0.5 | feeds the `fc_*` columns |
| `ict-promotion-readiness` | ~04:10Z | 0.06 | 0.01 | 0.1 | reads live shadow rows |

**Versus the quotas.** The cycle uses 0 GPU-hours today (CPU trainers). A hypothetical 4-vCPU runner doing the same 2.16 CPU-h/day of single-core-equivalent work would run it in roughly an hour of wall time (assumption: ~2–3× the Ampere core's throughput, unmeasured; the real figure needs one dual-run, §7 phase 2). That is ~1 of the runner's 6 h per job, and 1 of a pool shared with every other workflow (20 concurrent jobs on the Free plan, plan tier unverified). The 30 GPU-h/week would only be touched by the GPU heads (`torch_sequence`/TCN), which are a small fraction of 73 manifests. Per-job overhead on Kaggle was 186 s (≈90 s queued + ≈90 s start, pilot run 38383862376); one data point.

## 2. Data refresh cadence and freshness

* **Today's chain on the VM:** the primary cycle rebuilds the dataset families, then trains; the catch-up run rebuilds again at ~05:20–05:45Z (BTC/ETH/SOL 15m `market_raw` shows rewrite times 05:18/05:36/05:42Z). The shard is therefore not "ready" until ~05:45Z, and the published export is scheduled at 06:40Z for that reason.
* **Nightly training off-VM needs the dataset by ~06:45Z and gives models ≈ 7 h older than a 00:05Z VM cycle would** (the 00:05Z run trains on the previous build). For daily regime heads whose labels look 5 bars ahead and which retrain every night anyway, that staleness is within the noise of "nightly"; confirm per manifest class in the dual-run.
* **Freshness is a gate, not a hope.** The export already records `last_ts` / `stale_days` per shard and withholds a shard that disagrees with its own metadata (the BTC/SOL 15m shards were cut short, `ML-SHARD-TRUNCATED`). A training job must refuse a dataset whose `last_ts` is older than its declared tolerance (proposal: 36 h for nightly heads) and say so, instead of training on a stale shard and registering a model that looks fresh. This check does not exist yet.
* **Drift retrain is a different problem:** it needs new bars every hour. Options, in order of cost: (a) leave it on the VM; (b) hourly export of the last ~30 days (2,880 rows per 15m shard, a few MB) merged into the standing dataset on the runner. Do not do (b) until the VM share it frees is worth it (§6).
* **Builds themselves:** the builders need internet (Bybit public klines). Kaggle kernels run with internet off here; GitHub-hosted runners are geoblocked by Bybit (`trainer-offload-train.yml` header, "Bybit geoblocks US GH-runners") and use Binance-vision instead, a **different feed** (models trained on it are not comparable to the VM's without a check). So dataset builds stay on the VM in v1 and the published dataset is their output.

## 3. Runtime limits

* **Kaggle:** GPU 30.00 h/week, TPU 20.00 h/week, refreshing weekly (`kaggle quota`, observed). Session length and CPU hours: **not verified** (the pilot only ran 186 s; the figures 9–12 h and ~4 cores/30 GB are from memory and must be checked before a design depends on them). One research kernel slug exists today, so research runs on Kaggle are serial; a nightly cycle needs its own slug(s).
* **GitHub runner:** 6 h/job, 20 concurrent (Free plan, official limits page fetched 2026-10-13), 4 vCPU / 16 GB (precedent: `trainer-offload-train.yml`, 330-min timeout).
* **Cycle shape that fits:** 63 market manifests × ~1 min each (the 28–64 min/day of manifest time ÷ 67–69 manifests ≈ 25–55 s each on the Ampere core) is well inside a single job; the checkpoint/resume file the VM cycle already uses (`cycle_progress_<date>.json`) should be reused so a killed job resumes rather than restarts.

## 4. How artifacts and models come back into the registry

The registry (`ml/registry/model_registry.py`, `ml/registry-store`) is **trainer-local and append-only**. The existing return path is `ml/offload-inbox/` (`trainer-offload-train.yml` publish → git → trainer 15-min force-sync → `scripts/ml/offload/drain_inbox.py`), which **ships unarmed** and registers under a namespaced id at forced `candidate`. Facts that constrain a full-fleet design (from `ml/offload-inbox/README.md`): `model_state.json` p50 1.34 MB, max 2.05 MB; ~52 runs/day; committing every run would be ~70 MB/day of permanent git history, which is why publishing is opt-in per run with a 25 MB refusal.

Proposed return path (needs the operator for the Tier-2 parts):

1. The off-VM job trains with `--no-register` and writes `model_state.json`, `metrics.json`, `manifest.json` + a **content hash and the dataset MANIFEST hash it trained on**.
2. Two transports, a decision for the planning. (a) **Git, promotable subset only:** the runner commits just the models that pass a gate into `ml/offload-inbox/` (exists; the trainer's 15-minute force-sync delivers them; no new credential anywhere). (b) **A private Kaggle dataset `metis-model-drops`** for every run (no git growth), which needs a way for the trainer to read it: a credential on the VM (operator-originated) or a runner-to-VM push over the existing SSH route. Recommendation: start with (a); it is already built and the 25 MB refusal bounds history.
3. The drain registers on the trainer (**single writer**), keeping `candidate` as the forced stage until the existing promotion ladder moves it. Registering a runner-trained model under the SAME id as a VM-trained one is a behaviour change for the live shadow fleet: Tier-2, one operator OK, as `ml/offload-inbox/README.md` already says.
4. **Parity gate before any cutover:** the same manifest trained on the VM and off-VM on the same dataset hash must agree on the registered metric within a declared tolerance, for every manifest class, over several nights (§7 phase 2).

## 5. Secrets, and what a kernel may see

* Kernels and runner training jobs hold **no secret**. The runner holds `KAGGLE_API_TOKEN` (already used by two workflows) and, for the export only, the existing trainer SSH key. Nothing credential-shaped is in the dataset (`market_data_export.py` refuses columns that look like account data, and uploads only allowlisted market paths).
* **OURS data in Kaggle:** the operator's 15:55Z update makes private Kaggle datasets acceptable for everything, with the exception of credentials/account secrets. That decision is not taken lightly for the 10 OURS manifests: the trade journal carries account identifiers, balances and order packages, so the dataset would need an explicit **sanitising export** (drop account ids and balances; keep the label and feature columns the manifest names) and a statement of who can read a private dataset. **Recommendation: keep the 10 OURS manifests on the VM in v1.** It is 14% of manifests and the data is the most sensitive; revisit once the sanitising export exists.

## 6. What must stay on the VM

| stays | why |
|---|---|
| **Registry (single writer), shadow factory, drain** | models are loaded from trainer-local paths; registering from two places invites divergent registries |
| **`sync_trainer_data` → `trade_journal.db` and the 10 OURS manifests** | OURS data (§5) |
| **`ict-promotion-readiness`, shadow track-record reads** | read live shadow rows from the journal; promotion inputs are OURS |
| **Dataset builds (Bybit public klines)**, the `fc_*` forecast producer, the order-flow capture | need Bybit reachability (runners are geoblocked) and run continuously; the forecast producer is torch/Chronos and could move to a Kaggle GPU kernel later (small: 1.44 CPU-h/week) |
| **Drift retrain (v1)** | needs hourly fresh bars (§2); revisit only if the cycle + catch-up move and the VM is still saturated |
| **Mirror publish (2-min)** | feeds `/api/bot/ml/*` for the SPA; trivial CPU (0.72 h/week) |

Net effect if the cycle + catch-up leave and the rest stays: the trainer sheds 15.1 of 47.8 measured CPU-h/week (31.6%) and its two worst hours (01Z, 05Z, over-subscribed 1.74× and 1.96×); moving drift retrain too would shed another 30.45 (63.7%). Both numbers are measured on the VM; the benefit of each is arithmetic, not a trial.

## 7. Failure and no-halt handling

* **No pipeline halts itself** (operator directive 2026-10-09, binding). A failed off-VM job is contained per manifest, retried with the failure diagnosed (stderr tail recorded, as `run_training_cycle.sh` does), and falls back in this order: next free backend for that class (runner → Kaggle CPU/GPU → trainer VM), then the trainer VM cycle itself, which **stays installed and armed as the standing fallback** until a cutover is signed off. Only after N consecutive failures of the same manifest does ONE red flag reach the operator (checklist/ping); the pipeline keeps trying.
* **Quota/availability short:** a GPU head waits for Kaggle quota or falls to RunPod within the $10/month ledger (this lane's router change); a CPU head never waits on Kaggle (it has the runner). The queue never blocks other work.
* **Stale or cut-short data:** refuse and report (a dataset-integrity failure is a route fault, not a model result), exactly as the export's `withheld` list and `published_data.py` (exit 7) do today.
* **Dual-writer risk:** until cutover, VM and off-VM runs of the same manifest must write to different ids (`-offload` namespace) so one cannot silently overwrite the other's candidate.

## 8. Phased build plan with effort

Effort = lane working days of a Sonnet-class lane plus the operator OKs named; no calendar promise, no dollar figure (Claude usage is not metered per phase here). Phases 0–1 are in flight now.

| phase | scope | effort | gate to the next |
|---|---|---|---|
| **0 (this lane, PR #17898)** | classify; private market-data export (daily, 06:40Z); verify from a runner and a Kaggle kernel; router runs ML units on the published data; trainer as a windowed queue; RunPod fallback | done in code, **first export unproven** | first verified export |
| **1** | re-route the 16 queued ML research units (PR 2); fix `ML-SHARD-TRUNCATED` (find the 05:xx writer, repair BTC/SOL, add a row-count check to dataset validation) | 1–2 d (+ Tier-2 data repair OK) | one ML unit lands a record end to end; BTC/SOL shards whole |
| **2** | **dual-run:** the cycle's 63 market manifests trained on a runner from the published dataset with `--no-register`, same dataset hash as the VM, metrics compared per manifest class; add the freshness refusal; reuse the checkpoint/resume file | 3–4 d + a 7-night soak (calendar, not effort) | parity within the declared tolerance on all classes |
| **3** | return path: runner commits promotable models to `ml/offload-inbox/` (exists) or the private model-drops dataset; arm the drain for `-offload` ids at `candidate` | 2–3 d + **Tier-2 OK** to arm | a runner-trained model registers as `candidate` and is refused by the shadow factory as designed |
| **4** | cutover: the VM cycle + catch-up become the **fallback** (run only when the off-VM run did not finish by a deadline); promotion readiness unchanged | 1–2 d + **Tier-2 OK** (timer edit) | 14 clean nights, no manifest missed |
| **5 (optional)** | GPU heads and the forecast producer on Kaggle GPU under the weekly quota; hourly incremental export + drift retrain off-VM | 4–6 d | only if the VM is still saturated after 4 |
| **6 (optional)** | sanitised OURS export and the 10 OURS manifests off-VM | 3–5 d + operator decision on custody | explicit operator OK on private upload of trade-derived data |

**Cost of the first slice.** Ship the export and one routed unit end to end (phase 0–1) inside this lane's budget; phase 2 is the next lane (3–4 days of lane work). Phases 3–4 are Tier-2 and wait for the operator.

## 9. What this design does not claim

* Kaggle CPU session length/limits, concurrent-session limits and throughput relative to the Ampere core are **not verified**.
* The runner-vs-VM speed ratio is an assumption until the dual-run measures it.
* Per-manifest peak memory was not measured; the 6 GB OOM history is why the runner (16 GB) is preferred for the heavy ones.
* The writer that truncated the BTC/SOL 15m shards is **unidentified**. `build_funding_oi` re-overwriting the 15m shards minutes after a join is a documented precedent (`MB-20260726-FC-CLOBBER`, in `ml/configs/btc-regime-15m-lgbm-fc-pcv-v2.yaml`) and is the first suspect to read, but nothing here shows it is the cause.

## 10. Addendum 2026-10-15 — backend per manifest, and the parity gate as built (row KAGGLE-TRAIN-RUNNER-FIRST)

Operator, 2026-10-15 ~06:20Z: *"Train on free runner (Rec.)"*. This section is what the lane built and measured; §§ 0–9 above are unchanged.

**Backend per manifest** (MEASURED 2026-10-15 by parsing the 73 active `ml/configs/*.yaml` and testing each dataset path with
`market_data_export.covers()`; `.disabled` files excluded):

| backend | manifests | which |
|---|---|---|
| **off-VM now** — the shard is in the published `metis-market-data` allowlist | **18** | `market_features` BTC/ETH/SOL 15m/1h `v002` (LightGBM 15, RegimeClassifier 2, HMM 1). SOL 15m is currently WITHHELD by the export integrity check (ML-SHARD-TRUNCATED), so its 2 manifests skip until that shard is whole |
| **off-VM after export widening** — market data, shard not exported | **45** | `market_features` 5m v002 (10), 15m research versions v003/v004/v5xx/v901/v001 (~23), 1h v003 (1), 1d v002 (1), `setup_candidates` (10: synthetic labels over public bars) |
| **trainer only** — OURS data | **10** | `conviction_meta` 3, `execution_quality` 2, `account_context` 1, `setup_labels` 1, `setup_labels_audit` 1, `trade_outcomes` 1, `exit_candidates` 1 |
| **Kaggle GPU** | **0** | no active manifest uses a GPU trainer (trainers: LightGBM multiclass 47, LightGBM regression 11, PerStrategyWinRate 5, RegimeClassifier 5, CausalHMM 5). The GPU route is for future TCN/TSFM heads only |

**Which off-VM CPU, and why not the GitHub runner first.** The operator's pick was the free runner. MEASURED (row ML-DATA-RUNNER-FETCH, runs
38507974681 / 38512183556 / 38514877849): a GitHub runner cannot download the private dataset (kaggle 2.2.4 `DownloadDataset` 404s), while a
Kaggle CPU kernel mounts it (20/20 files re-hashed). So the off-VM CPU backend that has the data today is the **Kaggle CPU kernel**
(`kaggle_cpu`, free, not metered by the 30 GPU-h/week). It needs no new workflow: the research-queue dispatcher already routes
`routing.published_data` units there. Two ways to put the runner itself back in front, neither built: (a) fix the 404 (ML-DATA-RUNNER-FETCH);
(b) a runner workflow that pulls the shard over the existing trainer SSH route the export already uses — a new workflow holding `VM_SSH_KEY`,
so a hold PR for the manager, not a lane self-land.

**The parity gate (built, `scripts/research/train_parity.py`, tests `tests/test_train_parity.py`).** Both sides keep only rows with
`ts < cutoff`, byte for byte, so a shard the nightly rebuild has since extended still yields the identical input; its sha256 is recorded.
Both train through the production `run_experiment(register=False)` into a temp dir — no registry write anywhere. Rule, pre-registered on
RQ-20261015-8b93-02:

* **INDETERMINATE** — either run failed, or the input sha256 / row count differ (not the same rows; parity undefined).
* **PASS** — same input, every count metric (`n_eval`, `n_train*`, `support_*`) exactly equal, every score metric within **0.01 absolute**,
  and **≥ 0.99** of eval rows given the same predicted class.
* **FAIL** — same input, anything above out of tolerance.

Fixed manifest: `eth-regime-1h-lgbm-v1` (ETH 1h is whole and published; LightGBM is the 47-manifest majority class), cutoff
`2026-10-01T00:00:00Z`. Units: **RQ-20261015-8b93-01** (trainer reference, `needs_vm_data`, idle windows) →
**RQ-20261015-8b93-02** (`requires_result` 01 pass; `kaggle_cpu`; bundles 01's record). The dispatcher fires both; nobody hand-dispatches.

**What a PASS does and does not authorise.** One manifest class on one platform pair. It is the evidence for ONE operator OK: the first
off-VM artefact entering the registry as `candidate` under a namespaced id (`<model_id>-offload`, §7 dual-writer rule) through the existing
drain/ladder. It does not move the nightly cycle (phase 4, Tier-2 timer change) and does not cover the HMM / RegimeClassifier /
regression classes, which need their own parity unit before their manifests leave the trainer.
