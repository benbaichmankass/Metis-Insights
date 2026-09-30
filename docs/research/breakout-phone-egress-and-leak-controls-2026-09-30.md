# Breakout terminal egress — masking the VM's address, and the phone as the egress, 2026-09-30

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Checklist row **PROP-TERM**; pipeline **PI-20260929-FRJ7NMPU-0001**. Part 4 of
> [part 1](breakout-terminal-egress-scoping-2026-09-30.md),
> [part 2](breakout-terminal-egress-scoping-part2-2026-09-30.md) and
> [part 3](breakout-local-agent-requirements-2026-09-30.md).
>
> **SCOPE ONLY. Nothing is built, deployed, bought or routed, and nothing was run on a phone or a home
> network.** Tier-1 (docs only). Figures marked *estimate* are not measurements.

## 0. Operator answers this doc responds to (~05:37Z 2026-09-30, verbatim)

1. *"I do want to keep the our own VM's IP address masked. Like, it's not about anti-detection, it's just about like
   the privacy and security of the system itself. There's no need to expose that."* → the VM's address not leaking
   past the tunnel is a **design requirement** (§ 1).
2. *"…what about the option of using my phone and maybe even setting it up through the existing app that we already
   built? And if it's just the sending the trades back and forth, then maybe that's like a feasible option here. It's
   just also like the best option of knowing that like it'll always be on and we'll almost always have internet."* → § 2–§ 6.

**Recommendation in one line:** keep the always-on **home box (part 3, L3) as the primary** egress; the phone is a
viable but **worse primary** (address churn, data volume, Android background limits) and is best treated as an
optional **failover** to add later, using stock tools, **not** by building into the frozen Android app.

**Account risk, stated plainly:** *the phone egress still routes around Breakout's ban.* It uses a different address
to reach a site that blocks our current one, which may breach Breakout's terms and put the funded account at risk, on
top of the automation risk accepted on 2026-09-27. Breakout's first-party terms remain unread.

## 1. Keeping the VM's address from leaking (requirement, with every path found)

**Scope of "masked":** the VM's address must not reach **Breakout, Cloudflare or any third-party host the terminal
page loads**. Two honest limits: (a) `141.145.193.91` and the trainer's address are **already written in this public
repo** (`CLAUDE.md`, and in earlier docs of this scoping series), so the tunnel hides the address from the terminal
site, not from anyone reading the repo; (b) the repo and its Actions logs are public, so **the exit address (home or
phone) must never be logged or committed either**. Whether to scrub the existing mentions is a separate call for the
manager: history keeps them regardless.

