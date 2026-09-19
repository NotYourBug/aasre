"""Whole-document Feishu delivery, fallback cursors, and safe outcomes."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from itertools import pairwise

import pytest

import integrations.feishu.document_delivery as delivery
from integrations.feishu.card_document import CardPage, paginate
from integrations.feishu.delivery_types import (
    FeishuCardCallError,
    FeishuCardCallStage,
    FeishuDeliveryErrorCategory,
    FeishuDeliveryMode,
    FeishuDeliveryStatus,
    FeishuMessageSendResult,
    FeishuSendCertainty,
)

_INPUT = {
    "app_id": "cli_test",
    "app_secret": "s_secret",
    "receive_id": "oc_complete_target",
    "receive_id_type": "chat_id",
}


def _sent(message_id: str) -> FeishuMessageSendResult:
    return FeishuMessageSendResult(
        accepted=True,
        message_id=message_id,
        error_category=None,
        certainty=FeishuSendCertainty.CONFIRMED_SENT,
    )


def _uncertain() -> FeishuMessageSendResult:
    return FeishuMessageSendResult(
        accepted=False,
        message_id="",
        error_category=FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN,
        certainty=FeishuSendCertainty.MAYBE_SENT,
    )


class _FakeCardClient:
    def __init__(
        self,
        *,
        create_outcomes: Iterable[str | Exception] = (),
        send_outcomes: Iterable[str | Exception] = (),
    ) -> None:
        self.create_outcomes = iter(create_outcomes)
        self.send_outcomes = iter(send_outcomes)
        self.created: list[dict[str, object]] = []
        self.sent: list[tuple[str, str, str]] = []

    def create_card(self, spec: dict[str, object]) -> str:
        self.created.append(spec)
        outcome = next(self.create_outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def send_card(self, receive_id: str, card_id: str, *, receive_id_type: str = "chat_id") -> str:
        self.sent.append((receive_id, card_id, receive_id_type))
        outcome = next(self.send_outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _install_card_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_outcomes: Iterable[str | Exception] = (),
    send_outcomes: Iterable[str | Exception] = (),
) -> _FakeCardClient:
    client = _FakeCardClient(
        create_outcomes=create_outcomes,
        send_outcomes=send_outcomes,
    )

    def _build_client(_app_id: str, _app_secret: str) -> _FakeCardClient:
        return client

    monkeypatch.setattr(delivery, "FeishuCardClient", _build_client)
    return client


def _install_text_sender(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: Iterable[FeishuMessageSendResult | Exception],
) -> list[str]:
    remaining = iter(outcomes)
    captured: list[str] = []

    def _post(
        _app_id: str,
        _app_secret: str,
        _receive_id: str,
        _receive_id_type: str,
        text: str,
    ) -> FeishuMessageSendResult:
        captured.append(text)
        outcome = next(remaining)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(delivery, "post_feishu_message", _post)
    return captured


def _install_pages(monkeypatch: pytest.MonkeyPatch, pages: list[CardPage]) -> None:
    def _paginate(_markdown: str) -> list[CardPage]:
        return pages

    monkeypatch.setattr(delivery, "paginate", _paginate)


def _deliver(markdown: str) -> delivery.FeishuDocumentDeliveryResult:
    return delivery.deliver_feishu_document(**_INPUT, markdown=markdown)


def test_blank_body_is_skipped_without_constructing_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[tuple[str, str]] = []

    def _build_client(app_id: str, app_secret: str) -> _FakeCardClient:
        built.append((app_id, app_secret))
        return _FakeCardClient()

    monkeypatch.setattr(delivery, "FeishuCardClient", _build_client)

    result = _deliver(" \n ")

    assert result.status is FeishuDeliveryStatus.SKIPPED
    assert result.attempted is False
    assert result.delivery_mode is FeishuDeliveryMode.NONE
    assert result.error_category is FeishuDeliveryErrorCategory.VALIDATION
    assert built == []


def test_missing_configuration_is_skipped_before_any_sdk_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[tuple[str, str]] = []

    def _build_client(app_id: str, app_secret: str) -> _FakeCardClient:
        built.append((app_id, app_secret))
        return _FakeCardClient()

    monkeypatch.setattr(delivery, "FeishuCardClient", _build_client)

    result = delivery.deliver_feishu_document(
        app_id="cli",
        app_secret="secret",
        receive_id=" ",
        receive_id_type="chat_id",
        markdown="body",
    )

    assert result.status is FeishuDeliveryStatus.SKIPPED
    assert result.attempted is False
    assert result.error_category is FeishuDeliveryErrorCategory.CONFIGURATION
    assert built == []


def test_all_cards_confirmed_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    markdown = "first\n\nsecond"
    _install_pages(
        monkeypatch,
        [
            CardPage("first", 1, 0, 5),
            CardPage("\n\nsecond", 2, 5, len(markdown)),
        ],
    )
    _install_card_client(
        monkeypatch,
        create_outcomes=["c_1", "c_2"],
        send_outcomes=["om_1", "om_2"],
    )

    result = _deliver(markdown)

    assert result.status is FeishuDeliveryStatus.SUCCESS
    assert result.successful is True
    assert result.delivery_mode is FeishuDeliveryMode.CARDS
    assert result.confirmed_message_ids == ("om_1", "om_2")
    assert result.first_message_id == "om_1"
    assert result.error_category is None
    assert result.error == ""


def test_first_card_success_then_fallback_failure_is_whole_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown = "x" * 12_500
    _install_pages(
        monkeypatch,
        [
            CardPage("page one", 1, 0, 10),
            CardPage("page two", 2, 10, len(markdown)),
        ],
    )
    _install_card_client(
        monkeypatch,
        create_outcomes=["c_1", "c_2"],
        send_outcomes=[
            "om_card_1",
            FeishuCardCallError(code=230020, stage=FeishuCardCallStage.SEND_CARD),
        ],
    )
    _install_text_sender(
        monkeypatch,
        [_sent("om_text_1"), _sent("om_text_2"), _uncertain()],
    )

    result = _deliver(markdown)

    assert result.status is FeishuDeliveryStatus.FAILED
    assert result.attempted is True
    assert result.successful is False
    assert result.delivery_mode is FeishuDeliveryMode.TEXT_FALLBACK
    assert result.confirmed_message_ids == ("om_card_1", "om_text_1", "om_text_2")
    assert result.first_message_id == "om_card_1"
    assert result.error_category is FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN


def test_pagination_failure_falls_back_from_source_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown = "  complete markdown  "

    def _fail_pagination(_markdown: str) -> list[CardPage]:
        raise ValueError("body and secret must never be logged")

    monkeypatch.setattr(delivery, "paginate", _fail_pagination)
    captured = _install_text_sender(monkeypatch, [_sent("om_text")])

    result = _deliver(markdown)

    assert result.status is FeishuDeliveryStatus.DEGRADED_SUCCESS
    assert result.error_category is FeishuDeliveryErrorCategory.INTERNAL
    assert captured == [markdown]


@pytest.mark.parametrize(
    "markdown",
    [
        "x" * 4_096,
        "x" * 4_097,
        "x" * 9_000,
        "  " + "x" * 4_093 + "🙂" + "尾部  ",
    ],
    ids=["4096", "4097", "9000-single-line", "emoji-and-whitespace"],
)
def test_text_chunks_preserve_codepoints_and_source_ranges(markdown: str) -> None:
    chunks = delivery._text_chunks(markdown, 0)

    assert all(len(chunk.text) <= 4_096 for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks) == markdown
    assert chunks[0].source_start == 0
    assert chunks[-1].source_end == len(markdown)
    assert all(left.source_end == right.source_start for left, right in pairwise(chunks))


def test_structured_page_failure_falls_back_from_original_source_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = "```python\n" + "\n".join(f"print({i})" for i in range(600)) + "\n```"
    table = "\n".join(
        ["| key | value |", "| --- | --- |", *(f"| {i} | 值🙂 |" for i in range(400))]
    )
    markdown = code + "\n\n" + table
    pages = paginate(markdown, budget=1_200)

    def _paginate(_markdown: str) -> list[CardPage]:
        return pages

    monkeypatch.setattr(delivery, "paginate", _paginate)
    _install_card_client(
        monkeypatch,
        create_outcomes=["c_1", "c_2"],
        send_outcomes=["om_1", RuntimeError("send failed")],
    )
    remainder = markdown[pages[1].source_start :]
    chunk_count = (len(remainder) + 4_095) // 4_096
    captured = _install_text_sender(
        monkeypatch,
        [_sent(f"om_text_{index}") for index in range(chunk_count)],
    )

    result = _deliver(markdown)

    assert result.status is FeishuDeliveryStatus.DEGRADED_SUCCESS
    assert "".join(captured) == remainder
    assert captured[0] == remainder[:4_096]
    assert pages[1].text.splitlines()[0] not in captured[0].splitlines()[:1]


@pytest.mark.parametrize(
    ("create_outcome", "send_outcome", "expected_category"),
    [
        ("", "unused", FeishuDeliveryErrorCategory.DEFINITE_REJECTION),
        ("c_1", "", FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN),
    ],
)
def test_card_success_without_required_id_does_not_advance(
    monkeypatch: pytest.MonkeyPatch,
    create_outcome: str,
    send_outcome: str,
    expected_category: FeishuDeliveryErrorCategory,
) -> None:
    markdown = "body"
    client = _install_card_client(
        monkeypatch,
        create_outcomes=[create_outcome],
        send_outcomes=[send_outcome] if create_outcome else [],
    )
    captured = _install_text_sender(monkeypatch, [_sent("om_text")])

    result = _deliver(markdown)

    assert result.status is FeishuDeliveryStatus.DEGRADED_SUCCESS
    assert result.error_category is expected_category
    assert result.confirmed_message_ids == ("om_text",)
    assert captured == [markdown]
    assert len(client.sent) == (1 if create_outcome else 0)


def test_text_success_without_message_id_is_whole_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown = "body"
    _install_card_client(
        monkeypatch,
        create_outcomes=[FeishuCardCallError(code=200860, stage=FeishuCardCallStage.CREATE_CARD)],
    )
    _install_text_sender(
        monkeypatch,
        [
            FeishuMessageSendResult(
                accepted=True,
                message_id="",
                error_category=None,
                certainty=FeishuSendCertainty.CONFIRMED_SENT,
            )
        ],
    )

    result = _deliver(markdown)

    assert result.status is FeishuDeliveryStatus.FAILED
    assert result.confirmed_message_ids == ()
    assert result.error_category is FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN


def test_malicious_visible_send_exception_never_reaches_result_or_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    markdown = "# private body"
    card_id = "c_private"
    message_id = "om_private"
    authorization = "Authorization: Bearer private-token"
    malicious = RuntimeError(
        " ".join(
            (
                _INPUT["app_secret"],
                markdown,
                _INPUT["receive_id"],
                card_id,
                message_id,
                authorization,
            )
        )
    )
    _install_card_client(
        monkeypatch,
        create_outcomes=[card_id],
        send_outcomes=[malicious],
    )
    _install_text_sender(monkeypatch, [malicious])
    caplog.set_level(logging.INFO, logger=delivery.__name__)

    result = _deliver(markdown)

    assert result.status is FeishuDeliveryStatus.FAILED
    assert result.error_category is FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN
    exposed = repr(result) + caplog.text
    for value in (
        _INPUT["app_secret"],
        markdown,
        _INPUT["receive_id"],
        card_id,
        message_id,
        authorization,
    ):
        assert value not in exposed

    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("Feishu document delivery summary") == 1
    assert messages.count("Feishu document delivery degraded to text") == 1
    assert messages.count("Feishu document delivery failed") == 1
    assert all(
        getattr(record, field) >= 1
        for record in caplog.records
        for field in ("card_page_index", "fallback_chunk_index")
        if hasattr(record, field)
    )
    summary = next(
        record
        for record in caplog.records
        if record.getMessage() == "Feishu document delivery summary"
    )
    assert summary.confirmed_message_count == 0
