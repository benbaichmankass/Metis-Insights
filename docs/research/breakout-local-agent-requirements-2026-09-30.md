# Breakout terminal — free local agent: what it needs from the operator and what we build, 2026-09-30

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Checklist row **PROP-TERM**; pipeline **PI-20260929-FRJ7NMPU-0001**. Part 3 of
> [part 1](breakout-terminal-egress-scoping-2026-09-30.md) and
> [part 2](breakout-terminal-egress-scoping-part2-2026-09-30.md) (part 2 § 2 defines options L1–L4).
> Operator, verbatim, ~05:26Z 2026-09-30: *"The third option of building a free local agent. I want to
> understand what that would entail like what their requirements are, what I would need to provide."*
>
> **SCOPE ONLY. Nothing here is built, deployed, bought or routed, and nothing was run on a home network
> or on any new egress to write it.** Tier-1 (docs only). Every effort, time and lane-day figure is an
> **estimate**, not a measurement.

## 0. The recommended variant, and why

**Recommended: L3, a tunnel that makes the home connection an *egress only*.** The browser, the
adapter, the Breakout credentials and the write token all stay on our live VM. The operator's home box
runs nothing but an SSH client that dials **out** to the VM and offers itself as a SOCKS exit. The VM's
Playwright then sends the terminal's traffic through that exit, so Breakout sees the operator's home
address.

Why this one: it is the variant with the fewest operator steps, needs **no inbound port at home**, and
keeps every secret where it is today (part 2 § 2). Its cost is a build on our side (§ 2), the largest of
the "free" variants that does not put credentials on a second machine.

**Runner-up: L1, the existing adapter running on a home PC** (Playwright Chromium on the operator's own
machine). Cheaper to build, but the Breakout login and the write bearer for `POST /api/bot/prop/report`
then live on that machine, and the operator carries the ops burden. Summarised in § 5.

**Account risk, stated plainly (item 5):** *this still routes around Breakout's ban.* The ban is on our
current datacenter address; L3 exists to reach the same site from a different address. That may breach
Breakout's terms and put the funded account at risk, in addition to the automation risk the operator
accepted on 2026-09-27. Breakout's first-party terms remain unread (part 1 § 1). Nothing in this document
says the route is permitted or forbidden.

## 1. What the operator must provide (L3 checklist)

