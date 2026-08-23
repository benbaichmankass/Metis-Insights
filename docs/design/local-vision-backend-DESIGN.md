# Local vision backend — the prop screenshot reader without an outside service

**Status:** planned (P0 not started) · **Opened:** 2026-08-23 · **Anchor:** `MB-20260823-LOCAL-VISION-BACKEND`
**Tier:** 1 for P0–P2 (fixtures, harness, measurement) · **Tier 2** for P3 (a new service the live VM calls) · **Tier 2** for P4 (flipping the default)

> **Operator, 2026-08-23:** *"we still need to build out this LLM … the Anthropic
> credits are not relevant here at all. We cancel that out … We need to make sure
> that the screenshot parser is actually going to work on the local LLM, on the
> cloud, and not waiting for credits from external supports that aren't
> relevant."*
>
> **The credits framing was mine and it was wrong.** I reported the live
> `credit balance is too low` 400 as *"operator action — add credits"*. Paying to
> keep broker screenshots flowing to a third party is not a fix for a path that
> should not have been live. Billing is not a blocker on this milestone and does
> not appear again in this document.

## 0 · What "local LLM on the cloud" has to mean here

The phrase has already misled once — `llm-burst-worker-DESIGN.md` opened with
*"we wanted a local LLM on cloud"* while describing an external Gemini call, and
the operator reasonably read it as a statement about where inference happens.
So it is pinned here before anything else:

- **local** = the image and its extracted numbers never leave systems we control.
- **cloud** = it runs on our OCI tenancy, not on the operator's machine.

The trainer VM satisfies both readings exactly. Nothing else available to us does.

## 1 · Constraints — every one measured, none assumed

| # | Constraint | How it was established |
|---|---|---|
| 1 | **The repo is PUBLIC** (`"private": false`, `visibility: "public"`, read from the GitHub API 2026-08-23) | Actions **run logs and artifacts are world-readable** |
| 2 | The burst worker prints its result envelope **to the run log** and uploads large outputs as an artifact | `.github/workflows/llm-delegate.yml` header, verbatim |
| 3 | The burst worker is **text-only** | zero matches for `image\|vision\|inline_data\|base64` across `scripts/llm/*.py` |
| 4 | **Zero Ampere headroom** — 4 OCPU / 24 GB, fully allocated (live 2/12 + trainer 1/6 + gateway 1/6) | `oci_inventory` run 32131324874: 3 × `match`, 0 drift, against `comms/cloud/expected_topology.json` |
| 5 | The live VM is **excluded** as a host for anything CPU-saturating | money box; the shape of both June 2026 wedges (`MB-20260609-001`, `BL-20260609-001`) |
| 6 | The parser runs **on the live VM**, inside `ict-claude-bridge.service` | `deploy/ict-claude-bridge.service`: `WorkingDirectory=/home/ubuntu/ict-trading-bot`, `ExecStart=… -m src.bot.claude_bridge` |
| 7 | The photo handler already runs the read **off the event loop** | `claude_bridge._on_operator_photo` → `asyncio.to_thread(handle_screenshot, …)` |
| 8 | **No labelled screenshot corpus exists.** Telegram images are downloaded into memory and discarded | `_on_operator_photo` holds `image_bytes` locally; nothing writes them |

## 2 · The decisive finding: the burst worker cannot carry this flow

The obvious plan — *"we already built a burst worker, point the screenshot reader
at it"* — **is not available, and would be worse than what it replaces.**

Constraints 1 + 2 together mean routing a broker screenshot through
`llm-delegate` would put an image carrying **account balance, equity, the broker
account number and open positions** onto a GitHub-hosted runner and into a
**world-readable run log**. Against the operator's standard ("shouldn't be sent
to any outside service") a GitHub runner is an outside service, and unlike the
hosted-model path the output is *published* rather than merely transmitted.

Constraint 3 means there is no vision path there to point at even if the
transport were acceptable.

A **self-hosted** runner does not rescue it: GitHub's own guidance is that
self-hosted runners must not be used on public repositories, because a fork PR
can execute arbitrary code on them. That is a worse hazard than the one being
fixed.

**So Phase 3 of `llm-burst-worker-DESIGN.md` is not "add a GGUF backend to the
delegate". It is a separate service, and this document supersedes that line for
the screenshot flow.** The delegate keeps its own scope guard and its own
purpose (public repo code + docs); it is simply not the vehicle for live account
data, and never can be while the repo is public.

