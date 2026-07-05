# M19 — Next-Direction Deep-Research Brief (handoff, 2026-07-05)

**Purpose.** This is the **spawn prompt for the next session.** M19's active offline
exploration is substantially complete; rather than pick the next lever by default, the
next session should run a **deep-research pass** that weighs the candidate directions
against current data reality and returns a *prioritized, evidence-backed recommendation*
for which line the following execution session should pick up.

Use the **`deep-research`** skill. This is a decision-support research task, not an
implementation task — no `src/`/`config/`/`ml/` change, Tier-1.

---

## Where M19 stands (grounding — cite, don't re-derive)

- **Price-representation frontier: closed, three-for-three negative.** T0.1 frozen TSFM
  embeddings (marginal, base-rate cliff at the 0.005 operating point), T1.1 deep TCN
  (clean negative vs the LightGBM regime head), T1.2 SSL "reads-everything" corpus encoder
  (clean negative, replicated across two corpus widths — daily-panel→intraday-vol
  target mismatch). Evidence docs under `docs/research/T0.1-*`, `T1.1-*`, `T1.2-*`.
- **The one durable win: T0.4 `fc` forecast features** — quantile-forecast `fc_*` cols as
  a **vol-regime classifier feature**. Positive + base-rate-robust under purged-CV; live at
  **shadow** across BTC+ETH, soaking toward the fc→advisory Tier-3 gate.
- **fc geometry extension (Phase 2): inconclusive.** Using the forecast to size SL/TP was
  tested offline (`docs/research/T0.4-fc-sltp-geometry-evidence-2026-07-05.md`) — the
  forward triple-barrier simulator failed its reality-calibration check (real-realized
  −0.68R vs fixed-resim −0.06R), so the apparent edge is an in-simulator artifact. Needs a
  live soak, not a backtest.
- **The binding constraint (the M19 thesis): labels, not compute.** ~350 real-money trades;
  MES label-blank historically; the free 1-OCPU trainer clears the daily cycle in <1h; GPU
  bursts proven at ~$0.04/run against a $10/mo cap (spent ~$0.08 lifetime). Compute is a
  distant second constraint.
- **Deferred, data-walled:** T1.3 cross-sectional net-R ranker (214 labelable order-packages,
  thin allocator decision space); T2.1 multi-task foundation encoder; T2.2 offline-RL sizing/exit.

## The candidate directions to weigh (from ROADMAP M19 "Next research directions")

- **D1 — live fc-geometry shadow-soak.** Build the faithful Phase-2 test: observe-only logger
  of fc-scaled SL/TP vs placed SL/TP per opening order (`exit_ladder_soak` shape). Cost: a
  Tier-1/2 build + weeks of soak. Payoff: the only honest answer to "does fc geometry help?".
- **D2 — break the label wall.** Meta-labeling over dense triple-barrier candidates + MES label
  backfill to grow usable labels; unlocks the decision heads and the deferred T1.3 ranker.
  Highest leverage per the thesis; hardest; M14-style.
- **D3 — task-matched corpus-embedding head.** Re-use the (sound) T1.2 encoder + corpus store on
  a head whose target lives on the corpus's own daily/cross-asset clock (daily direction/risk
  head, or the M18 ranker). Cheap, fast, offline A/B; may be another acceptable cheap negative.
- **D4 — mature fc → advisory.** Build the head-pinned money-gate walk-forward + powered
  fresh-mirror RG4 harness so the eventual fc→advisory Tier-3 promotion has real evidence.
  Soak-gated; prep, not new frontier.

## The deep-research question

> **Given the current data reality (label scarcity, the fc classifier win in soak, the
> representation frontier exhausted, ~$10/mo compute available but not the constraint),
> which of D1–D4 — or a direction not yet listed — is the highest-expected-value next line
> of ML research for this bot, and why?**

Sub-questions the research should resolve with evidence (mix external literature + the
repo's own data/history via the diag/trainer relays):

1. **Label-wall economics.** How much would meta-labeling / triple-barrier candidate density
   realistically move the usable-label count, and does the external evidence support meta-labeling
   lifting decision quality at our sample size? (Ground against our own S5–S8 M14 findings — the
   meta-label did NOT beat the majority baseline on real BTC trades.)
2. **fc-geometry expected value.** Is there external + internal evidence that forecast-vol-scaled
   exits beat fixed R:R exits *in live trading* (not simulation)? Is the soak build worth the weeks
   of latency before a read?
3. **Task-matched embeddings.** Does the literature support a daily macro/cross-asset SSL embedding
   lifting a daily-horizon head or a cross-sectional ranker (vs the intraday-vol head it failed)?
   Is our M18 ranker's label/decision space rich enough to test it?
4. **Sequencing.** Given only one execution track at a time, what is the right *order* — e.g. start
   D4 (cheap, matures the one real win) in parallel with a cheap D3 probe, defer D1's build until the
   fc soak proves out, gate D2 behind a label-count trigger? Return a concrete sequenced plan.
5. **Anything missing.** Is there a higher-EV direction none of D1–D4 name (e.g. a
   non-ML execution-quality lever, a data-acquisition play, a different label source)?

## Deliverable of the next session

A cited deep-research report (`docs/research/M19-next-direction-recommendation-<date>.md`) with:
a ranked recommendation across D1–D4(+), the evidence for each, a concrete **sequenced** plan for
the following execution session(s), and any new ml-review-backlog items the research surfaces.
Then update the ROADMAP M19 "Next research directions" block with the chosen priority order.

## Guardrails (carry into the next session)

- Tier-1 research only; no live-path/config/ml change. Everything proposed still graduates
  observe-only through `candidate → shadow → advisory`; order-influence is backtest-A/B-gated +
  operator-approved.
- GPU spend stays gated (`comms/gpu_spend_ledger.json`, $10/mo cap) — a research pass needs none.
- fc → advisory is the one live money gate and is **not ripe** (soak young, RG4 unpowered) —
  do not propose promotion without volatile-episode soak + a powered fresh-mirror RG4
  (`MB-20260705-FC-ADVISORY-READINESS`).
- Never put the running model's internal id string into any committed artifact — chat only.
