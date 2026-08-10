#!/usr/bin/env python3
"""Tier-1 read-only: report the LIVE value of an allowlisted env var on the VM.

Why this exists
---------------
`set-env` could WRITE an env var on the live VM and nothing could READ one back.
That asymmetry is the shape `provenance-consumer-guard` exists to catch — a
signal that is written and never read — one level up, at the ops surface. Its
concrete cost, measured 2026-08-10: `CONVICTION_SIZING_ACCOUNTS` scopes a Tier-3
size multiplier, an EMPTY value means *every* account including real money, and
no session surface could establish its live value. Sampling the soak log cannot
substitute — `bybit_2` having no qualifying signal in a window is
indistinguishable from `bybit_2` being excluded from the allowlist (an unasserted
denominator, sub-class C).

Two sources, and the disagreement is the point
----------------------------------------------
* **process** — `/proc/<MainPID>/environ` of the running unit. AUTHORITATIVE:
  what the process actually holds. A restart is the only thing that changes it.
* **file** — the unit's `EnvironmentFiles`, asked of systemd rather than
  hardcoded (field beats comment: the unit declares its own files). DECLARED:
  what the next restart will pick up.

They can differ, and that difference is a real, otherwise-invisible condition:
the `.env` was edited and the service never re-read it. This is exactly how
`BYBIT_TPSL_MODE` had to be verified by hand in a prior session (three ways,
`/proc/<MainPID>/environ` being the one that settled it). So both are reported
side by side and a mismatch is called out as `pending_restart`.

Four states per source, NEVER collapsed
---------------------------------------
`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states" — can this field say "we
did not look"?

* ``set``        — present and non-empty.
* ``set_empty``  — present and EMPTY. Distinct from ``unset`` and load-bearing:
                   for an allowlist var, empty is the WIDEST setting, not the
                   absence of a setting. Collapsing these two is the bug.
* ``unset``      — we looked; the key is not there.
* ``unreadable`` — we could NOT look (no MainPID, /proc denied, file missing).
                   Never rendered as ``unset``; "no data here" and "no value
                   here" are opposite claims.

Secrets are never printed — and THE OUTPUT IS PUBLIC
----------------------------------------------------
This runs under `system-actions`, which comments the script's stdout back onto
the originating GitHub issue, and **this repo is public** (guarded by the
collaborator-only interaction limit, not by privacy). So the binding rule for
`ALLOWED_KEYS` is: **a key belongs here only if its value is safe to publish.**
That is a property of the key, decided when it is added, not something a
reviewer can infer later from a run log.

Belt-and-braces on top of that: a key whose NAME matches the secret pattern is
served presence + fingerprint only (sha256 prefix + length), never its value —
so misjudging one entry cannot leak it. That still answers the question that
actually gets asked ("is `DASHBOARD_API_TOKEN` set on the VM?",
BL-20260705-DASHBOARD-API-TOKEN-UNSET, where a dropped value silently reopened
an anonymous write hole) and lets two environments be compared for equality
without either value leaving the box.

Reads only. Opens no socket, writes nothing, restarts nothing.

Exit codes: 0 report produced (even if a key is unset/unreadable — that IS the
report), 1 bad usage / disallowed key or unit, 2 could not measure anything at
all (an ABSENT result, not a clean one).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys

# --- Allowlist ------------------------------------------------------------
# Fixed and explicit, exactly like the workflow's action allowlist: there is no
# freeform key input by design, so a compromised or mistaken issue body cannot
# turn this into an arbitrary-environment dump. Adding a key is a one-line edit
# here plus a docs row — deliberately a code change, reviewed like any other.
#
# Scope rule: keys that GOVERN live behaviour (order-path modes, scope
# allowlists, kill-switches, cadences) — the ones whose live value a review
# session has to be able to check against the docs. Not a general env dump.
ALLOWED_KEYS: tuple[str, ...] = (
    # --- Order-path modes + scope allowlists (the reason this exists) ---
    "CONVICTION_SIZING_MODE",
    "CONVICTION_SIZING_DIRECTION",
    "CONVICTION_SIZING_ACCOUNTS",
    "NETTING_ATTRIBUTION_MODE",
    "NETTING_ATTRIBUTION_ACCOUNTS",
    "NEWS_INFLUENCE_MODE",
    "NEWS_VETO_ENABLED",
    "NEWS_SOURCE",
    "REGIME_ML_VERDICT_MODE",
    "ML_VOL_VERDICT_THRESHOLD",
    "BYBIT_TPSL_MODE",
    "FLIP_POLICY",
    "FLIP_CONFIDENCE_THRESHOLD",
    "FLIP_MIN_POSITION_AGE_HOURS",
    # --- Kill-switches (a dropped one silently disables a capability) ---
    "REGIME_ROUTER_DISABLED",
    "REGIME_BAR_SCORING_DISABLED",
    "ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED",
    "CROSS_ASSET_LIVE_DISABLED",
    "SIGNAL_DUAL_WRITE_DISABLED",
    # --- Cadence / budget knobs (an unparseable one changes behaviour) ---
    "TICK_INTERVAL_SECONDS",
    "HEARTBEAT_INTERVAL_SECONDS",
    "TICK_COST_WRITE_SECONDS",
    "EXPOSURE_SOAK_SECONDS",
    "CANDLE_CACHE_TTL_FRACTION",
    "REGIME_BAR_SCORING_BUDGET_S",
    "ACCOUNT_REACHABILITY_CHECK_SECONDS",
    "TRAINER_HEARTBEAT_CHECK_SECONDS",
    "PROP_MONITOR_PULSE_SECONDS",
    "IB_BROKER_NAKED_CHECK_SECONDS",
    # --- IB connection knobs (load-bearing during a gateway wedge) ---
    "IB_FETCH_TIMEOUT_S",
    "IB_PROBE_TIMEOUT_S",
    "IB_ACCOUNT_WARMUP_TIMEOUT_S",
    "IB_PLACE_CONFIRM_S",
    "IB_CLOSE_CONFIRM_S",
    # --- Paths (a wrong one is the stray-duplicate-journal class) ---
    "TRADE_JOURNAL_DB",
    "TRAINER_STORE_DB",
    "DATA_DIR",
    # --- Presence-only (fingerprinted, never printed) ---
    "DASHBOARD_API_TOKEN",
    "DIAG_READ_TOKEN",
)

#: Units whose process environment may be read. Bounded for the same reason the
#: `set-env` restart list is bounded.
ALLOWED_UNITS: tuple[str, ...] = (
    "ict-trader-live.service",
    "ict-web-api.service",
    "ict-claude-bridge.service",
    "ict-telegram-bot.service",
)

#: A key whose NAME matches is served presence + fingerprint only. Matching on
#: the name, not the value, so a secret can never leak by being unrecognised.
SECRET_NAME = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|_KEY$|^KEY_|PRIVATE|"
    r"CREDENTIAL|WEBHOOK|DSN)",
    re.IGNORECASE,
)

# The four states. Named so a reader cannot mistake one for another.
SET = "set"
SET_EMPTY = "set_empty"
UNSET = "unset"
UNREADABLE = "unreadable"


def _fingerprint(value: str) -> str:
    """A comparable, non-reversible stand-in for a secret value."""
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
    return f"sha256:{digest} (len={len(value)})"


def _render(key: str, state: str, value: str | None) -> str | None:
    """The reportable form of a value. Secret-named keys never yield one."""
    if state not in (SET, SET_EMPTY):
        return None
    if state == SET_EMPTY:
        return ""
    assert value is not None
    if SECRET_NAME.search(key):
        return _fingerprint(value)
    return value


def _systemctl_show(unit: str, prop: str) -> str | None:
    """One `systemctl show -p <prop>` value, or None when we could not ask."""
    try:
        r = subprocess.run(
            ["systemctl", "show", unit, "-p", prop, "--value"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def read_process_env(unit: str) -> tuple[dict[str, str] | None, str | None]:
    """The unit's LIVE process environment, or (None, why-we-could-not-look).

    Returning None rather than {} is the whole point: an empty dict would read
    as "the process has no env vars", which is never true and would make every
    key report ``unset``.
    """
    main_pid = _systemctl_show(unit, "MainPID")
    if main_pid is None:
        return None, "systemctl unavailable or unit unknown"
    if not main_pid.isdigit() or main_pid == "0":
        return None, f"unit has no running MainPID (MainPID={main_pid!r})"
    path = f"/proc/{main_pid}/environ"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except PermissionError:
        return None, f"{path} not readable (permission denied)"
    except FileNotFoundError:
        return None, f"{path} gone (process exited between calls)"
    except OSError as exc:
        return None, f"{path} unreadable: {exc}"
    env: dict[str, str] = {}
    for chunk in raw.split(b"\0"):
        if not chunk:
            continue
        text = chunk.decode("utf-8", "replace")
        name, sep, value = text.partition("=")
        if sep:
            env[name] = value
    if not env:
        # A live process always has SOME environment. An empty parse means the
        # read did not work, not that the environment is empty.
        return None, f"{path} parsed to zero variables (unexpected)"
    return env, None


def _parse_env_file(path: str) -> dict[str, str]:
    """KEY=VALUE lines, last definition wins. Comments/blanks skipped."""
    out: dict[str, str] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if not sep:
                continue
            name = name.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out[name] = value
    return out


def read_file_env(unit: str) -> tuple[dict[str, str] | None, list[str], str | None]:
    """The unit's DECLARED env, merged across its systemd EnvironmentFiles.

    The file list comes from systemd, not from a constant here — the unit is the
    authority on which files it reads, and hardcoding a path is how a doc drifts
    from the deployment.
    """
    raw = _systemctl_show(unit, "EnvironmentFiles")
    if raw is None:
        return None, [], "systemctl unavailable or unit unknown"
    paths: list[str] = []
    for token in raw.split("\n"):
        token = token.strip()
        if not token:
            continue
        # systemd renders each as "/path/to/file (ignore_errors=no)".
        paths.append(re.sub(r"\s*\(ignore_errors=(?:yes|no)\)\s*$", "", token))
    if not paths:
        return None, [], "unit declares no EnvironmentFiles"
    merged: dict[str, str] = {}
    errors: list[str] = []
    for path in paths:
        try:
            merged.update(_parse_env_file(path))
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    if errors and not merged:
        return None, paths, "; ".join(errors)
    return merged, paths, ("; ".join(errors) or None)


def classify(env: dict[str, str] | None, key: str, why: str | None) -> dict:
    """One source's verdict for one key, in the four-state vocabulary."""
    if env is None:
        return {"state": UNREADABLE, "value": None, "unreadable_reason": why}
    if key not in env:
        return {"state": UNSET, "value": None}
    value = env[key]
    state = SET_EMPTY if value == "" else SET
    return {"state": state, "value": _render(key, state, value)}


