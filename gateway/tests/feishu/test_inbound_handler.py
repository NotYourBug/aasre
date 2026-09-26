"""Feishu turn pipeline: rotation, credit metering, timeout, and /stop cancel.

These pin the dispatcher/arbiter wiring that Telegram already covers — a
``/new`` rotates without running the agent, a credit denial never reaches the
agent, a soft timeout finalizes the output, and a ``/stop`` is honoured via a
cancel Event that was registered at dispatch time (R19).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from config.constants.gateway import (
    CREDITS_DENIED_MESSAGE,
    ROTATE_SESSION,
    TURN_TIMEOUT_MESSAGE,
    USER_STOP_MESSAGE,
)
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from gateway.core.attachments.fetch import DownloadedAttachment
from gateway.core.billing import turn_metering
from gateway.core.billing.credits_client import CreditsOutcome
from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.middleware.conversation_locks import ConversationLockRegistry
from gateway.tests.billing.turn_metering_harness import metered_callback
from gateway.transports.feishu import inbound_handler
from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.inbound_handler import _run_turn
from gateway.transports.feishu.inbound_security import FeishuInboundDecision
from gateway.transports.feishu.pending_approvals import PendingApprovals
from gateway.transports.feishu.session_rotation import conversation_key
from gateway.transports.feishu.settings import FeishuGatewaySettings
from integrations.feishu import ResourceRef
from integrations.messaging_security import MessagingIdentityPolicy

TEST_ORG_ID = "org_feishu_turn"
LOGGER = logging.getLogger("gateway.test")


def _settings(**kwargs: Any) -> FeishuGatewaySettings:
    defaults: dict[str, Any] = {
        "app_id": "app",
        "app_secret": "secret",
        "allowed_open_ids": ["ou_user-1"],
    }
    defaults.update(kwargs)
    return FeishuGatewaySettings(**defaults)


def _inbound(
    text: str = "hello",
    *,
    chat_id: str = "oc_chat-1",
    open_id: str = "ou_user-1",
    message_id: str = "m1",
    root_id: str = "",
) -> FeishuInboundMessage:
    return FeishuInboundMessage(
        chat_id=chat_id,
        open_id=open_id,
        message_id=message_id,
        text=text,
        root_id=root_id,
    )


class _FakeSessionResolver:
    def __init__(self, session: SessionCore) -> None:
        self._session = session
        self.rotated = False
        self.rotation_count = 0
        self.resolved = False

    def resolve(self, **_kwargs: object) -> SessionCore:
        self.resolved = True
        return self._session

    def rotate(self, **_kwargs: object) -> SessionCore:
        self.rotated = True
        self.rotation_count += 1
        return self._session


@pytest.fixture(autouse=True)
def _authorized_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORGANIZATION_ID", TEST_ORG_ID)
    monkeypatch.setattr(
        inbound_handler,
        "enforce_inbound_feishu_message_security",
        lambda **_kwargs: FeishuInboundDecision(allowed=True),
    )

    class _UnavailableCardClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def create_card(self, _spec: object) -> str:
            raise RuntimeError("card unavailable")

    monkeypatch.setattr(
        "gateway.transports.feishu.turn_output.FeishuCardClient", _UnavailableCardClient
    )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    inbound: FeishuInboundMessage,
    handler: Any,
    resolver: _FakeSessionResolver,
    settings: FeishuGatewaySettings,
    active_cancels: ActiveTurnRegistry,
    turn_cancel: threading.Event | None = None,
    downloader: Any = None,
    reactions: Any = None,
    feedback: Any = None,
    fail_send_text: bool = False,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Run one turn synchronously; returns (outbound sends, chat replies)."""
    outbound: list[tuple[str, str]] = []
    replies: list[tuple[str, str]] = []

    def fake_send(
        _app_id: str, _app_secret: str, chat_id: str, text: str, **_kwargs: object
    ) -> None:
        outbound.append((chat_id, text))

    def send_text(chat_id: str, text: str) -> str:
        if fail_send_text:
            raise RuntimeError("notice send failed")
        replies.append((chat_id, text))
        return "message-id"

    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", fake_send)

    _run_turn(
        inbound,
        settings=settings,
        session_resolver=resolver,  # type: ignore[arg-type]
        active_cancels=active_cancels,
        conversation_locks=ConversationLockRegistry(),
        approvals=ApprovalBroker(),
        pending_approvals=PendingApprovals(),
        send_text=send_text,
        handler=handler,
        logger=LOGGER,
        turn_cancel=turn_cancel,
        downloader=downloader,
        reactions=reactions,
        feedback=feedback,
    )
    return outbound, replies


