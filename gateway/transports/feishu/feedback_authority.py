"""Durable server-side authority for opaque Feishu feedback buttons."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from filelock import FileLock

from config.constants.feishu import FEISHU_ACK_RETENTION_SECONDS, FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class FeedbackAuthority:
    token_digest: str
    requester_open_id: str
    chat_id: str
    message_id: str
    created_at: float
    expires_at: float
    state: str = "registered"


def _digest(token: str) -> str:
    if not isinstance(token, str) or not token or len(token) > 256:
        raise ValueError("Invalid feedback capability")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FeedbackAuthorityStore:
    """Reload authority under a cross-process lock for every security decision."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock

    def _lock(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return FileLock(str(self.path) + ".lock", timeout=FEISHU_LEDGER_LOCK_TIMEOUT_SECONDS)

    def _load(self) -> dict[str, FeedbackAuthority]:
        authorities: dict[str, FeedbackAuthority] = {}
        if not self.path.exists():
            return authorities
        with self.path.open(encoding="utf-8") as source:
            for line in source:
                if not line.endswith("\n"):
                    raise ValueError("Incomplete feedback authority")
                authority = FeedbackAuthority(**json.loads(line))
                if (
                    any(
                        not isinstance(value, str) or not value
                        for value in (
                            authority.token_digest,
                            authority.requester_open_id,
                            authority.chat_id,
                            authority.message_id,
                        )
                    )
                    or len(authority.token_digest) != 64
                    or any(c not in "0123456789abcdef" for c in authority.token_digest)
                    or authority.state not in {"registered", "consumed", "invalidated"}
                    or any(
                        not isinstance(value, (float, int)) or not math.isfinite(value)
                        for value in (authority.created_at, authority.expires_at)
                    )
                    or authority.expires_at <= authority.created_at
                ):
                    raise ValueError("Invalid feedback authority")
                authorities[authority.token_digest] = authority
        return authorities

    def _append(self, authority: FeedbackAuthority) -> None:
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as target:
            target.write(json.dumps(asdict(authority), allow_nan=False) + "\n")
            target.flush()
            os.fsync(target.fileno())

    def register(
        self, *, token: str, requester_open_id: str, chat_id: str, message_id: str
    ) -> FeedbackAuthority:
        """Persist server-owned bindings before exposing a button."""
        digest = _digest(token)
        if not all(
            isinstance(value, str) and value for value in (requester_open_id, chat_id, message_id)
        ):
            raise ValueError("Incomplete feedback authority")
        now = self._clock()
        authority = FeedbackAuthority(
            digest,
            requester_open_id,
            chat_id,
            message_id,
            now,
            now + FEISHU_ACK_RETENTION_SECONDS,
        )
        with self._lock():
            if digest in self._load():
                raise ValueError("Feedback capability already registered")
            self._append(authority)
        return authority

    def find(self, token: str) -> FeedbackAuthority | None:
        """Return only an unconsumed, unexpired server-side binding."""
        digest = _digest(token)
        with self._lock():
            authority = self._load().get(digest)
            if (
                authority is None
                or not hmac.compare_digest(authority.token_digest, digest)
                or authority.state != "registered"
                or self._clock() >= authority.expires_at
            ):
                return None
            return authority

    def _settle(self, token: str, state: str) -> bool:
        digest = _digest(token)
        with self._lock():
            authority = self._load().get(digest)
            if (
                authority is None
                or authority.state != "registered"
                or self._clock() >= authority.expires_at
            ):
                return False
            self._append(replace(authority, state=state))
            return True

    def consume(self, token: str) -> bool:
        """Mark one durable feedback capability consumed after its feedback write."""
        return self._settle(token, "consumed")

    def invalidate(self, token: str) -> bool:
        """Revoke a capability whose card append was definitively rejected."""
        return self._settle(token, "invalidated")

    def compact(self) -> None:
        """Atomically retain only unexpired capability snapshots."""
        with self._lock():
            authorities = self._load()
            now = self._clock()
            descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".feedback-")
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
                    for authority in authorities.values():
                        if authority.expires_at <= now:
                            continue
                        target.write(json.dumps(asdict(authority), allow_nan=False) + "\n")
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
