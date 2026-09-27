# Prop-account automation: options spec for Breakout and Apex (2026-09-27)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Lane **W6-P3** (prop) wrote the first two versions. Lane **W6 breakout-dx-build**
(branch `claude/w6-breakout-dx-build`) re-scoped it on 2026-09-27 to the
operator's ~10:52Z direction and added the platform-adapter boundary (§ 2).
The companion Apex evaluation is
[`prop-apex-vs-breakout-2026-09-27.md`](prop-apex-vs-breakout-2026-09-27.md).

**Marks** follow `docs/CLAUDE-RULES-CANONICAL.md` § MEASURED / INFERRED /
DECIDED. Every external fact carries a source row (§ Sources).

> ### Re-scope, 2026-09-27 ~10:52Z (operator, relayed by manager `session_01Ljhs6sFAdWHdMDhJpL5aBP`)
>
> Verbatim: *"We're assuming that it is ... If we end up breaching the account,
> then they'll close the account."* Earlier, ~08:52Z: *"we shouldn't gate that
> on the terms explicitly allowing it, but rather on feasibility/cost while the
> 'breach' possibility is within the regular account breach risk assessment
> ... just regular trading where the clicks are being placed by a bot under my
> mandate instead of me clicking directly."*
>
> **What this changed in this document (DECIDED):**
> - The drafted **Breakout support question is dropped.** It is not sent.
>   Pipeline item `PI-20260927-R6FQK6DS-0003` is closed with that reason.
> - **Option C (a DXtrade REST/API transport) is removed.** The operator does
>   not want an API route pursued. (For the record only: an unauthenticated
>   `GET https://wss.breakoutprop.com/dxsca-web/` returned `403 RBAC: access
>   denied`, MEASURED 2026-09-27, S26. Nothing below depends on it.)
> - **Every "is it allowed" and compliance-gating passage is removed.** The
>   terms-of-service exposure is carried as **one accepted line** in the
>   account risk assessment (§ 1). The build is gated on **feasibility and
>   cost only.**
>
> **What stays true from earlier versions:** breakout_1 is a **DXtrade**
> account. The operator logs in at `https://app.breakoutprop.com/` (operator,
> chat, ~10:17Z). Breakout FAQ 14215629 (S3) maps that URL to DXTrade accounts,
> and the DXtrade white-label is itself titled **"Breakout Terminal"** (S26),
> which is why the name alone never settled it. Login is **username + password,
> no 2FA** (operator, ~10:37Z). A 2FA or CAPTCHA screen is a **feasibility
> finding**: the bot stops and reports it.

---

## 0. The answer in five lines

1. **Build D: a bot under the operator's mandate places breakout_1's clicks**
   on its own web terminal, with the operator's own username and password, at
   our normal trade cadence. It is gated on feasibility and cost only.
2. **We trade on DXtrade now.** breakout_1 is a DXtrade account and cannot
   migrate (S3, S4).
3. **We may have to move to Breakout's proprietary terminal later** (dashboard
   → "Open Terminal"): Breakout sells no new DXtrade accounts, so any re-buy
   lands on the proprietary terminal (S3), and non-crypto instruments exist
   only there (S7). So the build sits behind a **platform adapter** (§ 2):
   one interface, a `dxtrade` adapter built now, and a `breakout_terminal`
   adapter that is scoped only.
4. **Slice 1 (this PR) is read-only:** a login check that logs in, reads
   balance, positions and working orders, prints them, and touches no order
   control. Order placement is the next slice, after the login check is
   observed working on the VM.
5. **A (cut operator toil on the manual bridge) stays the fallback** and the
   kill-switch target. It is partly built already.

---

## 1. Account risk assessment for breakout_1 (the one place ToS risk lives)

| risk | size | how it is held |
|---|---|---|
| Daily loss 3% of the day-start balance | **$150** on a $5,000 account, recalculated at 00:30 UTC, breached on intraday equity (`config/prop_rulesets/breakout.yaml`) | local rule guard before any click (§ 3.3), plus the broker-side bracket on every position |
| Static drawdown 6% | floor **$4,700**. Balance was **~$4,724** on 2026-09-27 (operator), so the cushion is about **$24** | same guard; at this cushion almost any new ticket is refused, which is the correct outcome |
| Breach penalty | all positions auto-closed, account permanently disabled (`breakout.yaml`, `[CONFIRMED — FAQ 2026-06-16]`, not re-verified here) | withdraw-above-start payout policy keeps un-withdrawn profit near zero |
| **Terms of service: Breakout decides the automation breaches its terms** | **the same loss as a drawdown breach: the account is closed.** Accepted by the operator, 2026-09-27: *"If we end up breaching the account, then they'll close the account."* | **no gate.** No anti-detection of any kind (no stealth plugins, fingerprint spoofing, fake jitter): the bot is a plain default headless browser doing what the operator does |

