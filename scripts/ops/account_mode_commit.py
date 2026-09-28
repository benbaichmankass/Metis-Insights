#!/usr/bin/env python3
"""Make a `set-account-mode` flip DURABLE: the change is a commit to `main`.

WHY (JC-CA-01, operator decision 2026-09-28 "Commit to main")
------------------------------------------------------------
Until this change the `set-account-mode` system-action edited
`config/accounts.yaml` in the VM WORKING TREE only. `ict-git-sync` runs
`git reset --hard origin/main` every ~5 minutes, and
`Coordinator.multi_account_execute` re-reads `accounts.yaml` through
`load_accounts()` on EVERY dispatch (no cache). So a flip lasted at most
one git-sync tick, and then the account silently went back to what `main`
said (audit finding CA-A12-set-account-mode-not-durable).

Now the action writes the one-line `mode:` change to a branch and opens a
PR against `main`. Once the PR merges, git-sync converges the VM on it and
`.github/workflows/account-mode-verify.yml` dispatches the Tier-1
`verify-account-mode` system-action. That action checks over SSH that the
VM's HEAD carries the new mode AND that the running trader's per-tick
`runtime_status.json` reports it.

WHY THE PR HOLDS FOR A MERGE INSTEAD OF LANDING ITSELF
------------------------------------------------------
`config/accounts.yaml` is in `TIER3_PATHS` (`scripts/ci/check_pr_landing.py`),
and Tier-3 never self-lands. The one exception is the R16 mandate route,
which admits only roster REMOVALS. This module therefore writes a
`landing: "hold"` declaration (`tier_2_3_needs_approval`), and the manager
merges on the operator's recorded approval. Adding a self-landing route for
this shape would be a change to the landing machinery, and that is not this
module's call.

⚠️ AND THERE IS NO VM-LOCAL EDIT ANY MORE, deliberately. Keeping the old
immediate edit alongside the PR would FLAP the account. The VM would read
the new mode, git-sync would revert it within 5 minutes, and then the merge
(20+ minutes of required CI later) would set it again. A mode that changes
three times for one decision is worse than one that changes once, later.

Subcommands
-----------
  prepare   edit accounts.yaml in place and write the hold declaration.
            Exit 0 = edited · 3 = already at that mode (no-op) · 1 = error
  changes   print the `mode:` changes between two accounts.yaml blobs as
            JSON lines ({"account", "before", "after"}); used by
            account-mode-verify.yml to decide what to verify.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_REL = "config/accounts.yaml"
MODES = ("live", "dry_run")
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
BRANCH_PREFIX = "automation/set-account-mode"


def _block(text: str, account: str) -> Tuple[int, int]:
    """(start, end) of `account`'s block under `accounts:`.

    The same two regexes `scripts/ops/set_account_mode.sh` has always used:
    a two-space-indented bare key opens the block, and the next one closes it."""
    m = re.search(rf"^  {re.escape(account)}:\s*$", text, re.MULTILINE)
    if not m:
        raise LookupError(f"account {account!r} not found in {ACCOUNTS_REL}")
    start = m.end()
    nxt = re.search(r"^  \w[\w-]*:\s*$", text[start:], re.MULTILINE)
    return start, (start + nxt.start() if nxt else len(text))


def read_mode(text: str, account: str) -> Optional[str]:
    start, end = _block(text, account)
    mm = re.search(r"^\s{4}mode:\s*([^\s#]+)", text[start:end], re.MULTILINE)
    return mm.group(1).strip("'\"") if mm else None


def edit_mode(text: str, account: str, mode: str, marker: str) -> Tuple[str, Optional[str]]:
    """Return (new_text, pre_mode). Replaces ONLY `account`'s `mode:` line.

    The line's trailing comment is replaced by `marker`, which records the run
    that made the change. When the new mode is `dry_run`, the marker also has to
    carry `mode-guard: allow`, the operator marker that
    `scripts/check_dry_run_in_diff.py` requires on a demotion."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if not _ACCOUNT_RE.match(account or ""):
        raise ValueError(f"account {account!r} has characters outside [A-Za-z0-9_-]")
    if "\n" in marker:
        raise ValueError("marker must be a single line")
    pre = read_mode(text, account)
    start, end = _block(text, account)
    block = text[start:end]
    new_block, n = re.subn(
        r"^(\s{4}mode:\s*)\S+.*$",
        lambda mm: f"{mm.group(1)}{mode}  # {marker}",
        block, count=1, flags=re.MULTILINE)
    if n != 1:
        raise LookupError(f"no `mode:` line in the {account!r} block")
    return text[:start] + new_block + text[end:], pre


