"""Is this Telegram bot actually POLLED, and would a tap reach a handler?

WHY THIS MODULE EXISTS
──────────────────────
``src/runtime/telegram_decisions.py`` opens with the distinction this module
mechanises: **delivery and answerability are different properties.** A
``sendMessage`` needs only a token. An inline-keyboard **button** is inert
unless some process is POLLING that bot *and* has a ``CallbackQueryHandler``
registered — the tap produces a ``callback_query`` update that nobody collects.

Before this module the answer was a COMMENT. ``answerable_route()`` hard-coded
"the trader bot, because ``ict-telegram-bot.service`` polls it", which was true
when written and is exactly the kind of claim that rots silently: a unit gets
disabled, a token gets rotated, a new bot gets provisioned and nobody polls it,
and the comment still reads correct while every button it authorises is dead.

⚠️ THE FAILURE THIS PREVENTS IS THE SILENT ONE. A bot whose token resolves but
which nothing polls produces a prompt that **looks completely healthy** — it
arrives, it renders buttons, the buttons highlight when tapped — and simply
never does anything. Nobody gets an error. That is the ``position_idx`` shape:
a wrong value that no surface disagrees with. So the point of this module is
NOT to be clever about polling; it is to make "nothing polls this" a state
somebody can READ, and to refuse to guess when it cannot tell.

THREE STATES, NEVER COLLAPSED
─────────────────────────────
``polled_with_handler``    a live process declares it polls this token variable
                           AND handles this callback prefix. A tap is received.
``token_only_not_polled``  we LOOKED, and a tap would NOT be received.
``unknown``                we could NOT look. Not a synonym for either.

The pair that carries the weight is ``token_only_not_polled`` vs ``unknown``,
per ``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed states": *can this field
say "we did not look"?* Collapsing ``unknown`` into ``polled_with_handler``
ships dead buttons; collapsing it into ``token_only_not_polled`` silently
condemns a perfectly good channel on the strength of an unreadable file. Both
are wrong, and they are wrong in opposite directions, which is precisely why
the third value has to exist rather than being inferred from a boolean.

⚠️ ONE DELIBERATE UNION, STATED RATHER THAN HIDDEN. Two distinct conditions map
onto ``token_only_not_polled``: *nothing polls this token at all*, and *some
process polls it but does not handle THIS prefix*. They are one state because
they are one **decision** — in both, a tap is not received, and the correct
action is identical. What must never merge is "we looked" with "we did not",
and that boundary is kept. The sub-condition is never lost: it is named in
``note``, which is what a human reads when asking why a prompt was held.

EVIDENCE, NOT DECLARATION
─────────────────────────
A registry a process can satisfy by *asserting* it polls would be worth
nothing — ``docs/CLAUDE-RULES-CANONICAL.md``: *a guard cheaper to lie to than
to satisfy is worse than no guard.* So the entry is a **heartbeat**, refreshed
on a job the polling loop itself drives. A process that dies, hangs, or is
stopped stops refreshing, and its claim expires on its own. An env var saying
``TELEGRAM_CLAUDE_BOT_POLLED=1`` would have been one line and would have been a
lie the moment the unit was masked.

⚠️ NEVER LOG A TOKEN. Entries are keyed by the **variable NAME** that answered
(``TELEGRAM_CLAUDE_BOT_SECRET``), never its value, exactly as
``telegram_routes.Route.describe()`` does. This repo is public.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

# ── the three states ─────────────────────────────────────────────────────────
POLLED_WITH_HANDLER = "polled_with_handler"
TOKEN_ONLY_NOT_POLLED = "token_only_not_polled"
UNKNOWN = "unknown"

POLL_STATES: tuple[str, ...] = (POLLED_WITH_HANDLER, TOKEN_ONLY_NOT_POLLED, UNKNOWN)

_REGISTRY_DIRNAME = "telegram_pollers"

#: How long a heartbeat stays trustworthy. Generous on purpose: the refresh job
#: shares an event loop with the poller, so a slow update is normal and a
#: too-tight window would report a healthy bot as dead — an alarm that cries
#: wolf is the `alarm fatigue is itself a P1` failure, and this one would push
#: prompts onto the wrong channel every time it fired.
_DEFAULT_STALE_SECONDS = 900.0
_DEFAULT_HEARTBEAT_SECONDS = 120.0

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")

#: In-process claims. A process that polls a token KNOWS it does, with no
#: filesystem in the path — so its own answer is authoritative and cannot be
#: broken by a read-only disk. The file exists for the CROSS-process question
#: (the sweep runs in one service; the Claude bot's poll loop in another).
_LOCAL: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class PollEvidence:
    """What we can HONESTLY say about whether a tap on this bot is received."""

    state: str
    token_var: Optional[str]
    prefix: Optional[str]
    note: str
    source: str = "none"           # "in_process" | "heartbeat" | "none"
    observed_at: Optional[str] = None
    service: Optional[str] = None

    @property
    def answerable(self) -> bool:
        """True ONLY for the one state that means a tap is received.

        ⚠️ Deliberately not ``state != TOKEN_ONLY_NOT_POLLED``. ``unknown`` must
        read as NOT answerable — the fail-closed direction — while remaining a
        distinct value for the human reading the log line.
        """
        return self.state == POLLED_WITH_HANDLER

    def describe(self) -> str:
        return (f"poll[{self.state}] token_var={self.token_var or '(none)'} "
                f"prefix={self.prefix or '(any)'} source={self.source} "
                f"service={self.service or '(unknown)'} — {self.note}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _float_env(name: str, default: float) -> float:
    """Unparseable falls back to the DEFAULT — never to 0.

    A zero staleness window would expire every heartbeat instantly and route
    every prompt away from its intended bot, so a typo must not be able to
    reach that state (the `_float_env` contract in ``telegram_decisions``).
    """
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        parsed = float(raw)
    except (ValueError, TypeError):
        logger.warning("telegram_poll_registry: %s=%r is not a number — using %s",
                       name, raw, default)
        return default
    if parsed <= 0:
        logger.warning("telegram_poll_registry: %s=%r is not positive — using %s",
                       name, raw, default)
        return default
    return parsed


def stale_after_seconds() -> float:
    return _float_env("TELEGRAM_POLL_STALE_SECONDS", _DEFAULT_STALE_SECONDS)


def heartbeat_interval_seconds() -> float:
    return _float_env("TELEGRAM_POLL_HEARTBEAT_SECONDS", _DEFAULT_HEARTBEAT_SECONDS)


def registry_dir(root: Optional[Path] = None) -> Path:
    return Path(root or runtime_logs_dir()) / _REGISTRY_DIRNAME


def entry_path(token_var: str, root: Optional[Path] = None) -> Path:
    """One file per token VARIABLE — never one shared file.

    Separate files mean two pollers never race each other's writes, and a
    corrupt entry condemns only its own bot rather than blinding the registry.
    """
    return registry_dir(root) / f"{_SAFE_NAME.sub('_', token_var)}.json"


# ═════════════════════════════════════════════════════════════════════════════
# Producer — called BY a polling process, about itself.
# ═════════════════════════════════════════════════════════════════════════════


def record_poll(
    token_var: str,
    prefixes: Iterable[str],
    *,
    service: Optional[str] = None,
    root: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> PollEvidence:
    """Declare that THIS process polls ``token_var`` and handles ``prefixes``.

    Call it once the handlers are actually registered on the ``Application`` —
    never before. Registering the claim earlier would make the claim true of
    the intent rather than of the process, which is the whole failure mode.

    Refresh it on a repeating job (see :func:`heartbeat_interval_seconds`): the
    heartbeat is what makes a stopped or hung poller expire by itself instead of
    leaving a permanent assertion behind.

    Never raises. A registry we cannot write is a degraded OBSERVATION surface,
    not a reason to take a bot's polling loop down with it.
    """
    ref = now or _now()
    declared = sorted({str(p) for p in prefixes if str(p).strip()})
    payload = {
        "schema": 1,
        "token_var": token_var,
        "prefixes": declared,
        "service": service,
        "pid": os.getpid(),
        "heartbeat_at": _iso(ref),
    }
    # In-process first, and unconditionally: it must survive a read-only disk.
    _LOCAL[token_var] = dict(payload)

    try:
        path = entry_path(token_var, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, path)
        source = "heartbeat"
    except OSError as exc:
        logger.warning(
            "telegram_poll_registry: could not persist the poll claim for %s "
            "(%s) — this process still knows it polls, but OTHER processes "
            "cannot see it and will treat this bot as unconfirmed",
            token_var, exc,
        )
        source = "in_process"

    return PollEvidence(
        state=POLLED_WITH_HANDLER, token_var=token_var,
        prefix=None, source=source, observed_at=payload["heartbeat_at"],
        service=service,
        note=f"this process polls {token_var} and handles {declared or '[]'}",
    )


def forget_poll(token_var: str, *, root: Optional[Path] = None) -> None:
    """Drop a claim. For tests and for an orderly shutdown."""
    _LOCAL.pop(token_var, None)
    try:
        entry_path(token_var, root).unlink()
    except (OSError, FileNotFoundError):
        pass


# ═════════════════════════════════════════════════════════════════════════════
# Consumer — the three-way read.
# ═════════════════════════════════════════════════════════════════════════════


def _from_record(
    rec: dict[str, Any], token_var: str, prefix: Optional[str], source: str,
    *, ref: datetime,
) -> PollEvidence:
    beat = _parse_iso(rec.get("heartbeat_at"))
    service = rec.get("service") if isinstance(rec.get("service"), str) else None
    declared = rec.get("prefixes")
    declared = [str(p) for p in declared] if isinstance(declared, list) else []
    common = {
        "token_var": token_var, "prefix": prefix, "source": source,
        "observed_at": rec.get("heartbeat_at") if isinstance(
            rec.get("heartbeat_at"), str) else None,
        "service": service,
    }

    if beat is None:
        # A claim we cannot DATE is a claim we cannot judge. Not "not polled":
        # the poller may be perfectly alive and the timestamp merely malformed.
        return PollEvidence(
            state=UNKNOWN,
            note=(f"a poll claim for {token_var} exists but carries no readable "
                  f"heartbeat time — cannot tell a live poller from a stale one"),
            **common,
        )

    age = (ref - beat).total_seconds()
    limit = stale_after_seconds()
    if age > limit:
        return PollEvidence(
            state=TOKEN_ONLY_NOT_POLLED,
            note=(f"the last poll heartbeat for {token_var} is {age:.0f}s old "
                  f"(stale after {limit:.0f}s) — the poller is not running, so a "
                  f"tap would not be received"),
            **common,
        )

    if prefix is not None and prefix not in declared:
        # Polled, but not for THIS callback prefix — see the module docstring's
        # "one deliberate union". Same decision, so the same state; the reason
        # survives in the note.
        return PollEvidence(
            state=TOKEN_ONLY_NOT_POLLED,
            note=(f"{token_var} is polled by {service or 'an unnamed process'} "
                  f"but declares no handler for {prefix!r} (declares "
                  f"{declared or '[]'}) — a {prefix!r} tap would not be received"),
            **common,
        )

    return PollEvidence(
        state=POLLED_WITH_HANDLER,
        note=(f"{token_var} is polled by {service or 'an unnamed process'} "
              f"with a handler for {prefix or 'every declared prefix'} "
              f"(heartbeat {age:.0f}s old)"),
        **common,
    )


def poll_state(
    token_var: Optional[str],
    *,
    prefix: Optional[str] = None,
    root: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> PollEvidence:
    """Can a tap on the bot behind ``token_var`` actually be received?

    Never raises: every failure to look becomes :data:`UNKNOWN`, which the
    caller must treat as *not answerable* while still reporting it distinctly
    from *not polled*.
    """
    ref = now or _now()
    if not token_var:
        # No variable answered, so there is nothing to be polled. We DID look.
        return PollEvidence(
            state=TOKEN_ONLY_NOT_POLLED, token_var=None, prefix=prefix,
            note="no token variable answered, so there is no bot to poll",
        )

    local = _LOCAL.get(token_var)
    if local is not None:
        # This process's own claim. Authoritative and filesystem-free.
        return _from_record(local, token_var, prefix, "in_process", ref=ref)

    path = entry_path(token_var, root)
    try:
        raw: Optional[str] = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw = None
    except OSError as exc:
        return PollEvidence(
            state=UNKNOWN, token_var=token_var, prefix=prefix,
            note=(f"the poll registry for {token_var} could not be read ({exc}) — "
                  f"we did not look, which is NOT the same as nothing polling it"),
        )

    if raw is None:
        # The entry is absent. That is only evidence if the registry itself is
        # reachable — otherwise we are reading absence out of a directory we
        # cannot see, which is `unknown`. ("A negative result needs a
        # denominator": prove the probe could have found a positive.)
        try:
            reachable = registry_dir(root).parent.is_dir()
        except OSError:
            reachable = False
        if not reachable:
            return PollEvidence(
                state=UNKNOWN, token_var=token_var, prefix=prefix,
                note=(f"no poll claim for {token_var}, and the registry root is "
                      f"not reachable either — absence here is not evidence"),
            )
        return PollEvidence(
            state=TOKEN_ONLY_NOT_POLLED, token_var=token_var, prefix=prefix,
            source="heartbeat",
            note=(f"{token_var} resolves to a bot, but no process has claimed to "
                  f"poll it — a button sent there would be inert"),
        )

    try:
        rec = json.loads(raw)
    except ValueError as exc:
        return PollEvidence(
            state=UNKNOWN, token_var=token_var, prefix=prefix,
            note=(f"the poll claim for {token_var} is malformed ({exc}) — "
                  f"we cannot tell whether it is polled"),
        )
    if not isinstance(rec, dict):
        return PollEvidence(
            state=UNKNOWN, token_var=token_var, prefix=prefix,
            note=f"the poll claim for {token_var} is not an object — cannot read it",
        )
    return _from_record(rec, token_var, prefix, "heartbeat", ref=ref)


def log_poll_banner(
    token_var: Optional[str],
    prefixes: Sequence[str],
    *,
    service: str,
    log: Optional[logging.Logger] = None,
) -> str:
    """One startup line naming WHICH token this process polls and what it handles.

    The line exists because the dead-button state is otherwise invisible: there
    is no error, no exception and no failed send to notice. A grep-able line per
    service start is the cheapest thing that makes the wiring auditable — and it
    names the VARIABLE, never the token.
    """
    out = log or logger
    if not token_var:
        line = (f"telegram poll: {service} has NO token variable set — it polls "
                f"nothing, so no button it sends could ever be answered")
        out.error(line)
        return line
    line = (f"telegram poll: {service} polls {token_var} with callback handlers "
            f"for {sorted(prefixes) or '[]'}")
    out.info(line)
    return line


__all__ = [
    "POLLED_WITH_HANDLER",
    "POLL_STATES",
    "PollEvidence",
    "TOKEN_ONLY_NOT_POLLED",
    "UNKNOWN",
    "entry_path",
    "forget_poll",
    "heartbeat_interval_seconds",
    "log_poll_banner",
    "poll_state",
    "record_poll",
    "registry_dir",
    "stale_after_seconds",
]