def test_processing_admission_and_feedback_require_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.transports.feishu.card_stream import FinalCardTarget
    from gateway.transports.feishu.reaction_lifecycle import AckAdmission, AckOutcome

    reactions, feedback, output = MagicMock(), MagicMock(), MagicMock()
    reactions.reserve.return_value = AckAdmission.ADMITTED
    target = FinalCardTarget("card", "final", 4)
    output.take_feedback_target.return_value = target
    monkeypatch.setattr(inbound_handler, "FeishuTurnOutput", lambda **_kwargs: output)
    handler = MagicMock()
    arguments = {
        "inbound": _inbound(root_id="root"),
        "handler": handler,
        "resolver": _FakeSessionResolver(SessionCore(store=InMemorySessionStore())),
        "settings": _settings(),
        "active_cancels": ActiveTurnRegistry(),
        "reactions": reactions,
        "feedback": feedback,
    }
    _run(monkeypatch, **arguments)
    reactions.reserve.assert_called_once_with("m1")
    reactions.start_marker.assert_called_once_with("m1")
    reactions.finish.assert_called_once_with("m1", AckOutcome.SUCCESS)
    feedback.issue.assert_called_once_with(
        target, requester_open_id="ou_user-1", chat_id="oc_chat-1"
    )
    reactions.reserve.return_value = AckAdmission.DUPLICATE
    _run(monkeypatch, **arguments)
    assert handler.call_count == 1
    assert feedback.issue.call_count == 1


def test_error_cleans_ack_without_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.transports.feishu.reaction_lifecycle import AckAdmission, AckOutcome

    reactions, feedback = MagicMock(), MagicMock()
    reactions.reserve.return_value = AckAdmission.ADMITTED
    with pytest.raises(RuntimeError):
        _run(
            monkeypatch,
            inbound=_inbound(),
            handler=MagicMock(side_effect=RuntimeError("failure")),
            resolver=_FakeSessionResolver(SessionCore(store=InMemorySessionStore())),
            settings=_settings(),
            active_cancels=ActiveTurnRegistry(),
            reactions=reactions,
            feedback=feedback,
        )
    reactions.finish.assert_called_once_with("m1", AckOutcome.FAILURE)
    feedback.issue.assert_not_called()


def test_output_setup_failure_releases_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.transports.feishu.reaction_lifecycle import AckAdmission

    reactions = MagicMock()
    reactions.reserve.return_value = AckAdmission.ADMITTED

    def fail_output(**_kwargs: object) -> None:
        raise RuntimeError("output setup failed")

    monkeypatch.setattr(inbound_handler, "FeishuTurnOutput", fail_output)
    with pytest.raises(RuntimeError, match="output setup failed"):
        _run(
            monkeypatch,
            inbound=_inbound(),
            handler=MagicMock(),
            resolver=_FakeSessionResolver(SessionCore(store=InMemorySessionStore())),
            settings=_settings(),
            active_cancels=ActiveTurnRegistry(),
            reactions=reactions,
        )
    reactions.abort.assert_called_once_with("m1")
    reactions.start_marker.assert_not_called()


def test_cancel_registration_failure_releases_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.transports.feishu.reaction_lifecycle import AckAdmission

    reactions = MagicMock()
    reactions.reserve.return_value = AckAdmission.ADMITTED
    cancels = MagicMock(spec=ActiveTurnRegistry)
    cancels.track.side_effect = RuntimeError("temporary registration failure")
    with pytest.raises(RuntimeError, match="temporary registration failure"):
        _run(
            monkeypatch,
            inbound=_inbound(),
            handler=MagicMock(),
            resolver=_FakeSessionResolver(SessionCore(store=InMemorySessionStore())),
            settings=_settings(),
            active_cancels=cancels,
            reactions=reactions,
        )
    reactions.abort.assert_called_once_with("m1")
    reactions.start_marker.assert_not_called()


