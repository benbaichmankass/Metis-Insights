# Sprint operator-onboarding — Colab key rotation, no-SSH operator flow

> **Sprint type:** Operator-driven feature sprint, autonomous Claude execution.
> **Owner:** Claude Code (autonomous, self-merging).
> **PM:** Ben.
> **Created:** 2026-05-01 (continued same-day from S-023). **Closed:** 2026-05-01.
> **Goal:** After S-023 surfaced exactly *which* env vars the bot was missing,
> give the operator a one-click way to push them to the VM without ever
> SSH'ing again. Plus all the bug fixes that surfaced when actually running
> the new flow end-to-end.

## Operator brief

> "These instructions are way too complicated for someone like me. There
> needs to be a solution that doesn't require to actually open the vm. The
> most i can do is a colab notebook. ... I should only need to run the
> notebook when i rotate keys."

## Outcome at a glance

| Goal | Status | Shipped in |
|---|---|---|
| Operator never SSHes to the VM for routine work | shipped | #248 |
| Step-by-step API key setup doc + fill-in template | shipped | #247 |
| Colab notebook for one-click rotation | shipped | #248 |
| `/set_keys` Telegram command returns the Colab link | shipped | #248 |
| SSH key as a file (drag-drop), not pasted Colab Secret | shipped | #249 |
| SSH key read from Drive (`My Drive/ICT_Bot_Secrets/`) | shipped | #250 |
| File-picker fallback when Drive lookup fails | shipped | #251 |
| Drive mount runs first, race condition fixed | shipped | #251 |
| Notebook writes `.env` (the file systemd actually reads) | shipped | #252 |
| Telegram Markdown stops eating underscores in env var names | shipped | #252 → #253 |
| Notebook restarts both trader AND telegram-bot | shipped | #253 |
| `/accounts_status` switched to HTML mode (correct underscore handling) | shipped | #253 |

## PRs merged

| # | Title |
|---|---|
| [#247](https://github.com/the-lizardking/ict-trading-bot/pull/247) | docs/operator: step-by-step API key setup + fill-in template |
| [#248](https://github.com/the-lizardking/ict-trading-bot/pull/248) | operator: Colab key-rotation notebook + `/set_keys` command |
| [#249](https://github.com/the-lizardking/ict-trading-bot/pull/249) | operator: SSH key as uploaded file, not Colab Secret |
| [#250](https://github.com/the-lizardking/ict-trading-bot/pull/250) | operator: notebook reads SSH key from Drive (preferred), falls back to /content |
| [#251](https://github.com/the-lizardking/ict-trading-bot/pull/251) | operator: fix Drive-mount race + automatic file-picker fallback |
| [#252](https://github.com/the-lizardking/ict-trading-bot/pull/252) | operator: notebook writes .env (systemd target) + escape underscores in /accounts_status |
| [#253](https://github.com/the-lizardking/ict-trading-bot/pull/253) | operator: notebook restarts telegram bot too + /accounts_status uses HTML mode |

7 PRs over the span of the operator's first end-to-end run. Each PR
shipped, the operator tried it, the next bug surfaced, the next PR fixed
it. Iterated until `/accounts_status` showed ✅ for every account.

## Final operator workflow

**One-time setup:**

1. SSH key in `My Drive/ICT_Bot_Secrets/` (named `ict-bot-ovm-private.key`, `vm_ssh_key`, or any standard SSH key name).
2. 8 Colab Secrets set: `BYBIT_API_KEY_1/2`, `BYBIT_API_SECRET_1/2`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `VM_SSH_HOST`, `VM_SSH_USER`.
3. VM has passwordless sudo for systemctl (one-line sudoers entry).

**Each rotation:**

1. `/set_keys` in Telegram → tap link.
2. Update the rotated Colab Secret (or replace the SSH key file in Drive).
3. **Runtime → Run all** in Colab.
4. `/accounts_status` to verify ✅.

No SSH, no terminal, no laptop courier.

## Lessons learned

1. **Iterating with the operator on real failures is the only way to find these bugs.** Each PR in this sprint fixed something the operator *actually hit*, not something I imagined. The "looks good in dev" → "still broken in their hands" gap was three full PRs wide (mismatched `.env` file, missing telegram-bot restart, broken Markdown escaping). Worth respecting going forward — ship the smallest plausible fix, let the operator try, then iterate.

2. **Telegram parse modes are landmines.** Three modes (Markdown / MarkdownV2 / HTML) with three different escape rules. Legacy Markdown silently ate `_`; backslash escapes don't work in legacy Markdown but DO in V2; HTML mode has the simplest reliable escape. **Rule** (now in `debug-memory.md`): any handler whose output contains user-visible identifiers uses HTML mode.

3. **systemd unit boundaries matter for env rotation.** The bot has 4+ separate processes that read `os.environ`. Restarting only the trader leaves `/accounts_status` (which lives in the telegram-bot process) reading stale env. The notebook restarts both. If a future session adds a new env-reading process, update `SERVICES_TO_RESTART` in the notebook.

4. **Convention drift across config files compounds.** `.env` vs `.env.live`, render script vs systemd vs main.py vs pipeline.py — four different files, four different paths, none reconciled. The notebook now writes both `.env` and `.env.live` defensively. Long-term fix is a deploy/ change to standardize on one file (out of scope for autonomous Claude per CLAUDE.md merging rules).

5. **Colab Drive mount can return early on stale tokens.** `drive.mount()` is supposed to block on auth, but in some sessions it returns with a stale token and an unmounted state. Always verify post-conditions explicitly: `os.path.exists("/content/drive/MyDrive")`.

6. **Always provide an interactive fallback.** When the Drive lookup miss left the operator with "drag the file in and re-run", they were stuck. Replacing that with `google.colab.files.upload()` (interactive widget) made it self-recovering.

## CLAUDE.md improvements proposed for next sprint

1. **Add a section on Telegram parse modes to `docs/claude/debug-memory.md`** — done in this PR.

2. **Add a "systemd units that read env" table to `docs/claude/repo-map.md`** — done in this PR.

3. **Loosen the deploy/ PM-review rule for `EnvironmentFile=` line additions.** The standardize-on-`.env.live` fix is a one-line systemd change but currently requires PM review. Argue: trivially reversible, no live-trading-logic risk, fixes a recurring drift class.

4. **Document the Colab notebook flow in `docs/claude/colab-workflows.md`.** That file currently focuses on Hugging Face + backtesting notebooks; the operator-facing rotation notebook deserves its own section.

## Verification (the operator already did this)

After PR #253, the operator ran the notebook end-to-end and reported:
- ✅ Notebook completed all cells
- ✅ Both `ict-trader-live.service` and `ict-telegram-bot.service` came back active
- ✅ `/accounts_status` shows real USDT balances per account
- ✅ Underscores render correctly (`BYBIT_API_KEY_1` not `BYBITAPIKEY1`)

The system is operational from the operator's seat without any SSH access.
