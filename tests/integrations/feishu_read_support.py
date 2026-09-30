"""Offline real-SDK transport for Feishu read tests."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

import pytest
from lark_oapi.api.im.v1 import GetMessageRequest
from lark_oapi.core.enum import AccessTokenType
from lark_oapi.core.http import Transport
from lark_oapi.core.model import BaseRequest, Config, RawResponse, RequestOption
from lark_oapi.core.token.manager import TokenManager


@dataclass
class ReadTransportProbe:
    requests: list[GetMessageRequest] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)
    app_ids: list[str] = field(default_factory=list)
    token_timeouts: list[float] = field(default_factory=list)


def message_payload(**overrides: Any) -> dict[str, Any]:
    item = {
        "message_id": "om_known",
        "chat_id": "oc_current",
        "deleted": False,
        "msg_type": "interactive",
        "body": {"content": '{"schema":"2.0","body":{"elements":[]}}'},
    }
    item.update(overrides)
    return {"code": 0, "data": {"items": [item]}}


def install_read_transport(
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[GetMessageRequest], dict[str, Any]],
) -> ReadTransportProbe:
    probe = ReadTransportProbe()

    class EmptyTokenCache:
        def get(self, _key: str) -> None:
            return None

        def set(self, _key: str, _value: str, _expire: int) -> None:
            return None

    def execute(
        config: Config, request: BaseRequest, option: RequestOption | None = None
    ) -> RawResponse:
        if isinstance(request, GetMessageRequest):
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