def test_new_rotation_short_circuits_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        inbound_handler,
        "enforce_inbound_feishu_message_security",
        lambda **_kwargs: FeishuInboundDecision(allowed=True, reply_text=ROTATE_SESSION),
    )
    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    callback = MagicMock()

    _outbound, _replies = _run(
        monkeypatch,
        inbound=_inbound("/new"),
        handler=callback,
        resolver=resolver,
        settings=_settings(),
        active_cancels=ActiveTurnRegistry(),
    )

    assert resolver.rotated is True
    assert resolver.resolved is False
    callback.assert_not_called()


def test_replayed_new_message_cannot_rotate_session_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.transports.feishu.reaction_lifecycle import AckAdmission, AckOutcome

    monkeypatch.setattr(
        inbound_handler,
        "enforce_inbound_feishu_message_security",
        lambda **_kwargs: FeishuInboundDecision(allowed=True, reply_text=ROTATE_SESSION),
    )
    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    reactions = MagicMock()
    reactions.reserve.side_effect = [AckAdmission.ADMITTED, AckAdmission.DUPLICATE]
    arguments = {
        "inbound": _inbound("/new"),
        "handler": MagicMock(),
        "resolver": resolver,
        "settings": _settings(),
        "active_cancels": ActiveTurnRegistry(),
        "reactions": reactions,
    }
    _run(monkeypatch, **arguments)
    _run(monkeypatch, **arguments)
    assert resolver.rotation_count == 1
    reactions.finish.assert_called_once_with("m1", AckOutcome.SUCCESS)


def test_session_setup_failure_releases_admission_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.transports.feishu.reaction_lifecycle import AckAdmission

    reactions = MagicMock()
    reactions.reserve.return_value = AckAdmission.ADMITTED
    session = SessionCore(store=InMemorySessionStore())
    resolve = MagicMock(side_effect=[RuntimeError("temporary setup failure"), session])
    monkeypatch.setattr(inbound_handler, "resolve_or_rotate_session", resolve)
    handler = MagicMock()
    arguments = {
        "inbound": _inbound(),
        "handler": handler,
        "resolver": _FakeSessionResolver(session),
        "settings": _settings(),
        "active_cancels": ActiveTurnRegistry(),
        "reactions": reactions,
    }
    with pytest.raises(RuntimeError, match="temporary setup failure"):
        _run(monkeypatch, **arguments)
    reactions.abort.assert_called_once_with("m1")
    reactions.start_marker.assert_not_called()
    _run(monkeypatch, **arguments)
    assert handler.call_count == 1


def test_new_message_is_deduped_without_processing_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.transports.feishu.reaction_lifecycle import AckAdmission, AckOutcome

    monkeypatch.setattr(
        inbound_handler,
        "enforce_inbound_feishu_message_security",
        lambda **_kwargs: FeishuInboundDecision(allowed=True, reply_text=ROTATE_SESSION),
    )
    reactions = MagicMock()
    reactions.reserve.return_value = AckAdmission.ADMITTED
    _run(
        monkeypatch,
        inbound=_inbound("/new"),
        handler=MagicMock(),
        resolver=_FakeSessionResolver(SessionCore(store=InMemorySessionStore())),
        settings=_settings(),
        active_cancels=ActiveTurnRegistry(),
        reactions=reactions,
    )
    reactions.reserve.assert_called_once_with("m1")
    reactions.start_marker.assert_not_called()
    reactions.finish.assert_called_once_with("m1", AckOutcome.SUCCESS)


def test_failed_new_session_notice_does_not_repeat_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.transports.feishu.reaction_lifecycle import AckAdmission, AckOutcome

    monkeypatch.setattr(
        inbound_handler,
        "enforce_inbound_feishu_message_security",
        lambda **_kwargs: FeishuInboundDecision(allowed=True, reply_text=ROTATE_SESSION),
    )
    reactions = MagicMock()
    reactions.reserve.side_effect = [AckAdmission.ADMITTED, AckAdmission.DUPLICATE]
    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    arguments = {
        "inbound": _inbound("/new"),
        "handler": MagicMock(),
        "resolver": resolver,
        "settings": _settings(),
        "active_cancels": ActiveTurnRegistry(),
        "reactions": reactions,
        "fail_send_text": True,
    }
    _run(monkeypatch, **arguments)
    _run(monkeypatch, **arguments)
    assert resolver.rotation_count == 1
    reactions.abort.assert_not_called()
    reactions.finish.assert_called_once_with("m1", AckOutcome.SUCCESS)


