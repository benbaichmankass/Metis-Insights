# Breakout proprietary terminal — egress scoping, part 2 (other-cloud VM in depth; free local agents), 2026-09-30

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Checklist row **PROP-TERM**; pipeline **PI-20260929-FRJ7NMPU-0001**. Continues
> [`breakout-terminal-egress-scoping-2026-09-30.md`](breakout-terminal-egress-scoping-2026-09-30.md)
> (part 1, PR #14516; **its § 6 decision is superseded by § 4 below**). Operator, verbatim,
> ~05:10Z 2026-09-30: *"Let's scope out 3, as well as looking for other options for running a
> local agent that can be free, as well as researching other prop platforms…"* The third
> item is a separate lane and is **not** covered here.
>
> **SCOPE ONLY. Nothing is built, deployed, bought or routed, and no new VM, tunnel or proxy
> was created to write this.** Tier-1 (docs only).

## 0. Two facts the rest depends on

1. **The block is on the Oracle ASN, not on "datacenters".** Measured (part 1 § 2): Oracle
   AS31898 (live VM, trainer VM in Paris) → 403 Error 1005. This session's Google-ASN egress
   (AS396982) → 403 **managed challenge** (`cf-mitigated: challenge`), not 1005. So a non-Oracle
   datacenter address is at least *not* hit by the ASN ban, but a plain `curl` was challenged;
   whether a real headless Chromium is challenged, passes, or is scored down is **UNMEASURED**
   for every non-Oracle egress.
2. **Whatever egress runs the adapter must mint its own login session there.** A Cloudflare
   clearance and the terminal's saved session are tied to the client that earned them. The live
   VM's saved `session_state.json` cannot simply be copied to another host, and the terminal's
   login has `email_code` / `2fa` states (`src/prop/platform/breakout_terminal.py`), so the first
   login on a new egress needs the operator once.

## 1. Option 3 in depth — a small VM at another cloud

### 1.1 Providers (list prices read 2026-09-30 from the vendors' own pages; not quotes)

| provider | smallest plan read | price | regions read | what I could NOT read |
|---|---|---|---|---|
| DigitalOcean Droplet (Basic) | 1 vCPU / 512 MiB / 10 GB / 500 GiB transfer; next 1 vCPU / 1 GiB / 25 GB / 1,000 GiB | **$4 / $6 per month** | SF, NYC, Toronto, London, Frankfurt, Bangalore, Singapore, Sydney | — |
| AWS Lightsail (Linux) | 0.5 GB / 2 vCPU / 20 GB / 1 TB, IPv4 bundle; IPv6-only bundle also listed | **$5 / month** ($3.50 IPv6-only) | not on the page read | region list |
| GCP Compute Engine `e2-micro` (Always Free) | 1 instance-month of hours, 30 GB-month standard disk | **$0** within limits | `us-west1` (Oregon), `us-central1` (Iowa), `us-east1` (S. Carolina) only | RAM of the e2-micro (not on the page read); **outbound cap is 1 GB/month North America** |
| Hetzner Cloud | plans exist in Cost-Optimized / Regular / General Purpose | **NOT READ** (the price cells were not in the fetched text) | Falkenstein, Nuremberg, Helsinki, Hillsboro OR, Ashburn VA, Singapore | every price and spec |
| Vultr | — | **NOT READ** (HTTP 403 to the fetch) | — | everything |

Sizing note (my judgement, not measured): a headless Chromium driving a charting terminal is
comfortable at ≥1 GiB; the 512 MiB plans are marginal. The GCP free tier's **1 GB/month
outbound** cap is a real constraint: a browser that re-opens the terminal every 5-minute tick
can move many MB per load. The size of one terminal load has not been measured, so whether
the free tier survives a month is unknown.

### 1.2 Which ASNs are likely blocked

**Known: Oracle is blocked; the Google sandbox address is challenged, not banned.** Everything
else is a guess. ASN numbers below are from my own knowledge and are **unverified**: DigitalOcean
(AS14061), AWS (AS16509), Hetzner (AS24940), Vultr (AS20473), Google Cloud (AS15169 / AS396982).
Cloudflare's bot scoring treats large hosting ASNs alike more often than not, so the Google result
suggests scoring rather than a per-ASN list, but that is an inference from **one** data point.
The cheapest way to turn any of this into a fact is a **landing-only curl** from the candidate
address (no credentials, nothing clicked) **before** any provider is chosen. This lane did not
create a VM to do it.

### 1.3 How the adapter would run there (design only)

- **Software:** the same repo checkout, `scripts/prop/prop_executor_tick.py --login reuse` (Playwright
  Chromium, headless), driven by a timer. Pull model, as today: it reads
  `GET /api/bot/prop/tickets?status=emitted` and writes back through `POST /api/bot/prop/report`
  (`src/prop/prop_executor.py`). The script already takes `--api-base` (default
  `http://127.0.0.1:8001`), so pointing it at `https://ict-bot.duckdns.org` (the Caddy front) needs
  **no inbound port on either side and no push from the live VM**. Whether the executor needs
  further changes to run split (a proxy/host flag, the journal-side lock) is unbuilt and unexamined.
- **Credentials:** two secrets must exist on the new box: the Breakout terminal login
  (`config/prop_platforms.yaml` `username_env` / `password_env`) and `DASHBOARD_API_TOKEN`,
  which is the **write bearer** for `POST /api/bot/prop/report`. The existing mechanism (GitHub Actions
  secrets pushed by the `set-env` system-action) targets the two known VMs only, so a third box needs
  a new path, which is itself a build. **Both secrets would leave our VMs.** A write-scoped token
  limited to the report route would shrink the blast radius; none exists today.
- **Latency on a 5-min tick:** an extra network hop is milliseconds against a tick measured in minutes;
  negligible. The real costs are a slower login, the once-per-session Cloudflare/2FA step, and any
  challenge page.
- **Failure modes:** (a) box down or unreachable, so the executor simply does not tick and tickets
  expire (the existing expiry prompt then asks the operator); (b) session rejected, so the tick exits
  without a login it may not retry (the live design counts logins: "one login at a time");
  (c) Cloudflare challenge appears later (bot scoring changes), the adapter's feasibility stop
  `challenge` fires; (d) API unreachable from the box, so no intake and no write-back, which
  `prop_reconcile` reads as a ticket placed with no journaled fill (an auto-revert condition in the
  registered go-live criteria). Each fails closed; none has been exercised.
- **Ownership:** us, plus a patching and key-rotation duty for one more machine.
- **Cost:** $0 (GCP free tier, with the caveats above) to ~$4–6/month (DigitalOcean 512 MiB/1 GiB) to
  ~$5 (Lightsail).

### 1.4 Account risk, stated plainly

Choosing this option **is** choosing to reach a site the site owner has blocked from our current
address by using a different address. That may breach Breakout's terms, and the funded account
would be at risk, on top of the automation risk the operator already accepted
(2026-09-27). **Breakout's first-party terms remain unread** (part 1 § 1). Nothing in this
doc says this option is permitted, and nothing says it is forbidden.

## 2. Free local-agent options (operator's own machine or network)

All of these put the adapter, or its traffic, on the operator's ordinary home/office address.
That is a *different* address from any datacenter, and it is presumably the address Breakout
sees when the operator logs in by hand, but that is an assumption, not a measurement. None of it
avoids the account-risk paragraph in § 1.4: it is still automation, still a route the ban did not
anticipate, still unread terms.

**Step zero for every option below (operator, ~1 minute, $0, nothing built):** from the home
network run one landing-only `curl -sS -D - -o /dev/null https://app.breakoutprop.com/` and paste
the status line and any `cf-mitigated` header. It answers whether the home address is banned,
challenged or clean for a plain client, and it costs nothing.

| | L1. Existing adapter on a home PC/Mac, on a schedule | L2. Same, on an always-on Raspberry Pi / spare box | L3. Tunnel: browser stays on the live VM, exits via the home connection | L4. Browser extension / userscript in the operator's own browser |
|---|---|---|---|---|
| How | clone repo, install Python + Playwright, set env, run `prop_executor_tick.py --api-base https://ict-bot.duckdns.org` from cron/launchd/Task Scheduler | identical, on a small always-on device | an SSH SOCKS tunnel from the home machine to the live VM (`ssh -D`, kept alive), and the live VM's Playwright launched with that proxy | a content script on the operator's logged-in terminal tab polls the ticket endpoint and fills the order form |
| Setup effort (operator) | highest: ~30–60 min once, updates by hand | as L1, plus hardware | **lowest steps**: one long-running command, but it needs a small **build** (a proxy option in the adapter, fail-closed if the tunnel is down) | moderate for the operator, but the extension itself is the biggest **build**, and it is DOM-fragile |
| Always-on need | machine awake and online at every tick, or tickets lapse | always-on by design | the home machine must stay online with the tunnel up | the browser tab must stay open and logged in |
| Cost | $0 if a machine is already on 24/7 | ~$35–60 one-off if hardware must be bought (not free unless owned) | $0 | $0 |
| Credentials | Breakout login and `DASHBOARD_API_TOKEN` leave our VMs, onto the operator's machine | same | **stay on the live VM**; the home machine relays encrypted traffic only | Breakout login never leaves the operator's browser; the write bearer must still be available to post fills back |
| What the agent must reach on our side | `GET /api/bot/prop/tickets`, `POST /api/bot/prop/report` (bearer) | same | nothing new (inbound SSH to the live VM, or the reverse) | the same two endpoints |
| Failure / reporting path | the existing report-back and `prop_reconcile` gaps; a down machine reads as a lapsed ticket | same | tunnel down leaves the browser with no route; must fail closed, not fall back to the banned direct path | silent when the tab closes; `prop_fills_staleness` would eventually flag missing fills |
| Fits unattended trading? | yes while the machine is up | yes | yes while the tunnel is up | no; semi-manual by nature |

**Fewest operator steps:** L3 (one command, credentials stay put), at the price of a small adapter
change. **Fewest builds:** L1/L2 (the adapter already runs as a script and already takes
`--api-base`). L4 is the largest build and the least suited to unattended running; it is listed
because it is the only one that uses the operator's *real* browser and session.

**Not applicable:** a GitHub Actions self-hosted runner on the operator's machine would add nothing here
(the existing diag/system-action workflows SSH into our VMs; they do not run the adapter),
and Actions-hosted runners were not measured (part 1 § 2).

## 3. What each option needs measured before anything is built

| option | first measurement (all landing-only, no credentials) |
|---|---|
| Home address (L1–L4) | the step-zero `curl` above, run by the operator |
| Other-cloud VM (§ 1) | a landing-only `curl` **and** a default headless-Chromium landing load from a candidate address; needs a VM to exist, so it is an operator-approved spend, however small |
| Any option | Breakout's first-party terms, forwarded by the operator (unreachable from this session) |

## 4. Updated operator decision (supersedes part 1 § 6)

1. **Ask Breakout first, and run the step-zero curl from home (recommended).** Two zero-cost actions,
   no build: the operator asks Breakout support for an allowlist or an approved egress, and runs
   the one home `curl` so the local options are grounded in a fact. PROP-TERM stays parked until
   the replies.
2. **Ask, plus forward Breakout's first-party terms text** so both the other-cloud VM and the local
   agents can be judged against what the terms actually say.
3. **Pick the free local agent (L1 or L3) and authorise a build lane** once the home `curl` is clean.
   L3 needs the smallest operator effort and leaves credentials on the live VM. Accepts the
   ban-avoidance account risk (§ 1.4).
4. **Pick the other-cloud VM (§ 1) and authorise a build lane and a small spend,** starting with
   the two landing measurements in § 3. Accepts the same account risk plus a new machine and a new
   secret path.

## 5. Not verified

Every cost; Hetzner and Vultr prices (unread); the e2-micro RAM and the terminal's per-load
bandwidth; any non-Oracle ASN's behaviour under a real browser; whether the operator's home address
is clean; Breakout's first-party terms; and whether the executor can run split without further code
changes. Nothing was executed on any candidate egress.
