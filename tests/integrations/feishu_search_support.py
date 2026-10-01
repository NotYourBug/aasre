"""Offline real-SDK transport for bounded Feishu history tests."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

import pytest
from lark_oapi.api.im.v1 import ListMessageRequest
from lark_oapi.core.enum import AccessTokenType
from lark_oapi.core.http import Transport
from lark_oapi.core.model import BaseRequest, Config, RawResponse, RequestOption
from lark_oapi.core.token.manager import TokenManager


@dataclass
class SearchTransportProbe:
    requests: list[ListMessageRequest] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)
    app_ids: list[str] = field(default_factory=list)
    token_timeouts: list[float] = field(default_factory=list)


def search_item(message_id: str = "om_known", **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "message_id": message_id,
        "chat_id": "oc_current",
        "deleted": False,
        "msg_type": "text",
        "create_time": "1500000",
        "body": {"content": '{"text":"故障恢复"}'},
    }
    item.update(overrides)
    return item


def search_page(
    items: list[dict[str, Any]], *, has_more: bool = False, page_token: str | None = None
) -> dict[str, Any]:
    return {"code": 0, "data": {"items": items, "has_more": has_more, "page_token": page_token}}


def install_search_transport(
    monkeypatch: pytest.MonkeyPatch, responder: Callable[[ListMessageRequest], dict[str, Any]]
) -> SearchTransportProbe:
    probe = SearchTransportProbe()

    class EmptyTokenCache:
        def get(self, _key: str) -> None:
            return None

        def set(self, _key: str, _value: str, _expire: int) -> None:
            return None

    def execute(
        config: Config, request: BaseRequest, option: RequestOption | None = None
    ) -> RawResponse:
        if isinstance(request, ListMessageRequest):
            probe.requests.append(request)
            probe.timeouts.append(config.timeout)
            probe.app_ids.append(config.app_id)
            assert request.token_types == {AccessTokenType.TENANT}
            assert option is not None and option.tenant_access_token == "offline-tenant-token"
            assert not option.user_access_token
            payload = responder(request)
        else:
            assert request.uri == "/open-apis/auth/v3/tenant_access_token/internal"
            probe.token_timeouts.append(config.timeout)
            payload = {"code": 0, "tenant_access_token": "offline-tenant-token", "expire": 7200}
        raw = RawResponse()
        raw.status_code = HTTPStatus.OK
        raw.content = json.dumps(payload).encode("utf-8")
        return raw

    monkeypatch.setattr(TokenManager, "cache", EmptyTokenCache())
    monkeypatch.setattr(Transport, "execute", execute)
    return probe