## 3 · Where inference can run

| Option | Verdict |
|---|---|
| **Trainer VM** (`ict-trainer-vm`, 1 OCPU / 6 GB, Ampere aarch64) | **Proposed.** Already allocated, already ours, autonomous territory, **no money at risk**. A saturation event there costs a delayed training cycle, not a trade — which is exactly the property the live VM lacks. Cost $0. |
| **Live VM** | **Rejected**, constraint 5. Not negotiable. |
| **A new OCI instance** | **Rejected at $0**, constraint 4. Available only as a paid decision the operator would have to take deliberately. |
| **GitHub Actions runner** (the burst worker) | **Rejected**, § 2. Public run logs. |
| **Deterministic OCR** (Tesseract on the VM, no model) | **Kept as a live alternative, not dismissed.** No weights, no new service, packaged for aarch64. It loses on layout variance and on honest-null (an OCR pass has no notion of *"this screen has no balance on it"*), but it is measured against the same fixtures in P2 and wins if it clears the gate. Choosing the VLM before measuring would be picking the fancier tool, not the better one. |

### The service shape: spawn per read, not a resident server

A resident llama.cpp server holds 2–3 GB permanently on a 6 GB box that also
builds datasets and trains models. The prop bridge sees **a handful of
screenshots a day**, so a resident process would spend >99% of its life holding
RAM away from the trainer's actual job.

So: **one bounded process per read, destroyed on completion** — the same reframe
that made the burst worker right ("a job IS the worker"), applied one level
down. The cost is model-load latency per request (to be measured, P2); the
benefit is that the trainer's steady-state memory is unchanged and a stuck read
cannot outlive its own timeout.

A **concurrency guard** is required, not optional: the read must refuse (three-state,
below) rather than queue behind or contend with a running training cycle.

### Transport

Live VM → trainer over the OCI VCN, the same private-subnet pattern the trader
already uses to reach the IB gateway at `10.0.0.251`. **Unverified and P3's
first task:** whether `ict-trainer-vm` currently has a private-subnet address the
live VM can route to, or whether today's live→trainer traffic goes over public
IPs. If it is public, the call carries a bearer token over TLS **and that fact is
recorded**, rather than being described as private because both ends are ours.

## 4 · The accuracy gate is the work; the plumbing is the easy half

**A misread digit here is a safety event, not a cosmetic one.** The extracted
balance flows `parse_screenshot` → `prop_report.ingest_report` →
`prop_account_status` → `/api/bot/prop/status` **rule-distance** → the operator's
cushion against the **$4,700 static-DD floor** that ends the account.

This is not hypothetical. Measured 2026-08-23 on one screenshot: the operator's
typed `bal 5040 5010` and the screenshot's **4869 / 4867** differ by **$171** —
a **$340** cushion versus a **$169** one, on the same account, on the same day.
Whichever number is right, that is the size of the error a bad transcription
produces.

So the gate is stated before any model is chosen:

1. **Per-FIELD exact match on money numbers** (`balance`, `equity`, `entry_price`,
   `exit_price`, `qty`), not a holistic "looks right". A model that gets four of
   five fields right on a balance screen has failed the field that matters.
2. **Honest-null graded separately, in its own column, against its own
   denominator.** A model that INVENTS a balance on a Position screen is
   categorically worse than one that omits it — the first arms the rule-distance
   guard against a fiction, the second degrades to "type it". These two numbers
   are **never pooled** (the M37 lesson: substantive claims 20/20 beside line
   citations 0/20 was the finding *because* the columns were kept apart).
3. **The hosted path is the control arm**, run on the same fixtures. The design
   this supersedes already required it: *"measured against the hosted backend
   before being preferred"*. A local backend that reads money numbers worse than
   the thing it replaces is not a privacy win, it is a different bug.
4. **A stated denominator on every figure.** n, and what n is made of.

### The corpus problem, which is P0 and blocks everything

**We have no labelled screenshots** (constraint 8). Nothing can be measured until
that changes, so P0 starts the corpus accruing and nothing downstream can start
without it.

⚠️ **The corpus must NEVER be committed.** A prop screenshot carries the broker
account number; this repo is public (constraint 1). It lives on the VM under
`/data/bot-data/prop_screenshots/`, alongside a sidecar JSON of the report that
was actually ingested — and the *label* is the operator's own typed correction
where one exists, which is the only ground truth we have that did not come from a
model. The gate therefore runs **on the VM or the trainer, never in CI**, and CI
sees only the aggregate result.

