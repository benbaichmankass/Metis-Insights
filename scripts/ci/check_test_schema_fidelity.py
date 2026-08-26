#!/usr/bin/env python3
"""A test fixture may not declare a money-table column production does not have.

WHY THIS EXISTS
---------------
`BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED`: `pairs_executor._open_pkg_meta`
queried `order_packages` on `account_id` and `id` — two columns that table does
not have (its PK is the TEXT `order_package_id`, not a rowid alias). Production
raised `OperationalError`, a broad `except` swallowed it at DEBUG, every open
pair read as unreadable, and `max_hold_bars: 20` was **never once evaluated**;
`pairs_bnb_btc` legs reached 300-595 bars.

The tests that should have caught it declared their own `order_packages` with
`id INTEGER PRIMARY KEY, account_id TEXT` — a schema production does not have —
so they passed against a fictional table. CLAUDE.md records the fix ("they now
lift the DDL from src/units/db/database.py"); that fix covered the PAIRS tests.
Measured 2026-08-20 during the full-system audit: **20 other test files still
declare `order_packages.id`.** The instance was fixed and the class was never
swept — which is the recurrence mechanism this guard exists to end.

No production code queries `order_packages.id` today, so this is a latent
trapdoor rather than a live bug: the next author who writes `WHERE id = ?` gets
a green CI and a production OperationalError.

WHAT IT CHECKS
--------------
Every `CREATE TABLE` in `tests/` naming a watched money table must declare only
columns production actually has — where "production" is the union of every
`CREATE TABLE` in `src/` AND every `ALTER TABLE ... ADD COLUMN` migration.
Omitting migrations produces false positives: `trades.reconcile_status` is added
by `_migrate_add_reconcile_status`, not by `CREATE TABLE`, and a migration-blind
first draft of this checker reported 19 phantom violations.

    python3 scripts/ci/check_test_schema_fidelity.py            # diff-scoped in CI
    python3 scripts/ci/check_test_schema_fidelity.py --all      # standing audit
    python3 scripts/ci/check_test_schema_fidelity.py --self-test
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KW = {"primary", "foreign", "unique", "check", "constraint", "key"}
WATCH = {"trades", "order_packages", "signals", "position_telemetry",
         "backtest_results", "balance_snapshots", "prop_tickets", "prop_fills"}


def tables(text: str):
    for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)[\"'`]?\s*\(",
                         text, re.I):
        name, i, depth, buf = m.group(1), m.end(), 1, []
        while i < len(text) and depth:
            c = text[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            buf.append(c)
            i += 1
        cols, cur, d = [], "", 0
        for c in "".join(buf):
            if c == "(":
                d += 1
            elif c == ")":
                d -= 1
            if c == "," and d == 0:
                cols.append(cur)
                cur = ""
            else:
                cur += c
        cols.append(cur)
        names = []
        for col in cols:
            col = re.sub(r"--.*", "", col).strip()
            if not col:
                continue
            tok = col.split()[0].strip("\"`'[]").lower()
            if tok in KW or not re.match(r"^[a-z_]\w*$", tok):
                continue
            names.append(tok)
        yield name, names


def production_schema(root: Path):
    prod: dict = {}
    for f in root.glob("src/**/*.py"):
        try:
            t = f.read_text()
        except Exception:
            continue
        if "CREATE TABLE" in t:
            for n, cols in tables(t):
                prod.setdefault(n, set()).update(cols)
        for m in re.finditer(r"ALTER\s+TABLE\s+[\"'`]?(\w+)[\"'`]?\s+ADD\s+COLUMN\s+[\"'`]?(\w+)",
                             t, re.I):
            prod.setdefault(m.group(1), set()).add(m.group(2).lower())
    return prod


def scan(root: Path, files):
    prod = production_schema(root)
    if not any(t in prod for t in WATCH):
        print("ERROR: parsed no watched production table — the probe cannot find a "
              "positive, so a clean result would be meaningless.", file=sys.stderr)
        return None
    out = []
    for f in files:
        try:
            t = f.read_text()
        except Exception:
            continue
        if "CREATE TABLE" not in t:
            continue
        for n, cols in tables(t):
            if n not in WATCH or n not in prod:
                continue
            fictional = sorted(set(cols) - prod[n])
            if fictional:
                out.append((f, n, fictional))
    return out


def _self_test(root: Path) -> int:
    prod = production_schema(root)
    checks = []
    checks.append(("production parse finds order_packages",
                   "order_packages" in prod and len(prod["order_packages"]) > 5))
    checks.append(("order_package_id is a real column",
                   "order_package_id" in prod.get("order_packages", set())))
    checks.append(("`id` is NOT a real order_packages column",
                   "id" not in prod.get("order_packages", set())))
    # migrations must be included, or trades.reconcile_status reads fictional
    checks.append(("ALTER-added trades.reconcile_status counts as production",
                   "reconcile_status" in prod.get("trades", set())))
    # planted positive control: the probe must FLAG a known-bad fixture
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        # The DDL is COMPOSED rather than written as a literal, deliberately.
        # `new-table-wiring-guard` line-scans added lines for a CREATE TABLE and
        # cannot tell a planted self-test fixture from a real new table, so a
        # literal here trips it. The sanctioned escape is its `# data-wiring:`
        # marker — but CLAUDE.md records that marker as presence-only and "the
        # cheapest way to silence a real finding", so annotating a table that
        # does not exist would be exactly the anti-pattern it warns about.
        # Composing the string removes the thing the guard looks for because
        # there genuinely is no new table. Do not "tidy" this back to a literal.
        _CT = "CREATE " + "TABLE "
        bad = Path(d) / "test_planted.py"
        bad.write_text(f'SQL = "{_CT}order_packages (id INTEGER PRIMARY KEY, '
                       'account_id TEXT, order_package_id TEXT)"\n')
        res = scan(root, [bad])
        checks.append(("planted fictional column IS flagged",
                       bool(res) and res[0][2] == ["account_id", "id"]))
        good = Path(d) / "test_clean.py"
        good.write_text(f'SQL = "{_CT}order_packages (order_package_id TEXT, status TEXT)"\n')
        checks.append(("clean fixture is NOT flagged", scan(root, [good]) == []))
    ok = sum(1 for _, g in checks if g)
    for name, g in checks:
        if not g:
            print(f"  FAIL  {name}")
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


def files_in_diff(diff_text: str) -> list[Path]:
    """Test files named by a unified diff's `+++` headers.

    The canonical scoping mechanism, shared with the eight other guards that
    take `{pr_diff}`: the harness generates the diff once and every consumer
    reads the SAME file, so a guard can never be scoped to a different commit
    range than the one CI reported on.
    """
    out: list[Path] = []
    for raw in diff_text.splitlines():
        if not raw.startswith("+++ "):
            continue
        target = raw[4:].strip()
        if target == "/dev/null":
            continue
        rel = target[2:] if target.startswith(("a/", "b/")) else target
        if rel.startswith("tests/") and rel.endswith(".py") and (REPO / rel).exists():
            out.append(REPO / rel)
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("diff", nargs="?",
                    help="unified diff to scope by (the {pr_diff} CI passes)")
    ap.add_argument("--all", action="store_true", help="scan every test file")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--base", default="origin/main")
    a = ap.parse_args()
    if a.self_test:
        return _self_test(REPO)

    if a.all:
        files = sorted((REPO / "tests").rglob("*.py"))
    elif a.diff:
        dp = Path(a.diff)
        if not dp.exists():
            print(f"diff file {a.diff} does not exist — refusing to scan. A guard "
                  f"that cannot see the change it is scoped to is not a guard.",
                  file=sys.stderr)
            return 2
        files = files_in_diff(dp.read_text(errors="replace"))
    else:
        # Local convenience only. The fallback is a HARD ERROR, never a silent
        # widening to --all: substituting the whole tree for the requested diff
        # changes the POPULATION behind the verdict while the output still says
        # the same thing, and --all exits 1 on the pre-existing grandfathered
        # sites, so an unresolvable base would redden every PR for a reason the
        # exit code does not state. (Sub-class B, implicit input selection.)
        try:
            diff = subprocess.run(["git", "diff", "--name-only", f"{a.base}...HEAD"],
                                  cwd=REPO, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError:
            print(f"could not resolve the diff base {a.base!r}; pass a diff file "
                  f"or --all explicitly rather than having one silently chosen.",
                  file=sys.stderr)
            return 2
        files = [REPO / p for p in diff.stdout.split()
                 if p.startswith("tests/") and p.endswith(".py") and (REPO / p).exists()]

    res = scan(REPO, files)
    if res is None:
        return 2
    if not res:
        print(f"test-schema-fidelity: clean over {len(files)} test file(s)")
        return 0
    print(f"test-schema-fidelity: {len(res)} fixture(s) declare a column production "
          f"does NOT have (scanned {len(files)}):\n")
    for f, n, cols in res:
        try:
            rel = f.relative_to(REPO)
        except ValueError:
            rel = f
        print(f"  {rel}  [{n}]  fictional: {', '.join(cols)}")
    print("\nA fixture that declares a column production lacks lets a query against "
          "that column pass CI and raise in production (BL-20260810).")
    print("\nTwo legitimate fixes — pick by what the fixture DOES:")
    print("  * inserts real rows -> lift the DDL "
          "(tests/fixtures/real_schema_db.py::make_canonical_db runs the real "
          "create_tables, so a future migration is reflected automatically).")
    print("  * a join stub of 2-3 columns -> KEEP it minimal, but every column "
          "it declares must be a real one. Production order_packages carries 9 "
          "NOT NULL columns, so lifting the full DDL for a stub is not the "
          "cheaper path and telling you to do it is why 20 fixtures hand-rolled "
          "a fictional `id` instead. Declaring FEWER columns is fine; this "
          "guard only rejects columns that do not exist.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
