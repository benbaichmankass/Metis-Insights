# Sprint Log: S-SECURITY-TOKEN-COMPROMISE-2026-08-01

## Date Range
- Start: 2026-08-01 ~05:08 UTC (operator screenshot of the @bict_trading_bot casino rename)
- End: 2026-08-01 08:37 UTC (all resolution criteria verified)

## Objective
- Primary goal: contain and resolve the @bict_trading_bot token compromise — revoke/rotate both Telegram bot tokens, find and close the leak vector, verify end-to-end.
- Secondary goals: recover any collateral damage; harden every path the incident exposed so the class cannot recur.

## Tier
- Tier 1 (code/CI/docs fixes) + Tier 2 (secret propagation, service restarts — operator-acked in chat) + operator-only credential actions (BotFather revocations, secret pastes).
- Justification: security incident on the operator control plane of a live-money system.

## Starting Context
- Active roadmap items: post-audit W0–W4 maintenance plan (W0–W2 complete, W1 executing in parallel with this incident).
- Prior sprint reference: S-AUDIT-P1-TRAINER-HONESTY-2026-07-31, S-AUDIT-P2-ENFORCEMENT-2026-07-31.
- Known risks at start: attacker held TELEGRAM_BOT_TOKEN (proven by the API-side rename); scope of leak unknown.

## Repo State Checked
- Branch or commit reviewed: main at e6c60015 → 4c9decf6 across the incident (4 PRs merged during it).
- Deployment state reviewed: live VM sha via /api/diag/status at 05:57, 06:45, 06:57, 07:19, 08:27, 08:37Z.
- Canonical docs reviewed: CLAUDE.md, CLAUDE-RULES-CANONICAL (tiers, autonomy), system-actions.md, diag-relay.md.

## Files and Systems Inspected
- Code files inspected: src/bot/claude_bridge.py, src/bot/telegram_query_bot.py, src/bot/alert_manager.py, src/utils/log_redact.py, src/runtime/validation.py, scripts/secret_scan.py.
- Config files inspected: live VM .env state (indirectly, via service behaviour — values never read).
- Deployment files inspected: deploy/ict-telegram-bot.service, deploy/ict-claude-bridge.service.
- Docs inspected: docs/TELEGRAM-SPEC.md, docs/claude/telegram-pings.md, docs/claude/system-actions.md.
- Services or timers inspected: ict-trader-live, ict-telegram-bot, ict-claude-bridge, ict-liveness-watchdog (via /api/diag/services + journalctl relays).
- GitHub Actions workflows inspected: system-actions.yml (set-env path), sync-vm-secrets.yml, vm-diag-snapshot.yml, trainer-vm-diag.yml.

