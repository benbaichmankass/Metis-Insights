✅ **DONE** · ping delivery ledger read surface · session `session_01TASYv35o6XciFnMC9wmnHi` · **PR #10719 merged (`c04f2b05`)** · CI **6/6 green**

Scope was `src/web/api/routers/diag.py` (`_LOG_FILES`) + the `CLAUDE.md` `log_file` row + one new test. Releasing the file. Tier-1 throughout; `config/`, the order path, `OPEN-ITEMS.json` and the backlog files were not touched.

---

## 1. The read surface is live and it works

```
GET /api/diag/log_file?name=pending_pings_delivered
{"path":"/home/ubuntu/ict-trading-bot/runtime_logs/pending_pings_delivered.txt",
 "present":true,"size_bytes":4680,   ← 72 recorded hashes
 "lines":[... ,"8ccf385180c770542d39a2f8325a31ae2e5a55552c22cb68b9fdd1d0b1c1b263"]}
```

⚠️ **Note the path, because it is the whole point.** The obvious implementation — `runtime_logs_dir() / "pending_pings_delivered.txt"`, which is how all 45 sibling entries are written — would have resolved to `/data/bot-data/runtime_logs/…` and returned **`present: false` forever**.

`notify_on_pull.py` never calls the path helpers; it hardcodes `REPO_ROOT / "runtime_logs"` and runs from `ict-git-sync.service`, which carries **no** data-dir drop-in. The reader runs in `ict-web-api.service`, which **does**. So the helper form points the reader at a path nothing writes, and serves an absent file that reads as *"nothing was ever delivered"* — the writer/reader split that hid the `ict-hourly-snapshot` balance stall for ~3 weeks (`BL-20260611-M15-2`), rebuilt on the surface meant to detect it.

Anchored to `repo_root()` instead. The new test derives its expectation by importing `notify_on_pull.DELIVERED_HASHES` rather than restating a path, and is **negative-controlled**: plant the naive form with `DATA_DIR=/data/bot-data` and it fails naming both paths.

**I did not "fix" this by moving the writer onto the helper.** Moving the ledger empties it, and an empty ledger re-fires every retained line in `pending-pings.jsonl` on the next pull — a ping storm at a sleeping operator, out of an observability change.

## 2. The digest: `delivered`

Three independent confirmations, and the first two did not need this PR — `journalctl?unit=ict-git-sync.service` was already allowlisted.

| drain | skipped as delivered | digest hash present | outcome |
|---|---|---|---|
| **00:24:33Z** (`cc1d71e → 523ad4b`) | **49** | no | **`Queued 1 ping(s)`** |
| 00:45:49Z (`523ad4b → 49f03e37`) | **50** | **yes** | `No pingable events` |

The file held 50 lines; 49 skipped, exactly 1 queued. On the next pull the digest's own hash is in the skip list — which only happens after `_record_delivered_hash`, i.e. after a successful enqueue. It recurs on all five later drains. And the new read surface now shows that hash in the ledger directly.

Key derived from code, not guessed: `_line_hash` hashes the **raw stripped jsonl line**, not the parsed payload.

⚠️ **To the precision the evidence supports:** the ledger records *enqueued to the bridge inbox*, not *Telegram returned 200*. `ict-claude-bridge.service` is `active` and drains every ~5s, but it logs **only that the job ran, never a send outcome** — so the last hop is not confirmable from the VM. Reported below.

## 3. ⚠️ The 02:20Z slot DID NOT FIRE

Watched 01:46Z → 03:05Z, polling `main` every 60s.

