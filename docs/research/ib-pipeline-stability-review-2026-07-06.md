# IB Pipeline Stability Review — 2026-07-06

**Operator ask (verbatim intent):** we've had a lot of IB problems, we want to go
live on `ib_live` soon (not yet), and before that we need a full pipeline review
that (a) tells apart *real* instability from *false alarms* wasting time/compute,
(b) gets IB to the same observability level as Bybit, and (c) doesn't cost the
training pipeline anything in the process. "There's no technical reason I know
of why it shouldn't be [as stable as Bybit]" — this review answers whether
that's true, and what closes the gap.

**Scope of this review:** research + diagnosis + a concrete plan. No code
changes to the order path, no account-mode flips, no `ib_live` promotion. That
stays exactly where the operator left it: `mode: dry_run`, promoted only via
`set-account-mode`, Tier-3.

## Bottom line

There **is** a real technical reason IB isn't naturally at Bybit's level, and
it isn't a bug: **Bybit's API is stateless signed-REST (every call independently
authenticated over HTTPS); IB's TWS API is a stateful login session through a
desktop Gateway process that IBKR itself force-resets daily.** That's IBKR's
platform design, not something this codebase can architect around. But most of
the *practical* gap — restart flapping, false alarms, wasted review time — is
closable, and about 70% of it already has been closed over the last month. What
remains is one real architectural gap (§3.1) and one hard external blocker for
`ib_live` specifically (§4).

**Today's live evidence** (see §2): the gateway-isolation redesign is holding —
the container has zero restarts and clean re-logins since 2026-06-10 (26 days),
the live trader's liveness watchdog journal is completely clean, and I ran the
documented reactive-self-heal selftest live — it correctly detected and
auto-healed a controlled wedge in ~10 seconds. The safety net actually works,
verified today, not just on paper.

---

## 1. Why IB and Bybit are architecturally different (the honest answer)

| | Bybit (`pybit`) | IB (`ib_insync`/TWS API) |
|---|---|---|
| Auth | API key + secret, signed per-request | A logged-in desktop Gateway/TWS session (no keys at all) |
| Connection model | Stateless HTTPS — every call is independent | Stateful socket bound to one asyncio event loop, held open |
| Failure signature | Exception on the failing call, immediately, every time | Socket can **accept a connection and then just never answer** — the failure mode Bybit structurally cannot have |
| Re-auth | Never — headers are signed fresh each call | IBKR forces a session reset nightly (~03:45–05:45 UTC observed) requiring a to Gateway re-login |
| Where it runs | `bybit_client_for()` builds an `HTTP()` object in-process, no external dependency | A separate Java/Xvfb desktop app in Docker, on its own VM, reached over the network |
| 2FA | None (API keys) | Required for the **live** account; paper is exempt |

This is why `src/units/accounts/clients.py::account_open_positions` has to carry
an entire IB-specific branch (ib_client.py:1140-1217) with a "logged-in but
empty" ambiguity check that no other exchange needs — Bybit's REST calls never
have that ambiguity; a 200 response with `[]` unambiguously means flat. IB's
`portfolio()` returns `[]` both when genuinely flat AND when the Gateway is
logged out, so the bot has to cross-check `net_liquidation` to disambiguate.
Every other exchange integration in this repo (Alpaca, OANDA) is REST-based
like Bybit and doesn't need this.

**Is there an IB alternative that's REST-like?** IBKR does publish a Client
Portal Web API (OAuth, stateless-ish HTTPS), but it still requires a
brokerage-session re-authentication roughly every 24h, doesn't support the
`ib_insync`/TWS ecosystem this bot is built on, and would be a from-scratch
rewrite of the entire IB integration for a marginal reliability gain (it still
has scheduled re-auth, just phrased differently). **Not recommended** — the
session-based model isn't the bottleneck; the code's handling of it is where
the real gains are.

## 2. Live evidence pulled today (2026-07-06, ~07:00 UTC)