def agreement(process: dict, declared: dict) -> str:
    """Do the running process and the declared files hold the same value?

    Three outcomes, and ``undetermined`` is not a polite way of saying "agree":
    a disagreement is only assertable when BOTH sides were readable. Comparing a
    real value against an unreadable side would manufacture a finding out of a
    failed measurement.
    """
    if UNREADABLE in (process["state"], declared["state"]):
        return "undetermined"
    if (process["state"], process["value"]) == (declared["state"], declared["value"]):
        return "agree"
    return "pending_restart"


def build_report(unit: str, keys: list[str]) -> dict:
    proc_env, proc_why = read_process_env(unit)
    file_env, file_paths, file_why = read_file_env(unit)

    entries = []
    for key in keys:
        process = classify(proc_env, key, proc_why)
        declared = classify(file_env, key, file_why)
        entries.append({
            "key": key,
            "secret_name": bool(SECRET_NAME.search(key)),
            "process": process,
            "declared": declared,
            "agreement": agreement(process, declared),
        })

    return {
        "unit": unit,
        "env_files": file_paths,
        "process_readable": proc_env is not None,
        "declared_readable": file_env is not None,
        "keys_requested": len(keys),
        "entries": entries,
    }


def _state_label(entry_side: dict) -> str:
    state = entry_side["state"]
    if state == SET:
        return repr(entry_side["value"])
    if state == SET_EMPTY:
        return "'' (SET BUT EMPTY — not the same as unset)"
    if state == UNSET:
        return "<unset>"
    return f"<UNREADABLE: {entry_side.get('unreadable_reason')}>"