- **No new `work_digest` row on `main`** at +45 min. A run always appends one — `no_changes` is an explicit state, and the workflow's own header says *"the empty run IS the evidence the cadence is alive."*
- **No `automation/work-digest*` branch**, and **no digest PR**: #10723 (02:12:41Z) and #10724 (02:25:51Z) bracket the slot with nothing between them.
- **The denominator is real** — six PRs landed across that window (#10722–#10727), so the PR/merge pipeline was demonstrably working. This is not "GitHub was down."

**Two observations, and I am deliberately not turning them into a rate:**

| slot | outcome |
|---|---|
| 22:20Z (2026-09-01) | a run appeared at **00:19:37Z** — ~119 min after the nearest prior slot, matching no configured slot |
| **02:20Z (2026-09-02)** | **no run within +45 min** |

n=2 bounds nothing. GitHub documents scheduled runs being delayed under load **and dropped entirely**, so both observations are individually consistent with documented behaviour. What they do NOT support is "the cron works."

I also killed the obvious alternative explanation: `work-digest.yml` has had exactly **two** cron values ever (`d1f173bf` `20 6 * * *`, then `6c230ec3` `20 2,6,10,14,18,22`, live from 20:11Z on 09-01). The 6-slot cron was already in force at 00:19:37Z, so that time is off-slot under either. The "unexplained" stands.

## 4. Will a digest reach @ict_cluade_bot at 06:20? — **Not reliably, and the workflow is what breaks**

Everything **below** the workflow is proven working tonight:

- drain → enqueue → ledger: **proven** (§2)
- `target="claude"` → `pending_claude_pings` → bridge: wired, bridge `active`
- git-sync pulls every 5 min, so a landed row reaches the bridge within ~5 min
- **the bridge now reports `token=TELEGRAM_CLAUDE_BOT_SECRET[dedicated] chat=TELEGRAM_CHAT_ID[fallback] deliverable=True isolated=True`** on three consecutive restarts (00:45:57Z, 01:34:36Z, 01:40:22Z)

The single point of failure is **the scheduled trigger firing at all**. One of two observed slots produced nothing; the other was ~2h off.

**If the operator needs the 06:20 report to exist, the reliable lever is `workflow_dispatch`** — `work-digest.yml` has it, and it was put there for exactly this ("so that observation can be forced rather than waited for"). ⚠️ A dispatch run is **not** a cron run and must not be recorded as one.

## 5. Rows to file — reported, not filed (backlog files are out of my scope)

1. **⚠️ HIGHEST VALUE — `work_digest.py`'s once-per-UTC-day latch would suppress the 06:20 digest if it ever worked.** `_already_sent_today()` reads `runtime_logs/work_digest_state.json`; the workflow calls `--write` **without `--force`**. That file is `.gitignore`d, is not in the workflow's `paths:` (only `pending-pings.jsonl`), and there is no `actions/cache` or artifact step — so on every ephemeral runner it is absent and the latch always admits. **The 6×/day cadence works only because the latch never persists.** It is a vestige of the daily-06:20 design. Make that state durable and the 02:20 run sets `lastDigestDay`, then **06:20 is silently suppressed** — the code prints and exits 0, so it fails as silence, on the operator's morning report.

2. **The digest window is 24h on a 4-hourly cadence, so digests overlap by 20h.** `BASE = git rev-list -1 --before='24 hours ago' main` was not narrowed when the cron went from daily to 6×/day. The same state change is reported in up to six consecutive digests, and the ledger will **not** suppress the repeats — each row carries a distinct `at`, so each is a distinct line with a distinct hash. Dedupe is per-line, not per-content.

3. **`ict-claude-bridge.service` logs job execution but never send outcomes.** "Telegram accepted it" and "the POST failed" are indistinguishable in its journal — the same read-surface class this PR just closed, one hop further down the same chain, and now the only remaining blind spot on the ping path.

4. **`notify_on_pull.py` bypasses the path helpers** (§1). Load-bearing today, but the ledger lives outside `DATA_DIR`: not on the block volume, not backed up with it, reset by a re-provision or re-clone. A decision, not a drive-by fix.

5. **`pending-pings.jsonl` is append-only and never truncated** — rows from 2026-07-06 are still present. The ledger is the only thing preventing a 50-ping replay.

## 6. Corrections — evidence, not conclusions

- **`OI-20260901-CLAUDE-CHANNEL-SEPARATION-SHIPPED-BUT-UNPROVEN` is STALE on its (a) half.** `CLAUDE.md` records the bridge logging `isolated=False` at 21:37:08Z with the dedicated token key missing, needing a Tier-2 set-env. **That key has since landed** — measured `isolated=True` with `token=…[dedicated]` on three restarts. Its condition (b) — a human confirming which conversation it lands in — is still open and, by that row's own reasoning, **cannot** be settled from the VM: in a DM the chat_id is the operator's own id for every bot. ⚠️ I did **not** edit the row (out of scope) — flagging it for whoever owns it.
- **The brief's "a digest also lands in the trader bot, a duplicate not an absence"** rests on that same stale state. With `isolated=True` the duplicate path is likely gone; unverifiable from here.
- **The VM-has-pulled-past-it claim cited `git_sha_on_disk`** — the **disk** sha, not the running one, and `diag.py`'s own comment says this endpoint exists because disk cannot answer that question. The conclusion survives: `d74e0b2c` **is** an ancestor of the running sha (`git merge-base --is-ancestor`).
- **`OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON` is untouched.** Its population is `probes.yml` + `due-list.yml`. `work-digest.yml` is a different workflow — and tonight's 02:20 miss is, if anything, *consistent* with that row rather than a refutation of it. It should not be closed.
