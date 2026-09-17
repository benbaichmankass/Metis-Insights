# M20 U39 — `alpaca_live` books DRY on a live account, and the surface that would show it cannot

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-278 U39** · RESEARCH lane · Tier-1 · works `BL-20260909-ALPACA-LIVE-CANNOT-SIZE-A-SINGLE-SHARE-SO-ITS-OPEN-ITEM-IS-UNREACHABLE-NOT-PENDING`.

Instrument: [`scripts/research/declared_vs_effective_dry.py`](../../scripts/research/declared_vs_effective_dry.py)
(56 self-test controls; 26 pytest controls in
[`tests/test_declared_vs_effective_dry.py`](../../tests/test_declared_vs_effective_dry.py)).

<!-- input-provenance
source: https://ict-bot.duckdns.org/api/diag/journal?table=trades&limit=1000
n_rows: 1000
id_field: id
rowset_digest: sha256:5b1cacd688858df3472bd0e07fa784947856685a163885e5c048739d9280d85b
order_digest: sha256:b073ee2cd3f571022c802c446db077d5e9d2bc2a30daf695ae90574fe32f1a1f
fields_covered: account_class,account_id,bias,broker_order_id,closed_at,cost_source,created_at,direction,entry_price,entry_reason,exit_price,exit_reason,fee_maker_usd,fee_taker_usd,funding_paid_usd,id,is_backtest,is_demo,killzone,notes,order_package_id,pnl,pnl_percent,position_size,protection_repair_first_at,protection_repair_last_at,protection_repair_last_kind,protection_repair_last_verified,protection_repairs,reconcile_status,setup_type,sl_order_id,status,stop_loss,strategy_name,symbol,take_profit_1,take_profit_2,take_profit_3,timestamp,tp_order_id
id_first: 5731
id_last: 4732
n_rows_without_id: 0
n_duplicate_ids: 0
-->

---

## One sentence

**The row's cause stopped binding on 2026-08-28 and the row was filed on 09-09: since 2026-09-01 every `alpaca_live` signal — four of them, newest 2026-09-11 — has been journaled `dry_run_no_order_placed` with `is_dry: True` on a `real_money` account whose BOTH declared gates read live, and the operator-facing surface that answers *is this account live* projects `accounts.yaml::mode` alone, so it cannot show it.**

---

## 1. The refusal cause has changed four times; the row reports the modal one

Live pull, 2026-09-13, `alpaca_live` rows in window: **33**, all `status: rejected`.

| era | cause | n | first → last |
|---|---|--:|---|
| 1 | `sizing_refused` (`balance=0.10`) | 13 | 2026-08-18 → 08-25 |
| 2 | `account_mode_dry_run` | 4 | 08-26 → 08-27 |
| 3 | `sizing_refused` | 1 | 08-27 |
| 4 | `account_mode_dry_run` | 5 | 08-27 → 08-28 |
| 5 | `sizing_refused` (`balance=200.10`) | 3 | 08-28 |
| 6 | `account_mode_dry_run` | 3 | 08-28 |
| **7** | **`dry_run_no_order_placed`** | **4** | **2026-09-01 → 09-11** |

Window census: `sizing_refused` 17 · `account_mode_dry_run` 12 · `dry_run_no_order_placed` 4.

**The row's headline cause is the modal one and it last fired on 2026-08-28.** Its remedy — *"the account is funded or its sizing basis changed such that a placement becomes arithmetically possible"* — targets a condition that stopped binding two weeks ago. Fund the account today and the four current-era rows are unaffected: they never reach sizing.

> ⚠️ **The row is not wrong about what it saw.** Its window (08-13 → 09-09) reached further back and caught more era-1/5 rows, and it did report `3x dry_run_no_order_placed`. What it did was characterise the account by the modal cause rather than the current one. That is why this instrument reports **eras** and grades the newest, and why a census alone is the reading that produced the defect.
>
> ⚠️ **Two other claims in the row are also stale.** *"alpaca_live's only routed real-money leg"* — the roster went 1 → **5 legs** on 2026-09-10 (`e88ef612a`, Option A). And `balance=0.10`, not `200.10`, is what the 13 oldest refusals quote: the account was funded between 08-21 and 08-25.

---

## 2. On a real-money account, with both declared gates live, the executor booked DRY

All four current-era rows:

| id | created | strategy | `account_class` | `is_demo` | `notes.is_dry` | reason |
|--:|---|---|---|--:|---|---|
| 5306 | 2026-09-01T16:05:36 | `tlt_pullback_1h` | `real_money` | 0 | **True** | `dry_run_no_order_placed` |
| 5415 | 2026-09-03T13:30:33 | `tlt_pullback_1h` | `real_money` | 0 | **True** | `dry_run_no_order_placed` |
| 5558 | 2026-09-08T13:30:28 | `tlt_pullback_1h` | `real_money` | 0 | **True** | `dry_run_no_order_placed` |
| 5686 | **2026-09-11T13:55:37** | `tlt_pullback_1h` | `real_money` | 0 | **True** | `dry_run_no_order_placed` |

