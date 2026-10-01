# Manager handoff — 2026-10-01 ~16:40Z

**From:** manager `session_01HYq6XtfesZ57VyaQrK6CL1` (Manager Session 2026-09-29; its context is very large, so cost per request is high).
**To:** the new manager session, which titles itself `Manager Session 2026-10-01`.
**Operator instruction, 2026-10-01, verbatim:** *"go ahead with the handoff now. you're not done when the handoff happens. You're done when you've verified that the new manager has taken on everything and has every all the information that they need in order to take on the work. And you've verified that all the items have changed to their ownership."*

The outgoing manager stays alive until it has verified the **TAKEOVER RECEIPT** in § 7. Until then, anything a lane sends the old session gets forwarded to you.

---

## 0. Read first
- `CLAUDE.md`
- `docs/CLAUDE-RULES-CANONICAL.md`
- `.claude/skills/manager/SKILL.md`: § "Start of session" and § "Every spawn carries provenance"
- the `in_flight` and `blocked` rows of `docs/claude/work/MANAGER-CHECKLIST.json`

You act with the operator's authority (§ "Lanes answer to the manager"). The repo is PUBLIC: never print secrets, account IDs or VM IPs, and never ask the operator to paste a token. Operator decisions go to the operator as AskUserQuestion popups, in your own session.

## 1. Operator directives in force (verbatim where quoted)

### 1.1 Going live and execution

**Go-live rule (~10:50Z):**
> "Not everything has to be 100% completely finished. The question is, do we have the capability to execute the trades? If the answer is yes, we should go live... if there are other bugs that need to be fixed, then we can continue working on it after it goes live... We should go live anyway, and we can monitor and keep working on it once it's already live."

Reviews are scoped to it. **BLOCK only on:**
- wrong-order risk (symbol, side, size or price, or a missing SL/TP);
- any click that could hit an order, position or instant-trade control (Bid/Ask cells, quick Buy/Sell, Positions/Orders rows, close buttons);
- private-data leaks into logs;
- breaking the live path, or skipping or delaying a live ticket.

Everything else is a non-blocking note.

**One-click is NOT a gate.** It only arms the instant-trade controls. Checks of it are informational, so don't spend resources on it.

**First trades (~12:20Z):**
> "let me know when each account goes live, and then make sure we monitor and thoroughly verify throughout the first trade on each (end to end pipeline verification)"

**Live while refining (~12:35Z):**
> "I don't think you need to double click the name. I think one click does the trick... whatever can be live should be live... We don't need to wait for an actual trade to come in to test the functionality... make sure that when a trade comes in, it'll actually place it and it won't get missed because we're still running some... secondary tests"

**$75 ticket risk: SETTLED. NEVER raise it again.** The operator was angry at being re-asked:
> "As i have told you already countless times - we are keeping the 75 ticket size along with the risk of breaching the account - this is within my defined acceptable risk, please don't keep asking me to reauthorize something that i have already made crystal clear ad naseum"

Both lanes were told this on #14947 (comment 5931783857). Wrong size, symbol, side or price, or a missing SL/TP, are still bugs.

**Lanes retry without per-run authorization:**
> "the lane should keep retrying until it succeeds - there's no reason for me to specifically authorize each test run"

### 1.2 Pace and model use

**Pace:** "Keep current pace" (popup). The standing target, on checklist row PACE-W40, is to end the week at 90–95% usage.

**Usage at the operator's 19:19 local screenshot (~16:19Z):**
- weekly limit 19%, about 22h into the week (it resets Wed 21:00 local, which is 18:00Z);
- Fable 8%.

**Projection:** about 0.9%/h would run out around Monday evening. The main driver is the old manager's huge context: `/usage` showed 69–80% of usage at >150k context.

Keep your own context lean. Hand off again before it grows large.

**Fable:**
> "it's about you figuring out which sessions will benefit most from it"

That is already the manager skill's rule (E11). Independent reviews run on Fable (`Agent` with `model: "fable"`), scoped to the go-live rule. Order-path code authoring stays on the main model.

## 2. Live accounts: state at handoff

### breakout_1: LIVE on SOLUSD and ETHUSD

- **Account:** $75/ticket; balance about 4724; DD floor 4700. Breach risk accepted (§ 1.1).
- **ETHUSD went live** with B5 #15143 (5b25185d), deploy observed at 12:29Z.

**Merged today, in order:**

| PR | sha | What it did |
|---|---|---|
| #15138 | 9f51c275 | symbol-switch review follow-up; single-click Symbol-cell opener; one-click made informational; tests defer to waiting live tickets |
| #15192 | 0e8d6e3d | regression fix for #15138 |
| #15151 | 88c2e866 | Tradeify switch-dry pre-opens the ticket |

**Why #15192 was needed:** after #15138 deployed, `symbol-switch-dry` ETHUSD (#15187) **failed to open a ticket**. It tried `['symbol_click', 'symbol_dblclick']`, and the last-resort watchlist-row double-click was skipped because the one-click read failed to be a positive OFF. #15192 skips the last resort only on a positive ON read, and records why in `last_resort`. In live attempt logs it shows as `opener`.

