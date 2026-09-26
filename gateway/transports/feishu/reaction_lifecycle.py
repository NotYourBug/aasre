"""Non-blocking processing markers with durable replay and cleanup ownership."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import replace
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Protocol

from config.constants.feishu import FEISHU_ACK_QUEUE_LIMIT
from gateway.transports.feishu.reaction_ledger import AckRecord, ReactionLedger
from integrations.feishu import FeishuReaction

logger = logging.getLogger(__name__)


class ReactionClient(Protocol):
    def add_eye(self, message_id: str) -> FeishuReaction:
        """Create a processing reaction and return its exact identity."""

    def delete(self, message_id: str, reaction_id: str) -> None:
        """Remove this exact bot-owned reaction."""

    def list_eye(self, message_id: str) -> tuple[FeishuReaction, ...]:
        """List processing reactions with operator evidence."""


class AckAdmission(StrEnum):
    ADMITTED = "admitted"
    DUPLICATE = "duplicate"
    UNTRACKED = "untracked"


class AckOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    SHUTDOWN = "shutdown"


class ReactionLifecycleManager:
    """Keep reaction I/O off turn threads and preserve unfinished cleanup."""

    def __init__(
        self,
        *,
        path: Path,
        client: ReactionClient,
        app_id: str,
        queue_limit: int = FEISHU_ACK_QUEUE_LIMIT,
    ) -> None:
        self._ledger = ReactionLedger(path)
        self._client = client
        self._app_id = app_id
        self._lock = threading.RLock()
        self._permits = threading.BoundedSemaphore(queue_limit)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="feishu-ack")
        self._futures: set[Future[None]] = set()
        self._owned: set[str] = set()
        self._pending_cleanup: set[tuple[str, str]] = set()
        self._removing: set[tuple[str, str]] = set()
        self._closed = False

    def _submit(self, work: Callable[[], None], *, reserved: bool = False) -> bool:
        if not reserved and not self._permits.acquire(blocking=False):
            return False
        try:
            future = self._executor.submit(work)
        except RuntimeError:
            self._permits.release()
            return False
        self._futures.add(future)

        def done(completed: Future[None]) -> None:
            with self._lock:
                self._futures.discard(completed)
                self._permits.release()
                self._drain_cleanup()

        future.add_done_callback(done)
        return True

    def _drain_cleanup(self) -> None:
        if self._closed:
            return
        for message_id, reaction_id in tuple(self._pending_cleanup):
            identity = (message_id, reaction_id)
            self._pending_cleanup.discard(identity)
            if not self._submit(partial(self._remove, message_id, reaction_id)):
                self._pending_cleanup.add(identity)
                return

    def begin(self, message_id: str) -> AckAdmission:
        """Reserve a message and start its marker when no session setup is needed."""
        admission = self.reserve(message_id)
        if admission is not AckAdmission.ADMITTED:
            return admission
        return self.start_marker(message_id)

    def reserve(self, message_id: str) -> AckAdmission:
        """Deduplicate before session side effects without showing a marker."""
        with self._lock:
            if self._closed or not message_id:
                return AckAdmission.UNTRACKED
            try:
                admitted = self._ledger.admit(message_id)
                if not admitted:
                    return AckAdmission.DUPLICATE
                self._owned.add(message_id)
                return AckAdmission.ADMITTED
            except Exception:
                logger.warning("Feishu ack admission unavailable")
                return AckAdmission.UNTRACKED

    def abort(self, message_id: str) -> None:
        """Release a pre-turn reservation after session setup failed."""
        with self._lock:
            if message_id not in self._owned:
                return
            try:
                record = self._ledger.change(
                    message_id,
                    lambda previous: (
                        replace(previous, state="aborted", updated_at=time.time())
                        if previous is not None and previous.state == "reserved"
                        else previous
                    ),
                )
                if record is not None and record.state == "aborted":
                    self._owned.discard(message_id)
            except Exception:
                logger.warning("Feishu ack reservation release unavailable")

    def start_marker(self, message_id: str) -> AckAdmission:
        """Start the processing marker only after a real turn is confirmed."""
        with self._lock:
            if self._closed or message_id not in self._owned:
                return AckAdmission.UNTRACKED
            if not self._permits.acquire(blocking=False):
                return AckAdmission.UNTRACKED
            try:
                changed = False

                def update(record: AckRecord | None) -> AckRecord | None:
                    nonlocal changed
                    if record is None or record.state != "reserved":
                        return record
                    changed = True
                    return replace(record, state="adding", updated_at=time.time())

                self._ledger.change(message_id, update)
                if changed:
                    if self._submit(partial(self._add, message_id), reserved=True):
                        return AckAdmission.ADMITTED
                    return AckAdmission.UNTRACKED
            except Exception:
                logger.warning("Feishu ack marker unavailable")
            self._permits.release()
            return AckAdmission.UNTRACKED

    def _transition(
        self,
        message_id: str,
        *,
        state: str | None = None,
        outcome: str | None = None,
        reaction_id: str | None = None,
        operator_id: str | None = None,
        operator_type: str | None = None,
    ) -> AckRecord | None:
        def update(record: AckRecord | None) -> AckRecord | None:
            if record is None:
                return None
            return replace(
                record,
                updated_at=time.time(),
                state=state if state is not None else record.state,
                outcome=outcome if outcome is not None else record.outcome,
                reaction_id=reaction_id if reaction_id is not None else record.reaction_id,
                operator_id=operator_id if operator_id is not None else record.operator_id,
                operator_type=operator_type if operator_type is not None else record.operator_type,
            )

        return self._ledger.change(message_id, update)

    def _add(self, message_id: str) -> None:
        try:
            reaction = self._client.add_eye(message_id)
        except Exception:
            with suppress(Exception):
                self._transition(message_id, state="add_failed")
            logger.warning("Feishu ack create unavailable")
            return
        try:

            def update(record: AckRecord | None) -> AckRecord | None:
                if record is None:
                    return None
                outcome = record.outcome or (AckOutcome.SHUTDOWN.value if self._closed else "")
                return replace(
                    record,
                    reaction_id=reaction.reaction_id,
                    operator_id=reaction.operator_id,
                    operator_type=reaction.operator_type,
                    updated_at=time.time(),
                    outcome=outcome,
                    state="removing" if outcome else "active",
                )

            with self._lock:
                record = self._ledger.change(message_id, update)
            if record is None or record.outcome:
                self._remove(message_id, reaction.reaction_id)
        except Exception:
            # Admission remains durable even when recording the returned ID fails.
            # The exact ID is still known here; clean it now rather than lose it.
            self._remove(message_id, reaction.reaction_id)

    def _remove(self, message_id: str, reaction_id: str) -> None:
        identity = (message_id, reaction_id)
        with self._lock:
            if identity in self._removing:
                return
            try:
                record = self._ledger.find(message_id)
                if record and record.state == "removed" and record.reaction_id == reaction_id:
                    return
            except Exception:
                logger.warning("Feishu ack cleanup state unavailable")
            self._removing.add(identity)
        state = "removed"
        try:
            self._client.delete(message_id, reaction_id)
        except Exception:
            state = "remove_failed"
            with suppress(Exception):
                remaining = self._client.list_eye(message_id)
                if not any(reaction.reaction_id == reaction_id for reaction in remaining):
                    state = "removed"
            logger.warning("Feishu ack cleanup unavailable")
        try:
            self._transition(message_id, state=state, reaction_id=reaction_id)
            if state == "removed":
                with self._lock:
                    self._owned.discard(message_id)
        except Exception:
            logger.warning("Feishu ack cleanup persistence unavailable")
        finally:
            with self._lock:
                self._removing.discard(identity)

    def finish(self, message_id: str, outcome: AckOutcome) -> None:
        """Persist the first terminal outcome and schedule exact-ID cleanup."""
        with self._lock:
            changed = False

            def update(record: AckRecord | None) -> AckRecord | None:
                nonlocal changed
                if record is None or record.outcome:
                    return record
                changed = True
                return replace(
                    record,
                    outcome=outcome.value,
                    updated_at=time.time(),
                    state=(
                        "removed"
                        if record.state == "reserved"
                        else "removing"
                        if record.reaction_id
                        else "terminal_pending_add"
                    ),
                )

            try:
                record = self._ledger.change(message_id, update)
                if changed and record and record.state == "removed":
                    self._owned.discard(message_id)
                if changed and record and record.reaction_id:
                    identity = (message_id, record.reaction_id)
                    if not self._submit(partial(self._remove, *identity)):
                        self._pending_cleanup.add(identity)
            except Exception:
                logger.warning("Feishu ack terminal persistence unavailable")

    def reconcile(self, *, budget_seconds: float, record_limit: int) -> None:
        """Schedule bounded recovery before admitting new turns."""
        deadline = time.monotonic() + budget_seconds
        try:
            self._ledger.compact()
            records = self._ledger.records()
        except Exception:
            logger.warning("Feishu ack recovery unavailable")
            return
        with self._lock:
            submitted = 0
            for record in records:
                if time.monotonic() >= deadline or self._closed or submitted >= record_limit:
                    break
                if record.state == "removed" or record.message_id in self._owned:
                    continue
                if record.state == "aborted":
                    continue
                if record.state == "reserved":
                    self._transition(record.message_id, state="aborted")
                    continue
                if not self._submit(partial(self._recover, record, deadline)):
                    break
                submitted += 1

    def _recover(self, record: AckRecord, deadline: float) -> None:
        if time.monotonic() >= deadline:
            return
        try:
            if record.state in {"reserved", "aborted"}:
                return
            self._transition(record.message_id, outcome=record.outcome or AckOutcome.SHUTDOWN.value)
            if record.reaction_id:
                self._remove(record.message_id, record.reaction_id)
                return
            ours = [
                reaction
                for reaction in self._client.list_eye(record.message_id)
                if reaction.operator_type == "app" and reaction.operator_id == self._app_id
            ]
            if not ours:
                self._transition(record.message_id, state="removed")
            for reaction in ours:
                if time.monotonic() >= deadline:
                    return
                if reaction.operator_type == "app" and reaction.operator_id == self._app_id:
                    self._transition(
                        record.message_id,
                        state="removing",
                        reaction_id=reaction.reaction_id,
                        operator_id=reaction.operator_id,
                        operator_type=reaction.operator_type,
                    )
                    self._remove(record.message_id, reaction.reaction_id)
        except Exception:
            logger.warning("Feishu ack recovery unavailable")

    def shutdown(self, *, timeout_seconds: float) -> None:
        """Stop admissions and leave any cleanup beyond the deadline recoverable."""
        deadline = time.monotonic() + timeout_seconds
        with self._lock:
            if not self._closed:
                self._closed = True
                # Queued adds observe _closed; already active records are recovered
                # here without doing disk or network work on the stopping thread.
                future = self._executor.submit(self._shutdown_cleanup)
                self._futures.add(future)
                self._executor.shutdown(wait=False)
            futures = tuple(self._futures)
        if futures:
            wait(futures, timeout=max(0, deadline - time.monotonic()))

    def _shutdown_cleanup(self) -> None:
        with self._lock:
            owned = tuple(self._owned)
        for message_id in owned:
            try:
                record = self._ledger.find(message_id)
                if record is None or record.state == "removed" or not record.reaction_id:
                    continue
                self._transition(message_id, outcome=record.outcome or AckOutcome.SHUTDOWN.value)
                self._remove(message_id, record.reaction_id)
            except Exception:
                logger.warning("Feishu ack shutdown cleanup unavailable")