`execute.py` writes that reason **only** when `_genuinely_dry` is true.

**Both declared gates were permissive at all four timestamps — verified against git history, not against today's file:**

* `config/accounts.yaml::alpaca_live.mode` = `live` since **2026-08-29** (`c1f50fc5d`); `dry_run` before that, which is exactly what eras 2/4/6 record.
* `config/strategies.yaml::tlt_pullback_1h.execution` = `live` across every commit in the window.

`/api/bot/config` read 2026-09-13T05:13:54Z returns
`trading_mode.live_per_account.alpaca_live: true`, on `git_sha 237b1bea4` with
`restart_pending: false`.

> ⚠️ **THAT LAST READING IS NOT INDEPENDENT EVIDENCE, AND § 4 IS WHY.** The 09-06
> row and MI-200's update both cite it as *"Runtime view agrees"*. It agrees **by
> construction** — it is a projection of the same `accounts.yaml::mode` already
> counted above, so it can only ever restate gate 1. It corroborates nothing, and
> treating it as a third confirming surface is what leaves the search with
> nowhere to go.

---

## 3. There is a third gate, and the canonical doc says there are two

`Coordinator.multi_account_execute` resolves `effective_dry` from **three** inputs:

```python
account_dry = bool(getattr(account, "dry_run", False))      # 1. accounts.yaml mode
...
state_dry = account_state_dry_run(account.name)             # 2. config/account_state.yaml
if state_dry is True and not effective_dry:
    effective_dry = True
...
if execution_mode(str(pkg.strategy)) == "shadow":           # 3. strategies.yaml execution
    effective_dry = True
```

`CLAUDE.md` § "The two execution gates" states *"Exactly two declared, default-permissive switches decide whether a strategy trades"* and *"There is no third gate"*. **`config/account_state.yaml` is a third one.**

> 🛑 **IT IS NOT THE CAUSE HERE, AND THIS MUST NOT BE READ AS DISCOVERING ONE.**
> `BL-20260906-ALPACA-LIVE-FOLDS-DRY-RUN-WHILE-ACCOUNTS-YAML-AND-THE-STRATEGY-BOTH-DECLARE-LIVE`
> exists, is open, and **MI-200 already ELIMINATED this exact branch on 2026-09-08**:
> the repo's `account_state.yaml` declares only `bybit_1` and `bybit_2`, so
> `account_state_dry_run("alpaca_live")` returns `None` and the branch is a
> no-op. I reached the same file independently and reproduced that elimination —
> it is a confirmation, not a find. **The remaining live candidate is the one
> MI-200 named and could not observe: the `dry_run` PARAMETER passed into
> `multi_account_execute` (`coordinator.py:1314`), which overrides both declared
> gates.** A session arriving here should not re-walk `account_state.yaml`.
>
> ⚠️ **What IS worth recording about it is a DOC-vs-CODE gap, not a cause.** It is
> not the anti-pattern the Prime Directive names — declared in its own file, with
> its own header, dry-only by construction (*"can never force an account live"*),
> and a sanctioned `/set-account-mode` write path. It is not a hidden default-off
> `*_ENABLED` flag. **But it is not one of the two**, and a session reading the
> canonical doc eliminates two gates, finds the account still dry, and has
> nowhere left to look. MI-200 had to read the source to know the third fold
> existed at all.

---

## 4. And the operator-facing surface cannot show it

`/api/bot/config::trading_mode.live_per_account` is written by
`runtime_status._read_live_per_account`, whose whole body is:

```python
raw_mode = str(cfg.get("mode", "live")).strip().lower()
out[name] = raw_mode not in {"dry", "dry_run", "dry-run", "paper"}
```

**It reads `accounts.yaml::mode` and nothing else.** It folds neither `account_state.yaml` nor `execution: shadow`. Yet:

* its own docstring says *"Resolution mirrors `src.units.accounts._resolve_mode` — **the canonical resolver the executor uses**"*, which is true of one of three folds and false of the resolution;
* the route's published note calls the field *"**the pipeline's runtime view**"*.

So an account can read `live: true` on the only surface that answers *is this account live* while the executor books every dispatch dry. That is **UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A** — the label names a quantity the code did not compute — on the **real-money execution gate**.

---

## 5. What could NOT be determined, stated rather than guessed

> ⚠️ **THE DRY FOLD ITSELF IS NOT A NEW FINDING.** It is
> `BL-20260906-ALPACA-LIVE-FOLDS-DRY-RUN-WHILE-ACCOUNTS-YAML-AND-THE-STRATEGY-BOTH-DECLARE-LIVE`,
> open since 2026-09-06 and independently reproduced by MI-200 on 09-08. What
> this unit adds is **two more rows (5558, 5686) extending it to four signals
> over ten days, newest 2026-09-11**; the **era analysis** that shows a SECOND
> open row describes the same account with an incompatible and now-stale cause;
> and the **surface correction** in § 4.