- **Merged, NOT yet confirmed deployed or observed.** No live ticket has arrived since 09-30 13:00Z, so nothing has been missed.

**ETH lane's next steps (told on #14947, comment 5935654967):**
1. Confirm the deploy.
2. `symbol-switch-dry` ETHUSD.
3. `symbol-switch-dry` SOLUSD.
4. `round-trip-dry` ETHUSD (never submits).
5. `round-trip-dry` SOLUSD.

**If the opener still misses, that is a live-path blocker: fix it immediately.**

**First-trade end-to-end watch:** the ETH lane owns it. It covers the first ETH **and** first SOL ticket, using the 10-stage checklist in #14947 comment 5931293180.

- **For SOL, read** `band_ok`/`band_wait` with its quote, then `place_bracket` or the NOT PLACED alert.
- **That closes** PI-20260930-KMYJ5XC7-0003 and -0010.
- **This also proves #14737:** the two SOL tickets of 9/30 failed with "form fields not found: ['price']".
- **Notify the operator** at the first trade and on any failure.

**The 24h no-ticket gap was a quiet market** (investigated ~14:20Z):
- 772 evals per prop leg, longest gap 224s;
- the base Bybit Donchian legs were silent too;
- in September there were 18 tickets on 8 of 30 days.

**Possible older drop:** filed as PI-20261001-01HYQ6XT-0001, due 10-03. Base legs dispatched on 9/17–9/21, but breakout_1 has no ticket row for those days.

### tradeify_1 ($10k eval): NOT live yet

Lane TRADEIFY-WIRE, reporting on #14910. Its next steps (comment 5935655964):
1. Confirm the #15151/#15192 deploy.
2. `symbol-switch-dry` ETH/SOL/XRP.
3. Measure per-symbol specs. The login check prints the specs of the active instrument.
4. `round-trip-dry`, proving Limit, both SL/TP toggles ON, Price units (not Pips) and side, all read back. The ticket keeps its prior state.
5. T5: one executor dry cycle.
6. Go-live PR **C #14673** to you. Review it on Fable under the go-live rule, merge it, confirm the deploy, then **notify the operator that tradeify_1 is live**.
7. First-trade end-to-end on #14910.

Tradeify symbols display with a slash (`ETH/USD`); canonical matching handles that. It has no instrument-info button.

**Pre-approval:** PROP-GOLIVE-PREAPPROVAL ("Approve both, as written") covers both go-lives under milestone gates. See the checklist row.

## 3. Lanes: session ids and where they report

Every lane reports to the manager either by a trigger with `persistent_session_id` = the manager's session id, or by PR comments. **Every lane must be told your session id**, otherwise its reports go to the old session.

| Row | Lane session | Reports on | State at handoff (what the old manager verified) |
|---|---|---|---|
| PROP-ETH (PROP-ETH-DOM) | session_01HS7ws2n8c57QMUquRobE9z | PR #14947 | ACTIVE. Steps in § 2. |
| TRADEIFY-WIRE | session_01BBHxEYiMMJtEhnCBhyhmk2 | PR #14910 | ACTIVE. Steps in § 2. |
| RESEARCH-W7 | session_0119TTEhEppYPRbD1Mx1rCzs | triggers | Was blocked on a decision since ~14:07Z. Decided 16:21Z: REGISTER BOTH. (1) a selection-sensitivity arm of RQ-20260930-701; (2) a VOL-axis feasibility check, then a unit. Each needs its rule registered before any run. Its context was ~73% full, so it may hand off (2). See PI-20261001-1MX1RCZS-0002. |
| BREAKOUT-ATTRITION | session_01KMYJ5xC7gtPjGVf6Nwywoq | — | CLOSED OUT 14:11Z, with zero tickets observed. Its first-SOL watch moved to PROP-ETH. Set the row to `landed_unproven`/done with that reason. |
| PROP-EXEC | session_01WDQADTgoiLiDKc2PNSPdNU | triggers | Not re-checked today. |
| PROP-TERM (blocked) | session_01KY4jjUa6FUhBRT3zfMyyi4 | triggers | Not re-checked today. |
| M20-EXITS | session_012sUFUwazYENjW2KZwVN6mg | triggers | Not re-checked today. |
| PROP-PLATFORMS | session_01LE7AmRWZNqGrbSoi5ocig8 | triggers | Not re-checked today. |
| PROP-DXTRADE-FIRMS | session_01Qq1du5HtYn9wnaWStCe9fh | triggers | Not re-checked today. |
| HYRO-STRATS (blocked) | session_011sppGNJr87nzGbLjf2zFxf | triggers | Not re-checked today. |
| DONCHIAN-PARITY | session_01KjACh47AwiaCPSgQPT6PQF | triggers | Reported closed earlier, but the row still reads `in_flight`, so it is STALE. Verify, then close it. |
| PAPER-RESET | session_01JjPRfAYQKaocTX3QnHBgnD | triggers | Not re-checked today. ALPACA-CLOSE-WEDGE is done. |
| M16-RESCOPE | session_01LF3eUyxahXxpH7bKnLRhe9 | triggers | Not re-checked today. |
| E74 | session_01KFmGepVA1gVroNbnBpxVC4 | triggers | Not re-checked today. |

