# M31 Track B — option-chain IV-skew soak + grading plan

**Status: credential-free pipeline BUILT; live soak + grading BLOCKED on the operator
registering the Schwab developer app.** This note pins the exact turnkey plan so the
moment the Schwab OAuth token exists, the soak → grade path is plug-and-play — no
re-design, no re-derivation. Design-before-code, so a fresh session (or the operator)
can pick this up cold.

## Why Track B exists (one line)

Track A found the program's **only** validated signal — `vix_term` (VIX3M/VIX term
structure → SP500), robust through S2 → S3 → walk-forward — but it is **equity-specific
on the free-FRED evidence** (does not generalize to oil; gold untestable on FRED, series
deprecated). Track B is the only path to (a) test whether that term-structure effect
generalizes to **other optioned underlyings** (NDX/DJIA via QQQ/DIA, gold via GLD) and
(b) mine a **richer skew** the FRED VIX family can't express (risk-reversal, butterfly,
smirk slope). It needs live option chains — Schwab is the operator-committed free source.

## What is already built (credential-free, offline-tested, on `main`)

| Stage | Module | Status |
|---|---|---|
| Signal math | `scripts/macro/iv_skew_probe.py` (`skew_features`: `atm_iv`, `rr25`, `bf25`, `skew_slope`, `term_ratio`) | ✅ #7561, 12 tests |
| Vendor normalization | `scripts/macro/schwab_chain_adapter.py` (`parse_schwab_chain`; `fetch_chain` credential-gated stub) | ✅ #7562, 6 tests |

The full pipeline **Schwab payload → normalized rows → skew features** is proven offline
end-to-end (injected payloads). Only the live OAuth fetch + accrued history are missing.

## The single operator hand-off (the critical path)

1. Register the **Schwab developer app** (Trader API product) at developer.schwab.com —
   ~1–3 day approval. Individual-developer tier is free and includes market data
   (option chains + Greeks).
2. Put the **app key** + **app secret** into repo **Actions secrets** (e.g.
   `SCHWAB_APP_KEY` / `SCHWAB_APP_SECRET`). The OAuth **refresh token** expires every
   **7 days** → a weekly re-auth is the standing operational cost (documented, accepted).

Everything below is mine to build + run once those secrets exist.

## Turnkey build order (once the token lands)

1. **OAuth token module** — exchange app key/secret → access token; refresh handling; a
   thin `http_get(url, headers, params)` the adapter's `fetch_chain` already accepts
   (injectable, so it's unit-testable against a canned Schwab body — no live call in CI).
2. **Soak accumulator** (`scripts/macro/iv_skew_soak.py`, next code build):
   `run_soak(symbols, date, *, token, http_get, store_path)` → per symbol:
   `fetch_chain` → `skew_features` → append **one dated row** to a JSONL store.
   Testable offline **now** via an injected `http_get` returning synthetic payloads
   (proves the whole loop without a credential); the store append is pure I/O.
   - **Store schema** (`runtime_logs/iv_skew_soak.jsonl`, one row per (date, symbol)):
     `{date, symbol, underlying, atm_iv, rr25, bf25, skew_slope, term_ratio, n_expirations, n_rows}`.
3. **Daily snapshot job** — a scheduled workflow that runs the soak once per session-day
   after the US close. **Do NOT create the scheduled workflow until the secret exists** —
   a workflow that fails every run until a secret lands is exactly the normalized-alarm
   anti-pattern (`CLAUDE.md` § "If you see something, say something"). The soak *script*
   ships first (default-manual); the timer is wired only when it can succeed.
4. **Grader** — once the store has enough **non-overlapping** history (≈ `N/H` anchors per
   horizon; option chains are point-in-time so the soak IS the history), reuse the exact
   Track A honest funnel: `iv_skew_probe`-side skew feature vs the underlying's forward
   log return → **S2** (non-overlapping directional IC, `|t|≥2`) → **S3** (OOS split +
   cost-aware conviction, `pays_oos`) → **S4-prep** (multi-fold walk-forward). Same rigor,
   same anti-overlap discipline (the entry-11 / M30 trap).

## Underlyings + the questions each answers

| Symbol | Underlying | Question it answers |
|---|---|---|
| **SPY** | S&P 500 | Does the ETF-chain `term_ratio` **reproduce** the robust FRED `vix_term`? (validation control) |
| **QQQ** | Nasdaq-100 | Does the equity-vol-term effect **generalize** to the Nasdaq leg? (the free-FRED gap: VXN has no FRED 3-month sibling) |
| **DIA** | Dow 30 | …and to the Dow leg? (VXD, likewise no FRED sibling) |
| **GLD** | Gold | Fills the **gold cross-asset cell** FRED left `no_data` (LBMA series deprecated) |

Plus the **richer skew** features (`rr25`, `bf25`, `skew_slope`) that the FRED VIX family
cannot express — a genuinely new construction dimension, not just a VIX re-run.

## Grading horizon note

The soak accrues ~1 obs/session-day, so an S2/S3/WF verdict needs a **multi-month** soak
before the non-overlapping N is large enough at the tradeable horizons (H = 5/10/21/42d)
— the same small-N caveat that made `vix_term`'s 42d cell marginal. Set expectations: the
soak is a **slow-accrual** experiment; the first honest read is ~1 quarter out, not days.

## Disposition

The program has established a **rigorous boundary** of where free signal is (macro,
microstructure, and implied-vol-FRED all wrung out — 1 narrow lead) and built the Track B
pipeline to the exact edge of what's possible without a credential. **Track B is now
operator-gated**; this note is the contract for resuming it the instant the Schwab app is
registered.