def test_pairing_reply_does_not_require_an_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity bootstrap must complete before an organization-scoped turn exists."""
    monkeypatch.delenv("ORGANIZATION_ID", raising=False)
    policy = MessagingIdentityPolicy(allowed_user_ids=["ou_user-1"])
    monkeypatch.setattr(
        inbound_handler,
        "enforce_inbound_feishu_message_security",
        lambda **_kwargs: FeishuInboundDecision(
            allowed=False,
            reply_text="Pairing successful!",
            persist_policy=True,
            updated_policy=policy,
        ),
    )
    persist = MagicMock()
    monkeypatch.setattr(inbound_handler, "persist_policy_if_needed", persist)
    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    callback = MagicMock()

    _outbound, replies = _run(
        monkeypatch,
        inbound=_inbound("/pair CODE"),
        handler=callback,
        resolver=resolver,
        settings=_settings(),
        active_cancels=ActiveTurnRegistry(),
    )

    assert replies == [("oc_chat-1", "Pairing successful!")]
    persist.assert_called_once_with(
        "feishu",
        FeishuInboundDecision(
            allowed=False,
            reply_text="Pairing successful!",
            persist_policy=True,
            updated_policy=policy,
        ),
    )
    assert resolver.resolved is False
    assert resolver.rotated is False
    callback.assert_not_called()


def test_denied_credits_stop_the_turn_before_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(_organization_id: str, *, reason: str, **_kwargs: object) -> CreditsOutcome:
        _ = reason
        return CreditsOutcome.DENIED

    monkeypatch.setattr(turn_metering, "consume_credits", deny)
    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    callback = MagicMock()

    outbound, _replies = _run(
        monkeypatch,
        inbound=_inbound("hello"),
        handler=metered_callback(callback),
        resolver=resolver,
        settings=_settings(),
        active_cancels=ActiveTurnRegistry(),
        turn_cancel=threading.Event(),
    )

    callback.assert_not_called()
    assert outbound[-1][1] == CREDITS_DENIED_MESSAGE


def test_turn_timeout_finalizes_output_and_sets_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    reactions, feedback = MagicMock(), MagicMock()
    seen_cancel: list[threading.Event] = []

    def hanging_handler(
        _text: str,
        _session: Any,
        sink: Any,
        _logger: logging.Logger,
    ) -> None:
        cancel = getattr(sink, "turn_cancel", None)
        assert isinstance(cancel, threading.Event)
        seen_cancel.append(cancel)
        release.wait(5.0)

    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    outbound: list[tuple[str, str]] = []

    def fake_send(
        _app_id: str, _app_secret: str, _chat_id: str, text: str, **_kwargs: object
    ) -> None:
        outbound.append((_chat_id, text))

    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", fake_send)

    error: list[Exception] = []
    done = threading.Event()

    def _thread() -> None:
        try:
            _run_turn(
                _inbound("hello"),
                settings=_settings(turn_timeout_seconds=0.05),
                session_resolver=resolver,  # type: ignore[arg-type]
                active_cancels=ActiveTurnRegistry(),
                conversation_locks=ConversationLockRegistry(),
                approvals=ApprovalBroker(),
                pending_approvals=PendingApprovals(),
                send_text=lambda _c, _t: "",
                handler=hanging_handler,
                logger=LOGGER,
                reactions=reactions,
                feedback=feedback,
            )
        except Exception as exc:  # pragma: no cover - failure path
            error.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_thread)
    worker.start()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not any(
        TURN_TIMEOUT_MESSAGE in text for _, text in outbound
    ):
        time.sleep(0.02)
    release.set()
    assert done.wait(5.0)
    worker.join(5.0)
    assert not error, error
    assert any(TURN_TIMEOUT_MESSAGE in text for _, text in outbound), outbound
    assert seen_cancel and seen_cancel[0].is_set()
    from gateway.transports.feishu.reaction_lifecycle import AckOutcome

    reactions.finish.assert_called_once_with("m1", AckOutcome.TIMEOUT)
    feedback.issue.assert_not_called()


def test_pre_registered_cancel_short_circuits_before_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /stop between dispatch and the turn body is honoured (R19)."""
    turn_cancel = threading.Event()
    registry = ActiveTurnRegistry()
    inbound = _inbound("hello")
    key = conversation_key(inbound)
    registry.register(key, turn_cancel)  # dispatch-time registration
    assert registry.request_stop(key) is True  # /stop arrives before the body

    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    callback = MagicMock()
    reactions = MagicMock()

    outbound, _replies = _run(
        monkeypatch,
        inbound=inbound,
        handler=callback,
        resolver=resolver,
        settings=_settings(),
        active_cancels=registry,
        turn_cancel=turn_cancel,
        reactions=reactions,
    )

    callback.assert_not_called()
    reactions.start_marker.assert_not_called()
    assert turn_cancel.is_set()
    assert outbound[-1][1] == USER_STOP_MESSAGE


