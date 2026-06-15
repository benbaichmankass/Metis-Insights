# Runbook — IB **live**-account login + 2FA (headless gateway)

**Status (2026-06-15): MES/MGC/MHG trade on IB *paper* and are healthy. The
real-money *live* login is BLOCKED on an IBKR 2FA-mode setting (Seamless OFF).**
This is a **pre-flight for a capability not yet in use** — `ib_live` is held
`mode: dry_run` and is not trading — so it is safe to defer. The validation
tooling is built and ready to re-run the moment the IBKR side is fixed.

## TL;DR

- The bot connects to IBKR with **one login** (the `IB_USERNAME`/`IB_PASSWORD`
  Actions secrets — a dedicated bot user with access to both the paper account
  `DUQ325724` and the live account `U25907316`). `TRADING_MODE` picks paper vs
  live; there is no separate live credential.
- **Paper logins are 2FA-exempt** by IBKR, so the paper gateway logs in with no
  tap — which is why paper "just works."
- **Live logins require 2FA.** The bot user's IB Key is in **Challenge/Response
  mode (Seamless Authentication OFF)** — confirmed by the operator's IBKR-Mobile
  screenshot showing the "Generate Response" challenge/response screen. In that
  mode IBKR shows a **challenge number on the login screen** and waits for a
  **response code** typed back. A headless gateway (Xvfb, no display) can't read
  the challenge or type the response, so the live login **hangs at
  "Authenticating…" and times out — and no push is ever sent.**

## Why Seamless (push), not challenge/response

A live trader's gateway **re-logs in unattended every day** after IBKR's
overnight server reset. Only **Seamless Authentication** (the IBKR-Mobile / IB
Key *push* — "tap to approve", no codes) can complete that without a human.
**Challenge/response cannot be automated** (a fresh challenge each login, typed
into an invisible screen) — so live IB trading is simply not possible until the
bot user is on Seamless push.

Reference: IBKR — *"Seamless Authentication is an option for IBKR Mobile (IB
Key). If disabled, you will be required to use a challenge code and pass code
sequence to log in."*
([IBKR guide](https://www.ibkrguides.com/advisorportal/seamless.htm),
[challenge-response when no notification](https://ibkrguides.com/securelogin/sls/notification-not-received.htm)).

## To enable the live login (operator, when ready — no urgency)

1. **Enable Seamless (push) authentication for the bot user.** This is the one
   real broker-side step. It is account-specific and IBKR's UI for it is fiddly
   (it is *not* the IBKR-Mobile app's Security page — that page only shows the
   IB Key activation status + the manual "Generate Response" tool). The bot user
   must be **registered in IBKR Mobile for push notifications**, with Seamless
   turned on. **Confirm the exact steps with IBKR client services** — they can
   point to the right toggle (Client Portal → Secure Login System, and/or the
   IBKR-Mobile enrolment) in a couple of minutes. Do **not** fiddle with the IB
   Key "Add User" / "Change PIN" / "Generate Response" screens blind — that
   risks the *paper* login that currently works.
2. **Re-run the validation:** open an issue labelled
   **`vm-ib-gateway-live-login-test`**. When the **IB Key push** arrives, approve
   it. Success = the run reports `Login has completed`.
3. If IBKR's device picker labels IB Key differently than the default, override
   it with an issue-body line `device: <exact label>` (the run prints
   "could not find … second factor device" + the device list on a mismatch).

## The validation tooling (already shipped)

`.github/workflows/vm-ib-gateway-live-login-test.yml` (label
`vm-ib-gateway-live-login-test`):

- SSHes to the dedicated **gateway VM** (`10.0.0.251`, private) **via ProxyJump
  through the live trader** — same pattern as `vm-ib-gateway-recover`.
- Spins a **throwaway** container `ib-gateway-livetest` in `TRADING_MODE=live`,
  `READ_ONLY_API=yes`, **no published port**, `TWOFA_DEVICE='IB Key Security via
  IBKR Mobile'` (IBC `SecondFactorDevice`). Polls ≤240 s for `Login has
  completed`, prints the auth log tail, and **always tears the container down**
  (`trap … EXIT`) so no real-money session lingers.
- **Non-disruptive + can't trade:** the running **paper** `ib-gateway` is never
  touched; the test container publishes no port and is read-only; the trader
  never connects to it; `ib_live` stays `mode: dry_run`. Credentials ride an
  scp'd env file, shredded after use.

## History (2026-06-15 session)

Four runs (issues #3631, #3633, #3635, #3640) all reached `IBC: Click button:
Log In` → `Authenticating…` and **timed out with no push** — including after
`SecondFactorDevice` was confirmed correctly set. The operator's IB Key
screenshot (challenge/response "Generate Response" screen) confirmed **Seamless
is OFF**, which fully explains it. The earlier two-2FA-devices theory (IB Key +
Mobile Authenticator both enrolled) was ruled out — pinning the device changed
nothing and no device-selection dialog ever appeared, i.e. the login never
reaches device selection because no push challenge is issued at all.

Tracked: health-review backlog `BL-20260615-IBLIVE-2FA`.
