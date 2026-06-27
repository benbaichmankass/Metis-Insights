# Research-session brief — Level-3 options on `alpaca_live`

> **Status:** QUEUED for a dedicated future session (operator directive 2026-06-27).
> This file is both the record that the initiative exists and the **paste-able
> kickoff prompt** for that session. Not started in the session that wrote it.

---

## Paste this to start the session

> **Deep research: which options strategies + underlyings should we trade on
> `alpaca_live` (Level-3), and what infra must we build to do it well?**
>
> We have **Level-3 options clearance on Alpaca** for `alpaca_live` — a **small,
> real-money "test live" account**. I want a rigorous research pass that lands on
> (a) a short list of options strategies + symbols worth trading, each with a real
> edge thesis, and (b) a concrete, feasibility-gated plan for the infra we'd build
> to trade + backtest + monitor them in this bot. **Guiding principle: only
> recommend what is BOTH well-researched AND feasible for us to build here — and
> have a back-and-forth with me; ask before assuming.**
>
> **Constraints to design within (confirm/refine with me early):**
> - **Budget ≈ $150** and **per-trade risk cap ≈ 2%** on this account. It's a
>   real-money *test* account, so we can tune the risk knobs to make sure trades
>   actually fire (the `RiskManager` refuses sub-minimum sizes — premium/contract
>   sizing must produce ≥1 contract within the budget). Clarify with me whether
>   "$150" means per-trade max-loss, total deployable capital, or daily-loss.
> - **Real money → DEFINED-RISK strongly preferred.** Level-3 (Alpaca) is long
>   options + multi-leg **defined-risk spreads** + covered calls + cash-secured
>   puts — NOT naked short (that's Level-4). **Verify Alpaca's actual Level-3
>   capabilities and multi-leg order support before designing around them.**
> - Must integrate with the existing architecture (`config/{strategies,accounts}.yaml`,
>   the per-account `RiskManager`, `AlpacaClient` execution, `order_monitor`,
>   `account_compat_matrix`, the robustness backtest engine) — or you scope the
>   new infra explicitly.
>
> **Strong synergy to exploit:** we just shipped a **live ML volatility-regime
> verdict** (Design A — `btc-regime-15m-lgbm-v2` at advisory; the vol-router beats
> the frozen-edge label in backtest + walk-forward). **Options ARE volatility
> trades**, so the regime vol-verdict is a natural *options-strategy selector*
> (e.g. harvest premium in calm regimes, buy defined-risk convexity in volatile
> ones). Treat "regime → which options structure" as a first-class research thread.
>
> **Research questions (the meat):**
> 1. **Strategy families** that fit a small defined-risk account — put/call credit
>    & debit spreads, iron condors, calendars/diagonals, covered calls (on the ETFs
>    we already hold), cash-secured puts, the wheel. For each: edge thesis, capital/
>    max-loss per trade, P&L profile, and whether it fits ~$150 + 2%.
> 2. **Underlyings** — liquid, optionable symbols whose premiums/strikes fit the
>    budget (tight bid/ask, deep chains). Lean into the ETFs we already trade where
>    options are deepest (SPY/QQQ/GLD/IWM/TLT). Balance liquidity vs premium size.
> 3. **Where is the actual edge?** IV-rank / term-structure / vol-risk-premium
>    harvesting, earnings vol, skew — and how the **regime vol-verdict** decides
>    which structure to deploy when. Be skeptical: options edges are easy to fool
>    yourself on.
> 4. **Backtesting feasibility (the hard part).** Options backtests need historical
>    chains + IV + greeks. What data is realistically available to us (Alpaca options
>    data API? cheap/free historical options sources? a BS/underlying-proxy
>    approximation?). State the fidelity limits honestly and propose the most
>    rigorous *feasible* validation — we do NOT promote an options edge to real money
>    on a hand-wave.
> 5. **Infra scope** — enumerate what must be built, with effort/risk per item and
>    the minimal first slice:
>    - **Execution:** Alpaca multi-leg options orders (`AlpacaClient` today does
>      equity *bracket* orders only — options need a new order path / multi-leg).
>    - **Instrument modeling:** option contracts (underlying/expiry/strike/right/
>      multiplier) + chain-selection logic; how an options cell is declared in YAML.
>    - **Risk sizing:** premium-/defined-max-loss-based sizing (not share-based) —
>      the `RiskManager` adaptation that still fires ≥1 contract under $150/2%.
>    - **Monitoring:** expiry handling, early-assignment risk, P&L on premium, roll
>      logic, optional greeks tracking — in `order_monitor`.
>    - **Data:** options chain + IV + greeks ingestion (live for trading, historical
>      for backtest).
> 6. **Phased, gated plan:** does Alpaca offer **paper options**? If so, paper →
>    shadow → tiny real-money pilot, each gated on the prior. Define the gates.
>
> **Deliverables of this session:**
> - A research memo (`docs/research/…`): ranked strategy/symbol candidates + edge
>   theses + the regime-fit + the honest backtest-fidelity assessment.
> - An infra-scope doc: the build list with feasibility/effort/risk, minimal first slice.
> - A phased build+test+gate plan.
> - A recommended **first pilot** — one strategy + one underlying — that is both
>   well-evidenced and minimal-infra.
>
> **How to work it:** start by reading `CLAUDE.md` + `docs/CLAUDE-RULES-CANONICAL.md`;
> scan the skills catalog (`deep-research`, `new-broker`, `new-strategy`,
> `backtesting`); pull the live `alpaca_live` config + the current `AlpacaClient`
> capabilities so the infra scope is grounded in what exists. Then iterate with me —
> propose, I steer, you refine. Don't build a plan around a capability you haven't
> verified Alpaca actually offers.

---

## Why this is promising (context for the future session)

- `alpaca_live` is already wired (real-money equities, 12 ETF cells) — the broker
  integration, account, and risk plumbing exist; options is an *extension* of a
  working venue, not a new broker from scratch.
- The bot just gained a **live volatility-regime classifier** — the single most
  useful input for *selecting* options structures. Options is arguably the highest-
  leverage place to *use* the regime work we just shipped.
- A small defined-risk account is a sane sandbox to build the options execution +
  sizing + monitoring infra with bounded downside before scaling.

## Open questions for the operator (raise these first in that session)

- Does "$150" mean per-trade max-loss, total deployable capital, or daily-loss?
- Paper-options first (if Alpaca supports it) or straight to a tiny real-money pilot?
- Appetite for paid options-data (for backtest fidelity), or stay free/approximate?
- Keep options on `alpaca_live`, or a separate dedicated options account?
