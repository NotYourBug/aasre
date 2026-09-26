"""Background Feishu gateway lifecycle handle."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from config.constants.feishu import (
    FEISHU_ACK_LEDGER_FILENAME,
    FEISHU_ACK_RECOVERY_RECORD_LIMIT,
    FEISHU_ACK_RECOVERY_SECONDS,
    FEISHU_FEEDBACK_AUTHORITY_FILENAME,
    FEISHU_FEEDBACK_FILENAME,
    FEISHU_INTERACTION_HTTP_TIMEOUT_SECONDS,
)
from config.constants.gateway import DEFAULT_STOP_TIMEOUT_SECONDS
from config.constants.paths import host_home
from gateway.core.storage.session.binding_store import BindingStore, open_binding_store
from gateway.transports.feishu.feedback import FeishuFeedbackService
from gateway.transports.feishu.feedback_authority import FeedbackAuthorityStore
from gateway.transports.feishu.inbound_security import is_feedback_actor_authorized
from gateway.transports.feishu.reaction_lifecycle import ReactionLifecycleManager
from gateway.transports.feishu.settings import FeishuGatewaySettings
from gateway.transports.feishu.turn_output import FeishuTurnOutputRegistry
from gateway.transports.feishu.worker import (
    _verify_feishu_credentials,
    run_feishu_gateway_thread,
)
from infrastructure.turn_host.turn_callback import TurnCallback
from integrations.feishu import FeishuReactionClient
from integrations.feishu.card_client import FeishuCardClient


class FeishuGatewayBackground:
    """Control handle for the background Feishu gateway worker."""

    def __init__(
        self,
        *,
        thread: threading.Thread,
        stop_event: threading.Event,
        ready_event: threading.Event,
        bindings: BindingStore,
        executor: ThreadPoolExecutor,
        output_registry: FeishuTurnOutputRegistry,
        reactions: ReactionLifecycleManager | None = None,
        feedback: FeishuFeedbackService | None = None,
    ) -> None:
        self._thread = thread
        self._stop_event = stop_event
        self._ready_event = ready_event
        self._bindings = bindings
        self._executor = executor
        self._output_registry = output_registry
        self._reactions = reactions
        self._feedback = feedback

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> bool:
        deadline = time.monotonic() + timeout
        self._stop_event.set()
        if self._reactions is not None:
            self._reactions.shutdown(timeout_seconds=0)
        if self._feedback is not None:
            self._feedback.shutdown(timeout_seconds=0)
        self._output_registry.shutdown()
        self._thread.join(timeout=max(0, deadline - time.monotonic()))
        if self._reactions is not None:
            self._reactions.shutdown(timeout_seconds=max(0, deadline - time.monotonic()))
        if self._feedback is not None:
            self._feedback.shutdown(timeout_seconds=max(0, deadline - time.monotonic()))
        self._executor.shutdown(wait=False, cancel_futures=False)
        try:
            self._bindings.close()
        except Exception:
            logging.getLogger(__name__).debug(
                "[feishu-gateway] binding store close failed", exc_info=True
            )
        return not self._thread.is_alive()

    def wait_until_ready(self, *, timeout: float) -> bool:
        return self._ready_event.wait(timeout)


def start_feishu_gateway_background(
    *,
    settings: FeishuGatewaySettings,
    logger: logging.Logger,
    handler: TurnCallback,
) -> FeishuGatewayBackground:
    """Connect to Feishu and dispatch inbound messages until stopped.

    Credentials are verified synchronously before the worker thread starts, so
    a bad ``FEISHU_APP_ID``/``FEISHU_APP_SECRET`` surfaces as
    ``ObtainAccessTokenException`` here rather than being swallowed by the
    daemon thread's error handler.
    """
    _verify_feishu_credentials(settings.app_id, settings.app_secret)
    bindings = open_binding_store()
    executor = ThreadPoolExecutor(
        max_workers=settings.max_concurrent_turns,
        thread_name_prefix="FeishuGatewayTurn",
    )
    stop_event = threading.Event()
    ready_event = threading.Event()
    output_registry = FeishuTurnOutputRegistry()
    gateway_home = host_home() / "gateway"
    reactions = ReactionLifecycleManager(
        path=gateway_home / FEISHU_ACK_LEDGER_FILENAME,
        client=FeishuReactionClient(settings.app_id, settings.app_secret),
        app_id=settings.app_id,
    )
    reactions.reconcile(
        budget_seconds=FEISHU_ACK_RECOVERY_SECONDS,
        record_limit=FEISHU_ACK_RECOVERY_RECORD_LIMIT,
    )
    authority = FeedbackAuthorityStore(gateway_home / FEISHU_FEEDBACK_AUTHORITY_FILENAME)
    try:
        authority.compact()
    except Exception:
        logger.warning("Feishu feedback authority recovery unavailable")

    def authorized(actor: str, chat: str) -> bool:
        return is_feedback_actor_authorized(
            open_id=actor,
            chat_id=chat,
            env_allowed_open_ids=settings.allowed_open_ids,
        )

    feedback = FeishuFeedbackService(
        authority=authority,
        feedback_path=gateway_home / FEISHU_FEEDBACK_FILENAME,
        card_client=FeishuCardClient(
            settings.app_id,
            settings.app_secret,
            timeout_seconds=FEISHU_INTERACTION_HTTP_TIMEOUT_SECONDS,
        ),
        authorized=authorized,
    )
    thread = threading.Thread(
        target=run_feishu_gateway_thread,
        kwargs={
            "settings": settings,
            "logger": logger,
            "handler": handler,
            "bindings": bindings,
            "executor": executor,
            "stop_event": stop_event,
            "ready_event": ready_event,
            "output_registry": output_registry,
            "reactions": reactions,
            "feedback": feedback,
        },
        name="FeishuGatewayThread",
        daemon=True,
    )
    thread.start()
    return FeishuGatewayBackground(
        thread=thread,
        stop_event=stop_event,
        ready_event=ready_event,
        bindings=bindings,
        executor=executor,
        output_registry=output_registry,
        reactions=reactions,
        feedback=feedback,
    )


__all__ = [
    "FeishuGatewayBackground",
    "start_feishu_gateway_background",
]