That fourth row is the only terms-of-service content in this document.

---

## 2. The platform-adapter boundary (DECIDED 2026-09-27)

Everything that is the same whichever terminal we drive sits **above** the
adapter. Everything that knows a URL, a selector or a page layout sits
**below** it. Switching terminal means writing one adapter and flipping one
config value; nothing above changes.

```
          ┌───────────────────────────── shared, platform-neutral ─────────────────────────────┐
 tickets ─►  ticket intake → reconcile → rule guards → act on ≤1 ticket → re-read → write-back  ├─► ingest_report
          │  kill switch (PROP_EXECUTOR_MODE) · fallback to the manual bridge                  │
          └───────────────────────────────────────┬────────────────────────────────────────────┘
                                                  │  PropPlatformAdapter (one interface)
                        ┌─────────────────────────┴──────────────────────────┐
                        │                                                    │
          dxtrade  (BUILT: read path in slice 1)          breakout_terminal  (SCOPED ONLY)
          app.breakoutprop.com                            dashboard → "Open Terminal"
```

### 2.1 The interface

Code: `src/prop/platform/base.py::PropPlatformAdapter`.

| method | slice | returns / does |
|---|---|---|
| `login(page, username, password)` | 1 | logs in, or raises `FeasibilityError(reason)` on a challenge, CAPTCHA, 2FA or rejected login |
| `read_account(page)` | 1 | `AccountSnapshot(balance, equity, …)`; unparseable fields are `None`, never `0` |
| `read_positions(page)` | 1 | `list[Position]` |
| `read_orders(page)` | 1 | `list[WorkingOrder]` |
| `place_bracket(page, ticket)` | 2 | one order with SL **and** TP attached |
| `modify_bracket(page, position, sl, tp)` | 2 | moves SL/TP |
| `cancel_order(page, order)` | 2 | cancels one working order |
| `flatten(page, symbol=None)` | 2 | closes one or all positions at market |

In slice 1 the four slice-2 methods exist on the interface and **raise
`NotImplementedError`** on both adapters. There is no code path that clicks an
order control.

### 2.2 Selection by config

`config/prop_platforms.yaml` holds one entry per prop account:

```yaml
accounts:
  breakout_1:
    platform: dxtrade          # dxtrade | breakout_terminal
    login_url: https://app.breakoutprop.com/
```

`src.prop.platform.get_adapter(account_id)` reads it and returns the adapter.
An unknown platform value raises; it never falls back silently.

### 2.3 `dxtrade` adapter (built, read path)

Code: `src/prop/platform/dxtrade.py`. All DXtrade selectors live in its
`SELECTORS` table so the order-placing slice reuses them.

- **Login form: MEASURED** from the DXtrade login page Breakout serves
  (S26, 2026-09-27): `form.loginForm-main` posting to `api/auth/login`, with
  `input#username`, `input#password` and a hidden `input#vendor`. The 2FA
  panel is `input#securityCode` (`name="code"`, "6-digit Code") inside
  `Dx2FAForm`; the password-expired panel is `#userNameForChangePassword`.
- **Post-login DOM: NOT MEASURED.** Nobody has logged in from code yet. The
  adapter reads the account metrics by their visible **labels** (Balance,
  Equity, …) and the positions and orders from **tables by header text**,
  which survives class-name churn better than class selectors. If the labels
  it expects are not on the page, it says so (`unparsed`) instead of
  reporting zero. **The first live run is the only real proof of the read
  path**, and it may need a selector fix.
- `app.breakoutprop.com` returned a Cloudflare `403` challenge to `curl` from
  the sandbox (MEASURED 2026-09-27). Whether a default headless Chromium from
  the live VM passes is exactly what the login check measures. If it gets a
  challenge, that is a feasibility finding (§ 4), not a design task.

### 2.4 `breakout_terminal` adapter (scoped only; no login)

Code: `src/prop/platform/breakout_terminal.py`: every method raises
`NotImplementedError`. What first-party material says, and nothing more:

- Reached from the Breakout dashboard by clicking **"Open Terminal"** (S23);
  the dashboard is `portal.breakoutprop.com`, behind a Cloudflare challenge to
  non-browser clients (MEASURED with curl, 2026-09-27).
