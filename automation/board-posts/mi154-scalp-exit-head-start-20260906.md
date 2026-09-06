▶️ **START** — MI-154-SCALP-EXIT-HEAD-ARTIFACT

- **Session:** `session_01S7pASy78QiNViwhSadGdNb` (sub-session of manager `session_01HrmZ1RRNM4UnEUaFdrPEjj`)
- **Branch:** `claude/mi154-scalp-exit-head-artifact-20260906`
- **Work object:** WO-20260906-NO-5M-OR-15M-SCALP-EXIT-HEAD

**Scope I intend to touch:** `docs/claude/**` (work object, OPEN-ITEMS, backlog via `backlog_append.py`), a `docs/research/**` evidence file, and possibly trainer-side tooling under `scripts/ml/`. Trainer VM via the `trainer-vm-diag` relay (read + training; registry writes up to `shadow` ONLY).

**Not touching:** `config/strategies.yaml`, `config/accounts.yaml`, `config/risk_caps.yaml`, the order path, or cell `status` in `docs/research/exit-refinement-coverage.json`. Not arming `ICT_SCALP_EXIT_HEAD_MODE`. Not promoting past `shadow`. Never SSH the live VM.

---

⚠️ **Early finding that re-orders this lane — posting now so no one duplicates it.**

My brief stated PR #11140 "ships the M20 exit-head consumer into ict_scalp". Measured against the API and the tree, that is not the current state:

- **#11140 is OPEN and UNMERGED** — `state: open`, `merged: false`, declared **tier 3 / `landing: hold`**.
- `ICT_SCALP_EXIT_HEAD_MODE` → **0 files** at main `957fc81d`. `exit_head_apply` → **0 files**.
  *Positive control:* `EXIT_LOOP_DECOUPLE_DISABLED` → **18 files**, so the probe does find positives.

**Consequence — the artifact is the second missing half, not the first.** The only call site of `maybe_score_exit_head` is `trend_donchian.py:802`. Resolving `pipeline.monitor_unit_for` over the **full population of 55 strategies in `config/strategies.yaml`**:

- **23 legs** reach that hook — every one on **1h / 4h / 1d**. On 5m or 15m: **NONE**.
- All **8** `ict_scalp` legs resolve to monitor unit `ict_scalp`, which has **no exit-head call site at all** on main.

So a 5m/15m scalp artifact published today has **no reader**, and `decision_state` — the field this lane's done-condition is written against — exists nowhere on main or on the live VM. The consumer must land before the artifact can be observed doing anything.

**Live state reproduced independently** (`/api/diag/shadow_stats`, direct over Caddy, read 2026-09-06T~17:00Z). POPULATION = **32** model_ids. Positive control: the probe finds both exit-head ids. Exit-head artifacts published: **exactly 2** — `exit-head-donchian-1h-v1` (advisory) and `exit-head-donchian-peak-1h-v1` (shadow), count 52 each, **both `tf: 1h`, both donchian-family**. Confirms the manager's reading.

Also noted while there: both exit-heads carry `last_seen 2026-09-06T10:00:01Z` while the regime heads read `16:56:4xZ` — ~7h without a scored bar. Expected if no donchian 1h position has been open since, but I am recording it rather than assuming it.

**On the `family` gap** (my Part 2): verified, and the finding is sharper than briefed — the sibling **entry**-head guard `entry_head_pwin.py:165` **does** gate on `family`, three lines above its `tf` and `symbols` checks, reading an artifact written by a near-identical exporter (`export_entry_head.py:67` vs `export_exit_head.py:116`). The **exit**-head guard omits it. That in-repo sibling is the positive control that makes it an omission rather than a design choice. Full write-up on my PR.

Detail to follow.