`dry_attribution` returns **`unattributed_dry`**, scoped to the current era.

**Eliminated, with evidence:**

| candidate | eliminated by |
|---|---|
| the account gate | git history — `mode: live` since 2026-08-29 |
| the strategy gate | git history — `execution: live` across the window |
| `exchange_client is None` | **the reason string**: that branch writes `exchange_client_unavailable_no_order_placed` by construction (BL-20260707-MGCTREND-REASON-MISMATCH), and all four rows say otherwise |

**Also eliminated, by MI-200 on 2026-09-08 and reproduced here:** the
`account_state.yaml` fold (no `alpaca_live` key → the accessor returns `None`),
and the credentials/`configured=False` path (the venue answered `ACTIVE`,
`trading_blocked: false`, `buying_power 200.22`).

**Remaining, and it is the one MI-200 named:** the **`dry_run=` parameter passed
into `multi_account_execute`** (`coordinator.py:1314` — `if dry_run is not None:
effective_dry = bool(dry_run)`), which overrides both declared gates and is not
observable from a read-only surface. A VM-side `account_state.yaml` edit is a
distant second: the file is git-tracked and the repo copy has no such key, so a
VM-side divergence would also have to survive `ict-git-sync`.

**The decisive read this row has asked for since 2026-09-06 is still not done** —
`get-env` against `/proc/<MainPID>/environ` on the running trader. It needs a
dispatch a research container cannot make.

> ⚠️ **`unattributed_dry` is the honest terminal state, not a fallback bucket.** Naming a cause here would be inventing one. A planted *"unattributed silently becomes account_mode"* is caught by three controls.

---

## 6. Why this is loud: two `loud: true` open items are waiting on it

* `OI-20260831-ALPACA-LIVE-FIRST-REAL-MONEY-LEG-ROUTED-BUT-HAS-NEVER-TRADED` — clears on a placement by `tlt_pullback_1h` **and a complete round trip**.
* `OI-20260910-ALPACA-LIVE-OPTION-A-FOUR-LEGS-ADDED-TO-REAL-MONEY-AND-THE-ORDER-PATH-HAS-NEVER-BEEN-EXERCISED` — clause (3) needs one `order_packages` row from the four new legs, and its text states *"on this account the `strategies:` list is the ENTIRE execution gate — mode is already `live` and there is no second gate behind it"*.

**That premise is contradicted by the journal.** While the current era holds, neither clears — and both read as soaks in progress rather than as blocked. `OI-20260910`'s clause (3) also asks for a positive control (a long package on `alpaca_paper`/`alpaca_portfolio` in the same window); this finding says the control would be necessary but not sufficient, because the alpaca_live side is refused before dispatch regardless of whether a setup occurred.

> ⚠️ **This does NOT establish that the four Option-A legs would place if the dry condition were lifted.** They are 1d legs, three have no closed rows anywhere, and no long has ever reached this order path. Absence there still needs its denominator.

---

## 7. Verification

* **56 self-test controls, 26 pytest controls.**
* **11 defects planted, all 11 caught** — among them *attribute over the WINDOW instead of the current era* (2 fired), *report the MODAL cause as the current era*, *an absent `is_dry` reads as False* (3 fired), *fold `client_unavailable` into the dry bucket* (3 fired), *an unknown declared state reads as dry*, *mixed causes grade `structurally_unable`*, *no rows grades `agrees_live`*, *an unquoted balance becomes 0.0* (3 fired), *`unattributed_dry` silently becomes `account_mode`* (3 fired), and *drop the loud real-money warning*.
* **Two plants were malformed on the first pass and are recorded as such rather than counted** — one embedded a literal `\n`, and one renamed a loop variable inconsistently so the run CRASHED (exit 1, zero `FAIL` lines), which my grep-based counter would have scored as a clean no-op. Both were re-run correctly: the real defect fires 1 control, the real no-op fires 0.
* **2 no-op sanity plants fired zero.** Six plants re-run against the pytest surface: 6/6 caught, no-op green.

---

## 8. Scope

**No code outside `scripts/research/` and `tests/` is modified.** `src/core/coordinator.py`, `src/units/accounts/execute.py`, `src/runtime/orders.py`, `src/web/runtime_status.py`, `config/accounts.yaml`, `config/strategies.yaml` and `config/account_state.yaml` were **read only**. No parameter is proposed. The remedies implied — folding the third gate into `live_per_account`, or reconciling `CLAUDE.md`'s "two gates" with the code — are **Tier-2** and are not applied.

---

## 9. Standing

`OI-20260911-THE-DIRECTIONAL-LEGS-BROKE-ON-2026-08-30-AND-THE-CAUSE-IS-UNATTRIBUTED` is `loud: true` and unchanged: **17 consecutive losing days, −$38,851.81**, onset 2026-08-27, bounded below by a **+$4,172.64 winning day on 2026-08-26**. U39 does not address it — `alpaca_live` is not among the accounts in that streak.
