# AUD-5 — Infra / VMs (E75 system audit, 2026-09-29)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Lane AUD-5, dispatched by AUD-LEAD. Findings: [`AUD-5.findings.jsonl`](AUD-5.findings.jsonl) (14 rows). Only-find: nothing was changed.

## 1. The question and the answer

**Question.** Does every declared systemd unit and timer on both VMs match what the fleet depends on it doing, and is the live VM's checked-out HEAD within one ict-git-sync interval (~5 min) of origin/main at a sampled instant?

**Answer.** Mostly yes, with three real gaps and no critical or high finding.
- *Freshness — established as observed.* Across 16 sampled instants (09:52–10:00Z) the live HEAD was at most 1 commit and ~3.5 min behind main, and the trainer was 8m49s behind against its 15-min interval. Both within interval, at one window only.
- *Running code — established as observed.* Last runtime commit `0fa97802` (09:32:33Z) precedes the web-api sha `b9d51851` and trader `boot_utc` 09:37:59Z, so the trader runs the code on main. Every commit since is docs/.github-only, which deploy_pull_restart.sh deliberately does not restart for.
- *Failed units:* 0 of 47 live-reported and 0 on the trainer. But `restart_pending=true` on /api/diag/version is a false positive (`SA-AUD-5-restart-pending-false-positive`), and `status.git_sha` reports the checkout, not the process (`SA-AUD-5-status-git-sha-is-disk-not-process`).
- *Still open from CA:* no deploy/ unit has `OnFailure=` (CA-A10-402; still not fixed), so the masking pattern behind PI-20260927-YZRZQ725-0002 would recur silently. New: trainer root disk 93% full; `ict-promotion-readiness.timer` differs from the repo copy; 5 live-VM units have no read surface; 4 trainer units live only in cloud-init.

Merged, deployed, observed: for CA's git-sync work (FIX-CA-01c and the sync cadence) I established **observed** for the sync cadence only. I did not test FIX-CA-01c's operator-stopped-trader behaviour (that needs stopping the trader — out of bounds).

## 2. Findings

| id | sev | one line |
|---|---|---|
| head-lag-live-within-interval | info | NON-ISSUE: live HEAD ≤3.5 min behind main across 16 samples |
| trainer-head-lag-within-interval | info | NON-ISSUE: trainer 8m49s behind, interval 15 min (1 instant) |
| failed-unit-none-live | info | NON-ISSUE: 0 failed of 47 live-reported units |
| trainer-no-failed-units | info | NON-ISSUE: 0 failed, 8/8 timers waiting, all Result=success |
| trainer-offload-drain-installed-ca306-not-manifesting | info | CA-A10-306's units are installed and current |
| drift-retrain-exit-11 | info | NON-ISSUE: exit 11 is declared success |
| onfailure-still-absent | medium | No `OnFailure=` in deploy/ (CA-A10-402 unfixed) |
| trainer-disk-93pct | medium | Trainer root fs 93% used, 3.6G free |
| restart-pending-false-positive | low | `restart_pending` true with no runtime commit outstanding |
| status-git-sha-is-disk-not-process | low | status `git_sha` is the checkout, not the trader's loaded sha |
| five-declared-units-unreadable | low | heartbeat timer/service, env-check, and others have no diag surface |
| trainer-promotion-timer-drift | low | installed timer ≠ repo (1 of 9 hashes) |
| trainer-units-cloud-init-only | low | 4 trainer units unverifiable against repo |
| doc-names-nonexistent-units | low | docs name units that don't exist |

Counts: critical 0 · high 0 · medium 2 · low 5 · info 7.

## 3. Coverage

**Behavioural.** Exercised end to end against real data: 6 of 8 asserted capabilities.
1. Live unit-state read (`/api/diag/services`, 47 units) — done.
2. Live HEAD vs origin/main at sampled instants (16) — done.
3. Trader process start vs last runtime commit — done.
4. Trainer unit/timer inventory (`systemctl` via relay, #14062) — done.
5. Trainer installed-vs-repo hash (#14071) — done, 9 files.
6. Trainer HEAD vs main — done, 1 instant.
7. Failed-unit-behind-active-timer — **not exercised**: no unit was failed, so the masking path was not observed live; relied on CA-A10-400/401.
8. Gateway VM (10.0.0.251) units — **not reachable** by any relay.

**Reading.** Read: `deploy/ict-git-sync.{service,timer}`, `deploy/ict-ib-gateway-watchdog.timer`, `deploy/trainer/ict-promotion-readiness.timer`, the drift-retrain unit header, excerpts of `scripts/deploy_pull_restart.sh` (lines ~140–165, 283–331), `src/web/api/routers/diag.py` `version()`, the CA-A10 findings list, spec §3/§4.5/§5. Cross-referenced 70 unit names from workflows, skills and docs against 60 declared files by grep. **Not read:** the other ~45 unit files' contents, `scripts/install_systemd_units.sh` body, `deploy/training-vm-cloud-init.yaml` beyond grep hits, the other CA lane files, all workflows other than by grep.

## 4. Could-not-look

- **Gateway VM (`ict-ib-gateway`, 10.0.0.251):** no relay reaches it. Its watchdog/reset timers were not inspected; on the live VM the watchdog timer reads `inactive`, which is by design (gateway-only install).
- **Live units outside the diag allowlist** (`ict-heartbeat.*`, `ict-env-check`, `ict-smoke-once`): journalctl returned HTTP 400; state unknown.
- **Trainer follow-up #14077** (installed promotion-timer text, publish unit, disk top-dirs) was queued behind another lane's trainer run and had not returned when this landed; the two findings that would use it say so.
- **Overnight / failure-window lag:** freshness was measured over one ~8-minute window, not across a git-sync failure.
- **FIX-CA-01c behaviour** (operator-stopped trader stays stopped): not testable without stopping the trader.
- **ict-trainer-publish** shows `ExecMainExitTimestamp n/a` despite its timer firing at 09:55:56Z; not explained.

## 5. Spend note

No LLM/API spend beyond this session. Relay cost: 3 trainer-diag issues (#14062, #14071, #14077), 0 live-VM issues (direct diag_fetch worked for all live reads), ~35 direct diag calls. Two large `actions_list` outputs were wasteful.
