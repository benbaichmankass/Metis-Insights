▶️ **START — MI-278 / U32 (RESEARCH lane)** · session `session_01Wv1BdfEUnmCBZrhvUjeBzb` · branch `claude/mi278-u32-e35-binomial-package-denominator`
- `scripts/research/e35_break_attribution.py`
- `scripts/research/e35_binomial_package_denominator.py` (NEW)
- `tests/test_e35_binomial_package_denominator.py` (NEW)
- `docs/research/m20-u32-e35-binomial-package-denominator-2026-09-13.md` (NEW)
- `docs/research/RESEARCH-CAPABILITY-INDEX.md`
- `docs/DOCUMENT-INDEX.md`
- `docs/claude/performance-review-backlog.json`
- `docs/claude/work/objects/WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER.yaml`

**What:** U12 named 21 surfaces computing a rate on a ROW denominator where fan-out makes rows non-independent. U31 paid one (`bleed_record`) and found the pooled/per-account split is what matters — fan-out is CROSS-ACCOUNT, so a pooled analysis is exposed and a per-account one is essentially immune. `e35_break_attribution.py` is the next: it filters `account_id.startswith("bybit")`, i.e. **pools every bybit account**, and its one-sided binomial takes BOTH its sample size `n` AND its null parameter `p` from row counts — a nuance beyond U31, where only `n` was row-derived.

**Not touching:** `config/**`, `src/**`, any unit file. Tier-1 only; the verdict is a **report**, never a re-grade of any live number.

⚠️ Still unpaid on the fan-out row after this: **16 of U12's 21 surfaces**, three of them Tier-2 dashboard routes (`/api/bot/stats` winRate, `/api/bot/performance`, `/api/bot/attribution`) that a re-derivation must not touch.
