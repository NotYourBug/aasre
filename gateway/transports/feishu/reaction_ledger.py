"""Durable message admission and processing-reaction snapshots."""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from filelock import FileLock

from config.constants.feishu import (
    FEISHU_ACK_LEDGER_COMPACT_EVERY,
    FEISHU_ACK_RETENTION_SECONDS,
    FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AckRecord:
    message_id: str
    reaction_id: str = ""
    operator_id: str = ""
    operator_type: str = ""
    state: str = "adding"
    outcome: str = ""
    created_at: float = 0
    updated_at: float = 0


class ReactionLedger:
    """Serialize admission and snapshot changes across processes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._cached_records: dict[str, AckRecord] | None = None
        self._signature: tuple[int, int, int, int] | None = None
        self._appends_since_compaction = 0

    def _file_signature(self) -> tuple[int, int, int, int] | None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def _load(self) -> dict[str, AckRecord]:
        signature = self._file_signature()
        if self._cached_records is not None and signature == self._signature:
            return self._cached_records
        records: dict[str, AckRecord] = {}
        if signature is None:
            self._cached_records = records
            self._signature = None
            return records
        with self.path.open(encoding="utf-8") as source:
            for line in source:
                if not line.endswith("\n"):
                    raise ValueError("Incomplete reaction ledger")
                record = AckRecord(**json.loads(line))
                strings = (
                    record.message_id,
                    record.reaction_id,
                    record.operator_id,
                    record.operator_type,
                    record.state,
                    record.outcome,
                )
                timestamps = (record.created_at, record.updated_at)
                if (
                    any(not isinstance(value, str) for value in strings)
                    or any(
                        not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                        for value in timestamps
                    )
                    or not record.message_id
                    or record.outcome
                    not in {"", "success", "failure", "cancelled", "timeout", "shutdown"}
                ):
                    raise ValueError("Invalid reaction ledger")
                if record.state not in {
                    "reserved",
                    "rotated",
                    "aborted",
                    "adding",
                    "active",
                    "terminal_pending_add",
                    "add_failed",
                    "removing",
                    "removed",
                    "remove_failed",
                }:
                    raise ValueError("Invalid reaction ledger")
                records[record.message_id] = record
        self._cached_records = records
        self._signature = signature
        return records

    def change(
        self, message_id: str, update: Callable[[AckRecord | None], AckRecord | None]
    ) -> AckRecord | None:
        """Append the update durably before returning it to a side-effect owner."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(self.path) + ".lock", timeout=FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS):
            records = self._load()
            previous = records.get(message_id)
            record = update(previous)
            if record is not None and record != previous:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as target:
                    target.write(json.dumps(asdict(record), allow_nan=False) + "\n")
                    target.flush()
                    os.fsync(target.fileno())
                records[message_id] = record
                self._signature = self._file_signature()
                self._appends_since_compaction += 1
                if self._appends_since_compaction >= FEISHU_ACK_LEDGER_COMPACT_EVERY:
                    try:
                        self._compact_locked(records)
                    except OSError:
                        self._appends_since_compaction = 0
                        logger.warning("Feishu ack ledger compaction unavailable")
            return record

    def records(self) -> tuple[AckRecord, ...]:
        """Return a coherent snapshot for bounded startup recovery."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(self.path) + ".lock", timeout=FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS):
            return tuple(self._load().values())

    def find(self, message_id: str) -> AckRecord | None:
        """Look up one durable admission under the ledger lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(self.path) + ".lock", timeout=FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS):
            return self._load().get(message_id)

    def compact(self, *, abandon_reserved: bool = False) -> None:
        """Expire settled records; startup may release crashed pre-turn reservations."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(self.path) + ".lock", timeout=FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS):
            self._compact_locked(self._load(), abandon_reserved=abandon_reserved)

    def _compact_locked(
        self, records: dict[str, AckRecord], *, abandon_reserved: bool = False
    ) -> None:
        now = time.time()
        cutoff = now - FEISHU_ACK_RETENTION_SECONDS
        retained: dict[str, AckRecord] = {}
        for message_id, record in records.items():
            if abandon_reserved and record.state == "reserved":
                record = replace(record, state="aborted", updated_at=now)
            if record.state in {"removed", "aborted"} and record.updated_at < cutoff:
                continue
            retained[message_id] = record
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".ack-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
                for record in retained.values():
                    target.write(json.dumps(asdict(record), allow_nan=False) + "\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            self._cached_records = retained
            self._signature = self._file_signature()
            self._appends_since_compaction = 0
        finally:
            Path(temporary).unlink(missing_ok=True)

    def admit(self, message_id: str) -> bool:
        """Reserve a previously unseen inbound message before any network call."""
        admitted = False

        def update(previous: AckRecord | None) -> AckRecord:
            nonlocal admitted
            if previous is not None and previous.state != "aborted":
                return previous
            admitted = True
            now = time.time()
            return AckRecord(
                message_id=message_id, state="reserved", created_at=now, updated_at=now
            )

        self.change(message_id, update)
        return admitted
