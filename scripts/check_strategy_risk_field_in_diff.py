"""Strategy-risk guard — diff-scan layer (per-strategy risk removal, 2026-06-29).

Designed for the GitHub Actions guard
(``.github/workflows/strategy-risk-guard.yml``) that runs on every PR.
Reads a unified diff from stdin (or the path passed as argv[1]) and exits 1 if
any **added** line re-introduces a per-strategy risk level:

  * a ``risk_pct:`` key under ``config/strategies.yaml``, or
  * a ``strategy_risk_pct`` reference anywhere under ``src/``.

Why this exists
---------------
Operator directive 2026-06-29: position sizing is the ``RiskManager``'s sole
responsibility (account-level ``risk_pct`` basis × an internal confidence
scalar). A strategy carries NO risk level — it only produces order packages.
The per-strategy ``risk_pct`` multiplier (injected as
``meta["strategy_risk_pct"]``) was removed end-to-end. This guard catches a
regression that would silently re-disperse the risk function back across
strategies (the under-sizing footgun) at PR time rather than at audit time.

What it doesn't flag
--------------------
* ``risk_pct`` under ``config/accounts.yaml`` — that is the ACCOUNT-level basis,
  the canonical (and only) place per-trade risk is set.
* The confidence-sizing config keys (``confidence_sizing`` / ``confidence_floor``
  / ``confidence_knee``) — those live in the account ``risk:`` block + the
  RiskManager, not in ``strategies.yaml``.
* Anything in ``tests/`` or ``docs/`` (and this guard script itself).
* Anything carrying an inline ``# allow-strategy-risk: <reason>`` justification.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

_STRATEGIES_YAML = "config/strategies.yaml"
_SRC_PREFIX = "src/"

_YAML_RISK_RE = re.compile(r"^\s*risk_pct\s*:")
_CODE_TOKEN_RE = re.compile(r"\bstrategy_risk_pct\b")
_ALLOW_RE = re.compile(r"#\s*allow-strategy-risk:", re.IGNORECASE)
# Tests/docs legitimately reference the historical token; this guard script +
# its test do too.
_IGNORE_PATH_RE = re.compile(
    r"(^|/)(tests?|test_)/|/test_[^/]+\.py$|^docs/|\.md$|"
    r"^scripts/check_strategy_risk_field_in_diff"
)


def _iter_added_lines(diff_text: str) -> Iterable[Tuple[str, int, str]]:
    """Yield ``(file_path, new_lineno, content)`` for every added line.
    Mirrors the parser in ``scripts/check_env_gate_in_diff.py``."""
    current_file: str | None = None
    new_line_no = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                current_file = None
            else:
                current_file = (
                    target[2:] if target.startswith(("a/", "b/")) else target
                )
            new_line_no = 0
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,\d+)?", raw)
            new_line_no = int(m.group(1)) - 1 if m else 0
            continue
        if raw.startswith("---") or raw.startswith("diff "):
            continue
        if raw.startswith("+") and not raw.startswith("++"):
            new_line_no += 1
            if current_file:
                yield current_file, new_line_no, raw[1:]
            continue
        if raw.startswith("-"):
            continue
        new_line_no += 1


def scan_diff(diff_text: str) -> List[str]:
    """Return human-readable findings (empty list ⇒ clean)."""
    findings: List[str] = []
    for path, lineno, content in _iter_added_lines(diff_text):
        if _IGNORE_PATH_RE.search(path):
            continue
        if _ALLOW_RE.search(content):
            continue
        if path == _STRATEGIES_YAML and _YAML_RISK_RE.search(content):
            findings.append(
                f"{path}:{lineno} — per-strategy risk_pct re-introduced: "
                f"{content.strip()[:120]}"
            )
        elif path.startswith(_SRC_PREFIX) and _CODE_TOKEN_RE.search(content):
            findings.append(
                f"{path}:{lineno} — strategy_risk_pct reference re-introduced: "
                f"{content.strip()[:120]}"
            )
    return findings


def main(argv: List[str]) -> int:
    if len(argv) > 1:
        diff_text = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    else:
        diff_text = sys.stdin.read()
    findings = scan_diff(diff_text)
    if not findings:
        print("strategy_risk_field_in_diff: clean (no offending changes)")
        return 0
    msg_lines = [
        "🚨 STRATEGY-RISK GUARD: a PR re-introduces a per-strategy risk level.",
        "Sizing is the RiskManager's sole responsibility (account-level risk_pct "
        "basis × confidence). See docs/research/position-sizing-confidence-DESIGN.md.",
        "If genuinely required, add an inline `# allow-strategy-risk: <reason>`.",
        "",
        "Findings:",
        "",
    ]
    msg_lines.extend(f"  - {f}" for f in findings)
    print("\n".join(msg_lines), file=sys.stderr)
    for f in findings:
        print(f"STRATEGY_RISK_GUARD\t{f}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
