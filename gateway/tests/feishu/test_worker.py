"""Feishu worker dispatch: cancel Event is registered at dispatch time (R19)."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.session_rotation import conversation_key
from gateway.transports.feishu.settings import FeishuGatewaySettings
from gateway.transports.feishu.worker import _dispatch_turn

LOGGER = logging.getLogger("gateway.test")


def test_dispatch_registers_cancel_before_handler_runs() -> None:
    """The cancel Event is published before the dispatched turn body runs."""
    registry = ActiveTurnRegistry()
    inbound = FeishuInboundMessage(
        chat_id="oc_chat-1",
        open_id="ou_user-1",
        message_id="m1",
        text="hello",
    )
    started = threading.Event()
    observed: list[bool] = []

    def spy(_inbound: FeishuInboundMessage, **_kwargs: object) -> None:
        # When the dispatched turn first runs, the cancel Event must already be
        # registered for this conversation key (dispatch-time registration).
        observed.append(registry.request_stop(conversation_key(_inbound)))
        started.set()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            with patch("gateway.transports.feishu.worker.handle_inbound_turn", spy):
                _dispatch_turn(
                    inbound,
                    settings=FeishuGatewaySettings(
                        app_id="app",
                        app_secret="secret",
                        allowed_open_ids=["ou_user-1"],
                    ),
                    session_resolver=MagicMock(),  # type: ignore[arg-type]
                    active_cancels=registry,
                    conversation_locks=ConversationLockRegistry(),
                    send_text=lambda _c, _t: None,
                    handler=lambda *_args: None,
                    logger=LOGGER,
                    executor=executor,
                    loop=loop,
                    turn_slots=threading.BoundedSemaphore(2),
                )
            await asyncio.to_thread(started.wait, 2.0)
            assert observed == [True]
        finally:
            executor.shutdown(wait=True)

    asyncio.run(_run())
