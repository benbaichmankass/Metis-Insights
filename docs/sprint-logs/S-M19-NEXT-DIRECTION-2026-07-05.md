# Sprint Log: S-M19-NEXT-DIRECTION-2026-07-05

## Date Range
2026-07-05 (single research session).

## Objective
Run the M19 next-direction deep-research brief: weigh the four candidate
directions (D1 live fc-geometry shadow-soak · D2 break the label wall · D3
task-matched corpus-embedding head · D4 mature fc→advisory) against the
current data reality, answer the brief's five sub-questions with evidence
(external literature + our own data via the diag relays), and land a ranked,
sequenced recommendation for the next M19 execution line.

## Tier
Tier 1 throughout — docs + backlog only. No `src/`, `config/`, `ml/`, or
live-path change; no GPU spend ($0); no promotion proposed (explicitly out of
scope per the operator constraint).

## Starting Context
Representation frontier closed 3-for-3 (T0.1 marginal / T1.1 TCN negative /
T1.2 SSL negative); fc head the one durable win, at shadow on BTC+ETH; the
07-05 fc→SL/TP offline backtest INCONCLUSIVE (simulator failed its
reality-calibration anchor by ~0.6R); RG4 first look unpowered
(ANTI_PREDICTIVE watch-flag on 48 stale-mirror rows). **Race found + resolved
at merge time:** the brief file, the ROADMAP "Next research directions" block,
and `MB-20260705-FC-ADVISORY-READINESS` that the directive referenced were
absent from `main` when this session branched (@3b8437c) — they landed in
#5593 (@60f6faf, ~11:55 UTC) minutes later, while the research ran. This
session initially reconstructed them; at merge time the reconstruction was
dropped in favour of the authentic committed versions and this session's
additions (the ranking, the powered-gate numbers, the censoring requirement)
were folded on top.

## Repo State Checked
`main` @ 3b8437c (fetched; local branch `claude/m19-next-direction-research-zl2544`
clean on top of it).

## Files and Systems Inspected
ROADMAP M19 block (T0.1–T2.2 rows); `T0.4-fc-sltp-geometry-evidence-2026-07-05`;
`T1.2-ssl-encoder-AB-evidence-2026-07-04`; `ai-model-strategy-roadmap-2026-07-01`
(label-wall/gaps sections); sprint logs `S-M19-FC-GRADUATION-PROGRAM-2026-07-04`,
`S-M19-T1.1-DEEP-SEQUENCE-2026-07-02`, `S-M19-FC-SHADOW-T12-P1-2026-07-03`;
ml-review backlog. **Live pulls (diag relay):** #5610 BTC-fc shadow stats (199
preds 07-03→07-05), #5611 ETH-fc (74 preds since 07-04), #5613 db_info (3,179
trades / 2,756 order-packages). Direct VM egress confirmed firewalled from
this session (relay used, as designed).

## Work Completed
- **Deep-research run** (harness: 5 search angles → 22 sources → 103 extracted
  claims → 3-vote adversarial verification; 21 confirmed / 1 killed; the final
  verification batch + synthesis agent hit a session rate limit — synthesis
  completed in the main session, truncated-batch claims marked as
  quote-verified-only in the report).
- **Report:** [`docs/research/M19-next-direction-recommendation-2026-07-05.md`](../research/M19-next-direction-recommendation-2026-07-05.md)
  — answers the five sub-questions with confidence-annotated citations and
  ranks **D4 ▸ D1 ▸ D2 ▸ D3** (clocks first: D4's soak already runs, D1's
  clock must be built; D2 offline between clock reads; D3 dormant on trigger).
  Key derived numbers: powered RG4 needs ≥40–50 labeled volatile bars/symbol
  across ≥5 distinct episodes (~10–14 more soak days at ~4.4 positives/day/
  symbol); D1 soak must be censoring-aware (counterfactual exits only
  partially identified); D2 spike A = real+paper pooled labels (~2,700+ rows)
  with an `account_class` domain flag, real-money slice held for evaluation.
- **Brief:** the authentic handoff brief from #5593 is kept verbatim (this
  session's initial reconstruction was dropped at merge time in its favour).
- **ROADMAP M19:** new "Next research directions (2026-07-05)" block with the
  D1–D4 tier/gate/constraint table + the chosen priority order.
- **ml-review backlog:** opened `MB-20260705-META-LABEL-WALL` (D2 spike A);
  folded the powered evidence standard + fresh soak numbers into #5593's
  `MB-20260705-FC-ADVISORY-READINESS`; appended evidence-log updates to
  `MB-20260704-T12-SSL-NEGATIVE` (spectral-overlap pre-check on the D3
  trigger) and `MB-20260705-FC-SLTP-GEOMETRY` (D1 censoring design
  requirement).

## Validation Performed
- Live soak/journal numbers pulled fresh over the relay (not reused from the
  07-04 snapshot) — #5610/#5611/#5613 outputs quoted in the report.
- Backlog JSON validated (`json.load` clean; 65 items post-merge, no new dup ids).
- Every external claim in the report carries its verification status
  ([✓3-0]/[✓2-1]/[◐]) — nothing unverified is presented as verified.

## Documentation Updated
- `docs/research/M19-next-direction-recommendation-2026-07-05.md` (new)
- `ROADMAP.md` (M19 "Next research directions" block)
- `docs/claude/ml-review-backlog.json` (+1 item, 3 evidence-log updates)
- This log.

## Contradictions or Drift Found
A same-day merge race: the drafting session's handoff (#5593 — the brief, the
ROADMAP registration block, `MB-20260705-FC-ADVISORY-READINESS`) landed on
`main` minutes after this session branched, so this session first saw the
artifacts as missing and reconstructed them. Resolved at merge time by keeping
the authentic committed versions and folding this session's additions on top
(union on the backlog; unified ROADMAP block). Separately noted, not fixed
(pre-existing on main, both entries resolved): duplicate backlog id
`MB-20260609-001` appears twice in the ml-review backlog.

## Risks and Follow-Ups
- The deep-research verification of ~4 late claims (cross-sectional-ranking
  horizon detail, contrastive-asset-embedding numbers) was truncated by the
  rate limit — they are marked [◐]/unverified in the report and none is
  load-bearing for the ranking (D3 is dormant regardless).
- D4's calendar depends on the market supplying volatile episodes; the
  ~mid-July estimate is a floor, not a commitment.
- D1's `execute.py` wiring touch is Tier-2 — needs one operator OK at build
  time.

## Deferred Items
- The D1 build itself, the D2 spike A run, the D4 mirror-freshness fix +
  powered RG4 — all next-session execution items per the ranked plan.

## Next Recommended Sprint
Per the ranking: (a) fix the trainer shadow-log mirror freshness + script the
powered RG4 (D4a, small); (b) build the fc-geometry shadow-soak logger with
the censored flag (D1). Both start clocks; D2 spike A fills the next research
slot after.

## Wrap-Up Check
- [x] Objective met (five sub-questions answered with cited evidence; ranked, sequenced recommendation delivered).
- [x] Verified reality reported (fresh diag pulls; verification statuses honest; the missing-brief discrepancy surfaced, not papered over).
- [x] No live-path / config / ml change; $0 GPU spend; no promotion proposed.
- [x] Findings landed durably (report + brief + ROADMAP + backlog + this log).
