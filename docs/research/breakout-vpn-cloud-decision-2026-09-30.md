# Breakout terminal — VPN, another cloud, or a residential address? A decision page, 2026-09-30

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Checklist row **PROP-TERM**; pipeline **PI-20260929-FRJ7NMPU-0001**. Written for a non-technical reader. Operator, verbatim: *"…I want us to look at other cloud hosts - what might be able to do there so that we don't get blocked on the ip address? Can we just layer a vpn over the current vm?"*
> **SCOPE ONLY: nothing is bought, built or routed.** The live VM's address is not written in this file.

## 1. The short answer

**A VPN over the current VM, or a second cloud server, will probably still be stopped, just by a different screen.** We measured this today instead of guessing (§ 2): the two non-Oracle *datacenter* addresses we could test both got a Cloudflare "prove you're a browser" challenge at Breakout's dashboard, not the login page. VPN exits and rented cloud servers are also datacenter addresses. What has *not* been tested is a **home or mobile address**, and that costs you nothing to test (§ 4, step 1).

**Account risk, plainly:** every option here still routes around Breakout's block on our current address. It may breach Breakout's terms and put the funded account at risk (§ 6).

## 2. What we measured (free, no credentials, nothing clicked)

A real headless Chromium, one page load per address, run 2026-09-30 from a GitHub-hosted runner (Microsoft, AS8075; [PR #14608](https://github.com/benbaichmankass/Metis-Insights/pull/14608), run 36683865998):

| address tested | `app.breakoutprop.com` (the new terminal's dashboard) | `wss.breakoutprop.com` (the DXtrade terminal we already use) |
|---|---|---|
| Oracle live VM and trainer VM (earlier) | **blocked**, Cloudflare Error 1005 | works |
| GitHub runner, real Chromium (**new**) | **challenged**: HTTP 403, `cf-mitigated: challenge`, page titled "Just a moment…", no login form | **served**: HTTP 200, "Breakout Terminal", login form rendered |
| This session's cloud sandbox, plain `curl` (earlier) | **challenged** (same header) | served |
| Your home or mobile address | **not tested** | not tested |

**Limits:** the browser waited 5 seconds after the page began loading, so a slower challenge might still have cleared; one address per type; a `curl` and a browser can differ. Our adapter treats a challenge as a stop and does not try to solve it (the standing "no anti-detection" rule), so a challenge blocks us in practice.

## 3. The three options, side by side

| | **A. VPN over the current VM** | **B. A second cloud server** | **C. A paid residential-address service** |
|---|---|---|---|
| What you do | Buy a subscription; paste its login into GitHub **yourself** (never into a chat) | Make an account with a cloud company, add a card, make one access key, paste it into GitHub yourself | Buy a plan; paste its login into GitHub yourself |
| Cost | Mullvad **€5/mo** ([pricing](https://mullvad.net/en/pricing)); NordVPN Basic **$3.49/mo** intro (27 months, $94.23), then **$139.08/yr** ([pricing](https://nordvpn.com/pricing/)); PIA **$11.95/mo** or **$3.99/mo** on an annual plan ([pricing](https://www.privateinternetaccess.com/pages/buy-a-vpn)). A fixed-address add-on exists at PIA and NordVPN; **I did not read its price** | DigitalOcean **$4/mo** (512 MiB) or **$6/mo** (1 GiB, what a browser wants) ([pricing](https://www.digitalocean.com/pricing/droplets)); AWS Lightsail $5/mo | Decodo **10 GB $35/mo**, 25 GB $81.25, 100 GB $275; a 3-day, 100 MB free trial ([pricing](https://decodo.com/proxies/residential-proxies/pricing)) |
| Kind of address | Datacenter (a VPN company's rented servers; *general knowledge, not verified per provider*) | Datacenter | **Residential** (someone's home connection) |
| Likely outcome | Probably **challenged** like the runner (§ 2) | Probably **challenged** like the runner (§ 2) | **Unknown**, but the only one aimed at looking like a home address |
| Our build | ~1.5–2 lane-days, **Tier 2 on the live VM** | ~4–6 lane-days (a second machine, plus the one-time login below) | ~1.5–2 lane-days, **Tier 2 on the live VM** |
| Ongoing | the subscription | the server (~$6/mo) and looking after it | ~$35/mo, and the browser must stop re-downloading 7.5 MB per run (data: part 4 of this series) |

*(Build sizes are estimates, not measurements.)*

**How A and C stay contained.** Only the Breakout browser's page traffic goes through the outside address, by giving the three places our code starts the browser an explicit proxy setting (`prop_executor_tick.py`, `breakout_login_check.py`, `breakout_terminal_probe.py`). It fails closed: if the proxy is down, the browser does not load; it never falls back to the direct route. Bybit, Alpaca, our own API and the git sync never start that browser and are never pointed at a proxy, so nothing else is rerouted. We do **not** turn on a VPN for the whole machine. Two provider notes: Mullvad's SOCKS proxy only works from inside a running Mullvad VPN connection ([Mullvad](https://mullvad.net/en/help/socks5-proxy)), which on the VM means isolating that connection so the rest of the machine is untouched; NordVPN offers standalone SOCKS5 servers with separate service credentials in the US, Netherlands and Sweden ([NordVPN](https://support.nordvpn.com/hc/en-us/articles/20195967385745-What-is-a-SOCKS5-proxy-and-how-do-I-use-it-with-NordVPN)), which is simpler.

**The "log in once" step on a new address (B, and to a lesser extent A/C).** A new address can make the terminal ask for an emailed code. **Today there is no way to hand a code to the login**: the code only recognises that one is being asked for (`email_code`, `breakout_terminal.py`) and stops. So we would need to add a small way for you to reply with the code (for example a Telegram message), roughly half a day to a day of build (estimate). For you it would be: receive an email, forward the six digits. Nothing to install.

**Cheapest way to set B up, if it were chosen:** DigitalOcean's 1 GiB plan, with the machine created by a script we run, using the same access key we already use for the other servers. Your part: sign up, add a card, create one access token, paste it into GitHub. About 15 minutes (estimate). Not recommended first, because § 2 suggests it would be challenged too.

## 4. What to do, in order

1. **Free, 30 seconds, no technical steps:** on your laptop at home, open `https://app.breakoutprop.com/` in a normal browser; then on your phone using **mobile data** (Wi-Fi off), open the same address. Tell me, for each, which of three screens you saw: **"Just a moment…"** (challenge), **"Access denied / Error 1005"** (blocked), or the **Breakout login page** (served). Do not sign in.
2. **If either shows the login page**, a home-type address works, and **option C** is the lowest-effort way to get one without hardware or a machine at home. Before paying, the free 100 MB trial is enough to measure the same landing page through that service, using a login you paste into GitHub yourself.
3. **If both show a challenge or a block**, no address change is likely to help; ask Breakout (part 1, option a).

## 5. Recommendation and the decision for you

**Do step 1 now; buy nothing yet.** If a home or mobile address is served, try **C**, starting with the free trial. Skip **A** and **B** unless a measurement shows a datacenter address is served: both cost more effort and, on the evidence so far, land on the challenge screen.

**Decision (pick one):**
1. **Do step 1 (the two browser checks) and report back.** Recommended: $0, no build.
2. **Also start the free Decodo trial** to measure a residential address from GitHub's side, without waiting on step 1.
3. **Go straight to a VPN (A)** and accept the likely challenge, ~$4–12/mo plus a 1.5–2 day Tier-2 build.
4. **Ask Breakout to allow our server**, and pause every workaround.

## 6. Terms (read before choosing any option)

Breakout's own FAQ, read today: *"Yes, you can use a VPN while trading your funded account - as long as it's not used to conceal or misrepresent your jurisdiction, or to evade any law or regulation covered by your Funded Trader Agreement."* ([source](https://intercom.help/breakoutprop/en/articles/11647245-can-i-use-a-vpn-while-trading-with-payward-oceanic-ltd-pol)). So an exit in **your own country** is what the wording allows; an exit elsewhere is the part it warns about. It says nothing about routing around a site block, and nothing about automation. **The Funded Trader Agreement, which governs, has not been read.** See part 5 of this series for the other quoted FAQ text.

## 7. Not verified

Any VPN provider's exit behaviour at Breakout (only a GitHub runner and a sandbox were tested); the fixed-address add-on prices at PIA and NordVPN; whether a longer wait or a different browser setting clears the challenge (deliberately not tried); Vultr and Hetzner prices; every build size; a home or mobile address; the Funded Trader Agreement; and the FAQ's own remark that DXTrade accounts live at `app.breakoutprop.com`, which conflicts with this repo's reading of that host.
