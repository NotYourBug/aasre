"""Cross-process, durable deduplication for metadata-only feedback."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from filelock import FileLock, Timeout

logger = logging.getLogger(__name__)


class FeedbackWriteResult(StrEnum):
    WRITTEN = "written"
    DUPLICATE = "duplicate"
    FAILED = "failed"


def _key(entry: Mapping[str, object], fields: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(entry.get(field) for field in fields)
    if not fields or any(not isinstance(value, str) or not value for value in values):
        raise ValueError("Feedback identity fields must be nonempty strings")
    return tuple(str(value) for value in values)


def append_feedback_entry_once(
    entry: Mapping[str, object],
    *,
    idempotency_fields: tuple[str, ...],
    path: Path,
    lock_timeout_seconds: float,
) -> FeedbackWriteResult:
    """Append a durable row once per identity, failing closed on unreadable history."""
    try:
        key = _key(entry, idempotency_fields)
        line = json.dumps(dict(entry), ensure_ascii=False, allow_nan=False) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(path) + ".lock", timeout=lock_timeout_seconds):
            keys: set[tuple[str, ...]] = set()
            if path.exists():
                with path.open(encoding="utf-8") as source:
                    for previous in source:
                        if not previous.endswith("\n"):
                            raise ValueError("Incomplete feedback history")
                        row = json.loads(previous)
                        if not isinstance(row, dict):
                            raise ValueError("Invalid feedback history")
                        keys.add(_key(row, idempotency_fields))
            if key in keys:
                # A previous writer may have appended the row but failed fsync.
                # Repair durability before acknowledging a duplicate as settled.
                with path.open("ab") as existing:
                    os.fsync(existing.fileno())
                return FeedbackWriteResult.DUPLICATE
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as target:
                target.write(line)
                target.flush()
                os.fsync(target.fileno())
        return FeedbackWriteResult.WRITTEN
    except (OSError, TypeError, ValueError, Timeout):
        logger.warning("Feedback persistence failed")
        return FeedbackWriteResult.FAILED
