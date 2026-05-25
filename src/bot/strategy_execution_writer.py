"""Sanctioned writer for the per-strategy execution gate.

This is the strategy-level twin of ``scripts/ops/set_account_mode.sh``
(which owns the account-level ``mode:`` gate). It performs a **targeted
single-line edit** of ``config/strategies.yaml`` to flip one strategy's
``execution: live | shadow`` field, preserving every surrounding
comment, field, and the trailing inline comment on the edited line
byte-for-byte.

Why a dedicated writer rather than a full YAML round-trip: the config
files are heavily commented and ordering-sensitive, and a
``yaml.safe_load`` → ``yaml.safe_dump`` round-trip would strip all of
that. The block-find regex here mirrors ``set_account_mode.sh`` so the
two gates stay consistent and the ``dry-run-guard`` CI check's
expectations hold (the kill-switch edits happen at runtime on the VM,
never as a static PR diff).

Pure + offline: callers pass the YAML path; this module performs no
service restart and no coordinator reload (the bot wiring layer calls
``coord.reload_strategy_config()`` after a successful write).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

VALID_EXECUTIONS = ("live", "shadow")

# Strategy keys live at 2-space indent under the top-level ``strategies:``
# mapping; their fields (``execution:`` etc.) live at 4-space indent.
# Identical block shape to accounts.yaml, so the regexes mirror
# set_account_mode.sh.
_STRATEGY_KEY_RE_TMPL = r"^  {name}:\s*$"
_NEXT_KEY_RE = re.compile(r"^  \w[\w-]*:\s*$", re.MULTILINE)
_EXECUTION_LINE_RE = re.compile(r"^(\s{4}execution:\s*)\S+(.*)$", re.MULTILINE)


class StrategyExecutionWriteError(RuntimeError):
    """Raised when the targeted YAML edit cannot be performed safely."""


def _strategy_block_bounds(content: str, strategy: str) -> Tuple[int, int]:
    """Return ``(start, end)`` char offsets of *strategy*'s field block.

    ``start`` is just past the ``  <strategy>:`` key line; ``end`` is the
    start of the next sibling strategy key (or EOF). Raises
    ``StrategyExecutionWriteError`` if the strategy key is not found.
    """
    key_re = re.compile(
        _STRATEGY_KEY_RE_TMPL.format(name=re.escape(strategy)), re.MULTILINE
    )
    m = key_re.search(content)
    if not m:
        raise StrategyExecutionWriteError(
            f"strategy {strategy!r} not found in strategies.yaml"
        )
    start = m.end()
    nxt = _NEXT_KEY_RE.search(content, start)
    end = nxt.start() if nxt else len(content)
    return start, end


def read_strategy_execution(yaml_path: str | Path, strategy: str) -> str:
    """Return the current ``execution`` value for *strategy*.

    Defaults to ``"live"`` when the strategy block omits the field
    (the permissive default declared in strategies.yaml).
    """
    content = Path(yaml_path).read_text(encoding="utf-8")
    start, end = _strategy_block_bounds(content, strategy)
    m = _EXECUTION_LINE_RE.search(content[start:end])
    if not m:
        return "live"
    # group(1) is "    execution: "; the value is what follows up to the
    # first whitespace/comment.
    value = content[start:end][m.start():m.end()]
    val = value.split("execution:", 1)[1].strip().split()[0]
    return val.strip().lower()


def set_strategy_execution(
    yaml_path: str | Path, strategy: str, execution: str
) -> Tuple[str, str]:
    """Flip *strategy*'s ``execution`` gate to *execution* in place.

    Returns ``(previous, new)``. Preserves all surrounding content and
    any trailing inline comment on the edited line. If the strategy
    block has no ``execution:`` line, one is inserted at the top of the
    block at 4-space indent.

    Raises ``StrategyExecutionWriteError`` on an unknown strategy or an
    invalid *execution* value.
    """
    execution = (execution or "").strip().lower()
    if execution not in VALID_EXECUTIONS:
        raise StrategyExecutionWriteError(
            f"invalid execution {execution!r}; must be one of {VALID_EXECUTIONS}"
        )

    path = Path(yaml_path)
    content = path.read_text(encoding="utf-8")
    start, end = _strategy_block_bounds(content, strategy)
    block = content[start:end]

    new_block, n = _EXECUTION_LINE_RE.subn(
        lambda mm: f"{mm.group(1)}{execution}{mm.group(2)}", block, count=1
    )
    if n == 1:
        previous = read_strategy_execution(yaml_path, strategy)
    else:
        # No execution line in this block. ``start`` sits just before the
        # newline that ends the strategy key line, so prepend the new
        # field with a leading newline to land it at 4-space indent as the
        # block's first field.
        previous = "live"
        new_block = f"\n    execution: {execution}" + block

    path.write_text(content[:start] + new_block + content[end:], encoding="utf-8")
    return previous, execution
