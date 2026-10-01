"""Pin bounded work, authority-before-body and safe whole-call failures."""

from collections.abc import Callable
from http import HTTPStatus
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from lark_oapi.api.im.v1 import ListMessageRequest, ListMessageResponse
from lark_oapi.api.im.v1.resource.message import Message
from lark_oapi.core.model import RawResponse

from integrations.feishu.message_search import search_current_messages
from integrations.feishu.read_types import FeishuReadScope
from integrations.feishu.search_types import (
    FeishuSearchError,
    FeishuSearchErrorCode,
    FeishuSearchInput,
    FeishuSearchStopReason,
)
from tests.integrations.feishu_search_support import (
    install_search_transport,
    search_item,
    search_page,
)


def _not_cancelled() -> bool:
    return False


def _search(
    *,
    query: str = "",
    limit: int = 10,
    cancel: Callable[[], bool] = _not_cancelled,
    **overrides: Any,
) -> Any:
    arguments: dict[str, Any] = {
        "app_id": "cli_test",
        "app_secret": "fake-secret",
        "scope": FeishuReadScope("cli_test", "oc_current"),
        "inputs": FeishuSearchInput(query, 1000, 2000, limit),
        "cancel_requested": cancel,
    }
    arguments.update(overrides)
    return search_current_messages(**arguments)


@pytest.mark.parametrize("last_page_has_more", [True, False])
def test_history_scan_freezes_request_and_stops_after_three_pages(
    monkeypatch: pytest.MonkeyPatch,
    last_page_has_more: bool,
) -> None:
    view = {"chat": "oc_current", "start": 1000}
    calls = 0

    def respond(request: ListMessageRequest) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        view.update(chat="oc_foreign", start=1999)
        assert request.container_id == "oc_current" and request.container_id_type == "chat"
        assert (request.start_time, request.end_time, request.sort_type, request.page_size) == (
            "1000",
            "2000",
            "ByCreateTimeDesc",
            50,
        )
        assert request.card_msg_content_type == "user_card_content"
        assert request.page_token == (None if calls == 1 else f"page-{calls - 1}")
        more = calls < 3 or last_page_has_more
        return search_page(
            [search_item(f"om_{calls}_{n}") for n in range(50)],
            has_more=more,
            page_token=f"page-{calls}" if more else None,
        )

    probe = install_search_transport(monkeypatch, respond)
    result = _search(query="故障", limit=2)
    assert (
        result.pages_fetched,
        result.scanned_count,
        result.matched_count,
        len(result.items),
    ) == (3, 150, 150, 2)
    assert result.stop_reason.value == ("page_limit" if last_page_has_more else "source_exhausted")
    assert result.scan_complete is not last_page_has_more
    assert probe.timeouts == [10.0] * 3 and probe.token_timeouts == [10.0] * 3
    assert probe.app_ids == ["cli_test"] * 3
    with pytest.raises(FeishuSearchError) as caught:
        _search(app_id="cli_changed")
    assert caught.value.code == FeishuSearchErrorCode.AUTHORIZATION and len(probe.requests) == 3


def test_whole_page_identity_gate_precedes_every_body_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched: list[str] = []

    class GuardedItem:
        message_id = "om_first"
        chat_id = "oc_current"
        create_time = "1500000"
        deleted = False
        msg_type = "text"

        @property
        def body(self) -> Any:
            touched.append(self.message_id)
            raise AssertionError("FOREIGN-BODY-CANARY")

    foreign = GuardedItem()
    foreign.message_id, foreign.chat_id = "om_foreign", "oc_foreign"

    def respond(_self: Message, _request: ListMessageRequest) -> ListMessageResponse:
        response = ListMessageResponse({"code": 0})
        response.data = SimpleNamespace(
            items=[GuardedItem(), foreign], has_more=False, page_token=None
        )
        return response

    monkeypatch.setattr(Message, "list", respond)
    with pytest.raises(FeishuSearchError) as caught:
        _search()
    assert caught.value.code == FeishuSearchErrorCode.HISTORY_UNAVAILABLE
    assert touched == []
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize(
    "failure,code",
    [
        ({"code": 230027, "msg": "UPSTREAM-CANARY"}, "history_unavailable"),
        (search_page([], has_more=True, page_token="page-1"), "upstream_error"),
        (search_page([], has_more=True), "upstream_error"),
    ],
)
def test_pagination_failure_discards_earlier_page_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: dict[str, Any],
    code: str,
) -> None:
    calls = 0

    def respond(_request: ListMessageRequest) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return (
            search_page([search_item()], has_more=True, page_token="page-1")
            if calls == 1
            else failure
        )

    probe = install_search_transport(monkeypatch, respond)
    with pytest.raises(FeishuSearchError) as caught:
        _search()
    assert caught.value.code.value == code and len(probe.requests) == 2
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "UPSTREAM-CANARY" not in str(caught.value) + caplog.text