- It is **a separate platform** from DXtrade with its own mobile app (S24).
  Existing DXtrade accounts do not migrate (S4). The same trading rules apply
  on both (S6).
- It is the **only** platform for new purchases (S3) and for non-crypto
  instruments (S7).
- **Unknown until someone holds a proprietary-terminal account:** the login
  flow (a third-party review, S27, describes an emailed number-match at
  sign-in; if that happens on every login the adapter is infeasible), the DOM,
  and whether the order ticket is DOM or canvas.

**Scoping verdict:** buildable in principle behind the same interface, but
**unbuildable today**: breakout_1 is DXtrade, so there is no proprietary-terminal
login to measure, and the one login-flow signal we have (emailed code) would
kill it if it holds.

---

## 3. Shared layer above the adapter

### 3.1 Where it runs

A separate systemd unit on the live VM (proposed `ict-prop-executor.service`,
with `MemoryMax=` and `CPUQuota=`), never inside `ict-trader-live`. It talks to
the system only over the local API: `GET /api/bot/prop/tickets` in and
`POST /api/bot/prop/report` out. One egress IP for the account. Headless
Playwright Chromium in its **default** configuration; the live VM is arm64 and
Playwright ships arm64 Chromium.

Slice 1 has no unit: the login check is a one-shot system-action.

### 3.2 Ticket intake and reconciliation (slice 2)

1. **Consume** `GET /api/bot/prop/tickets?account_id=breakout_1&status=emitted`.
   Skip tickets past `valid_until` with `ingest_report({kind: fill, status:
   skipped, reason: expired})`. **The ticket id is the idempotency key**: it
   goes in the order's comment/label field if the platform has one, else in a
   local intent ledger written **before** the click.
2. **Enter** one bracket with SL and TP attached at entry (the manual bridge's
   invariant: no ticket without both).
3. **Confirm by re-read, never by click success**: `placed` if an entry order
   rests; `open`/`filled` if a position exists with both protective legs;
   otherwise UNCONFIRMED (§ 3.5).
4. **Reconcile every cycle** against the journal's open `prop_fills` by
   `prop_position_identity` (account + symbol + direction): closed on terminal
   → report `closed`; position with no journal row → orphan, alert, halt new
   entries, do not touch it; SL/TP differs → report `amend`. Post
   `account_status` every cycle.

### 3.3 Rule guards before any click (local, fail-closed)

Computed from the terminal's own balance and equity read **in the same
cycle**, never from a journal snapshot:

- **Daily loss:** refuse if `equity − open_risk − ticket_risk ≤
  day_start_balance × 0.97 + margin` (`day_start_balance` captured at the first
  cycle after 00:30 UTC).
- **Static DD:** refuse if `equity − open_risk − ticket_risk ≤ 4,700 + margin`
  (floor = `account_size × 0.94`).
- **Gate:** `prop_risk_gate.grade_account_ticket_risk` in **`enforce`** mode
  for the executor, whatever the global default. Anything but "fits",
  including "could not look", means no click.
- **Structure:** no SL or TP, a size that rounds to zero, or a symbol absent
  from `breakout_routing.yaml` means no click.

### 3.4 Write-back

Every outcome goes through `src/prop/prop_report.py::ingest_report`, served as
`POST /api/bot/prop/report`. No second ingestion route.

### 3.5 Kill switch, fallback and failure containment

- `PROP_EXECUTOR_MODE=off|read_only|live` in the VM env, flipped by a
  system-action; `read_only` is the default until the keep-alive passes.
  `systemctl stop ict-prop-executor` stops it outright. Either reverts to the
  manual bridge with no other change, because the ticket emitter never stops
  emitting.

| failure | detected by | contained by |
|---|---|---|
| selector drift | the read path fails to parse, every cycle, before any write | halt new entries, alert, manual bridge |
| expired session | login redirect on read | one re-login; if it fails, halt and alert; never loop |
| partial click (no SL/TP) | post-submit re-read | place the missing leg once; if still missing, close at market and alert |
| duplicate submit | ledger/label shows 2 orders for one ticket | cancel the duplicate; if both filled, close the excess and alert |
| timeout after submit | no confirmation | **never resubmit**; re-read by label or (symbol, side, qty, time); not found after N reads → `skipped: unconfirmed_submit`, alert |
| disconnect with a position open | heartbeat | nothing needed for safety: the broker-side bracket protects; reconcile on reconnect |
| rule guard says no | § 3.3 | report `skipped` with the reason |
| journal and terminal disagree | § 3.2 step 4 | alert, halt entries; never "fix" the terminal to match the journal |