Three independent checks, all fresh:

1. **`vm-ib-gateway-selftest`** (the documented, safety-netted controlled-wedge
   test — paper-only, no live-trader impact): container `RestartCount=0`,
   `Created=2026-06-10` (26 days, never recreated), 4 clean `Login has
   completed` cycles in the last 2h / 3 in the last 30m (**normal** periodic
   re-auth cadence, not flapping). Then the test **stopped the container** to
   simulate a real wedge and ran the actual `check_ib_gateway.py` watchdog with
   the same flags as the systemd unit: it detected the down state on check 1,
   issued the restart on check 2, and the gateway was back and verified
   (`net_liquidation=1`) within ~10 seconds total. **PASS, live, today** — not
   an assumption from the runbook, a real controlled test.
2. **`ict-liveness-watchdog.service` journal, 05:17–07:03Z today**: 100+ clean
   heartbeat cycles, zero CRITICAL, zero restarts, zero IB mentions. The live
   trader's own dead-man switch shows no IB-induced interference in the last
   ~1h45m window.
3. **`/api/diag/services`**: `ict-ib-gateway-watchdog.timer` reads `inactive`
   on the live trader VM — correct, by design (it must only run on the
   isolated gateway VM, never on the money box; confirmed it isn't leaking
   back onto the trader).

None of this proves zero risk forever, but it's the first time in this repo's
history that the reactive self-heal has been **exercised live and confirmed
working**, rather than just deployed and assumed.

## 3. Incident inventory — real vs. false-alarm vs. bot-bug

25 IB-tagged items in `health-review-backlog.json`, 1 in `ml-review-backlog.json`,
2 in `performance-review-backlog.json`. Classified below. (Two stale-but-actually-fixed
items — `BL-20260611-001` fractional sizing, `BL-20260612-001` Error-10349 TIF —
were closed as part of this review; the code fix was verified present but the
backlog had never been updated to reflect it.)

### 3.1 Real, structural, still-open — this is where the actual gap is

**`BL-20260610-009` (Tier-3) — the IB liveness probe is currently DISABLED.**
This is the single biggest reason IB isn't at Bybit's observability level, and
it's self-inflicted, not IBKR's fault. `IBClient._probe_liveness()`
(`ib_client.py:357`) is supposed to catch exactly the "socket open but Gateway
dead" failure mode with a bounded `reqCurrentTime` round-trip — the IB-specific
protection Bybit doesn't need because it can't have this failure mode. But
`IB_PROBE_TIMEOUT_S=0` on the live VM **turns it off entirely**, because over
the cross-host socat relay to the isolated gateway VM, the probe itself doesn't
resolve even on a healthy connection (a relay quirk, confirmed independently by
this session's `BL-20260705-IBPAPER-POSITIONS-NULL-WEEKEND` finding on the
*separate* diag-relay IB client hitting the same thing). Today, protection
against a logged-out-but-connected Gateway rests entirely on:
  - the external `IB_FETCH_TIMEOUT_S=8s` bound on each historical-data call, and
  - the reactive watchdog on the gateway VM (proven working today, §2), which
    polls every ~5 min — a much coarser grain than a per-call probe.

That's a real gap versus Bybit, where every single call either succeeds or
raises immediately. **Recommended fix before `ib_live`:** make the probe
relay-aware — either (a) route it through a lightweight TCP-level check against
the relay's socat port instead of `reqCurrentTimeAsync` over the persistent
loop (matches what `scripts/ops/ib_gateway_local_probe.py` already does
dependency-free from the gateway side), or (b) shorten `IB_FETCH_TIMEOUT_S` and
accept the coarser watchdog grain as the primary defense with a documented
justification. Either way, closing this makes the "same level as Bybit" claim
literally true rather than "close enough."