## 5 · Phases, each with what would FALSIFY it

Per the standing directive (recorded under M31): a next step states what would
show it is wrong, not what would confirm it.

| Phase | Work | Tier | Falsifier |
|---|---|---|---|
| **P0** | Persist every prop screenshot + the report actually ingested, on the VM, uncommitted. Retention bounded. | 2 (writes on the live VM) | *A month later the directory holds too few distinct screen LAYOUTS to state a denominator over* — in which case a corpus was not the blocker and the gate must be defined on something else. |
| **P1** | An offline harness: fixture dir → per-field score + honest-null column, one command, provider-agnostic (local / hosted / OCR behind one interface). | 1 | *The harness cannot reproduce the known 2026-08-23 conflict* (typed 5040/5010 vs read 4869/4867). A scorer that cannot show the one error we have on record is not measuring the thing. |
| **P2** | Measure candidates on P0's corpus: ≥1 small VLM (GGUF, aarch64), Tesseract OCR, and the hosted control. **Ask the runtime what it serves; never ship a model id from memory** — that lesson cost three failed runs on M37 and then cost this very module a silent primary-provider outage on a non-existent `claude-sonnet-5`. | 1 | *No local candidate reaches the hosted control's per-field accuracy at acceptable latency on 1 OCPU.* Then say so plainly: the honest outcome is that the feature stays typed-report-only, and that is a real result, not a failure to try. |
| **P3** | Build the winner as a bounded per-read process on the trainer + the live→trainer call. Verify the transport is actually private before describing it as private. | 2 | *A read contends with a training cycle, or exceeds its timeout, in normal operation.* The trainer's own job is not allowed to degrade for a feature that sees a handful of requests a day. |
| **P4** | Make `PROP_SCREENSHOT_BACKEND=local` **functional** instead of a refusal. | 2 | *Post-flip field accuracy on live screenshots diverges from P2's measured figure.* A gate passed on a fixture set is a claim about that set until live reads agree. |

## 6 · What is true today, and the operator's two levers

`PROP_SCREENSHOT_BACKEND` ships defaulting to **`local`**, which today **refuses**
rather than silently calling a hosted model. Three states, never collapsed:
`local` (intended, not built — refuse and name the remedy) · `external` (explicit
opt-in to the hosted providers) · `off` (deliberately disabled). An unparseable
value fails closed.

**So until P4 lands, the screenshot reader does not read screenshots.** That is a
real cost, stated plainly rather than buried: the manual bridge falls back to the
typed grammar, which works today and is unaffected — `bal 5040 5010`,
`close ETHUSD 2950 +80 tp`. The typed path is also the only path whose numbers
never passed through a model at all.

Two levers, both the operator's:

1. **Leave it at `local`** — screenshots refuse, typed reports carry the bridge,
   nothing leaves the system. This is the shipped default and matches the stated
   directive.
2. **Set `external`** — today's hosted behaviour, deliberately and on the record.
   If choosing this, note that the Anthropic key is out of credit, so `external`
   currently resolves to the Gemini **free tier**; check that tier's data-use
   terms first, because free tiers and paid tiers are not the same contract. This
   document does not assert what those terms say.

`local` and `off` are deliberately distinct: *"should work, not built yet"* and
*"we chose not to"* are different statements, and collapsing them would erase the
reason this milestone exists.

## 7 · Open and unmeasured — stated so it is not read as settled

- **Trainer private-subnet reachability from live** — unverified (§ 3).
- **Throughput of a small VLM on 1 Ampere OCPU** — unmeasured. No token/s figure
  appears in this document on purpose; the M37 Cerebras lesson is that a
  widely-quoted number from outside the system is not evidence about this system.
- **Whether the trainer's own workload leaves a window** — the ML lifecycle's
  duty cycle on that box has not been measured against a per-read spawn.
- **Whether any local candidate can read a dense DXtrade table at all.** Screen
  OCR on small VLMs is the weak axis, and the failure mode is a plausible wrong
  digit, not a refusal. P2 exists to find that out before anything is wired.
- **This document does not claim the screenshot path is the only site sending
  live data to a hosted model.** The M13 insights generator also calls hosted
  models and its inputs are trade data. Establishing the full set, with a
  denominator, is tracked on
  `BL-20260823-PROP-SCREENSHOT-SENDS-LIVE-ACCOUNT-DATA-TO-HOSTED-MODELS`.
