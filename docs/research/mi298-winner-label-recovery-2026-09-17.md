# MI-298 — the 43% unattributable-winners hole: it reproduces at 2× the population, and label recovery cannot close it

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**MI-298** · RESEARCH lane · Tier-1 · `session_017PQcFJne7qyuVHzH5RZtFC`
**Unit:** the work [`mi295-active-management-focus-2026-09-17.md`](mi295-active-management-focus-2026-09-17.md) § 6 named — *"close the 43% hole first: that is the highest-value research left… it needs the exit-label recovery applied over a wider window."*
**Instrument:** [`scripts/research/m20_u2_winner_close_attribution.py`](../../scripts/research/m20_u2_winner_close_attribution.py), the landed MI-278 U2 module. **Reused, not rebuilt.** No `src/`, `config/`, `deploy/` or order path.

---

## 0. The three deliverables, and which I established

| # | asked | established? |
|---|---|---|
| **1** | the recovered-label population, with a positive control | **YES — but the window SLID rather than grew, and the reproduction control is PARTIAL. Read § 2.** |
| **2** | § 1.2 recomputed | **YES. The 43% REPRODUCES at 2.06× the winners — 42.6% vs 42.9%. It is not a small-n artifact.** |
| **3** | do the two survivors survive it | **NO, AND THAT IS THE ANSWER: the wider population buys ZERO power on them. `ict_scalp_xrp_15m` is still n=10.** |

> ### The finding that changes what to do next
>
> **The hole cannot be closed by label recovery, and the numbers say so rather than my judgement.** Of the 43 unattributable-or-contaminated winners, **38 are invariant across the entire tolerance range** — they are unattributable for a **PROVENANCE** reason, not a labelling one: `unattributable_price` (22 — the exit price is an *estimated anchor*, i.e. **we could not look**) and `contaminated` (16 — `netting_attributed`, 100% ESTIMATED and a live unfixed defect). The tolerance moves **only** `unattributable_level`, 13 → 1.
>
> **So this is a Tier-2 provenance repair, not a research task, and it is NOT mine.** Re-running the recovery at any tolerance cannot recover a price nobody measured.

---

## 1. Input provenance

`GET https://ict-bot.duckdns.org/api/diag/journal?table={trades,order_packages}&limit=1000`, read **2026-09-17T21:2xZ**, browser-direct through Caddy, `DIAG_READ_TOKEN` bearer.

* `trades` — **1000 rows, ids 4882–5881**, `created_at` **2026-08-21T10:06:34Z → 2026-09-17T19:54:19Z**.
* `order_packages` — 1000 rows. ⚠️ Keyed on `order_package_id`, **not** `id`, so an id-range fingerprint of this pull is vacuous (MI-278 U45 records the same trap).

---

## 2. ⚠️ THE WINDOW SLID — it did not widen, and that bounds every comparison below

**The 1000-row cap is HARD and the window is a SLIDING one.** U2 pulled ids **4716–5715**; this pull is **4882–5881**. So it gained 166 newer rows **and permanently lost 166 older ones**.

**Consequence, stated rather than buried: U2's exact population is no longer reachable from any surface I can reach, so I CANNOT reproduce its headline figures and do not claim to.** What follows is a *shifted and wider* window, never a superset.

### 2.1 A wider pull is not reachable, proven with controls rather than assumed

| probe | result |
|---|---|
| `…&limit=2000` | **n=1000**, id range `(4882, 5881)` — identical. The cap is hard. |
| `…&limit=1000&offset=1000` | **n=1000**, id range `(4882, 5881)` — **identical**. `offset` is **silently ignored**. |
| `/api/bot/db/table/trades?limit=2000` | `{"detail":{"error":"invalid_session"}}` — needs `DASHBOARD_API_TOKEN`, not the diag bearer. |

⚠️ **The `offset` result is a NEW instance of a known defect class and is worth more than this unit.** It returns a **plausible wrong answer** — a full 1000 rows, no error — exactly like `/api/bot/performance`'s `days=`. A session paging this endpoint to build a larger corpus would get the same 1000 rows N times and believe it had N×1000. Filed.

### 2.2 The positive control that DOES work — the overlap reproduces byte-identically

Reproducing U2's *population* is impossible (§ 2). Reproducing its *rows* is not. Three mechanisms appear in both pulls with **identical counts and identical PnL to the cent**:

| mechanism | U2 (2026-09-12 pull) | this pull | agree? |
|---|---|---|---|
| `giveback_stop` | 2 rows, **+$2,471.70** | 2 rows, **+$2,471.70** | ✅ |
| `intent_reduce` | 1 row, **+$733.00** | 1 row, **+$733.00** | ✅ |
| `exit_head` | 1 row, **+$34.07** | 1 row, **+$34.07** | ✅ |

**That is the control the unit needed:** it shows the recovery returns the known answer on rows whose answer is already established, before any widened figure is trusted. It does **not** license the population comparison, which § 2 bounds.

**And the instrument's own control fires:** provenance over the 310-row population is `{measured: 157, estimated: 153}` — more than one bucket, so a quiet cell below is a real negative rather than a dead probe.

