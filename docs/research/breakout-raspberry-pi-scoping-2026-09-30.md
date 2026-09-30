# Breakout terminal egress — Raspberry Pi "home box" option, scoped, 2026-09-30

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Checklist row **PROP-TERM**; pipeline **PI-20260929-FRJ7NMPU-0001**. Part 5 of the
> [egress scoping series](breakout-local-agent-requirements-2026-09-30.md) (this is the L3 "home box" of part 3).
> Operator, verbatim: *"I want a short report on scoping the raspberry pi option - what would I need to procure, costs, benefits, etc."*
> **SCOPE ONLY: nothing is bought, built, deployed or routed.** Prices were read from vendor pages on **2026-09-30**;
> the live VM's address is deliberately not written in this document.

## 0. Update 2026-09-30 ~16:00Z: what happened since this was written, and the operator's decision

- **The $2 proxy test ran and FAILED for that proxy.** Run 36731753995 (14:47Z, `workflow_dispatch` on `main`): `via_proxy=yes`, `proxy_state=ok`,
  egress organisation **AS42049 Nadejda.Net Ltd**; `app.breakoutprop.com` **403, `cf-mitigated: challenge`, "Just a moment...", login form not rendered**;
  `wss.breakoutprop.com` 200, "Breakout Terminal", login form rendered; `--evaluate` verdict **FAIL, route ended.** (Read from the manager's relay of the run log, not re-read here.)
- **What that does and does not establish.** It shows one rented ISP proxy address is challenged at `app.breakoutprop.com` by a headless Chromium. It does **not** show
  the operator's *home* address would be: the operator's own browser loaded the login on home Wi-Fi and on mobile data (one visit each). Two things differ between that
  browser and the probe, and one run cannot separate them: **the address's organisation** and **the browser** (headless Chromium vs a person's browser). The next test
  therefore has to be headless Chromium **from the home address**: that is what separates them.
- **Operator decision (popup, verbatim): "Home device (R2)."** So the Pi in this memo is now the leading candidate device, not a purchase to make blind.
- **The tunnel design changed.** § 4's SSH reverse SOCKS (`ssh -N -R 1080`) was the L3 design here; the R2 plan uses **Tailscale exit-node routing instead** and adds a
  **cheaper first test with a device the operator already owns.** The shopping list, phone-followable steps, the Tier-2 route design, the probe extension and the
  terms risk are in [breakout-r2-home-device-plan-2026-09-30.md](breakout-r2-home-device-plan-2026-09-30.md). Where this memo and that one differ on the tunnel, that one wins.
- **The rest of this memo stands** (BOM and prices as of the morning of 2026-09-30; § 5 alternatives; § 7 unverified list), except that the "step zero" curl in § 1 has
  effectively been superseded by the operator's own two browser loads plus the proxy result above.

## 1. Bottom line

**Do not buy yet. Run the $0 step-zero command from the home network first (part 3 § 4).** If it returns `200` or a Cloudflare
*challenge* (not `error code: 1005`), buy the **~$155 cart in § 3** (Pi 5 **4 GB**, not 8 GB). If it returns 1005, a Pi at that address is
worthless and the money is saved. Under the recommended L3 design the Pi runs **only an SSH client**; the browser, credentials and
adapter stay on the live VM.

**Account risk, stated plainly:** this still routes around Breakout's ban on our datacenter address. It is a different address used to
reach a site that blocks our current one, and it may breach Breakout's terms and put the funded account at risk (§ 5).

## 2. What the Pi must run (derived from the code)

- **L3 (recommended): only OpenSSH.** Every Playwright/Chromium launch is on the VM: `scripts/prop/prop_executor_tick.py`,
  `scripts/prop/breakout_login_check.py`, `scripts/prop/breakout_terminal_probe.py` (`chromium.launch(headless=True)`). The Pi
  dials **out** to the VM and offers a SOCKS exit (`ssh -N -R 1080 …`, part 3 § 2). Needs: any 64-bit Linux, `ssh`, systemd for
  auto-restart; RAM, CPU and storage are trivial (well under 512 MB and 4 GB).
- **L1 fallback (Chromium on the Pi):** would need what the VM scripts need: Python venv, Playwright, Chromium, the repo. **Measured this
  session** on an x86-64 sandbox, headless Chromium PSS memory (shared pages counted once): **≈256 MB blank, ≈326 MB idle and ≈337 MB
  peak on DXtrade's real login page** (its 15 assets served from a local copy). A naive RSS sum read 891 MB and overstates. That is the
  **login page only**; the post-login terminal (charts, tables) is larger and is **not measured**. Disk on this machine: Chromium
  ≈600 MB, repo checkout ≈370 MB. Size for **≥ 2 GB free RAM** at the terminal.
