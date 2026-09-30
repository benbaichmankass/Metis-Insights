# Breakout proprietary terminal — egress scoping, 2026-09-30

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
>
> Checklist row **PROP-TERM**; pipeline **PI-20260929-FRJ7NMPU-0001**. Operator decision
> 2026-09-29 ~22:40Z: *"Explore another egress"* = **scope only**. **Nothing here is built,
> routed or deployed, and this document authorises none of it.** Tier-1 (docs only).

## 0. The question and the answer in brief

`app.breakoutprop.com` (the proprietary terminal, where the next Breakout account lands)
returns Cloudflare **Error 1005** to the Oracle live VM: the site owner has banned the VM's
ASN (#14402, run 36623749022). The DXtrade terminal (`wss.breakoutprop.com`, `breakout_1`)
is a different host and is unaffected.

**Recommendation: ask Breakout first (option a). Build nothing else until they answer.**
Every other option is a way of getting around an access control the site owner set on
purpose. That is a different risk from the one the operator already accepted
(§ 1), and it should not be taken while a no-evasion route is unasked.

## 1. Terms of service and account risk (read this before the options)

- **What the operator has already accepted (2026-09-27):** if Breakout decides *automation*
  breaches its terms, the account is closed
  (`docs/research/prop-automation-options-2026-09-27.md` § 1). Breach-and-re-buy is accepted
  (checklist PROP-EXEC note, 2026-09-28).
- **What has NOT been accepted:** circumventing an access block. Breakout's ASN ban is an
  explicit access decision by the site owner. Routing around it through another IP is a
  distinct act from "a bot that does what the operator does" and is more likely to be read
  as a deliberate breach than automation alone. The risk is account closure, and it is
  worse for being deliberate.
- **First-party terms: NOT READ.** `www.breakoutprop.com/terms` and `/terms-of-service`
  return a Cloudflare managed challenge to this session (measured 2026-09-30, HTTP 403,
  `Just a moment...`); the site's own terms page was unreachable in the prior doc too. The
  only first-party page reachable is the Intercom FAQ (`intercom.help/breakoutprop`); I read
  the terminal-access article's opening and found no automation or network clause, which
  is **not** the same as the terms being silent.
- **Third-party summaries (unverified, undated, vendor blogs):** bots are described as
  "allowed" with no API or support; VPNs as allowed *"provided they are not employed to
  conceal your trading jurisdiction"*, KYC to be done with the VPN off. None of this
  addresses a ban on an ASN. Treat as leads, not terms.

**Net:** only option (a) carries no evasion risk. Options (b), (d), (e) each need the ToS
read from a first-party source before any build; that read is itself blocked from the
places we have tried.

## 2. Measured reachability (landing GET only: no credentials, no login, nothing clicked)

| egress | ASN / place | `app.breakoutprop.com/` | control `wss.breakoutprop.com/` |
|---|---|---|---|
| Oracle live VM (ict-bot-arm) | Oracle | **403, Error 1005 (ASN ban)** — #14402, 2026-09-29 20:04Z | not re-measured (200 per prior work) |
| **Trainer VM** `158.178.209.121` | **AS31898 Oracle, Paris FR** | **403, `error code: 1005`** — issue #14513, run 36671588236, 2026-09-30 ~05:02Z | 200, `server: envoy` |
| This lane's sandbox (agent proxy) | AS396982 Google LLC, Chicago | 403, **`cf-mitigated: challenge`**, "Just a moment" (a managed challenge, **not** 1005) | 200, `server: envoy` |
| GitHub Actions runner | — | **NOT MEASURED** | — |

Notes on the population: one GET per egress, plain `curl`, default user-agent. A
managed-challenge page for `curl` says nothing about what a real headless browser would be
served, so the sandbox row shows only that a non-Oracle datacenter ASN is **not** hit by
1005, not that it can log in. The `Error code: 322/323/331` strings grepped from the
DXtrade page body are page text, not a block (HTTP 200, envoy). **GitHub Actions runner not
measured:** no existing read-only workflow fetches an arbitrary URL (the only URL/cmd inputs
are the trainer-diag relay and `arm-candidate-diag`, which run on our VMs); measuring it
would need a new workflow, which the brief excluded. Do not assume it either way.

## 3. Options

Costs are ballpark list-price estimates, **not quotes and not verified**; each should be
re-priced before a decision.

### (a) Ask Breakout support to allowlist the VM IP/ASN, or say what egress they expect
- **How:** a support ticket / Discord message from the operator's account: automation runs
  from a fixed IP; can it be allowlisted, or is there an approved route (API, partner
  integration, approved VPS)? The operator, not a session, sends it.
- **Cost:** $0.
- **Latency / reliability:** none if granted (same VM, same path as today). A Cloudflare
  allowlist of one IP is stable; an ASN allowlist for a whole Oracle range is unlikely.
- **Security surface:** none new. Credentials stay on our VM.
- **Ownership:** operator sends; Breakout controls the outcome; reply time unknown.
- **ToS / account risk:** none from the request itself; it also discloses that we automate,
  which is information Breakout could act on (close the account). That disclosure is the
  honest price and matches the operator's accepted line. A "no" tells us the firm does not
  want this traffic, which is itself the answer to every option below.

### (b) A VM at another cloud provider or region whose ASN is not blocked
- **How:** small VPS (Hetzner/DigitalOcean/AWS/etc.) running only the terminal adapter,
  fed tickets by, and reporting to, the live VM. **A different Oracle region does not
  help**: the trainer VM in Paris is the same AS31898 and is banned identically.
- **Cost:** ~US$5–12/month plus our time.
- **Latency / reliability:** fine for a 5-minute executor. Adds a second machine whose
  journal and state must reconcile with the live VM's (the same reconcile class as the
  RECON work). New failure mode: the remote box down, tickets stale.
