"""Thread-safe registry of Feishu approval prompts awaiting a text reply.

Written by :class:`gateway.transports.feishu.approvals.FeishuApprovalPrompter`
on the turn's executor thread; read by the WS event handler
(:mod:`gateway.transports.feishu.worker`) on the asyncio loop thread — the lock
is what makes that safe, mirroring
:class:`gateway.core.middleware.approvals.ApprovalBroker`'s own locking.

A Feishu reply carries one ``parent_id`` pointing at the prompt message it
answers, so each entry is keyed by that prompt ``message_id`` rather than a set
of reply ids. Each entry also carries the *request's* own authority, not just
its approval id: Feishu group chats are multi-member, and a reply only has to
quote the prompt's message id to reach the handler — so matching on the prompt
alone would let any other participant approve or deny somebody else's protected
write. The requester's ``open_id`` and the ``chat_id`` the prompt was posted in
are therefore part of the match, on top of the live identity check the handler
still runs.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingApproval:
    """One posted approval prompt and the authority allowed to answer it."""

    approval_id: str
    requester_open_id: str
    chat_id: str


class PendingApprovals:
    """Maps a posted approval-prompt ``message_id`` -> its :class:`PendingApproval`."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def register(
        self,
        prompt_message_id: str,
        *,
        approval_id: str,
        requester_open_id: str,
        chat_id: str,
    ) -> None:
        """Record a posted prompt so a reply to it can resolve the approval."""
        with self._lock:
            self._pending[prompt_message_id] = PendingApproval(
                approval_id=approval_id,
                requester_open_id=requester_open_id,
                chat_id=chat_id,
            )

    def discard(self, prompt_message_id: str) -> None:
        """Forget a prompt once its wait has returned (decided or expired)."""
        with self._lock:
            self._pending.pop(prompt_message_id, None)

    def find(self, prompt_message_id: str) -> PendingApproval | None:
        """Return the prompt *prompt_message_id* matches, without consuming it.

        Lets the caller tell "this reply is aimed at a live prompt" from "this
        is an ordinary message" before deciding anything.
        """
        with self._lock:
            return self._pending.get(prompt_message_id)

    def claim(self, prompt_message_id: str, *, open_id: str, chat_id: str) -> str | None:
        """Consume and return the approval id *open_id* may resolve, if any.

        The entry stays pending unless the responder is the member whose turn
        raised the request, replying in the chat the prompt was posted to.
        Leaving it pending is deliberate: a rejected attempt must not burn the
        slot the rightful responder still needs.
        """
        with self._lock:
            pending = self._pending.get(prompt_message_id)
            if pending is None:
                return None
            if pending.requester_open_id != open_id or pending.chat_id != chat_id:
                return None
            del self._pending[prompt_message_id]
            return pending.approval_id

    def drain(self) -> list[str]:
        """Clear and return every outstanding approval id.

        Used at shutdown to release turns blocked in ``ApprovalBroker.wait``
        instead of leaving them to burn the full approval timeout.
        """
        with self._lock:
            approval_ids = [pending.approval_id for pending in self._pending.values()]
            self._pending.clear()
        return approval_ids


__all__ = ["PendingApproval", "PendingApprovals"]
