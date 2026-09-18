"""Tests for the Feishu turn output."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any

import pytest

from gateway.transports.feishu import turn_output
from gateway.transports.feishu.card_stream import CardStreamSession
from gateway.transports.feishu.turn_output import (
    FeishuTurnOutput,
    FeishuTurnOutputRegistry,
    _send_text,
    _split_text,
)


class _FakeStreamSession:
    """Record the output-to-S3 lifecycle without duplicating S3 behavior."""

    instances: list[_FakeStreamSession] = []
    fail_start = False

    def __init__(self, *, client: Any, chat_id: str, **_kwargs: Any) -> None:
        self.client = client
        self.chat_id = chat_id
        self.events: list[tuple[str, str]] = []
        self.finish_calls = 0
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.events.append(("start", ""))
        if self.fail_start:
            raise RuntimeError("card unavailable")

    def update(self, text: str) -> None:
        self.events.append(("update", text))

    def finish(self) -> None:
        self.finish_calls += 1
        self.events.append(("finish", ""))


@pytest.fixture
def fake_stream(monkeypatch: pytest.MonkeyPatch) -> type[_FakeStreamSession]:
    _FakeStreamSession.instances = []
    _FakeStreamSession.fail_start = False
    monkeypatch.setattr(turn_output, "FeishuCardClient", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(turn_output, "CardStreamSession", _FakeStreamSession)
    return _FakeStreamSession


def test_finalize_starts_updates_and_finishes_one_card_session(fake_stream) -> None:
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")
    out.finalize("hello world")

    assert len(fake_stream.instances) == 1
    assert fake_stream.instances[0].events == [
        ("start", ""),
        ("update", "hello world"),
        ("finish", ""),
    ]


def test_finalize_blank_answer_does_not_create_a_card(fake_stream) -> None:
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")
    out.finalize("")
    out.finalize("   ")
    assert fake_stream.instances == []


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        (["hello", " world"], "hello world"),
        (["hel", "hello", "hello world"], "hello world"),
        (["hel", "hello", "lo world"], "hello world"),
    ],
)
def test_stream_merges_delta_accumulated_and_mixed_chunks_without_duplication(
    fake_stream: type[_FakeStreamSession], chunks: list[str], expected: str
) -> None:
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")

    returned = out.stream(label="OpenSRE", chunks=iter(chunks))

    session = fake_stream.instances[0]
    assert returned == expected
    assert session.events[-2:] == [("update", expected), ("finish", "")]


def test_duplicate_completion_finishes_once(fake_stream: type[_FakeStreamSession]) -> None:
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")

    out.finalize("done")
    out.finish_streamed_response("done")

    assert fake_stream.instances[0].finish_calls == 1


def test_plain_text_fallback_chunks_the_complete_unicode_answer(
    monkeypatch: pytest.MonkeyPatch, fake_stream: type[_FakeStreamSession]
) -> None:
    fake_stream.fail_start = True
    sent: list[tuple[str, str, str, bool]] = []

    def _fake_send(
        _app_id: str,
        _app_secret: str,
        receive_id: str,
        text: str,
        *,
        reply_to_message_id: str = "",
        reply_in_thread: bool = False,
    ) -> str:
        sent.append((receive_id, text, reply_to_message_id, reply_in_thread))
        return "om_1"

    monkeypatch.setattr(turn_output, "_send_text", _fake_send)
    answer = "界" * 4097
    out = FeishuTurnOutput(
        app_id="a",
        app_secret="s",
        chat_id="oc_x",
        reply_to_message_id="om_root",
        reply_in_thread=True,
    )

    out.finalize(answer)

    assert [len(text) for _, text, _, _ in sent] == [4096, 1]
    assert "".join(text for _, text, _, _ in sent) == answer
    assert {(target, reply, in_thread) for target, _, reply, in_thread in sent} == {
        ("oc_x", "om_root", True)
    }


def test_plain_text_fallback_stops_after_a_failed_chunk_without_replaying_prefix(
    monkeypatch: pytest.MonkeyPatch, fake_stream: type[_FakeStreamSession]
) -> None:
    fake_stream.fail_start = True
    sent: list[str] = []

    def _fake_send(
        _app_id: str, _app_secret: str, _receive_id: str, text: str, **_kwargs: object
    ) -> str:
        sent.append(text)
        if len(sent) == 2:
            raise RuntimeError("send failed")
        return "om_1"

    monkeypatch.setattr(turn_output, "_send_text", _fake_send)
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")

    with pytest.raises(RuntimeError, match="send failed"):
        out.finalize("x" * 8193)
    out.finalize("x" * 8193)

    assert [len(part) for part in sent] == [4096, 4096]


def test_stream_accepts_an_arbitrary_iterable(fake_stream: type[_FakeStreamSession]) -> None:
    _ = fake_stream

    def _chunks() -> Iterable[str]:
        yield "a"
        yield "ab"

    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")
    assert out.stream(label="OpenSRE", chunks=_chunks()) == "ab"


def test_empty_stream_returns_empty_while_delivering_the_empty_response_copy(
    fake_stream: type[_FakeStreamSession],
) -> None:
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")

    assert out.stream(label="OpenSRE", chunks=[]) == ""
    assert fake_stream.instances[0].events[-1] == ("finish", "")


@pytest.mark.parametrize(
    ("text", "sizes"),
    [
        ("", []),
        ("x" * 4096, [4096]),
        ("界" * 4097, [4096, 1]),
        (("line\n" * 820), [4096, 4]),
    ],
    ids=["empty", "exact", "cjk", "newlines"],
)
def test_plain_text_chunk_boundaries_preserve_every_character(text: str, sizes: list[int]) -> None:
    chunks = _split_text(text)
    assert [len(chunk) for chunk in chunks] == sizes
    assert "".join(chunks) == text


def test_shutdown_and_completion_race_finishes_once(
    fake_stream: type[_FakeStreamSession],
) -> None:
    registry = FeishuTurnOutputRegistry()
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x", output_registry=registry)
    cancel = threading.Event()
    out.turn_cancel = cancel
    out.set_tool_status("working")
    barrier = threading.Barrier(3)

    def _shutdown() -> None:
        barrier.wait()
        registry.shutdown()

    def _finish() -> None:
        barrier.wait()
        out.finalize("done")

    threads = [threading.Thread(target=_shutdown), threading.Thread(target=_finish)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert fake_stream.instances[0].finish_calls == 1
    assert cancel.is_set() or fake_stream.instances[0].events[-2:] == [
        ("update", "done"),
        ("finish", ""),
    ]


def test_two_outputs_never_share_a_card_session(fake_stream: type[_FakeStreamSession]) -> None:
    first = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_1")
    second = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_2")

    first.finalize("one")
    second.finalize("two")

    assert len(fake_stream.instances) == 2
    assert {session.chat_id for session in fake_stream.instances} == {"oc_1", "oc_2"}


def test_registering_after_shutdown_cancels_before_the_turn_can_start(
    fake_stream: type[_FakeStreamSession],
) -> None:
    registry = FeishuTurnOutputRegistry()
    registry.shutdown()
    cancel = threading.Event()

    output = FeishuTurnOutput(
        app_id="a",
        app_secret="s",
        chat_id="oc_x",
        turn_cancel=cancel,
        output_registry=registry,
    )

    assert cancel.is_set()
    assert fake_stream.instances == []
    output.finalize("must not be delivered")
    assert fake_stream.instances == []


def test_product_output_uses_the_real_card_stream_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Client:
        def __init__(self) -> None:
            self.created: list[dict[str, object]] = []
            self.updated: list[str] = []
            self.closed = 0

        def create_card(self, spec: dict[str, object]) -> str:
            self.created.append(spec)
            return f"c_{len(self.created)}"

        def send_card(self, *_args: object, **_kwargs: object) -> str:
            return "om_1"

        def update_element(self, _card: str, _element: str, text: str, _sequence: int) -> None:
            self.updated.append(text)

        def close_streaming(self, _card: str, _sequence: int) -> None:
            self.closed += 1

    client = _Client()
    monkeypatch.setattr(turn_output, "FeishuCardClient", lambda *_args, **_kwargs: client)

    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")
    out.stream(label="OpenSRE", chunks=iter(["hello"]))

    assert isinstance(out._session, CardStreamSession)
    assert client.updated == ["hello"]
    assert client.closed == 1


def test_card_reference_send_failure_falls_back_once_without_updating_orphan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Client:
        def __init__(self) -> None:
            self.created = 0
            self.updated = 0

        def create_card(self, _spec: object) -> str:
            self.created += 1
            return "c_orphan"

        def send_card(self, *_args: object, **_kwargs: object) -> str:
            raise RuntimeError("reference failed")

        def update_element(self, *_args: object, **_kwargs: object) -> None:
            self.updated += 1

    client = _Client()
    sent: list[str] = []
    monkeypatch.setattr(turn_output, "FeishuCardClient", lambda *_args, **_kwargs: client)

    def _fake_send(
        _app_id: str, _app_secret: str, _receive_id: str, text: str, **_kwargs: object
    ) -> str:
        sent.append(text)
        return "om_fallback"

    monkeypatch.setattr(turn_output, "_send_text", _fake_send)
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")

    out.finalize("complete answer")
    out.finalize("complete answer")

    assert client.created == 1
    assert client.updated == 0
    assert sent == ["complete answer"]


def _stub_lark(monkeypatch: pytest.MonkeyPatch, *, create_impl: Any) -> None:
    """Replace the lark SDK with a stub whose ``message.create`` is *create_impl*."""

    class _Message:
        def create(self, request: Any) -> Any:
            return create_impl(request)

        def reply(self, request: Any) -> Any:
            return create_impl(request)

    class _V1:
        def __init__(self) -> None:
            self.message = _Message()

    class _Im:
        def __init__(self) -> None:
            self.v1 = _V1()

    class _Client:
        def __init__(self) -> None:
            self.im = _Im()

        @staticmethod
        def builder() -> Any:
            return _Builder()

    class _Builder:
        def app_id(self, _value: str) -> _Builder:
            return self

        def app_secret(self, _value: str) -> _Builder:
            return self

        def build(self) -> _Client:
            return _Client()

    class _Lark:
        Client = _Client

    monkeypatch.setattr("gateway.transports.feishu.turn_output.lark", _Lark)


def _response(*, ok: bool, msg: str = "success", message_id: str = "om_1") -> Any:
    data = type("D", (), {"message_id": message_id})()
    return type(
        "R", (), {"success": lambda _self: ok, "code": 0 if ok else 999, "msg": msg, "data": data}
    )()


def test_send_text_returns_message_id_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_lark(monkeypatch, create_impl=lambda _req: _response(ok=True))

    message_id = _send_text("cli_x", "s_secret_123", "oc_chat", "hello")

    assert message_id == "om_1"


def test_send_text_raises_on_business_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_lark(monkeypatch, create_impl=lambda _req: _response(ok=False, msg="bot not in chat"))

    with pytest.raises(RuntimeError, match="bot not in chat"):
        _send_text("cli_x", "s_secret_123", "oc_chat", "hello")


def test_send_text_uses_reply_api_for_an_inbound_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []

    def _create(request: Any) -> Any:
        captured.append(request)
        return _response(ok=True)

    _stub_lark(monkeypatch, create_impl=_create)
    _send_text(
        "cli_x",
        "s_secret_123",
        "oc_chat",
        "hello",
        reply_to_message_id="om_root",
        reply_in_thread=True,
    )

    assert captured[0].message_id == "om_root"
    assert captured[0].request_body.reply_in_thread is True
