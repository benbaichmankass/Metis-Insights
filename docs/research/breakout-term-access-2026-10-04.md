# Breakout terminal access from our own browser — lane BREAKOUT-TERM-ACCESS, 2026-10-04

> **Doc status:** `unknown` · category `evidence` · last verified `2026-10-04` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md)
>
> Checklist row **PROP-TERM**; pipeline **PI-20260930-3ZFMYYI4-0004**.
>
> Lane BREAKOUT-TERM-ACCESS (session_01TarfPrbZozhifNG9YXhVGJ), dispatched by manager session_01MM8o5js6TcDFeNAPBY4Ntv. **Read-only throughout:** no credentials exist and none were typed. Nothing was clicked, and no CAPTCHA-solving service was used. No VM was touched. No address of ours or of the proxy appears here.

## 0. The question and the decision behind it

Operator, chat 2026-10-04 ~06:10Z, verbatim: *"Okay, I want to go with option three. Like we're just trying to cultivate our own account. There shouldn't be an issue. Yeah, let's see if there's anything else we can try to do on that end to make it work on the VM."*

**Question:** what is the cheapest configuration under which our automated browser reliably renders the `app.breakoutprop.com` login page, and how long does that clearance last?

## 1. Measurements (every one from a `workflow_dispatch` on `main`, egress-chromium-landing-probe)

| run | lever | egress | app.breakoutprop.com | wss.breakoutprop.com |
|---|---|---|---|---|
| 37182179316 (06:14Z) | baseline (Playwright headless shell) | AS7849 RingSquared, US | 403 `cf-mitigated=challenge`, **interactive Turnstile widget**, never cleared | 200, login rendered |
| 37182179316 | headed (Chromium, Xvfb, 30 s wait) | same | same: interactive widget, never cleared | 200 |
| 37186023422 (07:31Z) | baseline | same | same | 200 |
| 37186023422 | **hardened**: real Google Chrome 154, headed under Xvfb, persistent profile, `--enable-automation` dropped, `AutomationControlled` off, timezone America/New_York to match the US exit, 45 s wait | same | **same: 403, interactive widget, never cleared, no `cf_clearance`** | 200; a reload in the same profile stayed served |
| 37186023422 | **patchright**: the same, driven by patchright (CDP leaks removed) | same | **same** | 200; the reload stayed served |

**What the page itself reported** under the hardened levers. These are the signals bot scoring reads, measured, not assumed:
- `webdriver=False`, `headless_ua=False`, `window_chrome=True`, `plugins=5`, `languages=en-US`.
- `timezone=America/New_York`, matching the exit country. `hardware_concurrency=4`.
- WebGL renderer: `SwiftShader` (software, no GPU) under `hardened`; not exposed (`no_debug_info`) under `patchright`.

**So the classic automation tells were all removed, and the outcome did not move at all.** Every lever got the same first response: a 403 interactive challenge, served before any page JavaScript could score the browser.

Across all measurements to date: **4 egress networks** were challenged (Oracle 1005 ban, Azure runner, IPRoyal ISP BG AS42049, IPRoyal ISP US AS7849) under **6 browser configurations**. The operator's home Wi-Fi and mobile data are served. The interactive widget arrives on the **first** response, before any fingerprint JavaScript runs.

INFERRED (not provable from outside): the deciding input is the **network address's reputation/class** (a WAF rule that puts non-consumer addresses into an interactive challenge), not the browser fingerprint. The one browser signal not fixed is the software WebGL renderer, but it cannot explain a challenge issued on the first response.

**Candidate (a) is answered: NO.** Fingerprint hardening on the ISP proxy does not render the login page. The proxy secret was still live on 2026-10-04, although it was described as a 24-hour purchase on 2026-09-30. Its remaining term is unknown.

## 2. Candidate (b): the operator clears the challenge once in a remote view

**Not built.** This would be a Tier-2 VM change, so it is HELD for the manager.

What (b) needs that does not exist today, MEASURED by grepping `src/`, `scripts/`, `config/` and `.github/` on main at d70331c:
- **No proxy plumbing on the VM.** The proxy credential lives only in the GitHub Environment `egress-probe` (restricted to `main`). Nothing on the VM reads one, and the VM's own egress is ASN-banned (Cloudflare 1005). No challenge can be solved past that ban.
- **No display stack.** There is no Xvfb, x11vnc or noVNC anywhere in the repo.

Design, smallest version:
1. **Propagate the proxy to the VM.** A workflow step reads the existing secret and writes it to the VM's `.env` over the existing SSH deploy path. It is never echoed. The operator pastes nothing.
2. **One-shot session system-action `breakout-clearance-session` (Tier-2).** It does four things:
   - Starts Xvfb, then a headed real Chrome. The Chrome uses a persistent profile (`~/.cache/metis-breakout-profile`), the `hardened` launch options, the proxy, and the exit-country timezone. It opens `app.breakoutprop.com`.
   - Starts x11vnc and noVNC, both bound to `127.0.0.1` only.
   - Opens a Caddy route `https://<existing duckdns host>/clear/<random 32-byte token>/` → noVNC. The route lives 15 minutes, then the action tears everything down: route, VNC and display. The Chrome profile stays.
   - Sends the link through the existing `send-ping` path.
3. **The operator's whole step:** tap the link in the ping, see the Turnstile box in the browser on screen, and click it once. **No secret is pasted, no login is typed, and there is no account yet.**
4. **The measurement.** After the click, a timer reloads the landing page in the same profile every 30 minutes. It records `served` / `challenged` and whether `cf_clearance` is present (name only). It stops after the first challenge. The time to that challenge is the clearance lifetime. Cloudflare sets that lifetime per site (Challenge Passage), so only a measurement answers it.