### 3.6 Credentials

`BREAKOUT_DX_USERNAME` and `BREAKOUT_DX_PASSWORD` are GitHub Actions secrets
(set by the operator, 2026-09-27 ~11:05Z). They reach the VM `.env` through
`sync-vm-secrets.yml` as **optional** secrets. ⚠️ **That workflow entry is NOT
in the slice-1 PR:** the lane's edit to add the two names was refused by its
session permission layer, so it is an open item on `PI-20260927-R6FQK6DS-0002`
until a session permitted to make it does. Until then the login check exits
`feasibility: no_credentials`. They never go into git, logs,
screenshots or reports. Playwright tracing and screenshots are off.

---

## 4. Build plan and feasibility

| step | what | "works" means | tier |
|---|---|---|---|
| **1: read-only login check (this PR)** | system-action `breakout-login-check` runs `scripts/prop/breakout_login_check.py` on the live VM: default headless Chromium opens `app.breakoutprop.com`, logs in, reads balance, equity, positions and working orders, prints them (no secrets). `emit_status: true` additionally posts one `account_status` through `POST /api/bot/prop/report`; default off. | exit 0 with `login: ok` and the values matching the operator's screen | Tier-2 held PR |
| 2: keep-alive | repeat step 1 every 5 min for 72 h | unattended re-login; selector stability measured | Tier-2 |
| 3: one minimum-size bracket, operator watching | `place_bracket` on the dxtrade adapter, plus the § 3.3 guards | entry, SL and TP all rest, confirmed by re-read; the report lands | Tier-2, separate held PR |
| 4: soak | executor live on breakout_1's roster, manual bridge still emitting | 14 days, zero unconfirmed submits, orphans or naked positions | Tier-2 |

**Feasibility stops** (each is reported as `feasibility: <reason>`, never
worked around): a Cloudflare-style challenge or CAPTCHA at the login page from
the VM; a 2FA prompt (the operator says there is none); a rejected login; a
canvas-only order ticket with no DOM hooks (found in step 3).

**Effort (INFERRED planning figure):** step 3 about 3–5 lane-days; step 4 is
calendar time.

---

## 5. The other options, briefly

- **A: stay manual, cut the toil (fallback).** A1 one-tap "placed as shown" /
  "skip" Telegram buttons that post `ingest_report`; A2 screenshot → report
  (already built, `screenshot_parse.py`) as the default for closes; A3
  `find_unacted_tickets` reminders only when a ticket goes stale.
- **B: a third-party copier.** Ranked last on feasibility: MetaCopier's docs do
  not name Breakout (S9), a copier reaches DXtrade through the API that
  Breakout's gateway does not expose to us, and Copygram says the Breakout
  Terminal is not a direct destination (S11).
- **E: Apex.** Closed by the operator, do not pursue (2026-09-27,
  `PI-20260927-R6FQK6DS-0001`). Details in the companion doc.

## 6. Roster interplay

- An executor places within one cycle, so shorter-timeframe crypto legs become
  routable to breakout_1 *if* they clear the prop EV bar; each needs its own
  prop-EV row (INFERRED).
- **Non-crypto instruments are not tradable on DXtrade** (S7). Reaching them
  needs a new, proprietary-terminal account and the `breakout_terminal`
  adapter.
- Two **evaluations** must not be traded from the same IP (Breakout's rule on
  multiple accounts from one household, device or IP, S1). Funded accounts may
  stack (S17).

## 7. Pipeline

- `PI-20260927-R6FQK6DS-0002`: the build. Updated with this PR.
- `PI-20260927-R6FQK6DS-0003`: the support question. **Closed** (operator
  dropped it 2026-09-27).
- `PI-20260927-R6FQK6DS-0001`: Apex. Closed earlier.

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
| S11 | "BreakoutProp API" claim; Breakout Terminal not a Copygram destination | https://copygram.app/blog/prop-firms/breakout-prop-firm-review-pass-challenge-copygram | 3rd party (vendor blog) | 2026-06-24 |
| S14 | Breakout x Kraken FAQ | https://support.kraken.com/articles/breakout-x-kraken-faq | Kraken (1st) | 2025-09-05 |
| S17 | Multiple funded accounts up to $200k combined | https://intercom.help/breakoutprop/en/articles/11647215 | Breakout (1st) | 2026-09-03 |
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

**Not attempted:** Breakout's Terms of Use and Trader Agreements (behind the same
challenge). Under the 2026-09-27 re-scope they do not gate anything (§ 1).