def render_text(report: dict) -> str:
    lines = [
        f"get-env — unit={report['unit']}",
        f"  process env readable : {report['process_readable']}",
        f"  declared env readable: {report['declared_readable']} "
        f"(files: {', '.join(report['env_files']) or 'none'})",
        "",
        "  process = what the RUNNING process holds (authoritative)",
        "  declared = what the unit's EnvironmentFiles say (next restart)",
        "",
    ]
    for e in report["entries"]:
        lines.append(f"  {e['key']}")
        lines.append(f"      process : {_state_label(e['process'])}")
        lines.append(f"      declared: {_state_label(e['declared'])}")
        if e["agreement"] == "pending_restart":
            lines.append("      ** DIFFER — the file was edited and the service "
                         "has not re-read it (restart pending) **")
        elif e["agreement"] == "undetermined":
            lines.append("      (agreement undetermined — one side was unreadable, "
                         "which is NOT evidence they match)")
        if e["secret_name"]:
            lines.append("      (secret-named: fingerprint only, value never printed)")
    return "\n".join(lines)


def _self_test() -> int:
    """Prove the states are distinguishable and secrets never render.

    A guard that is never shown to catch a positive is not evidence. This runs
    the pure classifier over planted inputs — no VM needed.
    """
    ok = True

    def check(label: str, got, want) -> None:
        nonlocal ok
        if got != want:
            print(f"  self-test FAIL: {label}: got {got!r}, want {want!r}")
            ok = False
        else:
            print(f"  self-test ok: {label}")

    check("present non-empty -> set",
          classify({"A": "x"}, "A", None)["state"], SET)
    check("present empty -> set_empty (NOT unset)",
          classify({"A": ""}, "A", None)["state"], SET_EMPTY)
    check("absent -> unset",
          classify({"B": "x"}, "A", None)["state"], UNSET)
    check("could-not-look -> unreadable (NOT unset)",
          classify(None, "A", "denied")["state"], UNREADABLE)
    # The distinction that motivated the tool.
    check("set_empty and unset are different states",
          classify({"A": ""}, "A", None)["state"] != classify({}, "A", None)["state"],
          True)
    # Secrets: value must never survive rendering.
    rendered = _render("DASHBOARD_API_TOKEN", SET, "hunter2")
    check("secret-named key is fingerprinted", rendered.startswith("sha256:"), True)
    check("secret value never appears", "hunter2" in (rendered or ""), False)
    check("non-secret key renders its value",
          _render("FLIP_POLICY", SET, "hold"), "hold")

    # Agreement must never be asserted against an unreadable side.
    readable_set = {"state": SET, "value": "apply"}
    unreadable = {"state": UNREADABLE, "value": None}
    check("same value both sides -> agree",
          agreement(readable_set, {"state": SET, "value": "apply"}), "agree")
    check("differing values -> pending_restart",
          agreement(readable_set, {"state": SET, "value": "annotate"}), "pending_restart")
    check("unreadable side -> undetermined (NOT agree)",
          agreement(readable_set, unreadable), "undetermined")
    check("unreadable process side -> undetermined (NOT agree)",
          agreement(unreadable, readable_set), "undetermined")
    # set_empty vs unset must not silently read as agreement.
    check("set_empty vs unset -> pending_restart, not agree",
          agreement({"state": SET_EMPTY, "value": ""},
                    {"state": UNSET, "value": None}), "pending_restart")

    print("  self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", default="",
                    help="allowlisted env var name, or ALL for every allowlisted key")
    ap.add_argument("--unit", default="ict-trader-live.service",
                    help=f"unit whose env to read (allowed: {', '.join(ALLOWED_UNITS)})")
    ap.add_argument("--json", action="store_true", help="emit the JSON report")
    ap.add_argument("--list-keys", action="store_true", help="print the allowlist and exit")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the four states are distinguishable; no VM needed")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    if args.list_keys:
        for k in ALLOWED_KEYS:
            marker = "  (secret-named: fingerprint only)" if SECRET_NAME.search(k) else ""
            print(f"{k}{marker}")
        return 0

    if args.unit not in ALLOWED_UNITS:
        print(f"::error::unit {args.unit!r} is not allowlisted "
              f"(allowed: {', '.join(ALLOWED_UNITS)})", file=sys.stderr)
        return 1

    if not args.key:
        print("::error::--key is required (a name from --list-keys, or ALL)",
              file=sys.stderr)
        return 1

    if args.key == "ALL":
        keys = list(ALLOWED_KEYS)
    elif args.key in ALLOWED_KEYS:
        keys = [args.key]
    else:
        print(f"::error::key {args.key!r} is not allowlisted. This action has no "
              f"freeform key input by design — see --list-keys. Adding a key is a "
              f"one-line edit to ALLOWED_KEYS in scripts/ops/get_env.py.",
              file=sys.stderr)
        return 1

    report = build_report(args.unit, keys)
    print(json.dumps(report, indent=2) if args.json else render_text(report))

    if not report["process_readable"] and not report["declared_readable"]:
        # Neither source answered. That is an ABSENT result, not a clean one —
        # the `check_workflow_shell.py` exit-2 convention.
        print("::error::read NOTHING — neither the process env nor the declared "
              "env files were readable. This is an ABSENT result, not 'the key is "
              "unset'.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