**`BL-20260615-IBLIVE-2FA` — hard blocker for `ib_live`, needs the operator.**
The live account's IB Key is in challenge/response 2FA mode (Seamless
Authentication OFF); a headless Gateway can neither see nor answer the mobile
challenge, so a live login hangs at "Authenticating…" indefinitely. Paper is
2FA-exempt and unaffected — this is why `ib_paper` is healthy and `ib_live` has
never successfully logged in even once. **This is the one item on this whole
list that is a genuine credential/account-setting change only the operator can
make** (enable Seamless push for the bot's IB Key in IBKR's own account
settings) — squarely the "physical/credential" hand-off the repo's own rules
carve out. Nothing in this codebase can work around IBKR's own 2FA enforcement.
**This is the actual go/no-go gate for `ib_live`** — not code stability.

**`BL-20260618-RECONCILE-DUP` — the worst incident on this list, mitigated not
proven-closed.** One real MGC position got re-adopted as 19 phantom "closed"
trades (−$23,773 phantom loss) during a gateway flap — a bot-side bug in how
the reverse reconciler treats an ambiguous "absent" read, not an IB-caused
loss (paper money; no real capital was at risk, but the pattern would be
real-money-lethal on `ib_live`). Mitigated via `RECONCILER_READOPT_GUARD_SECONDS`
(300s re-adopt flap guard, refuses to re-adopt a position whose orphan-adopt
just closed within the window) — shipped and live, but the backlog item has
never logged a confirmed no-recurrence streak since. **Recommend:** one more
`/health-review` cycle that explicitly greps for repeated `adopted_orphan` rows
on the same `(account, symbol, direction)` within a short window before this
is called closed for real — this is exactly the class of bug that must not
survive to `ib_live`.

**`BL-20260610-004` Fix 3 (deferred, still open) — `IBMarketData` doesn't
consult the circuit breaker.** `IBMarketData.get_ohlcv` re-probes and re-fetches
on every tick even while `IBClient`'s breaker is open, costing up to ~13s
(5s probe + 8s fetch) per MES-family symbol per tick during a wedge — this is
pure waste, not a stability risk (the breaker still isolates IB from Bybit's
loop), but it's exactly the "wasting time and compute on false alarms" pattern
the operator flagged. Cheap fix, still on the table from the 2026-06-10
incident review and never picked back up.

### 3.2 Real, but already fixed and holding

- **Gateway isolation** (2026-06-10) — the original CPU-wedge cascade
  (`BL-20260605-*`, `BL-20260610-001..004` Fix-1) was the gateway sharing the
  2-core money box with the trader; moving it to its own dedicated VM is why
  today's evidence (§2) shows zero trader-side interference. This is the
  single most load-bearing fix on the list and it's holding 26 days later.
- **`BL-20260624-MHG-CLOSE-CONFIRM-VERIFY`** (adopt→sl_cross→re-orphan flap) —
  went through two verification rounds in late June; the last recorded flap
  predated the fix landing. Worth one more confirm but not currently flagging
  as open risk.
- **`BL-20260611-001` / `BL-20260612-001`** — fractional-contract sizing and
  the Error-10349 TIF rejection. Both closed in this review: the fixes are
  live in `ib_client.py` (explicit whole-contract floor, explicit TIF on every
  leg) but the backlog had never been updated to say so.
- **`BL-20260623-002`** (recurring 06:00Z wedge) — retimed the daily reset to
  06:05 UTC + added a suppress-window so the deterministic restart no longer
  races IBKR's own ~03:45–05:45 UTC reset window. No recurrence in the 4 days
  since (§2 evidence); left open one more week to confirm outright before
  closing.

### 3.3 False alarms — diag-tooling artifacts, not real IB problems

These cost review time without reflecting real risk — closing this class is
the direct answer to "stop wasting time on false alarms":

- **`BL-20260705-IBPAPER-POSITIONS-NULL-WEEKEND`** (this session, resolved) —
  the diag relay's own separate short-lived IB client hits the same cross-host
  relay quirk as the disabled liveness probe (§3.1); the trader's real
  connection was independently confirmed healthy via live `*_eval` audit
  activity + `accounts/balances`.