| # | leak path | how the VM's address could reach the site | control |
|---|---|---|---|
| 1 | **Proxy down → direct connection** | Chromium falls back to the VM's own route | explicit `proxy` on every launch with **no fallback**; a unit test that a dead SOCKS port yields a connection error, not a direct load; the egress pre-flight (row 9) refuses first |
| 2 | **DNS resolved on the VM** | the VM's resolver queries for `*.breakoutprop.com` (reveals the VM as the client to the resolver, and can choose a geo-different CDN edge) | `socks5://` (Chromium resolves names through the proxy); verify with a test that a name with no local resolution still loads |
| 3 | **WebRTC / STUN (UDP)** | ICE candidates disclose local and public addresses; SOCKS over SSH carries **TCP only**, so UDP would go direct | launch flag `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`; **now in scope by operator decision** (§ 0.1) |
| 4 | **Browser background networking** | component updater, safe-browsing, DNS prefetch, sync calls to vendor hosts (Google, not Breakout) | `--disable-background-networking`, `--no-pings`, disable prefetch; they would otherwise use the VM's route |
| 5 | **Other processes on the VM using the SOCKS port** | none leak the VM's address, but they would burn the home/phone link's data | bind the SOCKS listener to `127.0.0.1` only; the adapter is the only intended client |
| 6 | **Non-browser requests in the same script** | `urllib`/`requests` calls (API intake, the pre-flight lookup) go direct from the VM | intake and write-back go to **our own** API, not Breakout; the pre-flight must **not** be a request to a Breakout host from outside the proxy |
| 7 | **IPv6** | a dual-stack VM could reach an IPv6 origin directly | explicit proxy covers browser traffic (row 1); confirm no direct IPv6 path in the test |
| 8 | **Headers and page scripts** | `Forwarded` / `X-Forwarded-For` added by an intermediary; scripts that echo the source address | no intermediary is added by the tunnel; page-side echoes (see the terminal's own network calls) are **unmeasured** for the proprietary terminal |
| 9 | **Tunnel "up" but lying / stale** | exit is not the intended one | pre-flight through the proxy reads the **observed exit** and refuses unless it is present **and not equal to the VM's own address**; emit only a boolean, never the address (public logs) |
| 10 | **Logs, screenshots, artifacts, error text** | an address printed into a public Actions log, a PR comment or a committed file | the existing redaction posture of `scripts/prop/*` (no URL paths, tokens, cookies) extended to addresses; a test that the pre-flight output contains no address |
| 11 | **SSH host/session metadata** | the tunnel's own SSH connection shows the VM's address to the home/phone endpoint | expected and acceptable (the endpoint is the operator's own device); listed so it is not mistaken for a leak |

Nothing in this table is exercised yet; each is a **test to write** in the build.

## 2. The premise corrected: it is not only trades that cross the link

Under L3 the **VM's headless browser loads the whole web terminal through the phone**. What crosses the link is
page assets, API calls and the live price/positions stream, not just tickets and fills (the ticket path is tiny and
stays on our API, not on the tunnel).

### 2.1 What was measured (this session; public login page only, no credentials, nothing clicked)

DXtrade's public login page (`https://wss.breakoutprop.com/`), fetched with `curl` (gzip/br negotiated), bytes on the
wire:

| item | bytes |
|---|---|
| HTML | 197,987 |
| 15 static assets it references (CSS, JS, SVG, icon) | 7,347,151 |
| — of which `main.js` | 6,177,300 |
| — of which `lib/chart-combined.js` | 667,241 |
| **cold load, total** | **7,545,138 (≈ 7.5 MB)** |

**Limits, stated plainly:** (a) this is the **DXtrade login page**, not the post-login terminal (which loads more:
chart data, symbols, tables) and **not the proprietary terminal** (`app.breakoutprop.com` returns a Cloudflare challenge
to this session, so its weight is unmeasured); (b) it counts statically referenced assets only, not lazily fetched ones;
(c) the live stream was **not** measured (no login, and a Chromium page-load attempt from the sandbox failed on the
sandbox's TLS trust and was abandoned rather than worked around). Treat 7.5 MB as a **floor** for one cold load.

### 2.2 How often it loads, read from the code

The scheduled runs launch a fresh browser each time: `browser.new_context(storage_state=saved, viewport=…)` in
`scripts/prop/prop_executor_tick.py` (cookies only, **no persistent profile, so the HTTP cache is cold every launch**);
`scripts/prop/breakout_login_check.py` is launched the same way. The timers: the feed tick at `*:02/5`
(`deploy/ict-prop-feed.timer`) and the executor tick at `*:04/5` (`deploy/opt-in/ict-prop-executor.timer`), so **two
launches per five minutes = 576 launches a day** if both run.

| scenario | per launch | per day | per 30 days |
|---|---|---|---|
| as coded today: cold cache, feed + executor | ≈ 7.5 MB | **≈ 4.3 GB** | **≈ 130 GB** |
| cold cache, executor only | ≈ 7.5 MB | ≈ 2.2 GB | ≈ 65 GB |
| persistent browser cache, feed + executor (*assumption: ~0.5 MB per launch after the first; not measured*) | ≈ 0.5 MB | ≈ 0.29 GB | ≈ 8.6 GB |

The stream adds a further amount per tick: **not measured**, *estimate* tens to a few hundred KB per tick
(a small watchlist for ~30–60 s); small against the page assets either way.

**What that means on a mobile plan:** I have no verified plan details for the operator's carrier. Against any plan,
**~130 GB/month is not plausible** on cellular, and even the cached case (~9 GB) is a real, continuous draw. **The
adapter would first need a persistent browser profile (or fewer launches)**, which is a code change on the order-path
adapter (Tier-2) and is needed for **any** metered link, the phone above all.

## 3. Could the existing Android app host the egress?

**Found:** `benbaichmankass/ict-trader-android` (private), read this session at `df9910d4`, 2026-08-13.
- **Status: ON ICE.** Its `CLAUDE.md` says *"Do not open speculative work here… Reverse this only on an explicit operator
  instruction"*. The operator's message is a scoping question, not that instruction. **No work in that repo is proposed
  without one.**
- **What it is:** 67 Kotlin files, Jetpack Compose + Glance widgets, FCM push, WorkManager refresh; `minSdk 26`, `targetSdk
  35`. Manifest permissions are only `INTERNET` and `POST_NOTIFICATIONS`. It has **no foreground service, no boot
  receiver, no VPN service and no SSH/WireGuard library**.
- **What hosting an egress would take:** a foreground service (new permissions `FOREGROUND_SERVICE` and a
  service-type permission, `RECEIVE_BOOT_COMPLETED`, a battery-optimisation exemption request), an SSH client library
  and, **importantly, a SOCKS5 handler for the reverse channel**. Reverse *dynamic* forwarding (`ssh -R <port>` with no
  destination) is done by the **OpenSSH client**; a custom app on a library such as sshj would have to implement the
  SOCKS decoding itself (*from memory, unverified*). Plus a settings screen, a health/heartbeat report and a build
  and distribution path (it ships as Firebase App Distribution debug builds). Effort: **~4–6 lane-days** on top of the
  common work (§ 6), *estimate*.
- **A stock tool is simpler and more reliable:** **Termux** with OpenSSH (`ssh -N -R 1080 …`), `termux-wake-lock` and
  Termux:Boot, from the F-Droid build (the Play Store build is unmaintained; *from memory, unverified*). No app build,
  no repo unfrozen, the same command as the home box.

**A correction to the WireGuard idea:** a WireGuard client on a phone sends **the phone's** traffic out **through the VM**
(the opposite direction). Making the *VM's* traffic exit *through* the phone would need the phone to forward packets,
which stock Android does not do without root. So WireGuard, as a plain VPN client, is **not** a way to make the phone an
egress. It also needs an inbound UDP port opened on the live VM. **Do not pursue it for this.** SSH reverse SOCKS is
the workable transport (outbound-only from the phone).

## 4. Android reality (what breaks a long-lived tunnel)

| issue | effect | control / honest limit |
|---|---|---|
| **Doze and App Standby** | background sockets are throttled when the screen is off and the phone is idle | a foreground service (with a persistent notification) or Termux's wake lock; the phone should stay on the charger |
| **OEM battery killers** | Samsung/Xiaomi/Huawei etc. kill background apps regardless of the stock rules | exempt the app from battery optimisation; per-OEM settings; **not verifiable without the operator's phone model** |
| **Foreground-service limits (Android 14/15)** | at `targetSdk 35`, some service types are time-limited (`dataSync` is capped per day); a type that is not time-limited must be chosen *(from memory, unverified)* | a custom-app concern; Termux is a user-installed app with its own foreground service |
| **Wi-Fi ↔ mobile handover** | the TCP connection drops on every switch | `ServerAliveInterval 15`, `ServerAliveCountMax 2`, `ExitOnForwardFailure yes` and an auto-reconnect loop; expect a ~30 s gap per switch, during which the executor refuses (pre-flight) |
| **Carrier CGNAT** | inbound is impossible | irrelevant: the tunnel is outbound-only |
| **IPv6-only carriers** | the phone reaches an IPv4-only VM via the carrier's NAT64/CLAT | usually transparent; **unverified** for this carrier |
| **Battery drain** | a keep-alive holds the cellular radio active | plugged in; battery health is the operator's cost |
| **The phone moves** | it follows the operator between cities, networks and roaming | **the biggest problem**, next section |

**Address churn (the real risk of a daily phone).** A mobile carrier's shared CGNAT address changes on reconnects and
handovers, and its **geography follows the operator**. Breakout would see the same account logged in from a source that
keeps changing, which can drop the terminal session or trip its fraud checks and reset Cloudflare's clearance. Each
drop forces a **fresh login**, which the design deliberately rations (`ict-prop-feed`'s relogin ceiling, and the terminal
has `email_code`/`2fa` states). A home box's address is far more stable. A **dedicated spare phone left on home Wi-Fi**
would be stable, but then it is a worse home box (no wired link) whose one advantage is a SIM for the case where home
broadband or power is down.

## 5. Cloudflare on a mobile address

**Unmeasured.** Reasonable expectations, stated as such: mobile-carrier ranges are consumer ISP ASNs, not datacenter
ranges, and are unlikely to be on an **ASN** ban list like Oracle's; a plain non-browser client may still get a managed
challenge (as this session's Google-ASN sandbox did); a real headless Chromium may be scored differently again. Shared
CGNAT addresses carry other users' history, which can raise or lower the score. None of this is a measurement.

**Step zero for the phone (operator, ~1 minute, no credentials, nothing clicked).** Turn **Wi-Fi off** so it runs over
mobile data, then either:

- **Termux** (`pkg install curl` once): 
  ```
  curl -sS -m 25 -D - https://app.breakoutprop.com/ | grep -i -E '^HTTP|^cf-mitigated|error code|Just a moment|banned the autonomous'
  ```
  It prints only the status line, the mitigation header and any block-message lines (no address, cookie or account
  data). Safe to paste.
- **Phone browser, no install:** open `https://app.breakoutprop.com/` in Chrome on mobile data and report which of three
  things you see: a page saying **"Just a moment…"** (challenge), a page saying **"Access denied / Error 1005"**
  (banned), or the **Breakout login page** (served). Do not sign in.

Read the result with the table in part 3 § 4 (403 + `error code: 1005` = banned; 403 + `cf-mitigated: challenge` =
challenged; 200 = served). A `curl` result and a browser result can differ; report both if both are easy.

## 6. Comparison: L3 with the home box vs L3 with the phone

| | **L3, home box (Pi)** | **L3, phone (stock Termux)** | **L3, phone (custom app in the frozen repo)** |
|---|---|---|---|
| Uptime | as good as home power + broadband; wired, mains-powered | independent of home broadband/power (has a SIM), but dependent on charge, OEM killers, coverage, handovers | same as Termux, plus a service that can be tuned |
| Address stability (matters to the login ceiling) | high | **low if it is the daily phone**; better for a spare phone left at home | same |
| Data cost | none (home broadband) | **cellular: ~130 GB/month as coded, ~9 GB with a persistent browser cache** (§ 2.2; 7.5 MB measured cold on the login page) | same |
| Build effort (lane-days, *estimate*) | 2.5–3 (part 3 § 2) | 2.5–3 **plus** a persistent-cache change (~0.5–1) and a phone runbook (~0.25) | as Termux **plus** ~4–6 for the app; needs the freeze reversed by an explicit instruction |
| Operator steps | 5, ~45–60 min | ~6–8 (install Termux, keys, boot script, exemptions, charger), ~60–90 min; OEM settings are the unknown | install a debug build, permissions, exemptions |
| Failure modes | link/power outage; ISP address change | all of the left column's plus Doze, OEM kill, handover drops, address churn, battery, data exhaustion | same |
| Secrets on the device | an SSH key only | an SSH key only | an SSH key in the app's storage |
| Leak controls (§ 1) | same | same | same, in the app's own code (more to test) |

## 7. Recommendation and decision

**Recommend: the home box (L3) as the primary egress; the phone as an optional failover later, using stock Termux, not
the custom app.** Reasons: address stability (the login ceiling), no metered data, no Android background fragility, and no
need to unfreeze a repo the operator parked. The phone's real advantage, surviving a home broadband or power cut, is
better bought as a *second* egress once the first works, and only after the persistent browser cache exists.

**Decision for the operator (updates part 3 § 6):**
1. **Run the step-zero command at home (part 3 § 4) and, if easy, the phone variant (§ 5), and paste both results.**
   Recommended: $0, no build; it also tells us whether a mobile address is banned, challenged or served.
2. **Authorise the L3 build with the home box as primary (~2.5–3 lane-days)** once step zero is a 200 or a challenge,
   with the § 1 leak controls in scope and a persistent-cache change in the same lane.
3. **Choose the phone as primary anyway** (stock Termux, ~3.5–4 lane-days) and accept address churn and the cellular data
   cost; requires the persistent-cache change first.
4. **Unfreeze `ict-trader-android` for a custom egress service** (~4–6 further lane-days). Not recommended: it needs an explicit
   instruction to reverse the freeze and buys nothing that Termux does not.

## 8. Not verified

The proprietary terminal's page weight, the post-login DXtrade weight and the live-stream volume (only the DXtrade login
page was measured, statically); the operator's carrier, plan, data allowance and phone model; whether either a mobile or a
home address is banned, challenged or served; Chromium's exact behaviour with the flags in § 1 (to be tested in the build);
the Android foreground-service rules and Termux's current distribution (from memory); whether `ssh -R` dynamic forwarding
is allowed by the live VM's `sshd` as configured; all effort figures; and Breakout's first-party terms.
