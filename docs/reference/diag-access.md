# Diagnostic API access (S-051)

> Extracted verbatim from `CLAUDE.md` on 2026-09-21 by the operating reset.
> Reference material — read on demand, not at session start.
> Registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md).


Token-gated **read-only** surface for PM-side Claude / operator scripts.

**The endpoint table moved to
[`docs/reference/bot-api-reference.md`](docs/reference/bot-api-reference.md)
§ "Diagnostic API (S-051)" on 2026-09-02**, verbatim — `snapshot`, `audit`,
`audit_query`, `journal`, `journalctl`, `log_file` (and its allowlisted names),
`db_info`, `version`, `ib_state`, `ib_open_orders`, `bybit_open_orders`,
`venue_session`, `exchange_positions`, `broker_account_status`, `exposure`,
`tick_cost`, `position_telemetry`, `shadow_stats`. Open it when you need a
specific endpoint's payload.

**What binds you before you open it** — this is the part that changes how you
read a failed relay response, so it stays here:

- **Every endpoint returns 503 if `DIAG_READ_TOKEN` is unset, and 401 on a bad
  bearer.** Those are different failures with different remedies, and neither
  is "the VM is down".
- The diag surface covers the **live VM only**. There is no `/api/diag/*` on
  the trainer VM — read that box through the `trainer-vm-diag` relay.
- It is **read-only**: diagnostics, journal `SELECT`s, service state, log
  tails. It is not an order path and carries no exchange credentials. ⚠️ That
  read-only premise is what the closed token-rotation decision below rests on —
  **if `/api/diag/*` ever gains a write route or starts returning secrets, that
  decision must be re-put to the operator rather than assumed to carry over.**

See `docs/claude/vm-operator-mode.md` § 9 for the trust contract.

### Reaching `/api/diag/*` from a PM-side / web session

Two transports, identical JSON — **try direct, fall back to the relay.**

1. **Direct HTTP — try it FIRST, and only `DIAG_READ_TOKEN` is required**
   (corrected 2026-08-20). `scripts/ops/diag_fetch.sh '<path>'` (exit `0` =
   JSON; exit `3` = fall back). **`DIAG_BASE_URL` is OPTIONAL**: the script
   tries an ORDERED list of candidate bases and puts the canonical HTTPS
   host (`https://ict-bot.duckdns.org`, the Caddy route) FIRST whenever the
   configured value is plain-http or names a known VM IP — i.e. exactly the
   cases the sandbox proxy drops. It prints `served by <base>` on stderr, so
   a reader can tell WHICH host answered rather than assuming. This row
   previously demanded **both** vars, which sent every session to the relay
   whenever the canned `DIAG_BASE_URL` was stale — and it has been stale
   since the 2026-06-14 cutover. The diag surface covers the **live VM
   only**; there is no `/api/diag/*` on the trainer VM.
2. **GitHub-issue relay (fallback).** Open an issue titled
   `[diag-request] <path>` with label `vm-diag-request`; the
   `vm-diag-snapshot` workflow runs the fetch over SSH + curl, posts
   the JSON back as an issue comment, and closes the issue.

Full flow, the direct/relay contract, token management
(`get-diag-token` / `set-diag-token`), and failure modes are in
`docs/claude/diag-relay.md`. The bearer lives in repo secrets
(`VM_SSH_KEY`, `DIAG_READ_TOKEN`) and on the VM. ⚠️ **`get-diag-token`
REFUSES on a public repo as of 2026-08-25, so on this repo it is not a
delivery path — do not reach for it** (`BL-20260818-GET-DIAG-TOKEN-EMITS-SECRET-TO-PUBLIC-SURFACE`).
This row previously read *"deliver it for a cloud env var via the
`get-diag-token` workflow, not by hand-copying"*, which was written when
the repo was private and stayed after it went public on 2026-07-07 — the
workflow duly wrote a live bearer into a **world-readable** issue comment
(#1615, 2026-05-21) that still authorized three months later. The
workflow now reads `repository.private` at run time and fails closed on
`public` **and** on an unreadable visibility, so the value only ever
lands on a repo-visible surface when that surface is private. **On a
public repo the operator originates the value and puts it in both places
themselves** (the repo Actions secret, and the consuming environment's
`DIAG_READ_TOKEN`); `set-diag-token` then pushes it to the VM, moving it
one way only and never handing it back.

🛑 **THE DIAG-TOKEN ROTATION QUESTION IS CLOSED — DO NOT RAISE IT.** Operator
decision, 2026-08-30: the token is **not being rotated again**, and the exposure
is an accepted risk. The live value has been readable in a public issue comment
since 2026-05-21 and **still authorizes** — re-measured 2026-08-30T05:09:21Z,
`/api/diag/version` → HTTP 200 (`git_sha 35211baf`) — so this is closed on
evidence, not on fatigue. Two rotation attempts have failed to take (2026-08-18,
and one on 2026-08-30), because the restore mechanism is itself broken
(`BL-20260713-SET-DIAG-TOKEN-RESTORE-BROKEN`, which stays OPEN); proposing a
rotation before that is fixed is proposing an action that does not work. What is
accepted: any reader of that comment holds a working bearer for `/api/diag/*`,
which is **read-only** — diagnostics, journal SELECTs, service state, log tails.
It is not an order path, cannot place/modify/cancel a trade, and carries no
exchange credentials, so the harm is disclosure of internals, not loss of funds.
Do not file a successor row, do not add it to `OPEN-ITEMS.json`, and do not put
it in a review's `flags_raised[]`. Full record:
`BL-20260818-DIAG-READ-TOKEN-PUBLIC-EXPOSURE-UNREMEDIATED` (`wont_fix`).
⚠️ **The one thing that reopens it:** if `/api/diag/*` ever gains a WRITE route
or starts returning secrets, the read-only premise this decision rests on is
gone — re-put it to the operator rather than assuming it carries over.

**Trainer VM** has no HTTP diag API — read it via the `trainer-vm-diag`
relay (arbitrary SSH bash, label `trainer-vm-diag-request`). SSH from a
web session is impossible regardless (proxy is HTTP/HTTPS-only), so
trainer access is relay-only.