- **arm64 support: yes.** Playwright's system requirements list *"Debian 12 / 13, Ubuntu 22.04 / 24.04 / 26.04 (x86-64 or arm64)"*
  ([playwright.dev/docs/intro](https://playwright.dev/docs/intro#system-requirements)). Stronger, the production evidence: the live VM
  is an **Ampere aarch64** instance ([`docs/reference/vm-topology.md`](../reference/vm-topology.md)) and runs this same Playwright/Chromium
  executor path; the checklist row PROP-TERM/PROP-EXEC records executor ticks exiting `0` there (2026-09-29).
- **Relay to/from the VM:** L3 carries the browser's traffic (measured cold load of DXtrade's login page ≈ 7.5 MB, ~130 GB/month as coded,
  ~9 GB with a persistent browser cache, part 4 § 2). Home broadband makes that free; it is the reason not to use a metered link.

## 3. Bill of materials (US, checked 2026-09-30; before tax and shipping)

| item | choice | price | source |
|---|---|---|---|
| Board | **Raspberry Pi 5, 4 GB** (in stock) | **$110** | [PiShop US](https://www.pishop.us/product/raspberry-pi-5-4gb/) (Adafruit lists $130: [5812](https://www.adafruit.com/product/5812)) |
| Power | Official 27 W USB-C PSU | $12.95 | [PiShop US](https://www.pishop.us/product/raspberry-pi-27w-usb-c-power-supply-white-us/) |
| Case + fan | Official Pi 5 case with fan (active cooling included) | $12.00 | [Adafruit 5816](https://www.adafruit.com/product/5816) (alt: Active Cooler alone $10.95, [PiShop](https://www.pishop.us/product/raspberry-pi-active-cooler/)) |
| Storage | 32 GB microSD, A2/V30 (**out of stock at PiShop**; any equivalent high-endurance card) | $19.95 | [PiShop US](https://www.pishop.us/product/raspberry-pi-sd-card-32gb/) |
| Network | Wired Ethernet (existing patch cable; Wi-Fi is built in as a fallback) | ~$0 | not priced |
| **One-off total** | | **≈ $155** | 110 + 12.95 + 12.00 + 19.95 = 154.90 |

**Why 4 GB, not 8 GB:** the recommended L3 role needs almost nothing; 4 GB covers the L1 fallback (Chromium at ≈330 MB on the login page,
larger logged in), and 8 GB is **+$65** ($175 vs $110, [PiShop 8 GB](https://www.pishop.us/product/raspberry-pi-5-8gb/)) for headroom nothing here needs.
**Cheaper if L3 only:** Pi 4 2 GB is $55 and in stock ([PiShop](https://www.pishop.us/product/raspberry-pi-4-model-b-2gb/)); Pi 5 2 GB $65 and Zero 2 W $17.25
are out of stock ([2 GB](https://www.pishop.us/product/raspberry-pi-5-2gb/), [Zero 2 W](https://www.pishop.us/product/raspberry-pi-zero-2-w/)); I did **not** price a Pi 4 PSU.
The 4 GB buys the fallback if SOCKS-tunnelled WebSockets misbehave (unmeasured).

**Optional, not in the cart:**
- **NVMe (M.2 HAT+ $17.40, [Adafruit 5902](https://www.adafruit.com/product/5902), + 256 GB SSD ≈ $75, out of stock at both [Adafruit 6090](https://www.adafruit.com/product/6090) and [PiShop](https://www.pishop.us/product/raspberry-pi-nvme-ssd-256gb/)) ≈ $93.**
  SD-card wear is a real failure mode for a constantly-writing browser profile, but in L3 the Pi barely writes; skip it. Buy it only if L1 is used.
- **UPS HAT:** not priced (not verified). A Pi UPS is pointless if the router dies; better one small UPS for router + Pi, price not checked.

**Running cost.** Idle draw quoted by community measurements: Pi 5 ≈ 2.2–4.84 W ([Raspberry Pi forum](https://forums.raspberrypi.com/viewtopic.php?t=360658),
[Tom's Hardware review](https://www.tomshardware.com/reviews/raspberry-pi-5); from search-result summaries, pages not opened). At **18.31 ¢/kWh**
(US residential average, July 2026, [EIA](https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a)): 24 h × 365 × 4 W = 35 kWh ≈ **$6.4/year**
(range 2.6 W → $4.2, 4.84 W → $7.8). Your own tariff may differ.

## 4. Setup the operator does physically, and what we automate

1. Order the cart (after step zero).
2. Flash the microSD with Raspberry Pi Imager: **Raspberry Pi OS Lite 64-bit**, set hostname, user, Wi-Fi/SSH key in the Imager's customisation screen.
3. Insert the card, fit the case, plug in **Ethernet + power**.
4. Send us the box's **public** key (one line). No inbound router port, no static IP.
5. Run **one bootstrap command** we supply, which installs the tunnel service and starts it.

We automate: the bootstrap script and `systemd` unit (`Restart=always`, keep-alives), the **restricted tunnel account and key on the VM** (no shell,
one forwarded port; Tier-2), the adapter's fail-closed proxy option, the exit pre-flight, a heartbeat and Telegram alert, and updates via `git pull` on the VM
(nothing to update on the Pi). Build ≈ **2.5–3 lane-days** (estimate, part 3). **The VM's address stays private:** the Pi needs it to dial out, so it is
entered on the Pi during step 5 and **never committed or logged**; the tunnel uses outbound SSH only. Tailscale would also avoid a router port, but adds a
third-party coordination service and, to send only the terminal's traffic through the exit, more routing work; plain outbound SSH is simpler.

## 5. Benefits and risks against the alternatives

| | **Pi (L3)** | Phone (Termux) | Operator's PC | Paid residential proxy |
|---|---|---|---|---|
| One-off / running | ≈ $155 / ≈ $6 a year | $0 (spare phone) / cellular data | $0 / PC power | $0 / [Decodo](https://decodo.com/proxies/residential-proxies/pricing): 10 GB $35/mo; 100 GB $275/mo (this page, read today) |
| Residential IP | yes, your ISP | mobile CGNAT, shared, changes | yes, your ISP | yes, someone else's, shared/rotating |
| Always-on | high: wired, ~4 W, no sleep | medium: battery, Doze, OEM killers | low–medium: sleep, updates, restarts | high (vendor uptime not verified) |
| Home power / ISP outage | **exposed** (executor refuses; tickets expire; Telegram fallback) | independent of home | exposed, plus PC state | independent of home |
| IP churn | home address rarely changes; frequency **unmeasured** | high, follows the operator | same as Pi | depends on sticky-session terms (not verified) |
| Security | SSH key only; **credentials stay on the VM** | same | same for L3 (secrets move if L1) | a third party carries the traffic; credentials stay on the VM |
| Data | free (home broadband) | ~9–130 GB/mo | free | 130 GB/mo exceeds the largest listed plan; ~9 GB fits 10 GB |

**Firm-policy question (flagged risk, not decided).** Quoting Breakout's own FAQ (reachable via Intercom, read today):
- VPN article: *"Yes, you can use a VPN while trading your funded account - as long as it's not used to conceal or misrepresent your jurisdiction, or to evade any law or regulation covered by your Funded Trader Agreement."* ([source](https://intercom.help/breakoutprop/en/articles/11647245-can-i-use-a-vpn-while-trading-with-payward-oceanic-ltd-pol))
- Breach: *"You engage in Prohibited Trading (defined in your Funded Trader Agreement). You violate or fail to satisfy any other term of your Funded Trader Agreement."* ([source](https://intercom.help/breakoutprop/en/articles/11647199-what-constitutes-a-breach-in-a-funded-account))
- Prohibited practices list includes *"Using any trading or order-entry method expressly prohibited by a liquidity provider"* and *"Trading in a way that, in [the operator's] sole discretion, jeopardizes its relationship with an exchange or market maker"*; violations are *"assessed at Breakout's discretion and can result in account termination."* ([source](https://intercom.help/breakoutprop/en/articles/11647201-what-trading-practices-are-prohibited-in-my-funded-account))

What this does and does not establish: the FAQ has **no clause on automation and none on which address orders come from** other than the jurisdiction wording above. It says nothing about evading a site access block. **The Funded Trader Agreement, which governs, was not read** (not on the FAQ). A home address in the operator's own jurisdiction does not conceal it, but the ban on our datacenter address is an access control the FAQ neither permits nor forbids routing around.

## 6. Recommendation

**Don't buy yet; do step zero.** If it passes, buy the **≈ $155 cart** (Pi 5 4 GB + 27 W PSU + case/fan + 32 GB microSD, wired Ethernet). While it ships (a few days), and **only once the build is authorised**, build the fail-closed proxy option, the exit pre-flight, the restricted tunnel account, the bootstrap script and the alert, and test them against a laptop as the "home box". If the operator already owns an always-on Linux machine, **skip the purchase**: L3 needs only `ssh`.

## 7. Not verified

Any price after 2026-09-30 and shipping/tax; the Pi's power draw (from search summaries); stock at other vendors; the logged-in terminal's memory (only the login page was measured, on x86-64, not on a Pi); SOCKS-tunnelled WebSocket behaviour; the operator's ISP address stability; whether the home address is banned, challenged or served; the Funded Trader Agreement; and the FAQ's own remark that DXTrade accounts use `app.breakoutprop.com`, which conflicts with this repo's reading of that host as the proprietary-terminal dashboard and should be resolved before the build.
