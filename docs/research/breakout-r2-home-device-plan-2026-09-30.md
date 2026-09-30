# Breakout terminal egress — R2 "home device" plan, 2026-09-30

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Checklist row **PROP-TERM**; pipeline **PI-20260929-FRJ7NMPU-0001**. Part 6 of the
> [egress scoping series](breakout-local-agent-requirements-2026-09-30.md); follows the [Raspberry Pi memo](breakout-raspberry-pi-scoping-2026-09-30.md) (§ 0 there has the proxy result).
> Operator decision (popup, verbatim): **"Home device (R2)."** Manager brief 2026-09-30T15:54Z.
> **SCOPE AND DESIGN ONLY. Nothing is bought, installed, changed on the live VM or routed.** Every VM change below is **Tier 2** and waits for the manager's go and the operator's OK.
> The live VM's address is deliberately not written in this document.

## 1. Bottom line

- **Why R2 at all.** The one proxied probe (run 36731753995, 14:47Z) was **challenged at `app.breakoutprop.com`** from a rented ISP address (AS42049 Nadejda.Net Ltd), while the operator's own browser
  **loaded** it from home Wi-Fi and from mobile data. So the open question is no longer "is a non-datacenter address enough" but "**does headless Chromium from the operator's home address get through**".
  Only a probe from that address answers it, and it is cheap to run before anything is bought.
- **Recommended order, cheapest evidence first:**
  1. **Test with a device the operator already owns** (a spare Android phone on home Wi-Fi, or a PC that stays on). $0. Steps A below.
  2. **Only if that probe passes** (3 headless runs at least 1 h apart, same organisation): choose a permanent device: the **~$155 Raspberry Pi 5 kit** or keep the phone/PC.
  3. **Only then** the Tier-2 change on the live VM (§ 5), after the manager's go.
- **Account risk, stated plainly:** every route in this series still uses a different address to reach a site that blocks our current one, **and may breach Breakout's terms** (§ 7). The route also puts
  the automated account on **the operator's household address**, the same one their personal devices use.

## 2. What to buy or use (pick one; A is the test, the others are permanent)

| | **A. Spare Android phone** (test) | **B. Raspberry Pi 5 kit** | **C. PC that stays on** |
|---|---|---|---|
| Cost | **$0** (a phone you already have) | **≈ $155 one-off** (Pi 5 4 GB $110 + 27 W PSU $12.95 + case/fan $12.00 + 32 GB microSD $19.95; prices read from vendor pages this morning, see the [Pi memo § 3](breakout-raspberry-pi-scoping-2026-09-30.md)) + ≈ $6 a year power | $0 + PC power |
| Can the operator set it up **from a phone alone** | **Yes** | **No: flashing the SD card needs a computer once** (Raspberry Pi Imager); ask someone, or use a computer for that one step | No, needs the PC itself |
| Uptime | medium: battery, OEM background killers; keep it **plugged in** and exempt from battery optimisation | high: wired, ≈ 4 W, no sleep | low to medium: sleep, updates, restarts |
| Also needed | home Wi-Fi, a charger | **wired Ethernet to the router** (or Wi-Fi), a phone to do the account steps | home network |
| Tailscale exit node supported | Android is on Tailscale's list of exit-node platforms ([docs](https://tailscale.com/kb/1103/exit-nodes)) | Linux is | Windows / macOS are |

An old laptop also works as C. **Do not** use the operator's everyday phone as the exit node: on mobile data it would be the carrier's shared address and it moves with them (part 4 of this series).
**Tailscale itself: free "Personal" plan** (read from the vendor page earlier today; no card needed).

## 3. The operator's steps (numbered; every step is doable on a phone except 2b, which is Pi-only)

Button labels are from Tailscale's docs or from memory; **anything marked (unverified) was not checked** and may read slightly differently. **Never paste a key or the proxy line into chat**: keys go into GitHub only (steps 8–9).