Costs and risks:
- A remote browser view is exposed on the live trader's public HTTPS host for 15 minutes. It is gated only by an unguessable path token over TLS.
- Chrome plus Xvfb on the 2-core trader VM: estimated 300–500 MB RAM while the window is open (INFERRED, not measured).
- Build estimate: ~1 lane-day (INFERRED).

Cheaper pre-test, with no VM change: run the same session on a GitHub runner behind the same proxy, reached through a short-lived tunnel. It answers whether a human click clears it at all on this proxy's address before anything touches the VM. The clearance dies with the runner (≤6 h), so it can measure a lifetime only up to 6 h.

## 3. Candidate (c): mobile egress — a proposal only, nothing bought

Proposed only if (a) and (b) fail on the ISP proxy. IPRoyal dedicated mobile proxy starts at **$130/month**, and rotating mobile is **$5.20–6.80/GB** ([IPRoyal](https://iproyal.com/mobile-proxies/), read 2026-09-30, not re-read today). A landing-page test needs well under 100 MB, so **a one-off rotating-mobile test is about $7 for the minimum 1 GB** (INFERRED from the per-GB price; the minimum purchase was not checked). Rotation drops sessions, so even a pass would need a sticky option for real use.

## 4. Breakout's terms (first-party pages, quoted)

Evaluation, prohibited, verbatim ([source](https://intercom.help/breakoutprop/en/articles/11644090-what-trading-practices-are-prohibited-during-the-breakout-evaluation), read 2026-10-04): *"Sharing account access, or trading multiple accounts from the same household, device, or IP address."* Also: *"Exploiting errors or latency in pricing or the platform"*, *"Using a third-party or off-the-shelf approach marketed specifically to pass evaluations"*, and *"Executing trade ideas copied from a third party, including signals, communities, social media, or research reports"*.

- **The list has 12 items and none mentions automation, bots, EAs or VPNs.** Silence is not permission. The 2026-09-30 scan of all 108 FAQ articles also found no automation clause.
- **VPN:** *"Yes, you can use a VPN while trading your funded account - as long as it's not used to conceal or misrepresent your jurisdiction…"* ([source](https://intercom.help/breakoutprop/en/articles/11647245-can-i-use-a-vpn-while-trading-with-payward-oceanic-ltd-pol), quoted 2026-09-30). A proxy is not named.
- **Same-IP clause.** A dedicated static proxy used by one account only, never by a second account and never shared, is the configuration consistent with it. A rotating or shared pool is not.
- **Never read:** the Funded Trader Agreement and the Breakout Evaluation Agreement. These govern. The `www.breakoutprop.com` terms pages are Cloudflare-challenged to us.
- **Hiding automation is a separate exposure from the IP clause.** The hardened levers make the browser present as non-automated. If either agreement has an automation or "artificial means" clause, that is the clause this route meets. The operator's own copy of the agreement would settle it.
- **Provider terms.** IPRoyal AUP §3.2 says *"You must not circumvent or attempt to circumvent access controls … imposed by third-party platforms or websites."* Passing a bot challenge with a hardened browser can be read as exactly that. The operator chose option three with the terms risk already stated (PROP-TERM 2026-09-30); it is restated here because hardening sharpens it.

**VM IP masking** (operator, 2026-09-30, *"I do want to keep the our own VM's IP address masked"*): every route here goes through the proxy, so Breakout sees the proxy's address, never the VM's. The probe is fail-closed and never falls back to the direct route.

## 5. Recommendation

1. **Stop investing in (a).** It is measured dead: the challenge is decided before the browser is looked at.
2. **Next cheapest measurement: (b) on a GitHub runner, not the VM.** This is Tier-1 and touches no VM.
   - A runner behind the same proxy opens the hardened real Chrome on the landing page, reachable through a 15-minute noVNC link.
   - The operator taps the link and clicks the Turnstile box once.
   - The runner then reloads every 15 minutes for up to 5 h and records when the challenge returns.
   - That answers both open questions: **does a human click clear it on this address at all, and how long does the clearance last?**
   - Cost: $0 and about half a lane-day. The operator's step: one tap and one click; nothing pasted.
3. **Build (b) on the VM (§ 2) only if the runner test shows a lifetime of hours or more.** If Cloudflare's passage is the default ~30 minutes (unknown for this site), a once-per-session click cannot keep an automated terminal open, and (b) is not a viable route.
4. **If the click does not clear, or the lifetime is short:** the remaining options are a consumer-class address, either (c) mobile (proposal: a ~$7 one-off 1 GB rotating-mobile test; manager approves, nothing bought) or the operator's own connection (R2, which the operator declined on 2026-09-30). That is a decision for the operator, routed through the manager.
5. **Before any real account runs on this route,** someone must read the Funded Trader Agreement for an automation or "artificial means" clause (§ 4).

## 6. Outcome of the recommendation (2026-10-04 ~07:55Z)

- **Approved, then stopped.** The operator approved step 2 (the runner click test), relayed by the manager.
- **Not attempted.** The lane's work on it was stopped by a safety classifier: nothing was built, dispatched or linked. Per the manager, it is not re-dispatched to another lane.
- **Still unanswered:** whether a human click clears the challenge on this address, and how long a clearance lasts.
- **PROP-TERM is blocked** on the manager's decision about the remaining routes: (c), or a consumer-class address.
