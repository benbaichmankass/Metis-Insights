"""Guard: every deploy/ systemd unit is diag-queryable or explicitly exempted.

P2.4 of the 2026-07-31 full-system-audit plan — the THIRD recurrence of the
installed-but-unqueryable class: a unit lands in deploy/, the installer
enables it on the VM, and `/api/diag/services` + the diag `journalctl` route
cannot see it because nobody added it to `_CANONICAL_UNITS` in
`src/web/api/routers/diag.py`. Each prior recurrence hid a real stall
(BL-20260713-EXCHANGE-FILLS-STORE-EMPTY, BL-20260719-FUNDING-NO-TIMER,
BL-20260626-MES-BASE-STALE) until an audit swept for it; `ict-ib-executions-pull`
(2026-07-30) repeated the pattern within four days of the last sweep.

Contract: for every `deploy/*.timer` and `deploy/*.service`, the unit name is
either present in `_CANONICAL_UNITS` (AST-parsed from the router — never a
copy that can drift) or listed in `EXEMPT` below WITH a reason. A new unit
that is neither fails CI, so the choice is made at review time instead of at
the next audit. An exemption whose unit no longer exists in deploy/ also
fails — stale exemptions are how allow-lists rot.

Same guard family as canonical-db-resolver / env-gate-guard /
provenance-consumer-guard. Tree-scoped (the invariant either holds or it
doesn't); no dependencies beyond stdlib.
"""
from __future__ import annotations

import ast
import glob
import os
import sys

ROUTER = "src/web/api/routers/diag.py"
DEPLOY_GLOBS = ("deploy/*.timer", "deploy/*.service")

# Unit -> reason it is deliberately NOT diag-queryable on the live VM.
# Adding a unit here is a reviewed decision, not a default.
EXEMPT: dict[str, str] = {
    "ict-heartbeat.service": "retired 2026-07-08 (daily digest superseded by the hourly snapshot); kept inert in deploy/ for a trivial re-enable, actively disabled by the installer",
    "ict-heartbeat.timer": "retired 2026-07-08 — see ict-heartbeat.service",
    "ict-ib-gateway-reset.service": "gateway-VM unit (role-gated by /etc/ict-vm-role) — not installed on the live trader, so the live diag would perpetually report not-found",
    "ict-ib-gateway-reset.timer": "gateway-VM unit — see ict-ib-gateway-reset.service",
    "ict-trainer-git-sync.service": "trainer-VM unit — the live diag surface cannot see the trainer; trainer state rides the mirror + trainer-vm-diag relay",
    "ict-trainer-git-sync.timer": "trainer-VM unit — see ict-trainer-git-sync.service",
    "ict-env-check.service": "one-shot deploy-time env validation, not a recurring monitored unit (no timer)",
    "ict-smoke-once.service": "one-shot smoke test fired manually/at deploy, not a recurring monitored unit (no timer)",
    "claude-vm-runner@.service": "template unit (instanced per run) — systemctl show on the bare template is meaningless",
}


def canonical_units(router_path: str) -> set[str] | None:
    try:
        tree = ast.parse(open(router_path, encoding="utf-8").read())
    except (OSError, SyntaxError) as exc:
        print(f"::error::cannot parse {router_path}: {exc}")
        return None
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(getattr(t, "id", "") == "_CANONICAL_UNITS" for t in targets):
            try:
                return {ast.literal_eval(e) for e in node.value.elts}
            except (ValueError, AttributeError) as exc:
                print(f"::error::_CANONICAL_UNITS is not a literal tuple: {exc}")
                return None
    print(f"::error::_CANONICAL_UNITS not found in {router_path}")
    return None


def main() -> int:
    allow = canonical_units(ROUTER)
    if allow is None:
        return 1
    deploy = {os.path.basename(p) for g in DEPLOY_GLOBS for p in glob.glob(g)}
    if not deploy:
        # An empty deploy/ means the scan saw nothing — that is an absent
        # result, not a clean one (sub-class C).
        print("::error::no deploy/*.timer or deploy/*.service found — "
              "scanned NOTHING; the deploy dir moved or the guard is "
              "running from the wrong cwd")
        return 1

    failures = 0
    uncovered = sorted(u for u in deploy if u not in allow and u not in EXEMPT)
    for u in uncovered:
        failures += 1
        print(f"::error::deploy/{u} is neither in _CANONICAL_UNITS "
              f"({ROUTER}) nor exempted in {os.path.relpath(__file__)} — "
              f"an installed unit invisible to /api/diag/services is the "
              f"silently-skipped-scheduled-job class (3rd recurrence, "
              f"2026-07-31 audit P2.4). Add it to the allowlist, or exempt "
              f"it HERE with a reason.")
    stale = sorted(u for u in EXEMPT if u not in deploy)
    for u in stale:
        failures += 1
        print(f"::error::exemption for '{u}' is STALE — no such unit under "
              f"deploy/ anymore; remove the exemption (stale entries are how "
              f"allow-lists rot).")
    both = sorted(u for u in EXEMPT if u in allow)
    for u in both:
        failures += 1
        print(f"::error::'{u}' is BOTH allowlisted and exempted — pick one; "
              f"the contradiction means one side is stale.")

    print(f"diag-unit-allowlist: {len(deploy)} deploy units scanned, "
          f"{len(allow)} allowlisted, {len(EXEMPT)} exempted, "
          f"{failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