def test_discovery_counts_deleted_duplicate_unsupported_and_empty_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        search_page([], has_more=True, page_token="next"),
        search_page(
            [
                search_item("om_deleted", deleted=True, body={"content": "BODY-CANARY"}),
                search_item("om_live"),
                search_item("om_live", body={"content": "BODY-CANARY"}),
                search_item("om_image", msg_type="image", body={"content": "BODY-CANARY"}),
                search_item(
                    "om_unknown", msg_type="unknown-canary", body={"content": "BODY-CANARY"}
                ),
                search_item("om_invalid", body={"content": '{"text":"BODY-CANARY",'}),
            ]
        ),
    ]

    def respond(_request: ListMessageRequest) -> dict[str, Any]:
        return pages.pop(0)

    install_search_transport(monkeypatch, respond)
    result = _search()
    assert (
        result.pages_fetched,
        result.scanned_count,
        result.matched_count,
        result.unsearchable_count,
    ) == (2, 6, 3, 3)
    assert result.scan_complete and result.stop_reason == FeishuSearchStopReason.SOURCE_EXHAUSTED
    assert [item.message_id for item in result.items] == ["om_live", "om_image", "om_invalid"]
    assert [item.preview_available for item in result.items] == [True, False, False]
    assert "BODY-CANARY" not in repr(result) and "unknown-canary" not in repr(result)


@pytest.mark.parametrize("extra,want_scanned,want_complete", [(False, 8, True), (True, 9, False)])
def test_budget_and_cancellation_do_not_release_partial_work(
    monkeypatch: pytest.MonkeyPatch, extra: bool, want_scanned: int, want_complete: bool
) -> None:
    # Eight complete JSON bodies of exactly 256 KiB UTF-8 consume exactly 2 MiB.
    body = '{"text":"' + "中" * 87377 + 'xx"}'
    assert len(body.encode("utf-8")) == 262144
    items = [search_item(f"om_{n}", body={"content": body}) for n in range(8 + int(extra))]

    def respond(_request: ListMessageRequest) -> dict[str, Any]:
        return search_page(items)

    install_search_transport(monkeypatch, respond)
    result = _search()
    assert result.scanned_count == want_scanned and result.matched_count == 8
    assert result.scan_complete is want_complete
    assert result.stop_reason.value == ("source_exhausted" if want_complete else "work_limit")


@pytest.mark.parametrize("cancel_at", [0, 1, 2, 3, 4])
def test_cancellation_discards_results_at_request_response_and_page_boundaries(
    monkeypatch: pytest.MonkeyPatch, cancel_at: int
) -> None:
    event = Event()
    checks = 0

    def cancel() -> bool:
        nonlocal checks
        if checks == cancel_at:
            event.set()
        checks += 1
        return event.is_set()

    def respond(_request: ListMessageRequest) -> dict[str, Any]:
        return search_page([search_item()], has_more=True, page_token="next")

    probe = install_search_transport(monkeypatch, respond)
    with pytest.raises(FeishuSearchError) as caught:
        _search(cancel=cancel)
    assert caught.value.code == FeishuSearchErrorCode.CANCELLED
    assert len(probe.requests) <= 1


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"code": 230020, "msg": "CANARY"}, "rate_limited"),
        ({"code": 230073}, "history_unavailable"),
        ({"code": 231203}, "history_unavailable"),
        ({"code": 231204}, "history_unavailable"),
        ({"code": 9, "msg": "CANARY"}, "upstream_error"),
        ({"code": 0}, "upstream_error"),
        ({"code": 0, "data": {"items": [], "has_more": 1}}, "upstream_error"),
        (search_page([search_item(str(n)) for n in range(51)]), "upstream_error"),
        (search_page([], page_token="unexpected"), "upstream_error"),
        (search_page([search_item(create_time="1999999999")]), "history_unavailable"),
        (search_page([search_item(deleted=0)]), "history_unavailable"),
    ],
)
def test_history_error_classification_is_safe(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    payload: dict[str, Any],
    code: str,
) -> None:
    def respond(_request: ListMessageRequest) -> dict[str, Any]:
        return payload

    probe = install_search_transport(monkeypatch, respond)
    with pytest.raises(FeishuSearchError) as caught:
        _search()
    assert caught.value.code.value == code and len(probe.requests) == 1
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "CANARY" not in str(caught.value) + caplog.text


def test_http_rate_limit_and_transport_error_never_release_provider_details(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def rate_limit(_self: Message, _request: ListMessageRequest) -> ListMessageResponse:
        response = ListMessageResponse({"code": 0})
        response.raw = RawResponse()
        response.raw.status_code = HTTPStatus.TOO_MANY_REQUESTS
        return response

    monkeypatch.setattr(Message, "list", rate_limit)
    with pytest.raises(FeishuSearchError) as caught:
        _search()
    assert caught.value.code == FeishuSearchErrorCode.RATE_LIMITED

    def transport_error(_self: Message, _request: ListMessageRequest) -> ListMessageResponse:
        raise RuntimeError("SECRET-TRANSPORT-CANARY")

    monkeypatch.setattr(Message, "list", transport_error)
    with pytest.raises(FeishuSearchError) as caught:
        _search()
    assert caught.value.code == FeishuSearchErrorCode.UPSTREAM_ERROR
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "SECRET-TRANSPORT-CANARY" not in str(caught.value) + caplog.text