The old manager owns these rows (its id is in their `lane`/`owner` notes). **Rewrite them to your session id:** CA, PACE-W40, PROP-GOLIVE-PREAPPROVAL, M20-EXITS (owner manager), PROP-ETH (owner manager), REGIME-SCOPE, RESEARCH-W7, E11, JC-CA-06, DECIDE-AUTO.

**Standing routines (NOT bound to the manager session, so no action needed):**
- `trig_011Gx9uYc4DPEMSRczXQoT58`: the research-queue check-in, every 4h (`cse_01ArbCXdk1EjqLvhX5Jm4rkg`).
- `trig_01LFo98oHRUBpoMA6H9ra2QP`: the lane supervisor, every 6h (`session_01R9WFrBK1sbBcpTYgBmvG1q`). It verifies itself against the checklist's top-level `supervisor_lane`; leave that field alone.
- `trig_01NDbwo7UviviYj2tYnMVVrk`: weekly research planning, Mon 06:57Z, fresh session. "The manager brings this to the operator."

**Pending one-shot triggers aimed at the old manager:** none, verified by `list_triggers` at handoff.

## 4. Mechanics that bit the old manager
- **Subscribe to the lane PRs.** On 2026-10-01 two Tier-2 lane PRs, #15192 (a LIVE regression fix) and #15151, waited 40–60 min on the manager. The lanes had reported only as PR comments, which a manager not subscribed to the PR never sees. **`subscribe_pr_activity` on #14947 and #14910 immediately**, plus any lane PR you are asked to merge.
- **Landing:**
  - Tier-2 lane PRs carry `.github/pr-landing/<slug>.json` and the merge-slot claim.
  - Before each merge, `touch /tmp/.claude-merge-claim-<your-cwd-session-uuid>-<PR>`.
  - Merge with the full 40-char `expectedHeadSha`; a short sha fails.
  - Mark the PR ready (`draft: false`) first.
  - A 405 "Base branch was modified" means re-check mergeability (`git merge-tree`) and retry.
- **Lane messages:**
  - Use `create_trigger` with `persistent_session_id` = the lane and `run_once_at` = `date -u -d '+2 min'`; a time in the past errors.
  - Also post the substance as a PR comment, so there is a record.
- **Checklist JSON:** write it with `json.dumps(d, indent=2)` (default `ensure_ascii`), to avoid unicode churn.
- **Pipeline items:**
  - Each is one file under `docs/claude/work/pipeline/`.
  - `origin.kind` must be one of `audit`, `review`, `session`, `deploy`, `research` or `operator`.
  - A `date` due needs `due_when.due_date`.
  - Validate with `python3 scripts/ops/pipeline.py --check`.
- **Classifier denials:** never route around one. Ask the operator by popup.
- **The manager does not author code, config or tests,** and does not resolve lane merge conflicts.

## 5. Errors the old manager made (reusable)
1. **Cut a lane's authorization to "ONE dispatch".** The operator overrode it: keep retrying.
2. **Offered the operator a Fable allocation menu.** That decision is the manager's own, so decide it by measurement.
3. **Re-asked the $75 risk.** That decision was settled.
4. **Missed lane PR comments for 40–60 min,** because it was not subscribed to the lane PRs (see § 4).
5. **Let its own context grow huge,** which drove the weekly usage burn.

## 6. Next steps, in priority order
1. **Take over every lane in § 3:** a trigger naming you as manager. Subscribe to #14947 and #14910. Rewrite the manager-owned checklist rows to your id. This comes first because lane reports otherwise go to the old session.
2. **breakout_1 live path:** #15192's deploy, then the four dry tests. Any opener miss is a blocker to fix at once.
3. **tradeify_1 to live:** T4/T5, then C #14673.
4. **RESEARCH-W7's two units.**
5. **Re-check the lanes marked "not re-checked today"** and close any that are done (DONCHIAN-PARITY, BREAKOUT-ATTRITION).
6. **Register update:** record ETH live (12:29Z), the $75 "settled" directive, the first-trade E2E directive, and today's merges.

## 7. TAKEOVER RECEIPT (the new manager posts this; the old manager verifies it)

Post it as a comment on the handoff PR, with one line per item:
- [ ] titled `Manager Session 2026-10-01`; your session id
- [ ] subscribed to #14947 and #14910
- [ ] one trigger id per lane in § 3, each naming you as manager (all 13 non-closed lanes)
- [ ] checklist rows owned by the old manager rewritten to your id, with the PR/sha that landed it on `main`
- [ ] BREAKOUT-ATTRITION and DONCHIAN-PARITY disposition (or "verifying")
- [ ] the open items in § 2 and § 6 acknowledged, each with what you will do next
- [ ] anything in this doc that was unclear or missing
