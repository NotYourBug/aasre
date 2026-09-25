"""Durable message admission and processing-reaction snapshots."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from filelock import FileLock

from config.constants.feishu import FEISHU_ACK_RETENTION_SECONDS, FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS


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

    def _load(self) -> dict[str, AckRecord]:
        records: dict[str, AckRecord] = {}
        if not self.path.exists():
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
        return records

    def change(
        self, message_id: str, update: Callable[[AckRecord | None], AckRecord | None]
    ) -> AckRecord | None:
        """Append the update durably before returning it to a side-effect owner."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(self.path) + ".lock", timeout=FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS):
            previous = self._load().get(message_id)
            record = update(previous)
            if record is not None and record != previous:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as target:
                    target.write(json.dumps(asdict(record), allow_nan=False) + "\n")
                    target.flush()
                    os.fsync(target.fileno())
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

    def compact(self) -> None:
        """Expire cleaned terminal admissions while preserving unfinished cleanup."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(self.path) + ".lock", timeout=FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS):
            cutoff = time.time() - FEISHU_ACK_RETENTION_SECONDS
            records = self._load()
            descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".ack-")
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
                    for record in records.values():
                        if record.state == "removed" and record.updated_at < cutoff:
                            continue
                        target.write(json.dumps(asdict(record), allow_nan=False) + "\n")
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def admit(self, message_id: str) -> bool:
        """Reserve a previously unseen inbound message before any network call."""
        admitted = False

        def update(previous: AckRecord | None) -> AckRecord:
            nonlocal admitted
            if previous is not None:
                return previous
            admitted = True
            now = time.time()
            return AckRecord(message_id=message_id, created_at=now, updated_at=now)

        self.change(message_id, update)
        return admitted
