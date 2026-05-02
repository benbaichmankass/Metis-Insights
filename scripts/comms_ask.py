#!/usr/bin/env python3
"""
Author a comms request from a Claude session.

Writes a fully-validated ``comms/requests/REQ-*.json`` and (optionally)
commits it. The Telegram bot picks it up on its next 60-second poll
and sends it to the operator as an inline-keyboard menu.

Usage examples
--------------

Single yes/no question::

    python scripts/comms_ask.py \\
        --topic "Approve sprint S-028 plan?" \\
        --slug s028plan \\
        --question "approve" --type yes_no \\
            --prompt "Greenlight to proceed with S-028 sprint plan?"

Multiple-choice with "Other" free-text::

    python scripts/comms_ask.py \\
        --topic "Default mode for new account?" \\
        --slug acctmode \\
        --context "Adding a BTC-only sub-account; should it default to live or paper?" \\
        --question "mode" --type choice \\
            --prompt "Which mode?" \\
            --choice live=Live --choice paper=Paper \\
            --allow-other \\
        --expires-in 24h

Pure free-text question::

    python scripts/comms_ask.py \\
        --topic "Sprint goals" --slug sprintideas \\
        --question "ideas" --type free_text \\
            --prompt "What should the next sprint focus on?"

The script never pushes — git push is the bot's responsibility on
operator answer. This script's only commit happens with --commit.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT))

from src.comms import Choice, Question, Request, RequestStore  # noqa: E402
from src.comms.models import (  # noqa: E402
    CommsValidationError,
    make_request_id,
)

logger = logging.getLogger("comms_ask")


# ----------------------------------------------------------------------
# Argument parsing — multi-question support via repeated --question groups

class _QuestionGroup:
    __slots__ = (
        "question_id",
        "input_type",
        "prompt",
        "choices",
        "allow_other",
        "allow_free_text",
        "required",
        "default_choice",
    )

    def __init__(self) -> None:
        self.question_id: Optional[str] = None
        self.input_type: Optional[str] = None
        self.prompt: Optional[str] = None
        self.choices: list[Choice] = []
        self.allow_other: bool = False
        self.allow_free_text: bool = False
        self.required: bool = True
        self.default_choice: Optional[str] = None

    def build(self) -> Question:
        if not self.question_id or not self.input_type or not self.prompt:
            raise SystemExit(
                "every --question needs --type and --prompt before the next --question"
            )
        return Question(
            question_id=self.question_id,
            prompt=self.prompt,
            input_type=self.input_type,
            choices=self.choices or None,
            allow_other=self.allow_other,
            allow_free_text=self.allow_free_text,
            required=self.required,
            default_choice=self.default_choice,
        )


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[Question]]:
    """Two-pass parse: first pull --question repeated groups manually, then argparse the rest."""
    p = argparse.ArgumentParser(prog="comms_ask", description=__doc__)
    p.add_argument("--topic", required=True, help="Short label shown in Telegram menu header")
    p.add_argument("--slug", required=True, help="Unique slug for the request id; cleaned to [a-z0-9]{4..12}")
    p.add_argument("--context", default=None, help="Optional longer context shown above the questions")
    p.add_argument("--expires-in", default=None,
                   help="Relative TTL (e.g. '24h', '90m', '7d'). Default: no expiry.")
    p.add_argument("--default-on-timeout",
                   choices=["expire", "use_defaults", "close"], default="expire")
    p.add_argument("--source-actor",
                   choices=["claude", "operator", "system"], default="claude")
    p.add_argument("--branch", default=None)
    p.add_argument("--pr-number", type=int, default=None)
    p.add_argument("--task", default=None)
    p.add_argument("--commit", action="store_true",
                   help="git add + commit the artifact (no push). Default: write only.")
    p.add_argument("--print", action="store_true",
                   help="Print the artifact JSON to stdout instead of writing")
    p.add_argument("--repo-root", default=str(REPO_ROOT))
    # Captured separately below.
    p.add_argument("--question", action="append", default=[],
                   help="Begin a new question. Repeatable.")
    p.add_argument("--type", action="append", default=[],
                   choices=["choice", "multi_choice", "free_text", "yes_no"],
                   help="Input type for the most-recent --question.")
    p.add_argument("--prompt", action="append", default=[],
                   help="Prompt text for the most-recent --question.")
    p.add_argument("--choice", action="append", default=[],
                   help="`id=label` choice. Repeatable, attaches to the most-recent --question.")
    p.add_argument("--allow-other", action="append_const", const=True, default=[],
                   help="Add 'Other' free-text path to the most-recent --question.")
    p.add_argument("--allow-free-text", action="append_const", const=True, default=[],
                   help="Allow free-text supplement to the most-recent --question.")
    p.add_argument("--optional", action="append_const", const=True, default=[],
                   help="Mark the most-recent --question as not required.")
    p.add_argument("--default-choice", action="append", default=[],
                   help="Default choice id for use with --default-on-timeout=use_defaults.")

    args = p.parse_args(argv)

    questions = _stitch_question_groups(argv, args)
    if not questions:
        raise SystemExit("comms_ask requires at least one --question")
    return args, questions


def _stitch_question_groups(argv: list[str], args: argparse.Namespace) -> list[Question]:
    """Walk argv left-to-right and assign the per-question flags by position.

    argparse's append-style flags lose their pairing with --question
    boundaries; we recover the boundaries by scanning the original argv.
    """
    groups: list[_QuestionGroup] = []
    current: Optional[_QuestionGroup] = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--question":
            if current is not None:
                groups.append(current)
            current = _QuestionGroup()
            current.question_id = argv[i + 1]
            i += 2
            continue
        if current is None:
            i += 1
            continue
        if token == "--type":
            current.input_type = argv[i + 1]
            i += 2
        elif token == "--prompt":
            current.prompt = argv[i + 1]
            i += 2
        elif token == "--choice":
            current.choices.append(_parse_choice(argv[i + 1]))
            i += 2
        elif token == "--allow-other":
            current.allow_other = True
            i += 1
        elif token == "--allow-free-text":
            current.allow_free_text = True
            i += 1
        elif token == "--optional":
            current.required = False
            i += 1
        elif token == "--default-choice":
            current.default_choice = argv[i + 1]
            i += 2
        else:
            i += 1
    if current is not None:
        groups.append(current)
    return [g.build() for g in groups]


def _parse_choice(spec: str) -> Choice:
    if "=" not in spec:
        raise SystemExit(f"--choice expects 'id=label', got {spec!r}")
    cid, label = spec.split("=", 1)
    return Choice(id=cid.strip(), label=label.strip())


# ----------------------------------------------------------------------
# Expiry parsing

_EXPIRES_RE = re.compile(r"^(\d+)\s*([smhd])$")


def _parse_expires_in(spec: Optional[str]) -> Optional[str]:
    if spec is None:
        return None
    m = _EXPIRES_RE.match(spec.strip())
    if not m:
        raise SystemExit(f"--expires-in must look like '90m', '24h', '7d'; got {spec!r}")
    qty = int(m.group(1))
    unit = m.group(2)
    delta = {"s": timedelta(seconds=qty), "m": timedelta(minutes=qty),
             "h": timedelta(hours=qty), "d": timedelta(days=qty)}[unit]
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")


# ----------------------------------------------------------------------
# Main

def _git_commit(repo_root: Path, paths: List[Path], message: str) -> None:
    rels = [str(p.resolve().relative_to(repo_root.resolve())) for p in paths]
    subprocess.run(["git", "add", "--", *rels], cwd=str(repo_root), check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(repo_root), check=True)


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args, questions = _parse_args(argv)

    expires_at = _parse_expires_in(args.expires_in)
    request = Request(
        request_id=make_request_id(slug=args.slug),
        questions=questions,
        topic=args.topic,
        context=args.context,
        expires_at=expires_at,
        default_on_timeout=args.default_on_timeout,
        source_actor=args.source_actor,
        branch=args.branch,
        pr_number=args.pr_number,
        task=args.task,
    )

    if args.print:
        print(json.dumps(request.to_dict(), indent=2, ensure_ascii=False))
        return 0

    repo_root = Path(args.repo_root)
    store = RequestStore(repo_root / "comms")
    try:
        path = store.create(request)
    except (CommsValidationError, FileExistsError) as exc:
        logger.error("comms_ask: %s", exc)
        return 2

    logger.info("Wrote %s", path.relative_to(repo_root))
    if args.commit:
        _git_commit(
            repo_root,
            [path],
            f"comms(ask): {request.request_id} {args.topic}",
        )
        logger.info("Committed (no push). Bot will pick it up on next sync + poll.")
    else:
        logger.info("Not committed. Add & commit when you're ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
