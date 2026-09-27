# CA-A09 — config, mandates, auto-land route, landing & permission controls

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> Code audit 2026-09-27, lane CA-A09 (session_01CTCL5m5svVvXSgGu9VGVpe), dispatched by manager
> session_01KAQRJxRbRwpYTkPgBjNVyQ. Findings (schema §5): [`CA-A09.findings.jsonl`](CA-A09.findings.jsonl).
> This lane only FINDS problems. It changed nothing in src/, config/, scripts/, workflows or tests.

**The question:** does the code in scope do what it says it does, and in particular can the one
ARMED auto-land route (MD-DEMOTE-S2-S1) fire only on its own clauses and never add risk?

**Short answer:**
- **It cannot add risk.** This held under every plant I tried.
- **It also cannot land.** The workflow opens its PR under a PAT owned by a person. Clause A7 on
  the PR requires `github-actions[bot]`. So the first real fire goes red, and the de-risk cut waits
  for a hand merge, even though the ping says it lands itself.
- **Separately, no mechanical control stops a session merging a held Tier-3 PR.**

## Findings by severity

| sev | id | one line | disposition |
|---|---|---|---|
| HIGH | autoland-pr-author-pat | r4-demotion-gate opens the PR with the `BRANCH_PROTECTION_TOKEN` PAT, so the author is `benbaichmankass` (8/8 automation PRs measured). A7 on the PR refuses that author, so the armed route never lands. | PI-20260927-GU9VGVPE-0001 |
| HIGH | hold-merge-unenforced | A `landing: hold` PR is green on all 3 required checks. `mcp__github` (merge included) has been pre-allowed since #13138. The merge hook is inert on web. Required reviews were null at the last measurement. | PI-20260927-GU9VGVPE-0002 |
| medium | resolver-demote-nan-and-n | `_demote_s2` FIREs on a NaN net and on n_closed=1. The A5 replay is weaker than the producer. | filed here |
| medium | list-accounts-empty-roster-collapse | `data_loaders.list_accounts` shows `strategies: []` accounts (ib_live, oanda_practice) as routing all 55 strategies. | filed here |
| low | gate-empties-to-null | Emptying a block-form roster writes `strategies:` null. A2 then refuses, so an emptying demotion never auto-lands. The runtime reads null as `[]`, which is safe. | filed here |
| low | roster-removal-asymmetry | CONFIRMED: the dry-run guard passes a roster cut (rc 0) and fails `execution: shadow` (rc 1). A cut is still Tier-3 by path and cannot self-land. | verified-non-issue |
| low | cut-cannot-add-risk | A2 reconstruction, exits covering open packages, no roster-length sizing, and all Stage-2 symbols declared. Latent case noted in the finding. | verified-non-issue |
| low | armed-prose-stale | The workflow header, checker docstring and mandates.yaml note still say "not armed", and one cites a test that no longer exists. | filed here |

## Coverage

**Behaviour checked against real inputs:**
- `pytest` over 12 files: 271 passed, 1 skipped. The files are test_mandate_autoland, test_r4_demotion_gate, test_mandate_resolver, test_check_dry_run_in_diff, the 3 automerge tests, the 3 merge-slot tests, test_pr_landing_tier2_approval and test_paper_portfolio_accounts.
- `check_mandate_autoland.py --self-test`: 24/24 controls held.
- Planted inputs:
  - a roster-cut diff and a shadow diff through check_dry_run_in_diff;
  - NaN and n=1 records through the resolver;
  - an emptying block-roster cut through remove_from_roster;
  - a PAT-author event payload through pr_author_login.
- Ran `list_accounts()` at HEAD.
- Branch protection read live through the unauthenticated GitHub API.
- PR authorship read live through the GitHub MCP.

**Read fully:**
- check_mandate_autoland.py (1–820 of 1215; the self-test body was run, not read)
- r4-demotion-gate.yml (246)
- .claude/settings.json (102)
- MD-DEMOTE-S2-S1 entry and header of mandates.yaml (1–130, 462–520 of 675)

**Read partially:**
- r4_demotion_gate.py (1–120, 228–288 of 408)
- mandate_resolver.py (246–262, 380–500, 673–692 of 761)
- check_pr_landing.py (421–441, 1180–1470 of 2216)
- check_dry_run_in_diff.py (1–60 of ~200)
- run_guards.py (the pr-landing-guard entry)
- claude-pr-automerge.yml (landing-gate comments only)
- data_loaders.py, main.py, units/accounts/__init__.py, coordinator.py (the roster semantics only)

**Not read:**
- pr-opener.yml (168)
- check_manager_scope.py (3163)
- the rest of claude-pr-automerge.yml (645)
- .github/merge-slots and pr-landing contents beyond one example
- most of the 37 config files. For those I checked only that each is read by some loader: `config/bybit_config_template.py` is referenced nowhere, and the study specs are CLI-passed.

## Could not settle

- **Branch protection `required_pull_request_reviews` today.** The unauthenticated endpoint does not
  expose it. To settle: dispatch `branch-protection-report.yml` and read its output.
- **Whether a real r4-demotion-gate FIRE goes red exactly as predicted.** There have been zero fires
  (`comms/mandate_firings/` is absent). To settle: the first fire's PR check-runs, or a
  `workflow_dispatch` dry run on a fixture.
- **Whether a GITHUB_TOKEN-opened PR would get its required checks run.** This is the alternative fix
  for the HIGH. It is tied to the open bot-PR CI item (pipeline `20260927T055747…-3769b623`).