---

## 3. Deliverable (2) — § 1.2 recomputed, and the 43% holds

**Population, every time:** `closed` · NOT `is_backtest` · `pnl IS NOT NULL` · pairs sleeve excluded. Tolerance **0.05R** where one is quoted, with the full sensitivity curve beside it.

| | U2 · cut 08-27 · pull 4716–5715 | **this pull · cut 08-27** | **this pull · cut 08-21 (widest)** |
|---|--:|--:|--:|
| closes | 183 | 245 | **310** |
| **winners** | **49** | **70** | **101** |
| attributed | 23–27 (47–55%) | 38 (54.3%) | 50 (49.5%) |
| `unattributable_price` | 9 | 12 | **22** |
| `contaminated` | 7 | 8 | **16** |
| `unattributable_level` | 1–5 | 4 | 5 |
| **unattributable-or-contaminated** | **21 (42.9%)** | 24 (34.3%) | **43 (42.6%)** |
| **invariant core** (price + contaminated) | 16 (32.7%) | 20 (28.6%) | **38 (37.6%)** |

**THE 43% REPRODUCES AT 2.06× THE WINNER POPULATION — 42.6% against 42.9%.** It is not a small-n artifact, and that is the substantive result: a hole that survives a doubling of n is structural.

⚠️ **The middle column is why the window must be quoted with the rate.** At U2's *cut* on *this* pull the figure is **34.3%**, ~8.6pp lower. The rate is **window-sensitive**, so `43%` is not a standing constant — the older rows (2026-08-21 → 08-27) are markedly *more* unattributable. **Never quote either number without its cut and its pull.**

### 3.1 The conclusion MI-295 § 1.2 rested on is unchanged, and one part of it got sharper

* **The mass that is attributable still sits on the TARGET**: `tp` 25 + `tp_cross` 8 = **33 of 101 winners**.
* **The lever family did NOT grow with the population** — `giveback_stop` 2 + `exit_head` 1 = **3 of 101**, against **3 of 49** in U2. Doubling the winners added **zero** lever fires. That strengthens U2's *"no lever is cutting winners short"* rather than merely re-stating it.
* `giveback_stop`'s two fires still account for **+$2,471.70** — the largest lever contribution, and the lever working.
* **Short of target: 63 of 101.** `reconciler_filled` is the single largest short-of-target mechanism.

---

## 4. Deliverable (3) — the two survivors are UNGRADEABLE here, and the reason is n

Both MI-278 U4 proposals sit on **`ict_scalp_xrp_15m`**. Measured over the widest population:

| | |
|---|--:|
| closed rows with a pnl | **10** |
| winners | 5 |
| accounts | `bybit_1` ×10 (paper-class) |
| pnl | **−$4,049.90** |
| provenance | **7 `candle_at_close` (ESTIMATED) · 3 `exchange_fill` (measured)** |

**n = 10 — EXACTLY what U4 reported, on a different window.** The wider population buys **nothing** here: 101 winners fleet-wide, of which this leg contributes 5. Against a backtest verdict resting on **n=198 IS / 117 OOS**, ten live rows cannot confirm or refute either proposal, and **7 of the 10 carry an estimated exit price**.

**So: the survivors are neither confirmed nor killed, and a wider journal window is not the instrument that will do it.** That is an honest negative, and it means the Tier-3 decision still rests on the backtest evidence exactly as MI-295 left it — no better, no worse.

⚠️ **Do not read the leg's −$4,049.90 as evidence against the proposals.** It is 10 rows at 30% measured coverage, and both proposals are about *geometry*, not about whether the leg is currently profitable. Quoting it as a verdict would be the unprovenanced-diagnostic failure this repo has a guard for.

---

## 5. What I did NOT do, and why

* **I did not commission another M20 sweep**, per the manager's instruction and MI-295's own finding. **Nor did I find a reason to recommend one**: the sweep population is backtest candles, and this unit measured the *live journal*, so nothing here says the sweep corpus was contaminated.
* **I did not re-run the bracket calibration instrument** or rebuild ML-2.
* **I did not touch** the Tier-3 decision, #11933/#12205, `LOSING_STREAK_MIN_LOSS_USD`, or U47's diag stamp.
* **I did not lower the tolerance to manufacture a recovery.** The curve is published in full; the invariant core is invariant *because* it is reported across the whole range, not because a threshold was chosen.

## 6. What would actually close the hole — for routing, not for me to start

**Not a research unit.** The 38-row invariant core is two provenance defects:

1. **`unattributable_price` (22 of 101)** — the exit price is `candle_at_close`, an estimated anchor. Closing this means the close path recording a measured exit price, which is `src/`, **Tier-2**.
2. **`contaminated` (16 of 101)** — `netting_attributed`, already tracked as a live unfixed defect with real-money rows in its population.

**Until those move, the ceiling on attribution is ~62% of winners, and no tolerance, window or instrument changes that.** That ceiling is the number to put in front of the operator, not the 43%.