def test_in_flight_stop_cancels_the_turn_via_pre_registered_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /stop during the agent cancels it through the dispatch-time Event (R19)."""
    turn_cancel = threading.Event()
    reactions, feedback = MagicMock(), MagicMock()
    registry = ActiveTurnRegistry()
    inbound = _inbound("hello")
    key = conversation_key(inbound)
    registry.register(key, turn_cancel)

    release = threading.Event()
    agent_started = threading.Event()
    callback = MagicMock()

    def cooperative_agent(
        _text: str,
        _session: Any,
        sink: Any,
        _logger: logging.Logger,
    ) -> None:
        agent_started.set()
        cancel = sink.turn_cancel
        while not cancel.is_set():
            if release.wait(0.05):
                break
        if not cancel.is_set():
            callback(_text, _session, sink, _logger)

    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))
    outbound: list[tuple[str, str]] = []

    def fake_send(
        _app_id: str, _app_secret: str, _chat_id: str, text: str, **_kwargs: object
    ) -> None:
        outbound.append((_chat_id, text))

    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", fake_send)

    error: list[Exception] = []
    done = threading.Event()

    def _thread() -> None:
        try:
            _run_turn(
                inbound,
                settings=_settings(),
                session_resolver=resolver,  # type: ignore[arg-type]
                active_cancels=registry,
                conversation_locks=ConversationLockRegistry(),
                approvals=ApprovalBroker(),
                pending_approvals=PendingApprovals(),
                send_text=lambda _c, _t: "",
                handler=cooperative_agent,
                logger=LOGGER,
                turn_cancel=turn_cancel,
                reactions=reactions,
                feedback=feedback,
            )
        except Exception as exc:  # pragma: no cover - failure path
            error.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_thread)
    worker.start()
    assert agent_started.wait(2.0)
    assert registry.request_stop(key) is True
    release.set()
    assert done.wait(5.0)
    worker.join(5.0)
    assert not error, error
    assert turn_cancel.is_set()
    callback.assert_not_called()
    assert any(USER_STOP_MESSAGE in text for _, text in outbound), outbound
    from gateway.transports.feishu.reaction_lifecycle import AckOutcome

    reactions.finish.assert_called_once_with("m1", AckOutcome.CANCELLED)
    feedback.issue.assert_not_called()


def test_run_turn_wires_approval_tool_hooks_into_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The output's tool_hooks is the result of approval_tool_hooks(prompter)."""
    captured: dict[str, object] = {}
    sentinel = object()
    card_client = object()

    def fake_card_client(app_id: str, app_secret: str) -> object:
        captured["card_credentials"] = (app_id, app_secret)
        return card_client

    def fake_hooks(prompter: object) -> object:
        captured["prompter"] = prompter
        return sentinel

    def fake_output(**kwargs: object) -> MagicMock:
        captured["tool_hooks"] = kwargs.get("tool_hooks")
        captured["reply_to_message_id"] = kwargs.get("reply_to_message_id")
        captured["reply_in_thread"] = kwargs.get("reply_in_thread")
        return MagicMock()

    monkeypatch.setattr(inbound_handler, "approval_tool_hooks", fake_hooks)
    monkeypatch.setattr(inbound_handler, "FeishuCardClient", fake_card_client)
    monkeypatch.setattr(inbound_handler, "FeishuTurnOutput", fake_output)
    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", lambda *_a, **_k: None)

    # A pre-set cancel short-circuits the turn right after the output is built,
    # so the assertion pins the wiring without needing the metering path.
    turn_cancel = threading.Event()
    turn_cancel.set()

    _run_turn(
        _inbound("hello", message_id="om_child", root_id="om_root"),
        settings=_settings(),
        session_resolver=_FakeSessionResolver(SessionCore(store=InMemorySessionStore())),  # type: ignore[arg-type]
        active_cancels=ActiveTurnRegistry(),
        conversation_locks=ConversationLockRegistry(),
        approvals=ApprovalBroker(),
        pending_approvals=PendingApprovals(),
        send_text=lambda _c, _t: "",
        handler=MagicMock(),
        logger=LOGGER,
        turn_cancel=turn_cancel,
    )

    assert "prompter" in captured
    prompter = captured["prompter"]
    assert isinstance(prompter, inbound_handler.FeishuApprovalPrompter)
    assert prompter._card_client is card_client
    assert captured["card_credentials"] == ("app", "secret")
    assert captured["tool_hooks"] is sentinel
    assert captured["reply_to_message_id"] == "om_root"
    assert captured["reply_in_thread"] is True


