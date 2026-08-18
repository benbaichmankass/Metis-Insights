"""Delegation scope guard — decides what a third-party LLM may be shown.

The operator authorised exactly one scope for delegated subtasks (2026-08-18):
**public repo code + docs only**. No live trading data, no credentials.

Design rules, each of which exists because of a specific failure this repo has
already paid for:

* **Deny wins, and default is deny.** A path must match an explicit ALLOW rule
  AND no DENY rule. Anything unrecognised is refused. A guard whose default is
  "permit" silently widens every time someone adds a directory.
* **Three states, never collapsed** (``allowed`` / ``denied`` / ``missing``).
  "We were not allowed to read it" and "it is not there" are opposite facts;
  collapsing them lets a typo'd path read as a policy refusal, and a policy
  refusal read as an empty file.
* **The caller cannot bypass a verdict.** ``resolve_paths`` refuses the WHOLE
  batch if any single path is denied, rather than dropping it and proceeding.
  Silently sending 9 of 10 files is how a scope guard becomes decorative.

"Already public" is deliberately NOT the test. ``comms/`` is committed and
holds system reports with full per-trade PnL dossiers; ``config/`` is committed
and describes account topology. Both are public and both are outside the scope
the operator authorised, so both are denied.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

Verdict = Literal["allowed", "denied", "missing"]

# Refused outright. Checked FIRST and beats any allow rule.
DENY_GLOBS: tuple[str, ...] = (
    ".env*", "*/.env*",
    "*.db", "*.sqlite", "*.sqlite3", "*.db-wal", "*.db-shm",
    "*.key", "*.pem", "*.p12", "*.pfx", "*id_rsa*", "*id_ed25519*",
    "runtime_logs/*", "runtime_state/*", "artifacts/*", "data/*",
    "comms/*",             # committed, but holds PnL dossiers + ledgers
    "config/*",            # account topology, risk caps, strategy params
    "*secret*", "*credential*", "*token*",
    "*.pkl", "*.joblib", "*.parquet", "*.csv",
)

# Permitted when no deny rule matches.
#
# ⚠️ These are deliberately ROOT-SCOPED, not bare extension globs. A delegated
# review of this very file on 2026-08-18 (issue #9944, guard-review-006) found
# that repo-wide globs like `*.txt` / `*.toml` / `*.ini` / `*.md` admitted
# `trades.txt`, `settings.toml`, `connections.ini` and `reports/pnl_summary.md`
# — financial records and account configuration living outside the `config/`
# and `comms/` deny roots. The deny list cannot enumerate every such location,
# so the ALLOW side has to be narrow instead. Five of its six findings were
# valid; each is now a regression test.
ALLOW_GLOBS: tuple[str, ...] = (
    # Source code, under the roots that actually hold source.
    "src/*.py", "scripts/*.py", "tests/*.py", "ml/*.py",
    # Web app source — restricted to code/markup extensions, NOT a bare
    # `webapp/src/*`, which admitted `webapp/src/config/accounts.json`.
    "webapp/src/*.ts", "webapp/src/*.js", "webapp/src/*.svelte",
    "webapp/src/*.css", "webapp/src/*.html",
    # Documentation, under docs/ only, plus the small set of root-level
    # markdown files that are genuinely project docs.
    "docs/*.md",
    "README.md", "CLAUDE.md", "CONTRIBUTING.md", "ROADMAP.md", "ROADMAP_MACRO.md",
    # Packaging/tooling manifests at the repo root only.
    "pyproject.toml", "setup.cfg", "pytest.ini", "requirements.txt",
)

# A single file above this is refused rather than truncated: a silently
# truncated file is a wrong answer delivered confidently.
MAX_FILE_BYTES = 256 * 1024
# Whole-batch ceiling, so one task cannot ship the repo.
MAX_TOTAL_BYTES = 1024 * 1024


@dataclass(frozen=True)
class PathVerdict:
    path: str
    verdict: Verdict
    reason: str
    size_bytes: int | None = None

    @property
    def ok(self) -> bool:
        return self.verdict == "allowed"


def _matches(rel: str, globs: Iterable[str]) -> str | None:
    for g in globs:
        # match the literal glob and the recursive form, so "src/*.py"
        # covers "src/a.py" and "src/web/api/main.py" alike.
        if fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.replace("/*", "/**/*")):
            return g
        if g.endswith("/*") and rel.startswith(g[:-2] + "/"):
            return g
    return None


def classify(rel_path: str, repo_root: Path) -> PathVerdict:
    """Grade one repo-relative path. Never raises on a hostile input."""
    rel = rel_path.strip().lstrip("./")

    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return PathVerdict(rel_path, "denied", "path escapes the repo root")

    hit = _matches(rel, DENY_GLOBS)
    if hit:
        return PathVerdict(rel, "denied", f"matches deny rule {hit!r}")

    hit = _matches(rel, ALLOW_GLOBS)
    if not hit:
        return PathVerdict(
            rel, "denied",
            "no allow rule matches (scope is public code + docs only; default is deny)",
        )

    full = repo_root / rel
    if not full.is_file():
        # Distinct from denied — the guard permitted it, the file is absent.
        return PathVerdict(rel, "missing", "permitted by scope, but no such file")

    size = full.stat().st_size
    if size > MAX_FILE_BYTES:
        return PathVerdict(
            rel, "denied",
            f"{size} bytes exceeds MAX_FILE_BYTES={MAX_FILE_BYTES} "
            "(refused rather than truncated)",
            size,
        )
    return PathVerdict(rel, "allowed", f"matches allow rule {hit!r}", size)


def resolve_paths(paths: list[str], repo_root: Path) -> tuple[list[PathVerdict], str | None]:
    """Grade a batch. Returns (verdicts, refusal_reason).

    ``refusal_reason`` non-None means the batch must NOT be sent — the caller
    is expected to abort, not to filter and continue.
    """
    verdicts = [classify(p, repo_root) for p in paths]

    denied = [v for v in verdicts if v.verdict == "denied"]
    if denied:
        detail = "; ".join(f"{v.path} ({v.reason})" for v in denied[:10])
        more = f" …and {len(denied) - 10} more" if len(denied) > 10 else ""
        return verdicts, f"{len(denied)} path(s) outside the authorised scope: {detail}{more}"

    missing = [v for v in verdicts if v.verdict == "missing"]
    if missing:
        return verdicts, (
            f"{len(missing)} path(s) not found: "
            + ", ".join(v.path for v in missing[:10])
        )

    total = sum(v.size_bytes or 0 for v in verdicts)
    if total > MAX_TOTAL_BYTES:
        return verdicts, (
            f"batch is {total} bytes, over MAX_TOTAL_BYTES={MAX_TOTAL_BYTES}"
        )

    if not verdicts:
        return verdicts, "no paths supplied"

    return verdicts, None
