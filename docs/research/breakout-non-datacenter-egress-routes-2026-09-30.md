# Breakout terminal — routes through a non-datacenter address, for a non-technical operator, 2026-09-30

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Checklist row **PROP-TERM**; pipeline **PI-20260929-FRJ7NMPU-0001**. Follows [the VPN/cloud decision page](breakout-vpn-cloud-decision-2026-09-30.md). **SCOPE ONLY: nothing is bought, built or routed.** The live VM's address is not written here. Prices were read from vendor pages on 2026-09-30.

## 0. What the operator's check changed

Operator, verbatim: **"Loads on both"**: `app.breakoutprop.com` reaches the terminal login from **home Wi-Fi and from mobile data**. Measured earlier: the Oracle VMs are **blocked** (Error 1005), a GitHub/Azure runner and a Google-ASN sandbox are **challenged**. So the pattern is *datacenter addresses stopped, ordinary consumer addresses served*. Two limits: one browser visit each, and a person's browser is not our headless Chromium (§ 3 tests that before any money moves).

**Account risk, plainly:** every route below still uses a different address to reach a site that blocks our current one. It may breach Breakout's terms and put the funded account at risk (§ 5).

## 1. The routes

| | **R1. Managed static ISP proxy** | **R2. A device you already own, as the exit (Tailscale)** | **R3. Mobile-proxy service** |
|---|---|---|---|
| What it is | You rent one fixed "ISP" address (registered to a consumer ISP) that only you use | Your own home device (Apple TV, spare Mac/PC, or an Android phone left at home) relays our browser's traffic out through your home connection | Rent a real phone-carrier address |
| Monthly cost | IPRoyal from **$2.70/proxy/30 days** (24-hour test **$1.80**), dedicated, US pool listed ([IPRoyal](https://iproyal.com/isp-proxies/)); Decodo dedicated ISP **3 IPs for $9.99/mo**, 3-day free trial (100 MB), 14-day money-back ([Decodo](https://decodo.com/proxies/isp-proxies)) | **$0**: Tailscale Personal is free, up to 6 users, unlimited devices ([Tailscale](https://tailscale.com/pricing)); the device you already own | IPRoyal dedicated mobile from **$130/mo** (30-day), rotating **$5.20–6.80/GB** ([IPRoyal](https://iproyal.com/mobile-proxies/)) |
| **You click** (about 10–15 min) | 1. Make an account with the provider. 2. Buy **one** US static ISP proxy for the 24-hour test. 3. In GitHub (repo **Settings**, labels from memory, not checked): **Environments → New environment**, name `egress-probe`; under **Deployment branches and tags** choose **Selected branches and tags** and add `main`; then **Add environment secret**, name `EGRESS_PROBE_PROXY`, paste the proxy line the provider's dashboard shows (an ENVIRONMENT secret, not a repository secret; the probe workflow reads it only from that environment, and only on `main`). 4. Tell the manager "done". | About 20–30 min. 1. Install Tailscale on that home device and sign in (free account). 2. In the app choose to **run as an exit node** ([Tailscale docs](https://tailscale.com/kb/1103/exit-nodes): Linux, macOS, Windows, Android, iOS and Apple TV can). 3. In the Tailscale admin website: **Machines →** that device **→ route settings → "Use as exit node"** (admin approval is required). 4. Make an auth key (menu path **Settings → Keys**, from memory, not checked) and paste it into a GitHub secret the same way as R1. | As R1, but dearer |
| **We build** (estimates) | A proxy setting at the 3 places our code starts the Breakout browser, fail-closed; the secret to the VM by the existing `set-env` action; an exit check; a persistent browser cache; the emailed-code hand-off (see the decision page). **~2–3 lane-days, Tier 2 on the live VM** | The same proxy/cache/code hand-off pieces, plus Tailscale on the VM so only the browser uses the exit. Whether the VM can use an exit node in the mode that gives a local proxy is **not stated** in Tailscale's userspace docs ([kb 1112](https://tailscale.com/kb/1112/userspace-networking)); if not, the browser runs in an isolated network namespace. **~2.5–3.5 lane-days, Tier 2** | As R1 |
| Failure modes | ISP addresses are often *hosted in datacenters* (general knowledge, not verified for these providers), so Cloudflare may still challenge one (**that is exactly what the probe tests**); provider closes the account under its terms; provider is a third party who sees our traffic's destinations | Home power/internet cut; the device sleeps or is switched off; your ISP changes your address (session then re-logs in); tunnel key expiry | Address rotates, which drops the terminal's login session each time; 30 GB/day cap per dedicated proxy; the price |
| Provider terms | **IPRoyal** AUP §3.2: *"You must not circumvent or attempt to circumvent access controls, robots.txt directives, API limitations, technical rate limits, or other contractual or technical restrictions imposed by third-party platforms or websites."* §3.3: *"When targeting third-party websites or platforms, you must respect the platform's terms and applicable policies."* No clause bars trading sites ([AUP](https://iproyal.com/acceptable-use-policy/)). **Decodo** *"restrict[s] access to the majority of targets"* including *"Banking & other financial activities"*; whether a prop-firm terminal is on that list, or can be allowed, is **not stated** ([Decodo](https://decodo.com/proxies/ethical-residential-proxy-sourcing-and-usage)) | Tailscale's own terms **not read**. No third-party proxy AUP applies; the address is your own | Same providers, same AUPs |

**Not recommended: R3.** It is the dearest, and a rotating address is the worst fit for a login the terminal binds to a session. It is here for comparison only.

## 2. What Breakout's own rules say (first-party FAQ, read today)

I read all **108** articles in Breakout's FAQ Center (5 collections). A keyword scan for *bot, EA, algorithm, API, automated trading* found **no article that uses them**, so the FAQ is **silent on automation**. That is a scan of the FAQ, not of the agreements. What it does say:

- **VPN (funded):** *"Yes, you can use a VPN while trading your funded account - as long as it's not used to conceal or misrepresent your jurisdiction, or to evade any law or regulation covered by your Funded Trader Agreement."* ([source](https://intercom.help/breakoutprop/en/articles/11647245-can-i-use-a-vpn-while-trading-with-payward-oceanic-ltd-pol)) **VPN (evaluation):** the same, *"…otherwise evade laws and regulations covered by your Breakout Evaluation Agreement"* ([source](https://intercom.help/breakoutprop/en/articles/11644115-can-i-use-a-vpn-while-trading-with-breakout)). Neither mentions proxies.
- **Evaluation stage only, prohibited:** *"Sharing account access, or trading multiple accounts from the same household, device, or IP address."* ([source](https://intercom.help/breakoutprop/en/articles/11644090-what-trading-practices-are-prohibited-during-the-breakout-evaluation)) **This bears on the choice**: use a **dedicated** static address that only our one account uses (R1 dedicated, or R2), never a shared or rotating pool, and do not also log in to a second account from the same address. The funded-account list does not repeat this line ([source](https://intercom.help/breakoutprop/en/articles/11647201-what-trading-practices-are-prohibited-in-my-funded-account)).
- **Breach:** *"You engage in Prohibited Trading (defined in your Funded Trader Agreement). You violate or fail to satisfy any other term of your Funded Trader Agreement."* ([source](https://intercom.help/breakoutprop/en/articles/11647199-what-constitutes-a-breach-in-a-funded-account)) The prohibited list also includes *"Trading in a way that… jeopardizes its relationship with an exchange or market maker"*, at Breakout's discretion.

**Unread and governing:** the Funded Trader Agreement and the Breakout Evaluation Agreement. `www.breakoutprop.com` terms pages are Cloudflare-challenged to this session. The operator's own copy of either agreement would settle it.

## 3. How we check before any money moves (read-only)

The measurement tool already exists on `main`: `scripts/research/egress_chromium_landing_probe.py` and its workflow (PR #14608). Landing only: no login, nothing typed or clicked, no trading credential anywhere. To test a route it needs one small **Tier-1** extension (~0.5 lane-day, not built): an optional proxy setting read from a GitHub secret, and, for R2, a step that joins the runner to the Tailscale network and selects the home device as its exit.

**Pass, for `app.breakoutprop.com`, on three runs at least an hour apart:** HTTP `200`, `cf-mitigated` empty, page title not "Just a moment…", **login form rendered**, and the egress organisation identical across runs (the address is never printed). A single challenge, a `403`, or an organisation that changes ends that route.

**The cheapest real test is R1's 24-hour proxy (about $1.80–$2.70) or Decodo's free 100 MB trial**, which is plenty for a landing page. It also tests the provider terms in practice: if Decodo blocks the domain, that is the answer. After a build, the same check is repeated from the live VM with the existing `breakout-terminal-probe` (landing mode) through the route.

## 4. Recommendation

**Test R1 first, with IPRoyal's 24-hour static ISP proxy.** Reasons: it needs no device at home, it is the fewest clicks, it costs about $2 to learn the one thing that matters (does an ISP-registered address get the login page from headless Chromium), and it does not depend on your home staying powered. **Fall back to R2** if that test is challenged or the provider closes the account: R2 uses an address already shown to load, costs $0 and avoids a third-party proxy policy, at the price of a device you keep on and a less certain build. R3 only if both fail.

**Decision for the operator (pick one):**
1. **Approve the R1 test** (about $2, 10–15 min of your time, the manager prepares the probe extension). Recommended.
2. **Approve the R2 test instead** (free, 20–30 min of your time, needs a device that stays on).
3. **Both tests**, and choose on the results.
4. **Stop the workarounds** and ask Breakout to allow our server.

## 5. Not verified

Every build size; whether an ISP-registered address passes Cloudflare in headless Chromium (the test decides); whether Tailscale supports an exit node in the VM's proxy mode; Tailscale's terms; Decodo's target list for `breakoutprop.com`; IPRoyal's bandwidth terms for ISP proxies (the page prices per address, not per GB); mobile-proxy behaviour at Breakout; the Funded Trader Agreement and the Evaluation Agreement; and the FAQ's own remark that DXTrade accounts live at `app.breakoutprop.com`, which conflicts with this repo's reading of that host.
