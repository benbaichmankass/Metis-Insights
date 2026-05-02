"""
Filesystem store for comms request artifacts.

Layout (relative to repo root):

    comms/requests/REQ-*.json   active requests (any non-terminal status)
    comms/archive/REQ-*.json    terminal requests (acknowledged/expired/cancelled)
    comms/log.ndjson            append-only event log

The store is intentionally simple: one file per request, atomic writes
(``tmp + os.replace``), filename == ``<request_id>.json``. There is no
external lock — duplicate-send prevention is enforced by reading
``status`` + ``delivery.sent_at`` inside the artifact itself, and the bot
moves ``pending → sent`` in a single write.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .models import CommsValidationError, Request, Response
from .state import STATUS, can_transition

logger = logging.getLogger(__name__)

DEFAULT_ROOT = Path("comms")


class RequestStore:
    """Filesystem-backed store for comms requests.

    All paths are derived from ``root``. The store creates the ``requests/``
    and ``archive/`` subdirectories on demand so a fresh checkout (or a
    test using ``tmp_path``) just works.
    """

    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)
        self.requests_dir = self.root / "requests"
        self.archive_dir = self.root / "archive"

    # ------------------------------------------------------------------
    # path helpers

    def _ensure_dirs(self) -> None:
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, request_id: str) -> Path:
        return self.requests_dir / f"{request_id}.json"

    def archive_path_for(self, request_id: str) -> Path:
        return self.archive_dir / f"{request_id}.json"

    # ------------------------------------------------------------------
    # read

    def load(self, request_id: str) -> Request:
        path = self.path_for(request_id)
        if not path.exists():
            archived = self.archive_path_for(request_id)
            if archived.exists():
                path = archived
            else:
                raise FileNotFoundError(f"comms request not found: {request_id}")
        return self._read(path)

    def list_active(self) -> Iterator[Request]:
        """Yield every non-terminal request, oldest-created first.

        Malformed files are skipped (with a WARNING) so a single bad
        artifact cannot break the bot's poll loop.
        """
        if not self.requests_dir.exists():
            return iter(())
        files = sorted(self.requests_dir.glob("REQ-*.json"))
        return self._iter_files(files)

    def list_pending(self) -> list[Request]:
        """Active requests that have not yet been sent (status=pending)."""
        return [r for r in self.list_active() if r.status == STATUS.PENDING]

    def list_awaiting_response(self) -> list[Request]:
        """Active requests sent to the operator and waiting on an answer."""
        return [r for r in self.list_active() if r.status in STATUS.AWAITING_RESPONSE]

    def _iter_files(self, files: list[Path]) -> Iterator[Request]:
        for f in files:
            try:
                yield self._read(f)
            except (CommsValidationError, json.JSONDecodeError, OSError) as exc:
                logger.warning("comms: skipping malformed artifact %s: %s", f, exc)

    @staticmethod
    def _read(path: Path) -> Request:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return Request.from_dict(data)

    # ------------------------------------------------------------------
    # write

    def create(self, request: Request) -> Path:
        """Persist a freshly-built request. Refuses to overwrite."""
        self._ensure_dirs()
        path = self.path_for(request.request_id)
        if path.exists():
            raise FileExistsError(f"comms request already exists: {request.request_id}")
        if not request.history:
            request.append_history(
                from_status=None, to_status=request.status, actor=request.source_actor,
                note="created",
            )
        self._atomic_write(path, request)
        return path

    def save(self, request: Request) -> Path:
        """Persist an updated request (no transition validation here)."""
        self._ensure_dirs()
        path = self.path_for(request.request_id)
        self._atomic_write(path, request)
        return path

    def transition(
        self,
        request: Request,
        *,
        to_status: str,
        actor: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Request:
        """Validate + apply a status transition, append history, save."""
        if not can_transition(request.status, to_status):
            raise CommsValidationError(
                f"illegal transition {request.status!r} → {to_status!r} "
                f"for {request.request_id}"
            )
        request.append_history(
            from_status=request.status, to_status=to_status, actor=actor, note=note,
        )
        request.status = to_status
        self.save(request)
        return request

    def mark_sent(
        self,
        request: Request,
        *,
        telegram_chat_id: Optional[str] = None,
        telegram_message_id: Optional[int] = None,
    ) -> Request:
        """Atomic ``pending → sent`` transition, recording delivery metadata.

        The bot calls this *immediately* after the Telegram API confirms the
        send. Re-entrancy (a second poll-loop tick before the file write
        completes) is guarded by re-reading the artifact and refusing if
        ``status`` already advanced — see comms-architecture § Idempotency.
        """
        if request.status != STATUS.PENDING:
            raise CommsValidationError(
                f"mark_sent: {request.request_id} is {request.status!r}, expected pending"
            )
        delivery = dict(request.delivery)
        delivery["sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if telegram_chat_id is not None:
            delivery["telegram_chat_id"] = telegram_chat_id
        if telegram_message_id is not None:
            delivery["telegram_message_id"] = telegram_message_id
        delivery["send_attempts"] = int(delivery.get("send_attempts", 0)) + 1
        request.delivery = delivery
        return self.transition(request, to_status=STATUS.SENT, actor="bot", note="telegram delivered")

    def attach_response(
        self,
        request: Request,
        response: Response,
        *,
        new_status: str,
        actor: str = "bot",
    ) -> Request:
        """Replace the request's response sub-document and transition status."""
        if response.request_id != request.request_id:
            raise CommsValidationError(
                f"attach_response: id mismatch {response.request_id!r} != {request.request_id!r}"
            )
        request.response = response
        return self.transition(request, to_status=new_status, actor=actor, note="response attached")

    def archive(self, request: Request) -> Path:
        """Move a terminal request out of the active dir into archive/."""
        if not request.is_terminal():
            raise CommsValidationError(
                f"archive: {request.request_id} is not terminal (status={request.status!r})"
            )
        self._ensure_dirs()
        src = self.path_for(request.request_id)
        dst = self.archive_path_for(request.request_id)
        if not src.exists():
            self._atomic_write(dst, request)
            return dst
        os.replace(src, dst)
        return dst

    @staticmethod
    def _atomic_write(path: Path, request: Request) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(request.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)
        # tmpfile in the same dir guarantees os.replace is atomic across the FS.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, path)