def test_an_uncaptioned_attachment_turn_hands_the_agent_the_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The S2 exit criterion, end to end: no text at all, only a file."""
    seen: list[str] = []

    def _download(_url: str, _max_bytes: int, _keep_partial: bool) -> DownloadedAttachment:
        return DownloadedAttachment(
            data=b"ERROR boom\n", content_type="text/plain", truncated=False
        )

    inbound = FeishuInboundMessage(
        chat_id="oc_chat-1",
        open_id="ou_user-1",
        message_id="om_1",
        text="",
        attachments=(ResourceRef(kind="file", key="file_1", name="app.log"),),
    )
    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))

    _run(
        monkeypatch,
        inbound=inbound,
        handler=lambda text, *_args: seen.append(text),
        resolver=resolver,
        settings=_settings(),
        active_cancels=ActiveTurnRegistry(),
        downloader=_download,
    )

    assert len(seen) == 1
    assert "ERROR boom" in seen[0]


def test_a_failed_attachment_download_still_runs_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A download failure is a line in the prompt, never an error reply."""
    seen: list[str] = []
    inbound = FeishuInboundMessage(
        chat_id="oc_chat-1",
        open_id="ou_user-1",
        message_id="om_1",
        text="see attached",
        attachments=(ResourceRef(kind="image", key="img_1"),),
    )
    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))

    outbound, _replies = _run(
        monkeypatch,
        inbound=inbound,
        handler=lambda text, *_args: seen.append(text),
        resolver=resolver,
        settings=_settings(),
        active_cancels=ActiveTurnRegistry(),
        downloader=lambda _url, _max_bytes, _keep_partial: None,
    )

    assert len(seen) == 1
    assert "could not be downloaded" in seen[0]
    assert all("error" not in text.lower() for _chat, text in outbound)


def test_a_captionless_attachment_never_reaches_the_agent_as_an_empty_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the attachment layer itself fails, the agent still gets a line to read."""
    seen: list[str] = []

    def _explode(_url: str, _max_bytes: int, _keep_partial: bool) -> DownloadedAttachment:
        raise RuntimeError("boom")

    inbound = FeishuInboundMessage(
        chat_id="oc_chat-1",
        open_id="ou_user-1",
        message_id="om_1",
        text="",
        attachments=(ResourceRef(kind="image", key="img_1"),),
    )
    resolver = _FakeSessionResolver(SessionCore(store=InMemorySessionStore()))

    _run(
        monkeypatch,
        inbound=inbound,
        handler=lambda text, *_args: seen.append(text),
        resolver=resolver,
        settings=_settings(),
        active_cancels=ActiveTurnRegistry(),
        downloader=_explode,
    )

    assert len(seen) == 1
    assert seen[0].strip() != ""