| # | item | detail |
|---|---|---|
| 1 | **A box that stays on** | Anything that can run OpenSSH and stay powered: an old laptop, a Mac mini, a NAS with a shell, or a **Raspberry Pi**. Because Chromium does **not** run on the home box in L3, the requirement is tiny: a **Pi Zero 2 W or any Pi 3/4/5 with 1 GB RAM is enough**. (Only L1 needs the 4 GB+ class, § 5.) Cost: **$0 if owned; ~$15–60 one-off** if a Pi must be bought (a price range from memory, unverified; the operator's call). |
| 2 | **An OS** | Any with `ssh` (and, for auto-restart, systemd: Raspberry Pi OS or Ubuntu). Windows and macOS can do it with a scheduled task or `launchd` instead, at slightly more setup. |
| 3 | **Uptime** | The box and the home internet must be up **whenever the executor may place or manage an order**. 24/7 is the honest requirement: signals arrive on the bot's clock, not the operator's. |
| 4 | **Network** | **Outbound TCP 22 to `ict-bot-arm` (141.145.193.91)**. **No inbound port, no port-forward, no static IP, no dynamic-DNS.** The tunnel reconnects when the home address changes (see § 3 for what a changed address does to the Breakout session). |
| 5 | **One SSH key** | Generated on the home box; the operator sends us the **public** half (a `.pub` line). It never leaves the box in its private form. |
| 6 | **A one-time approval** | Tier-2 on our side: a restricted account/key on the live VM (§ 2). The operator says yes once in chat. |

**Accounts and keys, and where each lives**

| secret | where it lives in L3 | operator handles it? |
|---|---|---|
| Breakout login | the live VM, exactly as for `breakout_1` today | **no** (unchanged) |
| `DASHBOARD_API_TOKEN` (write bearer) | the live VM | **no** |
| VM SSH key (`VM_SSH_KEY`) | GitHub Actions secrets | **no** |
| home box's SSH private key | the home box only | **yes, but only ever generates it and keeps it there** |
| an email or 2FA code at the first terminal login | arrives to the operator | **yes, once**, through the existing login-check flow, not through the home box |

**What the operator never has to do:** hold or paste the Breakout password, the API token or the VM key;
open a router port; install Chromium or Playwright; touch the OCI console.

**One-time setup, counted:** **5 steps, about 45–60 minutes** (estimate): (1) run the step-zero command
(§ 4), ~1 min; (2) get the box onto the network with an OS and `ssh`, ~15–30 min (a fresh Pi: flash the
card); (3) `ssh-keygen`, send us the public key, ~2 min; (4) copy in the one service file we supply and
enable it, ~10 min; (5) confirm the heartbeat shows green in the ops channel, ~5 min. After that, no
routine steps.

**What happens when the box is off or the link is down** (design intent; **none of it exercised**):
- The executor's pre-flight (§ 3) finds no working exit and **refuses**: no login, no order, exit code 5
  (environment), the same way the executor already treats a missing browser (`scripts/prop/prop_executor_tick.py`).
- **Tickets are not queued.** Each has a validity window of `ttl_bars` (default 1.0) times its signal
  timeframe (`config/prop_rulesets/breakout_routing.yaml`, `src/prop/breakout_ticket.py`), so it **expires**
  and the existing expiry prompt asks the operator whether they placed it by hand.
- The **manual bridge (Telegram tickets) is the existing fallback**; the operator was told on 2026-09-28
  that tickets keep arriving there. That is documented for `breakout_1`; that a terminal account keeps the
  same path is **assumed, not verified**.

## 2. What we build (L3)

| component | what it is | est. |
|---|---|---|
| **Restricted tunnel account on the live VM** | a dedicated unprivileged user, the operator's public key in `authorized_keys` with `restrict,port-forwarding,permitlisten="127.0.0.1:1080"` (no shell, no commands, one forwarded port), plus the `sshd` settings to allow it. Tier-2 (VM access), so a system-action plus the operator's OK. | 0.5 lane-day |
| **The home-side unit** | one `systemd` service (or `launchd`/scheduled task) running `ssh -N -R 1080 -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes tunnel@141.145.193.91` with `Restart=always`. `-R 1080` with no destination makes the VM listen on a local SOCKS port that exits from the home box, so the home end needs only **outbound** TCP. Ships as a file in the repo with a 10-line runbook. | 0.5 lane-day |
| **Adapter proxy option** | a `proxy` argument for the **three** Playwright launch sites (`scripts/prop/prop_executor_tick.py`, `scripts/prop/breakout_login_check.py`, `scripts/prop/breakout_terminal_probe.py`), pointing at `socks5://127.0.0.1:1080`; unit tests that the option **fails closed** (no fallback to a direct connection). Tier-2 (order-path code). | 0.5–1 lane-day |
| **Egress pre-flight** | before any login or click, ask through the proxy for the observed exit address and refuse unless it exists and is **not** the VM's own address. See § 3. | 0.25 lane-day |
| **Heartbeat and alert** | a small periodic check that the SOCKS port is up and the pre-flight passes, alerting on a change of state through the existing Telegram/ops path (`ACCOUNT_REACHABILITY_*` is the model: latched, on-cross alert plus an `[OK]` on recovery). | 0.5 lane-day |
| **Update path** | none needed on the home box (it runs only `ssh`); the adapter updates with the normal `git pull` on the VM. | 0 |
| **Runbook and doc registration** | operator-facing steps, the failure table, the revert. | 0.25 lane-day |

**Total: about 2.5–3 lane-days** (estimate; the largest uncertainty is the fail-closed proxy work and
whether the terminal's own traffic, for example a WebSocket, behaves through a SOCKS proxy).
**Not in this estimate:** the terminal-adapter measurement work itself (login flow, DOM, ticket kind),
which is already PROP-TERM's and blocked on reachability; and the new Breakout account the operator will
open.

## 3. Failure modes and the refuse-not-guess posture

`src/prop/platform/` already treats an unreadable page as a *stop*, not a guess (feasibility stops such as
`challenge`, `captcha`, `email_code`, `asn_blocked`). L3 adds an egress layer with the same posture.

| failure | what happens | design intent |
|---|---|---|
| **Home ISP address changes** | the tunnel reconnects on its own; the terminal now sees a new source address and may drop the session or challenge. | The executor exits `6` (no reusable session) and waits for a login; the login ceiling (`ict-prop-feed`'s relogin counter) still applies. **Not measured:** how often the operator's ISP changes the address. |
| **Power cut / link drop mid-order** | the browser on the VM loses its connection during a submit. The result is *unknown*. | Re-read the terminal on the next reachable tick (`classify_confirmation`); `partial_no_sl_tp` is a fail. The design relies on the bracket being attached **at entry, broker-side**, so a position stays protected while we are offline (`prop-automation-options-2026-09-27.md` § 1). **On this terminal that atomic attach is unmeasured**: everything past the landing page is fixture-only, so this is the highest-risk assumption in the plan. A close/flatten also needs the tunnel, so it cannot happen while it is down. |
| **Cloudflare challenge at the home address** | the same `challenge` feasibility stop as today; nothing is clicked. | Step zero (§ 4) tells us beforehand whether a plain client is challenged from home. A real Chromium may still be scored differently: unmeasured. |
| **Tunnel down, SOCKS port closed** | Playwright cannot connect. | Must **fail closed**: a connection error, never a silent direct connection (a banned route anyway). Covered by the pre-flight and a unit test. |
| **Traffic leaks around the proxy** (for example WebRTC, or DNS resolved on the VM) | the site could see the VM's own address. | Use SOCKS5 with remote DNS. Chromium's WebRTC policy (`--force-webrtc-ip-handling-policy=disable_non_proxied_udp`) exists to keep traffic on the proxy. It is about honouring the tunnel, not disguising the client, but it is a browser-launch flag, and the prop posture forbids anti-detection features (`prop-automation-options-2026-09-27.md` § 1); **an operator/manager call is needed on whether that flag is in scope.** |
| **Home box compromised** | it can offer a wrong exit or drop traffic. | It sees only encrypted TLS to Breakout, not credentials (they stay on the VM, TLS ends in the VM's browser). The restricted key can forward one port and run nothing. |
| **Live-VM SSH surface grows** | a new account and key on the live VM. | Restricted key options above; Tier-2 review; revert by deleting one `authorized_keys` line. |
| **Executor cannot tell tunnel-up from tunnel-lying** | a proxy that connects but exits from the wrong place. | Pre-flight compares the observed exit address to the VM's own address and refuses if it matches or cannot be read. |

## 4. Step-zero measurement (operator, ~1 minute, no credentials, nothing built)

Landing-only: one GET of the public landing page, no login, no cookies, nothing clicked. It prints only
the HTTP status line, the Cloudflare mitigation header, and any block message lines; **no address, token
or account data** (the filter drops everything else). Safe to paste.

macOS / Linux / Raspberry Pi OS:

```
curl -sS -m 25 -D - https://app.breakoutprop.com/ | grep -i -E '^HTTP|^cf-mitigated|error code|Just a moment|banned the autonomous'
```

Windows (PowerShell or Command Prompt):

```
curl.exe -sS -m 25 -D - https://app.breakoutprop.com/ | findstr /i "HTTP cf-mitigated error Just banned"
```

How to read the result (from the measurements in part 1 § 2):

| output | meaning |
|---|---|
| `HTTP/2 403` and `error code: 1005` | the operator's home address is **banned like our VM**. Option L3/L1 are dead. Ask Breakout (option 1). |
| `HTTP/2 403` and `cf-mitigated: challenge` (and `Just a moment`) | a **managed challenge** for a plain client, the same result this lane's Google-ASN sandbox got. Not a ban; says nothing about a real browser. Worth a headless-Chromium measurement next. |
| `HTTP/2 200` (no `cf-mitigated`) | the address is served the page. Best case. |
| a `curl:` error or a timeout | no route or a local network problem; re-run once. |

`grep` prints nothing at all only if the page came back without any of those markers (for example a
redirect); paste whatever it does print.

## 5. Runner-up: L1, the existing adapter on a home PC

- **Provide:** a machine that stays on with **64-bit Linux, macOS or Windows and ≥4 GB RAM** (a Raspberry
  Pi 4/5 with 4–8 GB running a 64-bit OS; headless Chromium is the reason for the higher bar), Python +
  Playwright + our repo, and **the Breakout login and `DASHBOARD_API_TOKEN` on that machine** (they leave
  our VMs). Outbound HTTPS to `ict-bot.duckdns.org` and `app.breakoutprop.com`; no inbound port.
- **Operator effort:** about **10 steps, 1–2 hours**, plus ongoing updates and a machine to keep patched.
- **We build:** a packaging/runbook for the home machine, a scheduler, the `--api-base` pointing at the
  public Caddy front (the flag already exists), a heartbeat and alert (~0.5 day), and a **write-scoped token
  restricted to the report route** so a lost machine cannot do more than post reports. Estimate **~1.5–2
  lane-days** (no proxy work; more operator-side ops).
- **Failure and risk:** the same account-risk sentence applies. Unlike L3, a stolen home machine exposes the
  Breakout password.
- **Fewest operator steps: L3.** Fewest builds: L1.

## 6. Decision for the operator (updates part 2 § 4)

1. **Run the step-zero command (§ 4) and paste the output; also send Breakout the allowlist question.**
   Recommended. $0, no build. It decides whether L3/L1 are worth anything at all.
2. **Authorise an L3 build lane (~2.5–3 lane-days)** if the step-zero result is a `200` or a challenge, accepting
   the ban-avoidance account risk (§ 0) and the new restricted SSH account (Tier-2).
3. **Authorise L1 instead** (cheaper to build, secrets on the home machine, more operator upkeep).
4. **Drop the free-local route** and choose between the other-cloud VM (part 2 § 1) and the manual bridge.

## 7. Not verified

Every effort, time and price figure; the operator's hardware, ISP and address stability; whether the home
address is banned, challenged or served; whether `ssh -R` dynamic forwarding is permitted by the live VM's
`sshd` configuration as it stands; how the terminal's WebSocket traffic behaves through a SOCKS proxy;
whether a terminal bracket attaches atomically at entry; Breakout's first-party terms; and whether the
Telegram fallback applies to a terminal account. Nothing was run on any home or candidate egress.
