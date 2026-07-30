"""Guard against new persistent tables that aren't wired to a source of truth.

The 2026-06-21 prop-tickets incident: a feature added a brand-new SQLite table
(`prop_tickets`) and read the dashboard view from it, instead of PROJECTING over
the canonical `order_packages` where the data already lived. The new table was
empty for all historical records, so the dashboard showed "nothing sent" while
real tickets sat in the DB. Unit tests on a fresh DB passed — they can never
catch a wiring error. This is the exact failure class the `db-wiring` skill and
§ Generation Discipline Rule 3 (compliance gate before merge) exist to prevent.

This guard is the MECHANICAL backstop for that rule (docs that aren't enforced
get skipped). It reads a unified diff and fails when an **added** line creates a
persistent table (`CREATE TABLE ...`) in production code **without** an explicit
``# data-wiring: <relationship>`` annotation somewhere in that file's added
lines. The annotation forces the author to consciously declare, at the moment of
creating the table, where the data's source of truth is — exactly the question
that was skipped. It mirrors the ``# allow-silent: <reason>`` override the
silent-empty guard already uses.

What a compliant annotation looks like (the content is human-reviewed, like
allow-silent — the guard only checks presence):

    # data-wiring: canonical store for inbound prop fills — no existing table
    #               holds operator-reported fills; nothing else is the source.
    CREATE TABLE IF NOT EXISTS prop_fills ( ... )

    # data-wiring: enrichment sidecar keyed to order_packages.order_package_id;
    #               the canonical ticket record stays order_packages (projected
    #               via list_outbound_tickets), this only adds the rendered msg.
    CREATE TABLE IF NOT EXISTS prop_tickets ( ... )

What it does NOT flag: tables in tests/ (fixtures), the migration/guard scripts
themselves, or any file whose added lines carry the annotation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# Only production code — tests legitimately spin up scratch tables.
_IGNORE_PATH_RE = re.compile(
    r"(^|/)tests?/|/test_[^/]+\.py$|^docs/|\.md$|"
    r"scripts/check_new_table_wiring\.py$"
)

# A new persistent table. Tolerates `IF NOT EXISTS` + leading quote/paren.
#
# 2026-07-30 hardening (diagnostic-provenance sweep). Two defects made this
# guard's own output untrustworthy — the guard is a diagnostic, and "clean" is
# a claim a human acts on:
#
#  1. It matched PROSE. `CREATE\s+TABLE` fires on any added line containing the
#     words, including a docstring sentence or a comment describing the rule.
#     A guard that flags prose trains reviewers to dismiss its findings.
#  2. The `# data-wiring:` marker was PRESENCE-ONLY and file-scoped, so the
#     path of least resistance to silencing a real finding was to add a line
#     naming a table that does not exist. A guard that is cheaper to lie to
#     than to satisfy is worse than no guard.
#
# Fixed by (1) requiring a table NAME after CREATE TABLE and skipping lines
# that are comments or bare prose, and (2) requiring the marker to name an
# identifier that actually appears in the diff's added lines. Mirrors the
# verified-annotation rule in scripts/check_diagnostic_provenance.py.
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
# A line that is purely a comment/docstring mention, not code creating a table.
_PROSE_LINE_RE = re.compile(r'^\s*(#|"""|\'\'\'|\*|--\s)')
# The required justification marker (anywhere in the file's added lines).
_MARKER_RE = re.compile(r"#\s*data-wiring:\s*(.*)$", re.IGNORECASE)
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# Only scan code-ish files.
_CODE_SUFFIXES = (".py", ".sql")


def _iter_added_lines(diff_text: str) -> Iterable[Tuple[str, int, str]]:
    """Yield (file_path, new_lineno, content) for every added line."""
    current_file: str | None = None
    new_line_no = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            current_file = (
                None if target == "/dev/null"
                else (target[2:] if target.startswith(("a/", "b/")) else target)
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
    """Return findings: files that add a CREATE TABLE with no data-wiring marker."""
    creates: Dict[str, List[Tuple[int, str, str]]] = {}
    markers: Dict[str, List[str]] = {}
    added_idents: Dict[str, set] = {}
    for path, lineno, content in _iter_added_lines(diff_text):
        if _IGNORE_PATH_RE.search(path):
            continue
        if not path.endswith(_CODE_SUFFIXES):
            continue
        m_marker = _MARKER_RE.search(content)
        if m_marker:
            markers.setdefault(path, []).append(m_marker.group(1))
            # The annotation must NOT count as evidence for itself. Pooling its
            # own words into the identifier set is what let a marker naming a
            # fictitious table validate against its own prose.
            continue
        added_idents.setdefault(path, set()).update(_IDENT_RE.findall(content))
        if _PROSE_LINE_RE.match(content):
            # A comment or docstring line MENTIONING create-table is not a
            # table creation. Flagging it is how a guard earns a reputation
            # for noise and starts getting waved through.
            continue
        m_create = _CREATE_TABLE_RE.search(content)
        if m_create:
            creates.setdefault(path, []).append(
                (lineno, m_create.group(1), content.strip()[:100]))

    findings: List[str] = []
    for path, hits in creates.items():
        idents = added_idents.get(path, set())
        for lineno, table, snippet in hits:
            annotations = markers.get(path, [])
            # The annotation must name something REAL in this diff — ideally
            # the table itself, else at least an identifier the change touches.
            # A marker naming nothing that exists does not satisfy the guard.
            if any(table.lower() in a.lower() for a in annotations):
                continue
            if any(t in idents for a in annotations for t in _IDENT_RE.findall(a)):
                continue
            if annotations:
                findings.append(
                    f"{path}:{lineno} — table `{table}` has a `# data-wiring:` "
                    f"annotation that names neither the table nor any identifier "
                    f"in this change; name the real relationship: {snippet}")
            else:
                findings.append(f"{path}:{lineno} — new table `{table}` without "
                                f"`# data-wiring:` annotation: {snippet}")
    return findings


def main(argv: List[str]) -> int:
    diff_text = (
        Path(argv[1]).read_text(encoding="utf-8", errors="replace")
        if len(argv) > 1 else sys.stdin.read()
    )
    findings = scan_diff(diff_text)
    if not findings:
        # State the denominator. "clean" with nothing behind it is
        # indistinguishable from "scanned nothing" — an empty diff, a path
        # filter that matched no files, or a real all-clear all printed the
        # same word (diagnostic-provenance check C).
        n_files = len({p for p, _, _ in _iter_added_lines(diff_text)})
        print(f"new_table_wiring: clean — {n_files} changed file(s) scanned, "
              f"0 unannotated CREATE TABLE")
        return 0
    msg = [
        "🚨 NEW-TABLE WIRING GUARD: a PR creates a persistent table without "
        "declaring its source-of-truth relationship.",
        "Per docs/CLAUDE-RULES-CANONICAL.md § Generation Discipline Rule 3 + the "
        "db-wiring skill: prefer PROJECTING over the canonical store; a new table "
        "is the exception. Add a `# data-wiring: <relationship>` annotation in the "
        "file's added lines stating where the truth lives (canonical store vs "
        "enrichment sidecar) and how history is backfilled.",
        "",
        "Findings:",
        "",
    ]
    msg.extend(f"  - {f}" for f in findings)
    print("\n".join(msg), file=sys.stderr)
    for f in findings:
        print(f"NEW_TABLE_WIRING_GUARD\t{f}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