1. **Make a Tailscale account.** On the phone open `login.tailscale.com` and sign in with an existing Google, Microsoft, Apple or GitHub account. Choose the free Personal plan. (unverified: the exact sign-up wording)
2. **Install the Tailscale app on the home device** (A: the spare Android phone from the Play Store; B: Raspberry Pi, see step 2b; C: the PC's installer from `tailscale.com/download`) and **sign in with the same account**.
   - **2b (Pi only):** on a computer, flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager, set a hostname and Wi-Fi/SSH key in its customisation screen, insert the card, plug in Ethernet and power. Then, over SSH, run the install line from `tailscale.com/download/linux` (the page shows it; we do not paste a piped installer here) and `sudo tailscale set --advertise-exit-node` ([docs](https://tailscale.com/kb/1103/exit-nodes)). Linux also needs IP forwarding enabled, the docs' commands are `echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf`, the same with `net.ipv6.conf.all.forwarding = 1`, then `sudo sysctl -p /etc/sysctl.d/99-tailscale.conf` ([docs](https://tailscale.com/kb/1019/subnets#enable-ip-forwarding)).
3. **Make the device an exit node.** Phone/PC: in the Tailscale app choose the exit-node menu and **"Run as exit node"** (unverified label). Do **not** turn on "allow local network access" for it.
4. **Approve it.** Open `login.tailscale.com/admin/machines`, find the device (it shows an **Exit Node** badge), open its **⋯** menu, **Edit route settings**, tick **Use as exit node** ([docs](https://tailscale.com/kb/1103/exit-nodes)).
5. **Stop it expiring.** In the same **⋯** menu choose **Disable key expiry** (unverified label). Without it the device silently leaves the network after its key expires and the route goes dark.
6. **Phone (A) only, keep it alive:** leave it **plugged in**, on the home Wi-Fi, Tailscale set to run in the background and **exempt from battery optimisation** (Android: Settings > Apps > Tailscale > Battery > Unrestricted; unverified wording). Do not swipe the app away.
7. **Tell the manager the device's name as it shows in the admin page** (a name, not a key). It is a non-secret value the workflow needs (§ 4).
8. **Make the test key** (for the CI probe only): admin console **Settings > Keys > Generate auth key** (unverified path), switch **Reusable** on and **Ephemeral** on, expiry the shortest you are offered that covers the test (a few days). **Copy it once**, and go to step 9 immediately.
9. **Save it as a GitHub secret, not in chat:** repo **Settings > Environments > `egress-probe` > Add environment secret**, name **`TS_AUTHKEY_PROBE`**, paste, save. (The environment was created for the last test and is restricted to `main`; it holds `EGRESS_PROBE_PROXY`, which can stay unused. Not re-checked today.)
10. **Add the exit-node name:** same page, **Add environment variable**, name **`EGRESS_EXIT_NODE`**, value = the name from step 7.
11. **Tell the manager "done".** The manager dispatches the probe. **No further phone steps until the result.**

**Only after a pass, and only when the manager asks:** a **second** key for the live VM, saved as **`TS_AUTHKEY_VM`** (a GitHub secret, same care), used by the Tier-2 step in § 5. **Not needed for the test.**

### GitHub names this route needs

| name | kind | where | what | secret? |
|---|---|---|---|---|
| `TS_AUTHKEY_PROBE` | environment secret | environment `egress-probe` (main only) | Tailscale auth key for the CI probe, ephemeral + reusable | **yes** |
| `EGRESS_EXIT_NODE` | environment variable | environment `egress-probe` | the home device's Tailscale name | no |
| `TS_AUTHKEY_VM` | secret | GitHub secrets, used by the Tier-2 VM action | Tailscale auth key for the live VM | **yes**; **only after a probe pass and the manager's go** |
| `EGRESS_PROBE_PROXY` | environment secret | already exists | unused by the route test (the route's proxy is a local address the workflow builds itself) | yes (existing) |

## 4. The route test (probe extension, Tier-1, separate held PR)

Goal: **headless Chromium, from the home address, against `app.breakoutprop.com`**, judged by the same PASS rule (3 passing runs at least 1 h apart, same known organisation, first failure ends the route).

- **Mechanism.** The CI job starts `tailscaled` in **userspace-networking** mode with a local SOCKS5 port (`tailscaled --tun=userspace-networking --socks5-server=localhost:1055`, [docs](https://tailscale.com/kb/1112/userspace-networking)), joins the tailnet with `TS_AUTHKEY_PROBE`, selects the home device as its exit node, and runs the existing probe with the proxy set to `socks5://127.0.0.1:1055`.
  The probe already accepts a credential-less SOCKS5 proxy (`scripts/research/egress_chromium_landing_probe.py`, `parse_proxy`), and Chromium can use it without authentication. Userspace mode leaves the runner's own routing untouched, so the artifact upload and the rest of the job do **not** go through the operator's home connection.
- **The one thing the docs do not say, and it decides whether this works:** whether traffic sent through the userspace SOCKS port actually **leaves through the exit node**. The pages read today are silent on it; a 2021 GitHub issue reporting that it did **not** ([#1970](https://github.com/tailscale/tailscale/issues/1970), v1.8.5) is closed via PR #3448, and I did not read the fix or find a version note. So it is **treated as unproven, and the probe proves it every run**:
  the workflow records the runner's own organisation, and the probe adds a **fail-closed `route_leak` state** if the organisation seen through the proxy equals it (the traffic never left through the home device). Then the verdict cannot read PASS by accident.
- **Fail-closed, like the proxy mode:** an unreachable SOCKS port, a device that is not the active exit node, or a `route_leak` reports its state and **never navigates to a Breakout host and never falls back to the direct route.** Nothing typed or clicked; landing only.
- **Never printed:** the auth key, the exit node's name and any tailnet address (masked like the proxy string is now). Tests prove it on every failure path, as for #14680.
- **Fallback if userspace exit does not work:** the same probe over a plain SOCKS proxy the home device offers on the tailnet, or the reverse-SSH SOCKS in the [Pi memo](breakout-raspberry-pi-scoping-2026-09-30.md) § 4 (part 3's L3). Not built unless needed.
- **If the runner cannot reach a home device at all**, that ends this test as `proxy_unreachable`; it does not say the home address is banned.

## 5. The route from the live VM (Tier 2, **proposed, nothing done**)

**Scope: only `app.breakoutprop.com`; everything else the VM does is untouched.** Three launch sites start Chromium for Breakout (`scripts/prop/prop_executor_tick.py:304`, `scripts/prop/breakout_login_check.py:272`, `scripts/prop/breakout_terminal_probe.py:213`, each `chromium.launch(headless=True)`); the Breakout adapters make no other web request to that host (`src/prop/platform/breakout_terminal.py`).

**What changes on the VM (each item is Tier 2):**

1. **A second, separate Tailscale daemon** in userspace-networking mode with its own state directory, its own control socket and a SOCKS5 port on `127.0.0.1` only (a new `systemd` unit, e.g. `tailscaled-egress.service`). It creates **no network interface, no route, no firewall rule and no DNS change** on the host, and the package's default `tailscaled` service stays disabled.
2. **One-time join** with `TS_AUTHKEY_VM` and the home device as exit node, delivered through the `system-actions` allowlist (a new action; the operator originates the key, nobody pastes it into chat).
3. **An opt-in env var** for the adapter (e.g. `BREAKOUT_EGRESS_PROXY=socks5://127.0.0.1:1055`) and a small change at the three launch sites: when set, Chromium gets a **PAC that sends only `app.breakoutprop.com` through the proxy** and everything else direct. **Unset = today's behaviour, byte for byte.**
4. **Fail-closed at the adapter:** if the variable is set and the SOCKS port is down, the executor **refuses the tick with a logged cause** (the account stays live and trades are refused, per the Prime Directive: no auto-flip, no direct fallback that would hit the ban).

**Revert (each step independent):** unset the env var (Chromium goes direct as before); `systemctl disable --now tailscaled-egress`; run the node's logout and delete the machine in the Tailscale admin page; delete the unit file and state directory. **Nothing else was changed**, so there is nothing else to undo. The adapter change ships behind the unset variable, so it can merge before anything is switched on.

**Open point that can break this even if the probe passes:** a Breakout session may be tied to the address it started on. The plan sends `app.breakoutprop.com` from the home address but `wss.breakoutprop.com` (the DXtrade host) straight from the VM, and a proxy exit that changes address (home ISP renumbering) mid-session may log the terminal out. **Neither is measured.** The probe's three runs do not test a long session.

## 6. Failure modes of a home route (measured facts marked)

- **Home power or internet out:** the exit node vanishes; the adapter refuses ticks (fail-closed); tickets expire; the Telegram fallback applies. This is exposure a datacenter route does not have.
- **Home address changes:** Tailscale does not care, but a Breakout session might (§ 5). **Frequency of change is unmeasured.**
- **Key expiry** silently ends the route: step 5 exists for that.
- **Bandwidth:** the terminal's cold load was measured at ≈ 7.55 MB, about 130 GB a month as coded and about 9 GB with a persistent browser cache ([part 4](breakout-phone-egress-and-leak-controls-2026-09-30.md)); home broadband makes that free, but it is the operator's household connection.
- **Latency:** an extra hop and home upload speed; **not measured.**

## 7. Terms risk, restated for **more than one Breakout account on this route**

- **First-party text, read earlier today from Breakout's own FAQ (not re-fetched now):** a VPN is allowed *"as long as it's not used to conceal or misrepresent your jurisdiction, or to evade any law or regulation"*; the FAQ has **no clause on automation**; and its evaluation-stage rule names **"trading multiple accounts from the same household, device, or IP address."**
  **The Funded Trader and Evaluation Agreements, which govern, are unread.**
- **What R2 changes for one account:** the order-entry address becomes the operator's own **household address**: it does not conceal their jurisdiction, but it is still **a different address used to reach a site that blocks the current one**, which the FAQ neither permits nor forbids.
- **What R2 changes for several accounts:** **every Breakout account traded through this route shares one household address and one device path**, which is exactly the pattern that evaluation-stage rule names; it also **shares the address with the operator's personal use**. That turns a one-account question the FAQ is silent on into **a multi-account same-IP question the FAQ speaks to directly.** **Recommendation: one Breakout account on this route until the Agreements are read**, and read them before a second one.
- Stated once more: this may breach Breakout's terms and put the funded account at risk. That is the operator's call, not a technical one.

## 8. Not verified

Tailscale's exit-node-in-userspace behaviour (docs silent, § 4); the Android "Run as exit node" and battery labels, "Disable key expiry", and "Settings > Keys" paths; whether the Personal plan's free terms still hold at signup; a real Chromium run from a home address (none exists); whether a Breakout session survives an address change; the run 36731753995 details (read via the manager's relay, not the run log); the Funded Trader Agreement; any price after this morning; the cause of the never-firing hourly cron (pipeline row `PI-20260930-3ZFMYYI4-0002`).
