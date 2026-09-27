# Prop-account automation: options spec for Breakout and Apex (2026-09-27)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Lane **W6-P3** (prop), second deliverable. It was added by the manager's scope
addendum and then re-scoped by the operator, both on 2026-09-27. The companion
Apex evaluation is
[`prop-apex-vs-breakout-2026-09-27.md`](prop-apex-vs-breakout-2026-09-27.md).
Section E below summarises that document; it does not repeat it.

**Tier:** Tier-1 spec and research. **No code that places orders is in this
lane.** Any executor that clicks or POSTs an order is order-path work. It is
Tier-2 and goes in a held PR later. The read-only probe in § D.6 is a *design*.
The code to run even that probe also belongs in a later lane, because it needs
the operator's credentials on a VM.

**Marks** follow `docs/CLAUDE-RULES-CANONICAL.md` § MEASURED / INFERRED /
DECIDED. Every external fact carries a source row (§ Sources) with the URL and
the retrieval date. **"Could not look"** means the fetch was attempted and
blocked. It does not mean the fact is absent.

---

> ### ⚠️ CORRECTED 2026-09-27 (~10:00Z): platform
>
> **What the first version (merged in PR #13102) assumed:** breakout_1 runs on
> **DXtrade**. It built option C (the DXtrade REST API) and D.6 step 0 (a
> DXtrade REST login) on that assumption.
>
> **Why that was wrong to assume:** the assumption came from the repo's
> June design docs. Those docs *describe* a DXtrade bridge; they never
> *measured* which terminal the account is on. The operator believes
> breakout_1 is on the proprietary Breakout Terminal. Breakout has two
> platforms, and an account stays on the one it was bought on.
>
> **What this session established:** first-party material **cannot say which
> platform breakout_1 is on.** It does say how to tell: *"Breakout terminal
> accounts — access via your dashboard by clicking 'Open Terminal'. DXTrade
> terminal accounts — access via https://app.breakoutprop.com/"* (S3).
>
> ⚠️ The on-screen name does not settle it. Breakout's **DXtrade**
> white-label is *itself titled "Breakout Terminal"*. MEASURED 2026-09-27 by
> this session: `curl https://wss.breakoutprop.com/` returned 200 with
> `<title>Breakout Terminal</title>`, a `DXTFID` cookie,
> `"about.us.logo":"dxTrade"` and `"client.totp.authentication.issuer":"dxTrade5"`.
> So "we're on the Breakout terminal" and "we're on DXtrade" can both be true
> of the same screen.
>
> **✅ RESOLVED 2026-09-27 at about 10:17Z. Source: the operator, in chat,
> relayed by manager session `session_01Ljhs6sFAdWHdMDhJpL5aBP`.** Verbatim:
> *"I open and login through this link: https://app.breakoutprop.com/"*.
> Breakout's FAQ names that URL as the **DXTrade** login: *"DXTrade terminal
> accounts — access via https://app.breakoutprop.com/"* (S3). **breakout_1 is
> therefore a DXtrade account.** The DXtrade white-label's own title is
> "Breakout Terminal" (S26), which is why it read as the proprietary terminal.
> Nobody was wrong about what was on screen.
>
> **The version before the resolution specced both branches.** It makes identifying the
> platform D.6 **step 0**, which is one operator answer and needs no
> credentials. **Option C is downgraded**: see § C for the measured
> `RBAC: access denied` on the DXtrade REST path. Sections changed: § 0,
> § Which platform, § D.6, § D.8, § C, § B, § F, the ranking, the support
> question, the roster note and the operator decisions. Everything else
> (D.1–D.5, D.7, A, E) is platform-neutral and unchanged.

## Which platform is breakout_1 on? (added by the correction)

| evidence | what it shows | current or dated |
|---|---|---|
| Breakout FAQ 14215629 (S3, updated 2026-09-03) | Two terminals exist; an account keeps the one it was bought on. The **login path** identifies it: dashboard "Open Terminal" means the proprietary terminal, `app.breakoutprop.com` means DXtrade. | current, first-party |
| FAQ 14215018 / 14215682 / 14215706 (S4, S23, S24; all 2026-03-25) | No migration. The proprietary terminal opens from the dashboard. The two mobile apps are *"two separate and distinct applications"*. | first-party; the terminal existed by 2026-03-25 |
| X post from @breakoutprop (S25) | *"The Breakout terminal is now live in four new countries. Turkey. Great Britain. Australia. Canada. Global rollout coming soon."* The date **2026-04-06** is decoded from the post id, and the page itself **could not be read** (402). | first-party text via search title only; date inferred |
| breakout_1's purchase | about 2026-06-16 (PB-20260616-004, `config/accounts.yaml`). **Both terminals existed then**, so the date does not decide it. | repo, dated June |
| repo code and config (`breakout_routing.yaml` `dxtrade_symbol`, `breakout_executor.py`, `accounts.yaml` comments, `metacopier-bridge-DESIGN.md`) | All say DXTrade. **These are June design assumptions, never a measurement.** | dated 2026-06-16/17 |
| prop-report issues #12808, #13086, #10521 (2026-08-30 → 2026-09-27) | The operator's screenshots are transcribed as "the Breakout terminal": account `823528`, exec ids like `38033351:6827565`, "Position Effect Closing", margin level. **Given the finding above, "Breakout terminal" is ambiguous.** No transcribed field is tied to a platform by any first-party source we have. | current, not decisive |

**Verdict (RESOLVED 2026-09-27 ~10:17Z): DXtrade.** The operator logs in at
`https://app.breakoutprop.com/` (operator, chat). FAQ 14215629 (S3) maps that
URL to DXTrade accounts. Before that answer, the verdict read *"UNDETERMINED
… the operator's belief (proprietary terminal) is the working
hypothesis"*. The question that settled it is kept below for the record. One operator answer
settles it:

> *"Do you reach breakout_1 by clicking **Open Terminal** on the Breakout
> dashboard (portal.breakoutprop.com), or by logging in at
> **app.breakoutprop.com** with a separate username?"*

A secondary signal: if the terminal login page offers TOTP with an
authenticator app that labels the entry `dxTrade5`, it is DXtrade.

## 0. The answer in five lines

1. *(Resolved 2026-09-27 ~10:17Z: **breakout_1 is DXtrade**. The operator
   logs in at app.breakoutprop.com, and FAQ S3 maps that URL to DXTrade.
   D.6 step 0 is done.)* **D is ranked first, as the operator directed.** D is a bot that drives
   breakout_1's own web terminal with the operator's own credentials, at our
   normal cadence. It is **gated on feasibility and cost, not on the terms.**
   *(Corrected 2026-09-27.)* **Which terminal that is has not been
   established**, so D's probe **step 0 is now identifying the platform**,
   which takes one operator answer and no credentials (§ Which platform). The
   DXtrade and proprietary branches differ in login, reachability and
   feasibility (§ D.6).
2. **The proprietary Breakout Terminal is the durable target; DXtrade is
   not.** Breakout no longer sells DXtrade accounts. New accounts exist only
   on the proprietary terminal, and accounts cannot migrate (S3, S4). A D
   build for DXtrade dies with breakout_1. A build for the proprietary
   terminal survives a re-buy. **No API was found for either** (§ C).
3. **A (cut operator toil) is the fallback and is partly built already.** It
   has no ToS exposure. Screenshot parsing, the Telegram report grammar and the
   single `ingest_report` chokepoint all exist.
4. **B (a third-party copier) is ranked last for Breakout.** MetaCopier's
   current docs never name Breakout (S9). Copygram says the Breakout Terminal is
   "not a direct Copygram destination" (S11). The evaluation rules ban
   "sharing account access" (S1), which is the clause a copier holding our
   login collides with.
5. **E (Apex) is not an automation option at all.** Apex's current rules say
   *"No Automation or Algorithm Usage allowed"* on every account type, and the
   penalty is closure plus forfeiture. See the companion doc and § E.

---

## Re-verification of the 2026-06-16 findings (asked for by the addendum)

| 2026-06-16 finding (`breakout-compliance-2026-06-16.md`) | 2026-09-27 status | source |
|---|---|---|
| Breakout allows algo trading in principle | ⚠️ **NOT FOUND first-party.** A grep of all **94** FAQ articles (the 4 collections: Evaluation 40, Funded 41, Terminal 6, Non-Crypto 7) for `bot\|API\|automat\|algorith\|EA\|copier\|credential\|webhook\|script` found **no** statement that permits or forbids bots, APIs or copiers. Probe check: `automat` matched "automatically" in 2 articles, so the grep does find hits. The "bots allowed" claim now comes only from third-party blogs: a Velotrade review, where Velotrade is a *competitor*, and QuantVPS. | S1, S2, S12, S13 |
| No documented public API | **Still holds, as far as we could look.** `api.`, `docs.` and `developers.breakoutprop.com` all returned a Cloudflare 403 challenge, so **we could not look.** The "BreakoutProp API" appears only in a Copygram blog post (third-party, 2026-06-24). | S11, could-not-look list |
| Item 6 "third-party / off-the-shelf approach marketed to pass evaluation" | **Holds, reworded.** Evaluation: *"Using a third-party or off-the-shelf approach marketed specifically to pass evaluations."* Funded: *"…strategy marketed to achieve capital appreciation in a funded account."* | S1, S2 |
| Item 11 "account sharing incl. sharing credentials" | ⚠️ **CHANGED.** Evaluation now reads *"Sharing account access, or trading multiple accounts from the same household, device, or IP address."* The word "credentials" appears in none of the 94 articles. **The Funded list has no account-sharing line at all.** | S1, S2 |
| Item 10 copying third-party ideas | Holds: *"Executing trade ideas copied from a third party, including signals, communities…"* The strategies are our own, so this does not apply. | S1 |
| Copy trading across users banned | Holds: *"Copy trading across users: copying trades between different traders' accounts."* Self→self copying is not addressed. | S5 |
| Terminal = DXtrade | ⚠️ **CHANGED LOUDLY.** *"Breakout no longer offers new purchases on the DXTrade terminal — only the Breakout terminal is available for new evaluations and funded accounts… Existing DXTrade accounts remain fully eligible."* Existing accounts **cannot migrate.** *"Any future non-crypto asset listings will be added to the Breakout Terminal only."* The same prohibited practices apply on both terminals. | S3, S4, S6, S7 |
| MetaCopier supports Breakout via DXtrade | ⚠️ **Not confirmed.** MetaCopier supports DXtrade, but "Breakout" appears on none of its homepage or 5 docs pages. MetaCopier lists DXtrade prop firms that *block* API access (MyFundedFX, FTMO, Eightcap, Darwinex). Breakout is not on that list. That tells us nothing either way. | S8, S9 |
| Kraken | *"Kraken has acquired Breakout"*. Accounts are "not directly connected". The page says nothing about APIs or automation. | S14 |

INFERRED from the rows above: the June compliance analysis's "gray" rating for
a copier rested mostly on the word "credentials". That word is gone. The
narrower phrase "sharing account access" still applies to the *evaluation*
phase and to anything that hands our login to a third party, which is B. It
does not naturally read onto D or C, where the credentials never leave the
operator's own infrastructure. **The Terms of Use and the Trader Agreements
could not be read (Cloudflare).** Those are the most likely home of an
automation clause, so this inference is bounded by that gap.

---

## What exists today (the manual bridge), read from the code on `main`

- **Outbound:** `src/prop/breakout_executor.py::emit_prop_ticket` builds a
  per-account leg (entry, SL, TP, size from the ruleset) and emits a
  `prop_signal` ticket to FCM and the prop Telegram bot. It journals to
  `prop_tickets` (status `emitted`) and returns a `prop-manual-<uuid>` marker.
  No exchange position is created. Tickets are readable at
  `GET /api/bot/prop/tickets`.
- **Inbound: ONE chokepoint.** `src/prop/prop_report.py::ingest_report`, served
  as `POST /api/bot/prop/report`. Its docstring names *"any future automated
  executor"* as an intended caller. It accepts `fill` (`placed → open/filled →
  closed | skipped`), `amend` (SL/TP moved) and `account_status`
  (balance/equity).
- **Other inbound surfaces already feeding it:** the Telegram report grammar
  (`telegram_report_handler.py`, `close ETHUSD 2950 +80 tp`), screenshot vision
  parsing (`screenshot_parse.py`), and reconciliation
  (`prop_reconcile.match_fill_to_ticket`, `find_unacted_tickets`,
  `compute_rule_distance`).
- **Risk gate:** `prop_risk_gate.grade_account_ticket_risk` compares the
  ticket's risk with the live DD cushion. Its default mode is **`annotate`, not
  `enforce`** (`_DEFAULT_MODE`, `prop_risk_gate.py:115`). MEASURED by reading
  the file on this branch.
- **Rules to guard** (`config/prop_rulesets/breakout.yaml`, $5,000 account):
  daily loss 3% (**$150**), recalculated at **00:30 UTC** from balance and
  breached on intraday *equity*. Static DD 6% (**$300**), so the **floor is
  $4,700**. Neither rule raises on payout. The breach penalty is *"all positions
  auto-closed, account PERMANENTLY disabled."* Carried from that file's
  `[CONFIRMED — FAQ 2026-06-16]` marks and **not re-verified this session.**

Every option below must write through `ingest_report`. **A second ingestion
route is out of scope by construction** (operator re-scope, item 2).

---

## Option D — a bot drives breakout_1's web terminal (LEAD; platform per § Which platform)

### D.1 What it is

The operator decides (2026-09-27) to carry the ToS risk. A process under the
operator's mandate does the clicks the operator does today: it reads the
emitted ticket, places the bracket order on the operator's own DXtrade
account, confirms it, and reports back. Cadence is our existing trade
frequency. breakout_1 has **2** rostered legs, `trend_donchian_sol_prop` and
`trend_donchian_eth_prop`, both 1h (MEASURED: `config/accounts.yaml` and
`config/strategies.yaml` on this branch). That is a handful of tickets a day at
most. It is nowhere near high-capacity.

### D.2 ToS risk, as one line in the account's risk assessment

| clause (first-party, S1/S2) | applies to D? | plausible consequence |
|---|---|---|
| Eval: *"Sharing account access, or trading multiple accounts from the same household, device, or IP address"* | **Low.** The credentials stay on operator-controlled infrastructure. One account is traded from one IP (the VM's). ⚠️ The IP half *would* bite if we later ran **two evaluations** from the same VM. | termination at Breakout's discretion |
| *"Exploiting errors or latency in pricing or the platform"* | **No.** Entries are on 1h bar closes. | n/a |
| Funded: *"Using any trading or order-entry method expressly prohibited by a liquidity provider"* | **Unknown.** We could not read any liquidity-provider terms. | *"Violations are assessed at Breakout's discretion and can result in account termination"*; payout forfeiture is plausible (S2, S5) |
| An explicit "no automation / no bots" clause | **None found in 94 FAQ articles.** The Terms of Use and the Trader Agreements **could not be read.** | same as above |

**Honest size of the risk (INFERRED from the table):** no first-party clause
we could read prohibits it. The one route to a breach is a discretionary
judgement by Breakout, or a clause in the unread agreements. The worst case is
losing the account plus any un-withdrawn profit. The payout policy (withdraw
everything above start at every window, `breakout.yaml` `withdrawal_policy`)
already keeps un-withdrawn profit near zero. That cap on the downside belongs
in this row. It is the same order of loss as the static-DD breach we already
underwrite (the $45 eval, `breakout.yaml` `economics.account_fee_usd`,
`[CONFIRMED — plan card 2026-06-16]`, not re-verified).

### D.3 Architecture

- **Where it runs: a separate systemd unit on the live VM**
  (`ict-prop-executor.service`, a proposed name) with `MemoryMax=` and
  `CPUQuota=` caps. It must **not** run inside `ict-trader-live`. A browser
  that leaks memory must not be able to starve the trader. It talks to the
  system only over the local API: `GET /api/bot/prop/tickets` in and
  `POST /api/bot/prop/report` out. So it could equally run on the trainer VM
  or its own box. The live VM is preferred because it keeps **one egress IP**
  for the account (see D.2).
- **Headless Playwright Chromium, default configuration.** The live VM is
  Ampere arm64, and Playwright ships arm64 Chromium. No headed/Xvfb trick is
  used to look human. If the terminal refuses a default headless browser,
  that is a feasibility finding (see D.7). It is not a design task.
- **The transport is an interface.** One `TerminalTransport` protocol with the
  methods `login`, `read_account`, `read_positions`, `read_orders`,
  `place_bracket`, `modify_bracket` and `close`. It has two implementations:
  `DomTransport` (Playwright) and `DxRestTransport` (the documented DXtrade
  REST API, § C). The loop above it is identical, and the probe (D.6) decides
  which transport ships.
- **Loop, one cycle a minute plus one on each new ticket:**
  `read_account` → `read_positions`/`read_orders` → reconcile (D.4) → rule
  guard (D.5) → act on at most one pending ticket → re-read → report.

### D.4 Ticket consumption, order entry and reconciliation

1. **Consume.** Poll `GET /api/bot/prop/tickets?account_id=breakout_1&status=emitted`.
   Honour the ticket's `valid_until` and skip stale tickets with
   `ingest_report({kind: fill, status: skipped, reason: expired})`. **The
   ticket id is the idempotency key.** The executor writes it into the order's
   comment or label field if DXtrade exposes one (confirm in D.6 step 1).
   Otherwise it keeps a local `ticket_id → (symbol, side, qty, submitted_at)`
   intent ledger on disk, written **before** the click.
2. **Enter.** One bracket order with entry, SL and TP attached at entry. This
   keeps the manual bridge's hard invariant: *"no ticket is ever placed without
   an attached SL and TP"* (`breakout-poc-manual-bridge-DESIGN.md`). DXtrade
   sets TP/SL through **multiple requests, not atomically**
   (`metacopier-bridge-DESIGN.md` §2, citing MetaCopier's docs, 2026-06-16, not
   re-verified). So the post-submit re-read must confirm that **both
   protective legs rest**, not only the entry.
3. **Confirm by re-read, never by click success.** After submit, re-read the
   positions and orders. The outcomes are:
   - `placed` if an entry order rests;
   - `open`/`filled` if a position exists with both legs resting;
   - otherwise **UNCONFIRMED**, which goes to the failure table (D.7).
   Each result is reported through `ingest_report`.
4. **Reconcile every cycle.** Terminal positions and orders are compared with
   the journal's open `prop_fills`, using the existing
   `prop_position_identity` key (account + symbol + direction). Differences
   are handled as follows:
   - A **closed on terminal, open in journal** position is reported as
     `closed` with the terminal's exit price and P&L. This is the automatic
     version of today's report-back.
   - A **position on the terminal with no journal row** is an orphan. The
     executor alerts, halts new entries and does not touch the position.
   - An **SL/TP different from the journal** is reported as `amend`.
   - Each cycle also posts `account_status` (balance, equity, day P&L) from
     the terminal. That removes the staleness half of the 2026-08-25 incident
     (`prop_risk_gate.py` docstring: a 40.7 h old snapshot).

### D.5 Rule guards that run BEFORE any click (local, fail-closed)

These are computed from the **terminal's own balance and equity, read in the
same cycle.** They are never computed from a journal snapshot.

- **Daily loss.** `day_start_balance` is captured at the first cycle after
  00:30 UTC. Refuse if `equity − ticket_risk_usd ≤ day_start_balance × (1 −
  0.03)`, with a configurable safety margin (for example 20% of the limit).
- **Static DD.** Refuse if `equity − ticket_risk_usd ≤ 4,700 + margin`. The
  floor is `account_size × 0.94` from `breakout.yaml`.
- **Open risk.** Sum the stop distances of open positions (as in
  `compute_open_risk`) and include them in both checks.
- **Gate mode.** Call `grade_account_ticket_risk` in **`enforce`** mode for the
  executor, whatever the global annotate default is. Anything other than
  "fits", including *"could not look"*, means no click.
- **Structure.** No SL or TP means no click. A size that rounds to zero means
  no click. A symbol absent from `breakout_routing.yaml` means no click.

### D.6 Feasibility probe (smallest sequence that proves or kills D). CORRECTED 2026-09-27

*The first version's step 0 was a DXtrade REST login. It is replaced, because
the platform is not established and the DXtrade REST path is refused at the
gateway (§ C).*

| step | what | "works" means | Tier |
|---|---|---|---|
| **0: identify the platform (no credentials)** — ✅ **DONE 2026-09-27 ~10:17Z: DXtrade (`app.breakoutprop.com`)** | The operator answers the one question in § Which platform: dashboard "Open Terminal" or `app.breakoutprop.com`. | A definite answer, recorded on `PI-20260927-R6FQK6DS-0002`. | none (an operator answer) |
| **1: unauthenticated reachability from the VM** | Default headless Playwright Chromium, run on the live VM, loads **only the login page** of the confirmed platform. DXtrade: `app.breakoutprop.com` (the host S3 names). Proprietary: `portal.breakoutprop.com/sign-in`, then "Open Terminal" after login. No credentials are entered. | The login form renders with **no bot challenge.** ⚠️ From this sandbox, `app.`, `portal.` and `trade.breakoutprop.com` all return **403 Cloudflare challenge** to non-browser clients (MEASURED 2026-09-27 with curl). `wss.breakoutprop.com` (a DXtrade login page) returned **200 with no challenge**. Whether a real browser from the VM's IP passes is exactly what this step measures. **If it gets a challenge, D is infeasible** under the no-evasion boundary. | Tier-2 held PR (a new script run on the VM) |
| **2: read-only login** | Log in, read balance, equity, open positions and orders, and whether an order carries a label/comment field. Write **one** `account_status` via `POST /api/bot/prop/report` (or a dry print). **No order control is touched**; the probe has no code path that clicks one. | Login succeeds unattended. The values match the operator's screen. | Tier-2 held PR plus the operator's secrets |
| **3: session keep-alive** | Repeat step 2 every 5 min for 72 h. | Re-login works unattended, and 2FA does not need a human each time. Selector stability is measured. | same |
| **4: one minimum-size bracket, operator watching** | A **separate** held Tier-2 PR adds `place_bracket`. | Entry, SL and TP all rest, confirmed by re-read. The report lands. | Tier-2, operator OK |
| **5: soak** | Executor live on breakout_1's roster, with the manual bridge still emitting. | 14 days with zero unconfirmed submits, orphans or naked positions. | Tier-2 |

**Branch-specific feasibility, from what can be learned without logging in.**
*breakout_1 is the **DXtrade** column. The proprietary column is kept for a
future re-buy, since new accounts can only be proprietary (S3).*

| | DXtrade branch (`app.breakoutprop.com`) | Proprietary branch (dashboard → "Open Terminal") |
|---|---|---|
| login | **username + password, plus TOTP 2FA and backup codes.** Seen in the DXtrade login page's own markup at `wss.breakoutprop.com` (`Dx2FAForm`, `securityCode`, `backupCode`, TOTP issuer `dxTrade5`), MEASURED 2026-09-27. A TOTP seed can be stored as a secret, so unattended login is feasible if the account has TOTP on or off (**not an emailed code**). | **unknown.** The dashboard at `portal.breakoutprop.com` is behind a Cloudflare challenge. One Google Play user review (third-party, 2026-06-09) describes an **email number-match verification** at sign-in. If that happens on every login, it is the *"hard 2FA … needs a human"* infeasibility below. INFERRED from one review, not verified. |
| front door | the login page at `wss.` served with no challenge. `app.` returned a challenge to curl. | `portal.` returned a challenge to curl |
| UI | the dxTrade web app. Charts are canvas (dxcharts/TradingView strings seen). Whether the **order ticket** is DOM or canvas is unknown until step 2. | unknown; the stack is behind Cloudflare. A "built on Kraken infrastructure, TradingView charts" claim is an unread search summary |
| API fallback | the documented REST path is refused at the gateway (§ C) | none documented (0 hits in 94 FAQ articles, S1-series) |
| shelf life | dies with breakout_1 | survives a re-buy |

**Build effort (INFERRED, a planning figure, not a measurement):**
- Steps 1–3: about 2–3 lane-days, including the secret-sync plumbing.
- Step 4: about 3–5 lane-days for the order path, reconciliation, failure
  handling and tests.
- The soak is calendar time.

**What makes D infeasible** (each one is a finding to report, not a thing to
work around):
- a Cloudflare-style bot challenge or CAPTCHA at login from the VM. We
  *measured* this class on `breakoutprop.com` itself, where every fetch
  returned `cf-mitigated: challenge` (P2's `breakout-instruments-2026-09-27.md`
  and S-could-not-look). This session confirmed the same challenge on `app.`,
  `portal.` and `trade.breakoutprop.com` (curl, 2026-09-27). Whether a real
  browser from the VM passes is unknown until step 1;
- hard 2FA on every login, such as an emailed code or an app push with no
  "remember device";
- a canvas- or WebGL-rendered order ticket with no DOM or accessibility hooks,
  where the only way in is pixel clicking. That is too fragile for a money
  path;
- a terms change that names automation.

### D.7 Failure modes: detection and containment

| failure | detected by | contained by |
|---|---|---|
| **Selector drift** after a UI update | the step-level assertion "element not found / value unparseable" on the read path, which runs every cycle before any write | **halt new entries**, alert, and hand back to the manual bridge. Reads fail before writes do, because the cycle order is read → guard → write. |
| **Stale or expired session** | a login redirect or 401 on read | one re-login attempt. If it fails: halt and alert. **Never retry blindly in a loop.** |
| **Partial click** (entry placed, SL/TP not attached) | the post-submit re-read sees a position or order without both protective legs | immediately place the missing leg once from the ticket's levels, then re-read. If still missing: **close the position at market** and alert. The manual-bridge invariant says a naked position is never left standing. |
| **Double click / duplicate submit** | the intent ledger or order label shows 2 orders for one `ticket_id` | cancel the resting duplicate. If both filled: close the excess and alert. |
| **Timeout after submit** (did it go in?) | the submit returns no confirmation | **never resubmit.** Re-read by ticket label or by (symbol, side, qty, time window). Found means proceed; not found after N reads means mark `skipped` with reason `unconfirmed_submit` and alert. |
| **Disconnect with a position open** | the heartbeat misses | **no action needed for safety.** The broker-side bracket is the real-time protection and survives our disconnect (the manual-bridge design's core safety distinction). The first cycle on reconnect reconciles. |
| **Rule guard says no** | D.5 | ticket reported `skipped` with the guard's reason. No click. |
| **Journal/terminal disagreement** | D.4 reconcile | alert and halt new entries. Never "fix" the terminal to match the journal. |

**Kill switch:**
- `PROP_EXECUTOR_MODE=off|read_only|live` in the VM env, flipped by a
  system-action. `read_only` is the default until step 3 passes.
- `systemctl stop ict-prop-executor` stops it outright.
- Either one reverts to today's manual bridge with no other change, because
  the ticket emitter never stopped emitting.

### D.8 Credentials and 2FA

- **Resolved: breakout_1 is DXtrade, so the probe needs the DXtrade
  names** (2026-09-27 ~10:17Z): `BREAKOUT_DX_USERNAME`,
  `BREAKOUT_DX_PASSWORD`, and `BREAKOUT_DX_TOTP_SEED` **only if** the account
  has 2FA turned on. The per-branch list below is kept for a re-buy.
- **Secret names depend on the step-0 answer** (corrected 2026-09-27). They
  are **Actions secrets synced to the VM env** through the existing
  `sync-vm-secrets` route (the `credentials-and-vm-mutations` skill):
  - **DXtrade branch:** `BREAKOUT_DX_USERNAME`, `BREAKOUT_DX_PASSWORD`, and
    `BREAKOUT_DX_TOTP_SEED` only if 2FA is enabled on the account.
  - **Proprietary branch:** `BREAKOUT_PORTAL_EMAIL`, `BREAKOUT_PORTAL_PASSWORD`,
    and `BREAKOUT_PORTAL_TOTP_SEED` only if the dashboard offers
    authenticator-app 2FA. An emailed code on every login makes this branch
    infeasible; there is no secret to store for it.
- They never go into git, logs, screenshots or reports. The executor redacts
  them from Playwright traces, and **trace recording is off by default.**
- The operator originates the values. Everything else is ours.
- If the login uses TOTP, the TOTP seed is a secret of the same class. An
  emailed or pushed code that needs a human on every login makes D infeasible
  (D.6).

### D.9 What stays manual

Deciding whether to hold the account at all, withdrawals and payouts, re-buying
after a breach, and any ticket the guard skips (the operator may still place it
by hand, and it reports through the same grammar).

---

## Option C — a direct official API (DXtrade REST/WebSocket)

- **How it works:** the same loop as D with `DxRestTransport`. Devexperts
  documents a **REST API** (*"trading operations, account data, metrics, and
  market data"*), a **Push API** (websockets) and a **FIX API** (S10). REST auth
  is *"The user POSTs their username, domain, and password"*, which means the
  account's own platform credentials. Default rate limit: *"10 per second"*.
- **The gate:** *"Brokers and prop firms may decide to open up the APIs for
  external use, most do."* Also: *"your broker or prop firm controls the IPs
  permitted"* (S15).
- ⚠️ **CORRECTED 2026-09-27: C is downgraded to "blocked, pending support".**
  MEASURED by this session, unauthenticated, from the sandbox (not the VM's
  IP):
  - `GET https://wss.breakoutprop.com/dxsca-web/` returned `403 RBAC: access
    denied`.
  - `POST …/dxsca-web/login` with an empty body returned `403`. That is refused
    at the gateway *before* any credential check; a disabled-credentials
    case would be a 400/401.
  - The same host serves the DXtrade login page with a 200.

  INFERRED: Breakout's gateway does not expose the REST API on this host to
  arbitrary clients. It may be IP-allowlisted (S15), so the VM's IP *could*
  differ, but nothing suggests it.
- **C also does not apply at all if breakout_1 is on the proprietary
  terminal**, which has no documented API.
- **Credentials:** the same three as D. Nothing goes to a third party.
- **ToS risk:** the same as D or lower. It is the platform vendor's documented
  interface, not UI automation.
- **Failure modes:** the same table as D.7 minus selector drift. There are
  explicit error codes instead of DOM guesses. Token revocation (*"permission
  to use the REST API is revoked"*, S10) shows up as a 401 and halts.
- **Effort (INFERRED):** lower than D's order path, about 2–4 lane-days after
  the probe.
- **Shelf life:** DXtrade only, so breakout_1 only (S3, S4).

## Option A — stay manual, cut the toil (FALLBACK)

- **How it works:** the ticket is still placed by the operator, but less of
  the rest is manual:
  - **A1:** one-tap Telegram buttons on the ticket ("placed as shown" / "skip"),
    which post `ingest_report` with the ticket's own levels. This saves typing
    the `open …` line.
  - **A2:** screenshot → report is **already built** (`screenshot_parse.py`).
    Make it the default path for closes and status.
  - **A3:** `find_unacted_tickets` pushes a reminder only when a ticket is
    unacted past `valid_until`, replacing periodic status pings. The operator
    asked for that on 2026-09-21 (checklist row, "prop status request
    notifications even though we said we don't need that").
- **Credentials:** none leave our side. **ToS risk:** none.
- **Failure modes:** a missed fill (the operator never places it, which
  `find_unacted_tickets` detects); a mistyped level (`amend` corrects it). A
  naked position cannot come from our side because the operator places the
  bracket.
- **Effort (INFERRED):** A1 is about 1 lane-day. A2 and A3 are configuration
  and wiring on existing code.
- **What stays manual:** every click on the terminal.

## Option B — a third-party copier (MetaCopier or similar)

- **How it works:** the design is parked in `metacopier-bridge-DESIGN.md`
  (Mode A: master on Bybit, copier mirrors to the DXtrade slave).
- **Current facts:**
  - MetaCopier supports DXtrade but **does not name Breakout** (S8, S9).
  - DXtrade market data is *"disabled by default… contact the broker"* (S9).
  - Pricing: *"USD 0.27 per account/day, billed monthly"*, which is about
    $8/month per account, plus a $20 new-user credit (S16).
  - Copygram: the Breakout Terminal is *"not a direct Copygram destination"*
    (S11).
- **ToS risk: the highest of all options.** The copier holds our login, which
  collides with the evaluation's *"Sharing account access"* (S1). It is also a
  third-party tool widely *marketed* for passing prop evaluations (item 6). A
  copier adds a vendor-side failure surface (lag, divergence) that we cannot
  reconcile except through its webhooks.
- **Effort:** low for our side. It is vendor setup.
- **Verdict:** rank it third. Copiers reach DXtrade through its API, and that
  API is refused at Breakout's gateway (§ C, MEASURED 2026-09-27). On the
  proprietary terminal, Copygram says it is *"not a direct Copygram
  destination"* (S11). **B is effectively unavailable on either branch**
  unless support says otherwise.

## Option E — Apex (for comparison; details in the companion doc)

See `prop-apex-vs-breakout-2026-09-27.md` § 4. In one line: **Apex bans
automation on every account type, evaluations included.** Its Prohibited
Activities page (retrieved 2026-09-27) reads *"No Automation or Algorithm Usage
allowed: Rewards are intended to recognize human traders … not to reward
automated systems executing preprogrammed logic."* Its APIs make no
difference:
- Tradovate API access needs *"a LIVE account with more than $1000 in equity"*
  plus a paid subscription (api.tradovate.com).
- Rithmic's API needs conformance testing.

A compliant fully automated Apex route **does not exist**. The operator's
"carry the ToS risk" stance for Breakout D does **not** transfer:
- On Breakout, no clause we could read prohibits automation.
- On Apex, the ban is explicit.
- The legacy PA page states the penalty: *"immediate closure of your PA or
  Live account and the forfeiture of all funds."*

Option E is therefore **not an automation option.** It is ranked last.

## Option F — other routes found

- **F1: a proprietary Breakout Terminal API.** If Breakout publishes an API
  for its own terminal, it is the best route of all: official, and it
  survives a re-buy. Nothing first-party was found (0 hits for
  api/webhook/"API key" across all 94 FAQ articles; probe check: the same
  search finds "DXTrade"). The candidate hosts are Cloudflare-blocked. **Ask
  support** (question text below).
- **F2: move automation to another venue.** We checked Apex (§ E) and ruled
  it out: automation is banned there. No other futures prop was evaluated in
  this lane.

---

## Recommendation ranking

*(Re-ranked 2026-09-27 after the platform correction, and again at about
10:20Z once the platform was RESOLVED.)*

| rank | option | gate | why |
|---|---|---|---|
| **1** | **D on DXtrade** (breakout_1's confirmed platform) | D.6 step 0 ✅ done; next is steps 1–2, the read-only probe (Tier-2 held PR) | Operator-directed lead. No first-party clause read prohibits it. Credentials stay ours. The existing ticket, report and reconcile path is reused whole. The DXtrade branch has a known, storable login (user/pass/TOTP); the proprietary branch may be blocked by emailed-code login. |
| 2 | A (A1–A3) | none | Zero ToS risk. It is the fallback if D.6 kills D, and worth doing anyway. |
| 3 | C (DXtrade REST) | support answer; gateway currently refuses it | **Now applicable**, because breakout_1 is DXtrade. But the gateway returned MEASURED `403 RBAC: access denied` before auth. If support opens it, C becomes D's transport. |
| 4 | F1 (proprietary-terminal API) | support answer | Does not apply to breakout_1. It matters only for a future re-buy, which would be proprietary-only (S3). |
| 5 | B (copier) | needs C's API, or a copier supporting the proprietary terminal | Unavailable on either branch as far as we can see, and has the highest ToS exposure. |
| 6 | E (Apex) | none possible; the ban is explicit | Operator confirmed do-not-pursue 2026-09-27 (`PI-20260927-R6FQK6DS-0001`, closed). |

### The one question for Breakout support (the operator sends it; drafted here)

The operator chose not to gate D on the terms. The question is still worth
asking, because the answer is free. It settles the platform question, the
DXtrade sunset question, C and F1.

*(Rewritten 2026-09-27 for the platform correction, then updated at about
10:20Z once the operator confirmed DXtrade. Q1 is now a statement.)*

> Hello — I hold a Breakout account (account number \[823528 — operator to
> confirm\]). I trade my own systematic strategy: a few trades a day on hourly
> bars, always with a stop-loss and take-profit attached. I have some
> questions about platforms and automation.
> 1. My account is on **DXTrade** (I log in at app.breakoutprop.com).
> 2. **Is the DXTrade terminal being retired?** If so, on what date, and what
>    happens to existing DXTrade accounts?
> 3. Does the **Breakout terminal** offer an API for account holders (REST,
>    WebSocket, or API keys), or is one planned? If so, where is it
>    documented?
> 4. For DXTrade accounts, is the **DXTrade REST/Push API** enabled for
>    Breakout, and from which IPs?
> 5. If no API is available, **may I place my own orders through the web
>    terminal with a browser-automation script** running on my own server,
>    under my own login, with no third party involved?
>
> I want to stay fully within the rules. Please confirm in writing either way.

---

## Roster-expansion interplay

- **Faster, reliable fills (D or C) change *intraday* viability, not daily.**
  Under the manual bridge a 1h ticket can go stale before a human places it,
  which is why `valid_until` and the `find_unacted_tickets` reminders exist. An
  executor places within one cycle, so shorter-timeframe crypto legs that are
  already on `bybit_1` become routable to breakout_1 *if* they clear the prop
  EV bar. Candidates by name: the `*_pullback_2h`, `trend_donchian_*_4h` and
  `ict_scalp_*_15m` legs. INFERRED; each still needs its own prop-EV row (P2's
  `w6-crypto-candidates-2026-09-27.md` on branch `claude/w6-prop-candidates`).
- **Non-crypto legs (S&P500, XYZ100, SILVER, CL) exist only on the Breakout
  Terminal** (S7, and P2's `breakout-instruments-2026-09-27.md`). *(Corrected
  2026-09-27; resolved ~10:17Z.)* **breakout_1 is DXtrade, so these
  instruments are NOT tradable on it** (S7: *"available exclusively on the
  Breakout Terminal and are not available on DXTrade"*). A D build for
  breakout_1 cannot reach them. P2's non-crypto scoring
  (`mes_trend_long_1d`, `spy_*`, `qqq_*`, `slv_pullback_1d`, `uso_trend_1h`)
  is therefore only reachable on a **new** Breakout Terminal account or on
  Apex (futures).
- **A two-account setup** (keep DXtrade breakout_1 and add a
  proprietary-terminal account for non-crypto) runs into *"trading multiple accounts from
  the same… IP address"* during an **evaluation** (S1). Funded accounts may
  stack to $200k combined (S17). The automation host must not trade two
  *evaluations* from one IP.

---

## Operator decisions this spec raises (filed to the pipeline)

1. **`PI-20260927-R6FQK6DS-0002`: the read-only probe.** The operator
   pre-authorised it by popup at about 2026-09-27 09:42Z, *conditional on the
   platform being established first*. **The condition was met at about
   10:17Z (DXtrade).** The probe is built as a Tier-2 held PR. It needs the
   operator to add the DXtrade secrets named in § D.8.
2. **`PI-20260927-R6FQK6DS-0003`: send the Breakout support question** above.
3. The Apex verdict, `PI-20260927-R6FQK6DS-0001`, was **closed** by the operator (confirmed do-not-pursue, 2026-09-27).

---

## Sources

Retrieved 2026-09-27 by this lane's research agents with WebFetch/WebSearch.
"Page date" is the page's own lastUpdated where it shows one.

| id | what | URL | party | page date |
|---|---|---|---|---|
| S1 | Prohibited practices, Evaluation | https://intercom.help/breakoutprop/en/articles/11644090 | Breakout (1st) | 2026-09-03 |
| S2 | Prohibited practices, Funded | https://intercom.help/breakoutprop/en/articles/11647201 | Breakout (1st) | 2026-09-03 |
| S3 | DXTrade closed to new purchases | https://intercom.help/breakoutprop/en/articles/14215629 | Breakout (1st) | 2026-09-03 |
| S4 | No DXTrade → Breakout Terminal migration | https://intercom.help/breakoutprop/en/articles/14215018 | Breakout (1st) | 2026-03-25 |
| S5 | Hedging & copy trading rules | https://intercom.help/breakoutprop/en/articles/11644103 (and /11647220) | Breakout (1st) | 2026-09-03 |
| S6 | Same prohibited practices on both terminals | https://intercom.help/breakoutprop/en/articles/14211064 | Breakout (1st) | 2026-03-25 |
| S7 | Future non-crypto listings on the Breakout Terminal only | https://intercom.help/breakoutprop/en/articles/16188026 | Breakout (1st) | 2026-08-06 |
| S8 | MetaCopier supported platforms | https://metacopier.io/ | MetaCopier (1st for itself) | undated |
| S9 | MetaCopier specifications (prop firms blocking API; DXtrade market data off by default) | https://docs.metacopier.io/features/specifications | MetaCopier | undated |
| S10 | DXtrade developer index and REST API auth/limits | https://demo.dx.trade/developers/INDEX.md · https://demo.dx.trade/developers/DXtrade-REST-API.md | Devexperts (1st) | undated |
| S11 | "BreakoutProp API" claim; Breakout Terminal not a Copygram destination | https://copygram.app/blog/prop-firms/breakout-prop-firm-review-pass-challenge-copygram | 3rd party (vendor blog) | 2026-06-24 |
| S12 | "Bots are permitted…" (competitor review) | https://velotrade.com/blog/breakout-prop-review | 3rd party (competitor) | 2026-07-15 |
| S13 | "bots allowed… no API docs" | https://www.quantvps.com/blog/breakout-prop-firm-review | 3rd party | undated |
| S14 | Breakout x Kraken FAQ | https://support.kraken.com/articles/breakout-x-kraken-faq | Kraken (1st) | 2025-09-05 |
| S15 | DXtrade traders FAQ (broker controls IPs and API opening) | https://dx.trade/traders-faq/ | Devexperts (1st) | undated |
| S16 | MetaCopier billing | https://docs.metacopier.io/metacopier/billing | MetaCopier | undated |
| S17 | Multiple funded accounts up to $200k combined | https://intercom.help/breakoutprop/en/articles/11647215 | Breakout (1st) | 2026-09-03 |
| S18 | Apex Prohibited Activities ("No Automation or Algorithm Usage allowed") | https://apextraderfunding.com/help-center/getting-started/prohibited-activities/ | Apex (1st) | published 2026-09-24/25 (likely a site-wide re-publish) |
| S19 | Apex legacy PA compliance (automation penalty) | https://apextraderfunding.com/help-center/performance-accounts-pa/legacy-performance-account-pa-compliance/ | Apex (1st) | same |
| S20 | Tradovate API access requirements | https://api.tradovate.com/ | Tradovate (1st) | undated |
| S23 | Open Terminal from the dashboard | https://intercom.help/breakoutprop/en/articles/14215682 | Breakout (1st) | 2026-03-25 |
| S24 | Two separate mobile apps | https://intercom.help/breakoutprop/en/articles/14215706 | Breakout (1st) | 2026-03-25 |
| S25 | "The Breakout terminal is now live in four new countries…" (search-result title; page 402, date decoded from the post id) | https://x.com/breakoutprop/status/2041226299449250236 | Breakout (1st), could not open | 2026-04-06 (inferred) |
| S26 | Breakout's DXtrade login page, titled "Breakout Terminal" (dxTrade markup); `/dxsca-web/` returns `403 RBAC: access denied` | https://wss.breakoutprop.com/ | measured by this session with curl | 2026-09-27 |
| S27 | Google Play listing ("backed by Kraken"; user review describing email verification) | https://play.google.com/store/apps/details?id=com.breakoutprop.app | Breakout (1st) listing; the review is 3rd party | updated 2026-09-19 |

**Could not look** (Cloudflare 403 challenge, or a connection reset on
web.archive.org):
- `https://www.breakoutprop.com/` (and `/faq`, `/terms`, `/legal`, `/api`)
- `/article/breakout-terminal-launch/`
- `/article/algorithmic-prop-trading/`
- `/guides/breakout-terminal-walkthrough/`
- `https://app.breakoutprop.com/guest`
- `https://api.breakoutprop.com/`, `https://docs.breakoutprop.com/`,
  `https://developers.breakoutprop.com/`
- `https://metacopier.io/pricing` (client-rendered; the docs billing page was
  used instead)

**Not attempted:** Breakout's Terms of Use and its Evaluation and Funded Trader
Agreements. They are behind the same challenge, and **they are the most likely
home of an automation clause.**
