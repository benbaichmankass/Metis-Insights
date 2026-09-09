📋 MANAGER REPORT — MI-222 venue-truth detector · PR #11550 · session `session_01WmFdLwfq4U5aLLjFRDDy6b`

For the manager (`session_01HrmZ1RRNM4UnEUaFdrPEjj`). The three items asked for, with the population behind every number. **No order was placed, modified or cancelled on any account.**

---

## (i) The structural finding — CORRECTED, not confirmed

**I was told to build a roster-driven sweep over `config/accounts.yaml::<account>.symbols`, because it was "the only enumeration complete BY CONSTRUCTION" and did not exist. It already exists.**

`src/units/accounts/clients.py:1410`, inside `account_open_positions`, after the settleCoin page:

```python
for sym in _bybit_configured_symbols(account):
    if not isinstance(sym, str) or not sym or sym in seen:
        continue
    r2 = client.get_positions(category=category, symbol=sym)
```

`_bybit_configured_symbols` (`clients.py:1221`) falls back to loading `accounts.yaml` by `account_id` — the exact config-not-venue source named in my brief as the novel constraint. Added by `BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND` **for the identical failure shape on the identical real-money account**.

So list (a) is **account-wide ∪ roster-scoped-per-symbol**, not the account-wide list. ETHUSDT was inside its domain the whole time. And per MI-221 § 2 the cross-check **ran on ETHUSDT, succeeded, and returned no row with `size > 0` — twice**, verified with a positive control.

**My open parameter is answered NO, not YES.** Had I built the directed sweep it would have reported `bybit_2`/ETHUSDT **CLEAN** on every run — the blind detector the object forbids. This is the decision I most need you to ratify or overrule.

Verified line-by-line, all from source: (a) enumerates `account_open_positions` at `order_monitor.py:3128` ✔; (a) is only the account-wide list ✘ **REFUTED**; (b) is `FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0` at `order_monitor.py:9846-9849` ✔ (5471 is `closed`, genuinely missed); nothing runs `journal_venue_audit.py` ✔; ETHUSDT ∈ `bybit_2.symbols` ✔.

## (ii) Design — read source named, cost as a number

**The gap is not enumeration. It is that the read collapses states**, at `clients.py:1379-1381`:

```python
size = _f(p.get("size"))
if size <= 0:
    return          # no counter, no log, no positionIdx
```

*venue genuinely flat* / *venue returned a zero-size row* / *venue returned no row* are **one value** — our own "states are never collapsed" must-not, violated **inside the reader every sweep is built on**, and therefore inherited by every consumer. A detector cannot distinguish states its reader has already merged. The same missing instrument blocked root-cause on **two real-money P1s one day apart** (`BL-20260908-…ROWS0…`, MI-221 § 4c): an instrument gap, not a per-site nit.

**Read source proposed: the raw `get_positions` payload — zero-size rows and `positionIdx` included — exposed additively, without changing what `account_open_positions` returns.** #1 on MI-221's "what would settle it". It is the precondition for any non-blind detector; until the reader can say *which* of the three states it saw, anything above it can only say "clean".

**Cost, with population.** The per-cadence cost is **already being paid**: 1 settleCoin page + 1 scoped read per configured symbol not already surfaced, per `account_open_positions` call. Population = all 3 `exchange: bybit` accounts in `config/accounts.yaml`: `bybit_1` **6**, `bybit_2` **4**, `bybit_portfolio` **4**. **A new roster sweep duplicates this spend and adds zero coverage.** The proposed raw-payload surface adds **0** venue calls — it reports the payload of reads already happening.

**Two findings I did not go looking for:**
- The roster sweep **cannot be switched off from the cfg dict**. `symbols` is only preferred when *truthy*, so `symbols: []` on a real account is backfilled from `accounts.yaml`. For any account in that file the sweep is **unconditional**. (Found by getting a test assumption wrong and checking rather than adjusting the assertion.)
- **A zero-size sibling listed first does NOT mask a live book** — `_emit` returns on `size <= 0` *before* `seen.add(sym)`. Only **two non-zero books** collapse. This narrows MI-221 § 4a: that mechanism still requires a second genuinely live ETH book at 2026-09-08T13:37Z, still unestablished.

## (iii) The PR — #11550, Tier-1, held for you

Opened via `pr-opener.yml` (MCP `create_pull_request` 403s here, as does `add_issue_comment` — hence this relay). **Auto-merge deliberately NOT armed. No `merge_slot` claimed.**

Ships **Tier-1 only**: `tests/test_accounts_clients_position_read_state_collapse.py` (10 characterization tests) + evidence doc + `DOCUMENT-INDEX.md` row. No `src/`, no `config/`, no `deploy/`, no workflows, no runtime code.

Declared `landing: "hold"`, `hold_reason: awaiting_evidence`. Two reasons: **my work object is not on `origin/main`** (eight other `WO-20260909-*` are), so the contract does not exist to judge this against; and the substance is a **direction change**, which is yours to ratify. The diff would otherwise qualify to self-land. **On your word I flip to `self` and arm with the slot claim in one push per R13.**

**Deliberately not done:** no new roster sweep; `journal_venue_audit.py` **not scheduled** (its venue side reads the same collapsed function, so a cadence buys a green light over an unread book); **no change to `_emit`/`account_open_positions`** — that return value feeds the live close decision at `order_monitor.py:4348`, Tier-2/3, which MI-221 explicitly HELD.

**Verification:** 32 tests pass (my 10 + the 22 existing in the sibling file, run together to prove I did not break what I extended); `ruff` clean; `check_document_index.py` OK (population 1031, registered 1031); `check_canonical_doc_coherence.py` all passed; `check_pr_landing.py` OK `state=declared_hold`. **`tests/test_diag_exchange_positions.py` could NOT be run here** — `fastapi` absent from this container. Stating it unrun rather than implying coverage; untouched by this diff, CI covers it.

## ⚠️ Done-condition clause (3) is unsatisfiable as written — needs your call

It requires observing the detector report the **known positive**. There is **no observable known positive**: no repo surface can see the ETH, and MI-221 § 5.1 records three venue reads flat against the operator's eyes, **unreconciled**. MI-221 § 5.5 also records that the operator's own figures do not reconcile (+0.6464 on 0.04 implies a mark near **2470.13**, not the stated 2457.59, which gives **+0.1448**). **That is not a basis for dismissing the operator's report** — it means "is there a live ETH position at all" is genuinely open.

The 10 synthetic positives clear the **detector-capability** half of the clause at zero cost, touching no venue. The **live** half needs either the raw-payload surface or a planted control — and **planting one touches a real-money venue, which I will not do without your explicit instruction.**

**Three decisions I need:** (1) ratify or overrule not building the directed sweep; (2) approve the raw-payload surface and name its home — a route on `ict-web-api` is Tier-3 per § VM authority split; (3) rule on clause (3) — accept synthetic positives, or authorise a planted control.
