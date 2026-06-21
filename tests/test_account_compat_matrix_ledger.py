"""Round-trip proof for account_compat_matrix's --ledger path.

The compat matrix hard-rejects any --strategy not in scripts.backtest_system.ROSTER,
so aliased ETF / alt research cells (which live in the standalone harnesses, not
the BTC system engine) can't be scored that way. The --ledger path synthesizes a
closed-trade ledger from a harness --emit-trades JSONL such that it round-trips
EXACTLY through src.prop.montecarlo.ledger_to_r_sequence — i.e. the per-trade R
the Monte-Carlo reads back equals the emit's net_r to floating-point tolerance.

This test exercises the synth helper (imported from the script) against the real
ledger_to_r_sequence to prove the compounding walk reconstructs each net_r.
"""

from __future__ import annotations

import json

from scripts.prop.account_compat_matrix import synth_ledger_from_emit
from src.prop.montecarlo import ledger_to_r_sequence

_BASE_ACCOUNT = 5000.0
_BASE_RISK_PCT = 0.5


def _write_emit(tmp_path):
    """~60 rows alternating net_r +1.0 / -0.5 with hourly entry_time stamps."""
    rows = []
    for i in range(60):
        nr = 1.0 if i % 2 == 0 else -0.5
        # 2024-01-01T00:00:00Z + i hours (zero-padded, well-formed ISO).
        ts = f"2024-01-01T{i // 60:02d}:{i % 60:02d}:00+00:00"
        rows.append({"entry_time": ts, "exit_time": ts, "net_r": nr})
    p = tmp_path / "emit.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p, rows


def test_ledger_roundtrip_is_exact(tmp_path):
    emit_path, rows = _write_emit(tmp_path)

    # Reload the emit exactly as the script does (lines of JSON).
    loaded = [json.loads(line) for line in emit_path.read_text().splitlines() if line.strip()]
    assert loaded == rows

    ledger = synth_ledger_from_emit(
        loaded, base_account_size=_BASE_ACCOUNT, base_risk_pct=_BASE_RISK_PCT,
    )
    assert len(ledger) == len(rows)

    recovered = ledger_to_r_sequence(
        ledger, initial_balance=_BASE_ACCOUNT, base_risk_pct=_BASE_RISK_PCT,
    )
    assert len(recovered) == len(rows)

    for lt, src in zip(recovered, rows):
        assert lt.r_multiple == src["net_r"] or abs(lt.r_multiple - src["net_r"]) <= 1e-6, (
            f"round-trip drift: got {lt.r_multiple} expected {src['net_r']}"
        )


def test_synth_ledger_populates_keys_ledger_reader_uses(tmp_path):
    _, rows = _write_emit(tmp_path)
    ledger = synth_ledger_from_emit(
        rows, base_account_size=_BASE_ACCOUNT, base_risk_pct=_BASE_RISK_PCT,
    )
    # ledger_to_r_sequence reads `pnl` and `exit_ts` (via _get / _exit_key).
    for row in ledger:
        assert "pnl" in row
        assert "exit_ts" in row and row["exit_ts"]
