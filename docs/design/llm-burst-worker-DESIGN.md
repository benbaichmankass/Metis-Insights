# LLM burst worker — delegated coding/research subtasks

**Status:** pilot (Phase 1) · **Opened:** 2026-08-18 · **Tier:** 1 (no order path, no live data)

## Problem

A Claude session doing grunt work — reading 40 files to answer one question,
extracting a pattern across a subsystem — burns context and time on work a
small model could do. We wanted a "local LLM on cloud" the session could start
on demand, use, and stop, at zero cost.

> ⚠️ **THAT PHRASE IS MISLEADING AND IT MISLED THE OPERATOR** (corrected
> 2026-08-23). *"Local LLM on cloud"* describes the ergonomics we wanted — cheap,
> on-demand, disposable — and reads as a statement about WHERE INFERENCE HAPPENS,
> which it is not. **What shipped is external:** the worker calls Gemini (and
> optionally Cerebras) from a GitHub Actions runner. Nothing in this system runs
> a model locally; the local-GGUF backend below is Phase 3 and unbuilt. The
> operator read this as a local model and, on 2026-08-23, correctly objected when
> a flow was found sending live account data to a hosted provider. A label naming
> a property the implementation does not have is the same sub-class A defect this
> repo's guards exist to catch — here in our own design doc.

## The reframe

The lifecycle machinery everyone reaches for (start → health-check → idle
timeout → stop) exists only if you assume a **long-lived endpoint**. For bursty
delegation the unit of work is *one bounded subtask*, not *one live server* —
so a **GitHub Actions job IS the worker**. It starts on dispatch, does the task,
and is destroyed. There is nothing to stop, nothing to leak, and no idle cost.

## Options rejected, and why

| Option | Verdict |
|---|---|
| **Oracle VM as the worker** | No capacity. Oracle halved the Always Free Ampere allowance to 2 OCPU / 12 GB on 2026-06-15, and our pool is documented as fully allocated (live 2/12 + trainer 1/6 + gateway 1/6). It is also the money box — a CPU-saturating process there is the shape of both June 2026 wedges (`MB-20260609-001`, `BL-20260609-001`). At ~6–13 tok/s on a 3B model it would be slow *and* risky. |
| **Google Colab** | Google disallows SSH/remote-desktop from free runtimes and terminates free sessions used to serve a web endpoint. It also cannot be started headlessly — a human opens a browser tab every session, which defeats the entire goal. |
| **Persistent tunnelled endpoint from a runner** | Buys interactivity at the price of a public unauthenticated inference URL, tunnel flakiness, and a new idle-shutdown mechanism to get right. Revisit only if batch proves too coarse. |
| **Direct in-session API calls** | Measured 2026-08-18: the sandbox *can* reach Cerebras/Groq/Gemini, credentialed requests included. Genuinely smoother (seconds, not minutes) — but the key lives in Actions secrets by operator choice, so this path is not available today. Kept as a documented future option. |
| **Cerebras as the backend** | Rejected after live measurement, not on paper. The key authenticates and lists models, but chat completions return **HTTP 402 `payment_required`**. The widely-quoted "1M tokens/day free, no card" came from third-party blogs, not Cerebras docs. Still selectable via the `backend` input if billing is ever enabled. |

## Architecture

```
Claude ──actions_run_trigger(llm-delegate.yml)──▶ runner boots
                                                      │
                          scope_guard.py  ◀───────────┤  fail closed on any
                          (public code + docs only)   │  out-of-scope path
                                                      │
                          delegate.py ──▶ Gemini 3.6 Flash
                                                      │
                          result envelope ────────────┘
                                                      │
Claude ◀── get_job_logs (run log)  +  artifact for large outputs
```

Readiness is not checked because there is nothing to check — a running job is a
ready job. Teardown is `timeout-minutes`, and it is unforgeable.

## The two contracts that matter

**1. Scope is enforced, not conventional.** `scripts/llm/scope_guard.py` is
default-deny: a path must match an ALLOW rule *and* no DENY rule. One denied
path refuses the **whole batch** — sending 9 of 10 files is how a scope guard
becomes decorative.

*"Already public" is deliberately not the test.* `comms/` is committed and holds
system reports with full per-trade PnL dossiers; `config/` describes account
topology. Both are public, both are outside the authorised scope, both are
denied.

**2. The result envelope is three-state.** `completed` / `failed` /
`not_attempted`, and the two non-completed states must carry a reason.

An empty `output` under a bare success reads as *"the model found nothing"* when
the truth is *"we never asked"* — the unasserted-denominator failure this repo
has been bitten by before. So: an empty completion is a **failure**, a quota 429
is a **loud failure**, and a scope refusal is `not_attempted`, never an empty
success. A scope refusal exits **0** — it is a correct outcome, not a broken
workflow.

## Backend

**Gemini 3.6 Flash**, via Google's OpenAI-compatibility shim
(`/v1beta/openai`), so the client stays OpenAI-shaped with no adapter.
`GEMINI_API_KEY` was already in use by this repo's course-generation
workflows — so it is *proven* working and *proven* free-tier on this account,
rather than assumed from a blog. That distinction cost three failed runs to
learn and is the reason `mode=models` exists: **ask the backend what it
serves; never ship a model id from memory.** Two were shipped from memory
anyway (`llama3.1-8b`, `gemini-2.5-flash`), and both failed.

## Cost

$0. Public-repo standard runners are unmetered, and Gemini's free tier covers
the observed usage (~5.7k total tokens per review-sized task).

## Did it pay? — the first real result

The pilot's own success criterion was whether a delegated result is worth more
than the cost of checking it. Measured once, on `guard-review-006`
(issue #9944): the worker was asked to find paths `scope_guard.py` would ALLOW
but that fall outside the authorised scope. It returned six, of which **five
were valid defects** — bare repo-wide extension globs (`*.txt`, `*.toml`,
`*.ini`, `*.md`) and a `webapp/src/*` wildcard admitting `accounts.json`. The
allowlist is now root-scoped and each finding is a regression test.

n=1, so this is an existence proof, not a hit rate. But it is the right shape:
a bounded question, a checkable answer, and a real fix landed.

## Open / next

- **Unproven premise:** whether delegation *pays*. If verifying a small model's
  output costs more than doing the work directly, elegant plumbing is
  irrelevant. Phase 1 exists to measure that on real subtasks.
- Phase 2: more task types, driven by what Phase 1 shows.
- Phase 3: local GGUF backend (cached in the 10 GB Actions cache) if a privacy
  driver appears — measured against the hosted backend before being preferred.
  ⚠️ **THE PRIVACY DRIVER HAS APPEARED — 2026-08-23, operator-stated.** The
  condition this phase was gated on is now met, and the deferral rationale
  recorded elsewhere ("no privacy driver exists") was never the operator's
  position: it was OURS, asserted about their requirements without asking them.
  See `BL-20260823-PROP-SCREENSHOT-SENDS-LIVE-ACCOUNT-DATA-TO-HOSTED-MODELS`.