## Work Completed
- **Leak vector found and closed (two halves):** (1) committed httpx logs — artifacts/health/health_snapshot.txt carried full `bot<TOKEN>` getUpdates URLs; 2 real tokens in history since 2026-03-30, world-readable since the repo went public 07-07; redacted at HEAD in #8206. (2) The runtime source: claude_bridge.py was the one bot entrypoint without redaction wiring; fixed in #8217 along with the discovery that `install_redacting_filter` was logger-only (child-logger records skip ancestor logger filters — the filter never ran on the records it existed for; now attached to handlers) and a latent arg-stringification bug the handler attachment exposed (68 tests).
- **Scanner blind spot fixed** (#8206): the required secret-scan's telegram pattern began with `\b`, structurally unable to match a token after `bot` (letter→digit is no word boundary) — blind for ~4 months to the single most common leaked form. Fixed with `(?<!\d)`; URL-embedded regression test proves the pre-fix pattern fails it. The same `\b` blindspot was found and fixed in log_redact's bare-token regex.
- **Trader outage found and recovered (P0, 05:29→06:54Z, ~85 min):** the round-1 secret sync mirrored a then-empty TELEGRAM_BOT_TOKEN into .env and restarted the trader; validate_startup hard-requires the credential → crashloop to restart counter 389, with BOTH alert bots dead on the same empty values so no page fired. Root cause of the failed re-propagation was OUR tooling: system-actions set-env had no SECRET_TELEGRAM_BOT_TOKEN mapping and silently wrote empty regardless of the operator's paste. Fixed in #8217 (mapping added + set-env now refuses an empty resolved value before touching the VM). Recovery via sync-vm-secrets #8225 (correct mappings); trader verified ticking 06:56:50Z.
- **Rotation half-paste class hardened (#8228):** round 2/3 pasted only the secret half of the token (no bot-id prefix); PTB's InvalidToken exception echoed the pasted value into the crash traceback → journald → a public diag comment (#8226), burning that token. Shipped `assert_telegram_token_shape` (one owner of the id:secret rule, secret-free errors) — hard-fail in both bots (the echo risk), warning-only in the trader (deliberately NOT fatal so a notification-credential paste error can never crashloop the money loop again; caught pre-merge — the fatal version would have re-created the outage on next deploy).
- **Final rotation verified end-to-end (08:24–08:37Z):** operator revoked @bict (round 4 — the 08:27Z probe proved round 3 skipped the revocation, leaving the publicly-reconstructable token LIVE) + full-format pastes; sync #8241; both bots active; queued rotation pings delivered to the operator's phone; all three dead tokens confirmed 401 (original two + the burned round-2 replacement); trader healthy throughout.

## Validation Performed
- Tests run: 443 across log-redaction/secret-scan/validation/vwap/workflow-registry suites; URL-embedded + child-propagation + numeric-args + glued-token + shape regression cases all added and green.
- Dry-runs or staging checks: set-env empty-value guard verified by construction (workflow refuses before SSH); shape helper verified to raise without echoing.
- Manual code verification: full-history token census (git log -p over all commits); three independent getMe status probes from the trainer (never printing values).
- Gaps not yet verified: none for the incident's resolution criteria. journald retention on the VM still holds pre-fix token-URL lines for the *revoked* tokens (dead credentials; ages out).

## Documentation Updated
- Rules doc updates: none required (incident followed existing tiers).
- Roadmap updates: ledger row added alongside this log.
- Subsystem doc updates: docs/claude/system-actions.md gained the reconcile-netting-phantom-rows row (parallel W1 work); war-story comments at every fixed site.
- Backlogs: BL-20260801-TELEGRAM-BOT-TOKEN-COMPROMISE (resolved, full timeline in updates), BL-20260801-TELEGRAM-CRED-CRASHLOOPS-MONEY-LOOP (open Tier-3 design question), /system-review breach-sweep directive recorded on the incident row per operator instruction.

## Contradictions or Drift Found
- The redaction filter was decorative (logger-only install) — a guard that existed but never ran on its target records; same failure family as presence-only markers.
- set-env's secret-backed mapping list silently diverged from the set of secret-backed keys — a missing mapping wrote empty instead of failing (sub-class C: absent resolution read as a value).

## Risks and Follow-Ups
- Remaining technical risks: journald history on the VM contains dead-token URLs until rotation of the journal; git history contains the dead tokens (deliberate no-purge decision recorded — filter-repo would break referenced SHAs; all credentials in history are revoked).
- Remaining product decisions (Tier 3): BL-20260801-TELEGRAM-CRED-CRASHLOOPS-MONEY-LOOP — should validate_startup hard-require a Telegram credential at all (options a/b/c filed).
- Blockers: none.

## Deferred Items
- /system-review scoped breach sweep (directive on the incident row; operator running the review in a parallel session).
- Guard promotions (diag-unit-allowlist, claim-basis) after advisory soak — pre-incident plan item, unchanged.

## Next Recommended Sprint
- W3 ML follow-through (fc-pcv frozen swap overdue, SOL advisory-head re-gate + restore, MES never-trained manifests decision) — largely covered by the operator's parallel /system-review; then W4 hygiene.
