# Lane P — the before/after compat-verdict diff, measured

**Date:** 2026-08-29 · **Closes the direction question in** `OI-20260827-LANE-P-COMPAT-VERDICTS-UNMEASURED`
**Change under test:** `f2ea9e44` (#10364, Tier-3) — merged 2026-08-27 **without** this diff.

> The row's standing instruction: *"If the diff shows verdicts moving in an unexpected direction,
> that is a revert conversation, not a note."*
>
> **It does not. Every verdict that moved became MORE conservative. There is no revert
> conversation.**

---

## 0. Why this could be run tonight when it could not be run on 08-27

The original session recorded: *"The session that wrote it could not run the compat matrix (no numpy
in the sandbox)."* That is no longer true — `numpy 2.4.6`, `pandas 3.0.5`, `scipy`, `sklearn` are all
importable here after `pip install -r requirements-test.txt`. **The blocker was a missing dependency,
not a missing capability**, and it had made the measurement look like it needed the dispatcher armed.

It did not. The dispatcher stays dry.

---

## 1. Method — what is held fixed, and why that is the right experiment

Lane P's change is **account-side**: it fixes (1) `_standard_ruleset` building a `static` terminal
drawdown floor where `accounts.yaml::risk.max_dd_pct` means an intra-day resetting per-trade brake,
and (2) an account whose size could not be established being graded against a **synthetic $10,000**
and emitting a confident `skip`.

So the clean experiment holds **everything except the code** constant:

| held fixed | how |
|---|---|
| the ledger | one synthetic file per arm, byte-identical across both code versions (`sha256` recorded below) |
| `config/accounts.yaml` | the current file **copied into** the before-tree, so config drift cannot confound |
| account balances | one live read, seeded into a local journal DB, same DB for both arms |
| RNG | `--seed 20260829 --n-paths 300` on every run |
| varied | **only** the code: `git worktree` at `f2ea9e44^` vs this branch |

**Positive control run before trusting any of it:** `src/prop/account_rulesets.py` differs between the
two trees, and `src/prop/standard_account_size.py` (added by Lane P) is **absent** in the before-tree.
If those had matched, the A/B would have been measuring nothing.

**Balances are REAL**, not invented: one read of `/api/bot/accounts/balances` (`present: true`,
`source: db`, 11 accounts, `age_seconds` 2391 at fetch), seeded into a sqlite `balance_snapshots`
table whose **DDL was lifted verbatim from `src/units/db/database.py:868`** rather than re-typed —
a test declaring a schema production does not have is a filed failure in this repo
(`BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED`). Verified by having the repo's **own**
`Database.get_latest_balance_snapshots()` read all 11 back.

**Two ledgers, deliberately, to bracket the outcome space** — a book good enough to route and one
that is not, so a one-directional artifact of a single ledger cannot be mistaken for the result:

| ledger | n | total R | win rate | sha256 (first 16) |
|---|--:|--:|--:|---|
| positive | 240 | **+213.50** | 0.558 | `290b2e65e2f520a4` |
| negative | 240 | **−12.20** | 0.275 | `fafe5a09ec24e78b` |

---

## 2. The result — real balances supplied (the live-equivalent condition)

### Positive ledger — **3 of 11 verdicts move**

| account | before | after | size before | size after | state |
|---|---|---|--:|--:|---|
| **`alpaca_live`** | `ROUTE` | **`skip`** | 10,000 *(synthetic)* | **200.10** | measured |
| **`bybit_2`** | `ROUTE` | **`skip`** | 10,000 *(synthetic)* | **305.18** | measured |
| **`ib_live`** | `ROUTE` | **`UNGRADED`** | 10,000 *(synthetic)* | — | unreadable |
| `alpaca_options_paper` · `alpaca_paper` · `alpaca_portfolio` · `bybit_1` · `bybit_portfolio` · `ib_paper` · `oanda_practice` | `ROUTE` | `ROUTE` | 10,000 | real | measured |
| `breakout_1` | `ROUTE` | `ROUTE` | 5,000 | 5,000 | declared *(prop ruleset — unaffected by design)* |

### Negative ledger — **1 of 11 moves**

| account | before | after | note |
|---|---|---|---|
| **`ib_live`** | `skip` | **`UNGRADED`** | balance unreadable — an honest refusal replacing a confident grade |
| all others | unchanged | | |

---

## 3. Reading it

**The direction is one-way, and it is the safe one.** Every move is `ROUTE → skip` or
`→ UNGRADED`. **No verdict moved from `skip` to `ROUTE`; nothing became more permissive.**

**The two `ROUTE → skip` moves are the defect being caught in the act, on real money.** The
synthetic $10,000 made a **$200.10** account (`alpaca_live`) and a **$305.18** account (`bybit_2` —
**mainnet**) look routable for a book they cannot actually carry. Lane P turns two **false ROUTEs on
real-money accounts** into `skip`. That is the change earning its keep, and it is worth noting these
are precisely the two accounts a routing decision would most like to be wrong about in the
optimistic direction.

**The `UNGRADED` move is the second defect.** `ib_live` reports no balance, so before it was graded
against $10,000 and emitted a confident verdict on no evidence; now it declines to grade. Note this
happens **in both ledgers** — it is a property of the account, not of the book.

**Magnitude is modest and concentrated exactly where the mechanism predicted:** the accounts whose
real size is furthest from the synthetic $10,000. The seven accounts holding $66k–$1.34M were graded
against a *smaller* synthetic figure and still routed, so their verdicts are unchanged.

### ⚠️ Only ONE of Lane P's two defects shows verdict movement here

All movement above is attributable to **size resolution**. The **drawdown-type** half (`static`
terminal floor → intra-day resetting brake) produced **no verdict change on either ledger**.

**That is not evidence it never moves one.** It means these two ledgers do not stress it — plausibly
because a book that survives on the resetting model also survives the static floor at these account
sizes. A ledger with a deep early drawdown would be the probe that targets it. **Recorded as
unmeasured rather than reported as no-effect.**

---

## 4. What this does NOT establish — state the population

- **The ledger is SYNTHETIC.** This measures which verdicts Lane P *moves*, holding the book fixed —
  which is the row's question. It is **not** the true `gld_pullback_1h` verdict set. Those still want
  `gld-compat-matrix.yml` dispatched (its emit half needs Yahoo, firewalled here).
- **Balances are one point-in-time snapshot** (`age_seconds` 2391). An account near a threshold could
  sit on the other side of it at a different hour — `bybit_2` at $305.18 is the obvious candidate.
- **`n_paths=300`** for tractability, not the workflow's production setting.
- The before-tree ran with the **current** `accounts.yaml` copied in. That isolates the code change
  and is the intended experiment; it is *not* a reconstruction of what the matrix printed on 08-26.

---

## 5. Reproducing it

```bash
pip install -r requirements-test.txt                    # the 08-27 blocker was just this
git worktree add --detach /tmp/wt_before f2ea9e44^
cp config/accounts.yaml /tmp/wt_before/config/accounts.yaml
# seed /tmp/lp_journal.db::balance_snapshots from /api/bot/accounts/balances
#   (DDL verbatim from src/units/db/database.py:868)
for tree in /tmp/wt_before .; do
  ( cd $tree && TRADE_JOURNAL_DB=/tmp/lp_journal.db python3 scripts/prop/account_compat_matrix.py \
      --ledger /tmp/ledger_pos.jsonl --symbol GLD --seed 20260829 --n-paths 300 --out-dir … )
done
```

The ledger generator is deterministic (`random.Random(20260829)`, 240 draws from a fixed pool); the
`sha256`s in § 1 pin the exact files.