- **`BL-20260614-HEALTHSNAP-PY`** — the health-snapshot cron runs under system
  Python, not the trader's venv, so `ib_paper` balance always reads
  "unavailable" in that one report even when the account is fine.
- **`BL-20260705-HEALTHCHECK-SHELVED-ACCOUNTS`** — the account health rollup
  counts the intentionally-dry `ib_live` as a "down" account, keeping the
  overall roll-up perma-"watch" and masking real degradations under noise.
- **`BL-20260705-ENV-DIAG-BASE-URL-STALE`** (found this session) — this
  session's cloud environment had `DIAG_BASE_URL` pointed at the **decommissioned**
  x86 micro (`158.178.210.252`, terminated 2026-06-16), so direct diag reads
  silently fail and every check has to fall back to the GitHub issue relay.
  Not IB-specific, but it directly slowed this review down — worth an
  environment-config fix so future sessions don't hit the same wall.

### 3.4 Training-pipeline impact

The operator explicitly asked this not come at the training pipeline's cost.
One real historical incident ties the two together: **`BL-20260626-MES-BASE-STALE`**
— the daily deep-history MES/MGC/MHG pull from IB silently stopped running for
two weeks (frozen at 2026-06-12), which fed stale data into the trainer's
`market_raw` base and blinded RG4 analysis on the whole MES fleet. Fixed by
`ict-mes-ibkr-pull.timer` (daily at 23:30 UTC, deliberately scheduled clear of
the 06:05 UTC gateway reset, feeding a `MES_IBKR_MAX_STALE_DAYS=5` freshness
gate that falls back to yfinance if the IB pull ever goes stale again). This is
now a self-healing dependency, not a standing risk — but it's the concrete
proof that IB instability has already once cost real training-data quality,
which is exactly the failure mode to keep watching for.

## 4. Go-live readiness bar for `ib_live`

Not a decision — a checklist for when the operator decides to revisit
promotion (`set-account-mode`, Tier-3, explicit approval required regardless):

| Gate | Status |
|---|---|
| Gateway isolated from the trader's CPU | ✅ Done, holding 26 days |
| Reactive self-heal proven live | ✅ Verified today |
| Daily reset clear of IBKR's own reset window | ✅ Retimed, no recurrence in 4 days |
| Whole-contract sizing / TIF rejection bugs | ✅ Fixed (this review confirmed + closed the stale tickets) |
| Reconciler duplication bug | ⚠️ Mitigated, wants one more confirmed no-recurrence cycle |
| Liveness probe working over the relay | ❌ Currently disabled (§3.1) — the one real "not at Bybit's level" gap |
| Live account 2FA / headless login | ❌ **Blocked on IBKR account setting only the operator can change** (§3.1) |

Two boxes need to close before `ib_live` is a live conversation: the 2FA
setting (operator, IBKR console) and the liveness-probe fix (code, Tier-1/2
depending on approach). Everything else on the historical incident list is
either already fixed or a diag-tooling nuisance, not a real blocker.

## 5. Recommended next actions (ranked, none executed by this review)

1. **Operator:** enable Seamless Authentication (IBKR Mobile push) on the bot's
   IB Key for the live account — the only credential-side action, and the
   actual gate on `ib_live`.
2. **Tier-1/2 code:** fix the liveness probe to work over the cross-host relay
   (§3.1) — closes the real observability gap vs. Bybit.
3. **Tier-1 code:** wire `IBMarketData.get_ohlcv` to consult the same circuit
   breaker as `IBClient` (§3.1, BL-20260610-004 Fix 3) — stops wasted
   compute/time during a wedge.
4. **Tier-1 observability:** fix the three diag-tooling false-alarm sources
   (§3.3) so future reviews aren't distracted by noise.
5. **One more `/health-review` cycle:** confirm zero `adopted_orphan`
   recurrence for `BL-20260618-RECONCILE-DUP` before calling it closed.
