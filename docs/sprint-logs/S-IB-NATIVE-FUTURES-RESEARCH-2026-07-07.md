# Sprint Log: S-IB-NATIVE-FUTURES-RESEARCH-2026-07-07

## Date Range
2026-07-07 (single session, continued across context windows).

## Objective
Unblock honest intraday-futures backtesting with a roll-adjusted continuous
native-futures data pipeline, then use it to (1) resolve the `mgc_trend_1h`
shadow-vs-native contradiction, (2) find any real intraday edge on the IB
metals sleeve, and (3) size a candidate `alpaca_live` SPLG/IAUM promotion.
Secondary: harden the issue-driven-automation surface after a live external
attack, and reconcile the docs.

## Tier
Tier-1 throughout (tooling, research docs, CI/security workflow, doc fixes).
Every strategy/account/risk outcome is a **Tier-3 PROPOSAL only** — no
`config/*` or live-path change was made.

## Starting Context
The IB metals sleeve (`mgc_pullback_1d`/`mhg_pullback_1d` live, `mgc_trend_1h`
shadow) had only ever been validated on proxy series. The 2026-06-18
recombination sweep demoted `mgc_trend_1h` (GC=F 1h −15.5R, XAUUSD spot 1h
−50.7R) and flagged "unvalidated — no native ≤1h gold history" as the open
follow-up. The COMEX exchange fix (#5853) had just unblocked native MGC/MHG
pulls.

## Repo State Checked
`main` at each step; `config/strategies.yaml` + `config/accounts.yaml` read (not
changed); the prior research docs (recombination-sweep-2026-06-18, m15-phase0,
P5-orb-mes); the trainer per-contract shard `market_raw_percontract/MGC/1h/v001`.

## Files and Systems Inspected
`ml/datasets/adapters/ibkr_offvm.py`, `scripts/research/backtest_trend.py`,
`scripts/backtest_pullback.py`, `scripts/prop/account_compat_matrix.py`, the
trainer-vm-diag relay, the issue-driven-automation workflow guards (~30
workflows), `external-issue-alert.yml`, `docs/security/intrusion-surface-audit-2026-06-28.md`.

## Work Completed
- **Roll-adjusted continuous-futures tooling (#5893, merged):** `ml/datasets/continuous.py`
  (`build_continuous` panama/ratio/none), `scripts/research/build_continuous_contract.py`,
  `ml/datasets/percontract_pull.py`, an additive `iter_contract_bars` per-contract
  pull path, `pull-ibkr-history` `per_contract` knob, 15 tests, design doc.
- **Metals native backtest (#5893):** `mgc_pullback_1d` +25R and `mhg_pullback_1d`
  +30R **confirmed on native futures** (keep live).
- **Intraday shortlist (#5902, merged):** ran the deferred #1/#2/#5/#6/#7 matrix on
  native/continuous data. One strong find — **MGC pullback 1h +185R over ~3.3y,
  +0.56R expectancy, positive every calendar year**; trend/scalp cells weak; MES
  cells data-starved.
- **`mgc_trend_1h` aligned walk-forward (#5907, merged):** native MGC continuous
  is +196.2R full / **+77.0R on the 2024-01→2026-06 demote window (2023
  excluded)** vs GC=F −15.5R / spot −50.7R → **roll-artifact AND 2023-concentration
  both refuted; the cross-series conflict is structural** (instrument/vendor/session).
- **SPLG/IAUM `alpaca_live` promotion sizing (#5916, merged):** affordability floor
  × survival ceiling × edge → recommend `risk_pct ≈ 2.0%` both under a 5%-dd
  ruleset; `alpaca_paper` soak before live.
- **Security — external-comment-alert (#5910, merged):** during the session, five
  throwaway accounts (author_association NONE) dropped `*fix*.zip` attachments as
  comments on the trainer-diag relay issues (a targeted supply-chain lure — never
  downloaded). Audit confirmed the automation was already safe (no `issue_comment`
  trigger exists; every issue-triggered workflow is owner-guarded). Added
  `external-comment-alert.yml` — auto-hides (minimizes) + flags + Telegram-alerts
  any external comment. **Live-verified** minutes after merge.
- **Doc fixes (#5919 + this sweep):** corrected `CLAUDE.md`'s repo-visibility drift
  (public-by-choice since 2026-07-07, guarded by the collaborator interaction-limit
  + external-comment-alert, not privacy).

## Validation Performed
Native MGC continuous rebuilt (15,003 bars) and backtested per-window on the
trainer (`actions/runs/28893076178`); SPLG/IAUM sweep via `account_compat_matrix`
(`actions/runs/28895603207`). All PRs green (CI 14–16 checks each incl. the
canonical-doc-coherence guards). external-comment-alert live-verified on issue
#5911.

## Documentation Updated
`docs/research/{roll-adjusted-continuous-futures-DESIGN, ib-metals-native-backtest,
ib-intraday-strategy-survey, ib-intraday-shortlist-backtest, mgc-trend-1h-walkforward,
alpaca-live-splg-iaum-promotion-sizing}-2026-07-07.md`; `CLAUDE.md` (diag `ib_state`
endpoint earlier + visibility fix); `docs/security/intrusion-surface-audit-2026-06-28.md`
(§11 comment vector, this sweep); ROADMAP Last-Updated (this sweep).

## Contradictions or Drift Found
- `CLAUDE.md` said "private since 2026-07-06" while the repo is public — **fixed** (#5919).
- Dashboard `CLAUDE.md` "the report repo is private" aside is now stale — **fixed in a
  separate dashboard-repo PR**.
- `external-comment-alert.yml` referenced the intrusion-audit doc as its "full context"
  but that doc didn't cover the comment vector — **fixed** (§11 addendum, this sweep).

## Risks and Follow-Ups
- `mgc_pullback_1h` (+185R) needs a proper walk-forward + `account_compat_matrix`
  before it could be proposed as a live intraday variant — **Tier-3**.
- `mgc_trend_1h` stays shadow; the driver of the cross-series conflict needs a
  matched-session re-pull to isolate before any promotion — **Tier-3**.
- Deeper native-MES per-contract history would let MES pullback 1h / fvg 15m be judged.
- SPLG/IAUM live promotion is **Tier-3** (operator-gated); paper soak first.
- All logged to `performance-review-backlog.json` (this sweep).

## Deferred Items
Matched-session GC=F/spot re-pull; native-MES per-contract pull; the actual
promotions (all Tier-3, operator-gated).

## Next Recommended Sprint
`mgc_pullback_1h` walk-forward + `account_compat_matrix` (the strongest lead).

## Wrap-Up Check
`doc-freshness` run this session: canonical-doc-coherence PASS; decision-landing
completed (ROADMAP + this sprint log + performance-review-backlog); two doc-drift
bugs fixed.
