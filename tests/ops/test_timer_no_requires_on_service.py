"""A timer must not declare Requires=/BindsTo= on the service it fires.

Starting a timer that Requires= its service RUNS that service. On
ict-ib-gateway-reset.timer the service is `docker restart ib-gateway`, so every
deploy-time timer restart on the gateway VM bounced the IB Gateway (MEASURED
2026-10-08: 9 of 10 reset.service runs in a ~2h journal window began in the same
second as the timer's Stopping/Started lines; only the 06:05 run was the
schedule). IB-GATEWAY-DOWN.

The gateway pair is forbidden outright. KNOWN_OFFENDERS is a ratchet that is now
empty (TIMER-REQUIRES removed the line from every remaining timer): no timer may
add it back, and an entry may only be added with a stated reason.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = {
    "deploy/ict-ib-gateway-reset.timer",
    "deploy/ict-ib-gateway-watchdog.timer",
}
KNOWN_OFFENDERS: set[str] = set()


def _section(text: str, name: str) -> str:
    m = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", text, re.DOTALL | re.MULTILINE)
    return m.group(1) if m else ""


def _offenders() -> set[str]:
    found = set()
    timers = sorted((ROOT / "deploy").glob("*.timer")) + sorted(
        (ROOT / "deploy/opt-in").glob("*.timer")
    )
    for path in timers:
        text = path.read_text()
        m = re.search(r"^Unit=(\S+)", _section(text, "Timer"), re.MULTILINE)
        service = m.group(1) if m else path.name.replace(".timer", ".service")
        deps = {
            d
            for line in re.findall(
                r"^(?:Requires|BindsTo)=(.*)$", _section(text, "Unit"), re.MULTILINE
            )
            for d in line.split()
        }
        if service in deps:
            found.add(str(path.relative_to(ROOT)))
    return found


def test_gateway_timers_do_not_require_their_service():
    assert not (_offenders() & FORBIDDEN), (
        "ib-gateway timer declares Requires=/BindsTo= on the service it fires; "
        "a timer restart would run it (docker restart of ib-gateway for the reset timer)"
    )


def test_no_new_timer_requires_its_own_service():
    new = _offenders() - KNOWN_OFFENDERS
    assert not new, f"new timer(s) with Requires= on their own service: {sorted(new)}"


def test_known_offenders_list_only_shrinks():
    stale = KNOWN_OFFENDERS - _offenders()
    assert not stale, f"remove from KNOWN_OFFENDERS (fixed): {sorted(stale)}"