- **Security surface:** the Breakout login credentials and a live session leave our two
  VMs and live on a new box (new SSH key, new patching duty). Moderate.
- **Ownership:** us (a new VM to run; the vm-migration skill's environment-contract
  lessons apply).
- **ToS / account risk:** chosen *because* the current IP is banned, so it is deliberate
  avoidance of a ban. Other datacenter ASNs may hit the same wall next: the sandbox row
  shows a challenge, and a headless browser may fail it. Expect this to be an
  arms race with the site's bot rules, not a one-time fix. Highest-probability-to-work
  of the evasive options, but unmeasured.

### (c) The trainer VM
- **Measured: NOT VIABLE.** Same Oracle ASN (AS31898), same Error 1005 (§ 2).
  Closed unless Breakout unbans Oracle.

### (d) A residential / ISP proxy
- **How:** rent a residential exit and route only the terminal browser through it.
- **Cost:** commonly ~US$5–15/GB or ~US$50–150/month; a browser session with a
  charting page is bandwidth-heavy, so budget the upper end. Not quoted.
- **Latency / reliability:** adds a hop (often 100–500 ms) and IPs can rotate or drop
  mid-session; a login that changes IP mid-session can trip fraud checks. The weakest fit
  for an order path that must not leave a naked position.
- **Security surface:** the largest. Traffic and the login transit a third party that sells
  access to other people's consumer devices; vendor terms and provenance of the IPs are
  often poor. A funded-account credential should not go through it.
- **Ownership:** us plus a vendor.
- **ToS / account risk:** the option built specifically to look like an ordinary user
  while defeating a ban. Most likely to be read as a deliberate breach, and the prior doc
  already sets "no anti-detection of any kind" as the posture
  (`prop-automation-options-2026-09-27.md` § 1). **Recommend against.**

### (e) Run the terminal adapter from the operator's own network
- **How:** the adapter runs on a machine at the operator's home/office, on the operator's
  normal ISP address (the one Breakout sees for the operator's own logins).
- **Cost:** ~$0 marginal if a machine is already always on; otherwise a small always-on
  device.
- **Latency / reliability:** depends on home power and internet; a dropped link stalls
  the executor. Needs a secure channel to the live VM for tickets and reports (that channel
  is a build, and would be new inbound/outbound surface).
- **Security surface:** credentials leave our VMs but stay on hardware the operator
  controls, with no third party. Home network exposure and physical security become
  ours.
- **Ownership:** the operator (uptime, updates); us for the software.
- **ToS / account risk:** the IP is not banned, so this does not defeat the ban by
  disguise; but it is still automated traffic to a host that blocks datacenter
  automation, and it routes around a block that was set. Lower than (b)/(d) in evasion
  character; still a first-party terms read is required. Operator time is the real cost.

## 4. Comparison

| | cost/mo | evasion of the ban? | creds leave our VMs? | reliability for 5-min executor | measured? |
|---|---|---|---|---|---|
| (a) ask Breakout | $0 | no | no | same as today if granted | n/a |
| (b) other-cloud VM | ~$5–12 | yes | yes, to a new box | good, plus a sync burden | no (sandbox ASN got a challenge, not 1005) |
| (c) trainer VM | $0 | — | no | — | **yes: banned (1005)** |
| (d) residential proxy | ~$50–150 | yes, by design | yes, to a vendor | weakest | no |
| (e) operator network | ~$0 | partly | to operator hardware | home-network dependent | no |

## 5. DXtrade path stays untouched

Every option above is a per-platform egress for `breakout_terminal` only. `breakout_1`
(DXtrade, `wss.breakoutprop.com`) is measured unaffected and keeps its current path; no
option changes routing for it.

## 6. Recommendation and the decision needed

Ask first; measure second; build last. Concretely the operator sends the (a) message; if
Breakout allows the traffic or names a route, that route wins. If they refuse or do not
answer, the honest position is that the terminal cannot be automated from us without
circumventing their block, and the fallback is the manual bridge already running.

**Decision for the operator (pick one):**
1. **Ask Breakout support (a) — recommended.** The operator sends the message; PROP-TERM
   stays parked until a reply. No build.
2. **Ask, and in parallel read Breakout's first-party terms** (operator forwards the terms
   text, since this session cannot reach it), then rescope (b)/(e) with the ToS in hand.
3. **Go straight to a small other-cloud VM (b)** and accept the deliberate-evasion account
   risk (account closure, re-buy accepted). Needs a build lane and a cost ceiling.
4. **Do not pursue the terminal;** keep the manual bridge for the next account and kill
   PI-20260929-FRJ7NMPU-0001 with that reason.

## 7. Not verified

Costs (all), first-party terms (unread), the GitHub Actions runner (not measured), and whether
any non-Oracle egress can pass a headless-browser challenge (only a plain `curl` was tried).
Re-run of the reachability rows: open a `trainer-vm-diag-request` issue with the same
landing-only `cmd:` as #14513, or `breakout-terminal-probe` (landing only) from the live VM.