def marker_for(mode: str, run_id: str, reason: str) -> str:
    reason = " ".join((reason or "").split())[:160]
    head = "mode-guard: allow — " if mode == "dry_run" else ""
    return f"{head}set-account-mode run {run_id}: {reason}".rstrip()


def slug_for(branch: str) -> str:
    return branch.replace("/", "-")


def declaration(account: str, mode: str, pre: Optional[str], run_id: str,
                reason: str, issue: str) -> Dict[str, object]:
    src = f"issue #{issue}" if issue else "workflow_dispatch"
    return {
        "tier": 3,
        "landing": "hold",
        "hold_reason": "tier_2_3_needs_approval",
        "hold_text": (
            f"set-account-mode {account}: {pre} -> {mode}, requested through the "
            f"system-actions workflow (run {run_id}, {src}). An account-mode flip "
            f"is Tier-3 (config/accounts.yaml is in TIER3_PATHS) and Tier-3 never "
            f"self-lands, so this waits for a merge on the operator's recorded "
            f"approval. Reason given: {reason}"),
        "why": (
            f"The durable form of a set-account-mode flip (JC-CA-01): the "
            f"one-line `mode:` change is a commit to main, so ict-git-sync "
            f"converges the VM on it instead of reverting it within ~5 minutes. "
            f"After merge, account-mode-verify.yml dispatches verify-account-mode "
            f"to confirm the running trader reads {mode}."),
    }


def cmd_prepare(a: argparse.Namespace) -> int:
    root = Path(a.root)
    path = root / ACCOUNTS_REL
    text = path.read_text(encoding="utf-8")
    try:
        new, pre = edit_mode(text, a.account, a.mode, marker_for(a.mode, a.run_id, a.reason))
    except (LookupError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if pre == a.mode:
        print(f"{a.account} already reads mode: {a.mode} on main — nothing to commit")
        return 3
    path.write_text(new, encoding="utf-8")
    back = read_mode(new, a.account)
    if back != a.mode:
        print(f"ERROR: read back {back!r} after the edit", file=sys.stderr)
        return 1
    decl_dir = root / ".github" / "pr-landing"
    decl_dir.mkdir(parents=True, exist_ok=True)
    slug = slug_for(a.branch)
    (decl_dir / f"{slug}.json").write_text(
        json.dumps(declaration(a.account, a.mode, pre, a.run_id, a.reason, a.issue),
                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"account": a.account, "before": pre, "after": a.mode,
                      "declaration": f".github/pr-landing/{slug}.json"}))
    return 0


def mode_changes(before: str, after: str) -> List[Dict[str, Optional[str]]]:
    """Every account whose parsed `mode` differs between two accounts.yaml texts.

    Parsed through the canonical loader (`src.config.accounts_loader`), not
    regex, so a comment-only edit (which changes nothing that trades) is not
    reported. An account that is new in `after` counts as a change from None.
    ⚠️ A PARSE FAILURE RAISES. The loader returns {} on a bad file, and folding
    that into "no changes" would skip the verification of a real flip."""
    import tempfile
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from src.config.accounts_loader import load_accounts_dict

    def modes(t: str) -> Dict[str, Optional[str]]:
        if not t.strip():
            return {}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "accounts.yaml"
            path.write_text(t, encoding="utf-8")
            errors: List[Dict[str, object]] = []
            accts = load_accounts_dict(path, errors=errors)
        if errors:
            raise ValueError(f"accounts.yaml did not parse: {errors[0].get('error')}")
        return {k: (str(v.get("mode")) if isinstance(v, dict) and v.get("mode") is not None else None)
                for k, v in accts.items()}

    b, a = modes(before), modes(after)
    return [{"account": k, "before": b.get(k), "after": a[k]}
            for k in sorted(a) if a[k] in MODES and a[k] != b.get(k)]


def _blob(root: Path, rev: str) -> str:
    r = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{ACCOUNTS_REL}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def cmd_changes(a: argparse.Namespace) -> int:
    root = Path(a.root)
    for row in mode_changes(_blob(root, a.before), _blob(root, a.after)):
        print(json.dumps(row))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=str(REPO_ROOT))
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prepare")
    pp.add_argument("--account", required=True)
    pp.add_argument("--mode", required=True, choices=MODES)
    pp.add_argument("--run-id", required=True)
    pp.add_argument("--branch", required=True)
    pp.add_argument("--reason", required=True)
    pp.add_argument("--issue", default="")
    pc = sub.add_parser("changes")
    pc.add_argument("--before", required=True)
    pc.add_argument("--after", required=True)
    a = p.parse_args(argv)
    return cmd_prepare(a) if a.cmd == "prepare" else cmd_changes(a)


if __name__ == "__main__":
    sys.exit(main())
