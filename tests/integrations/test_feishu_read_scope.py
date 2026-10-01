"""Read authority must never come from write targets or inferred host metadata."""

from typing import Any

import pytest

from infrastructure.harness_providers.prompt_context import (
    ACTION_PROMPT_CONTEXT_RESOURCE,
    ActionPromptContext,
)
from integrations.config_models import FeishuConfig
from integrations.feishu.read_scope import (
    is_read_available,
    resolve_read_scope,
    resolve_runtime_read_scope,
)
from integrations.feishu.read_types import FeishuReadError, FeishuReadErrorCode, FeishuReadScope


def valid_view() -> dict[str, Any]:
    return {
        "feishu": FeishuConfig(app_id="cli_test", app_secret="fake-secret"),
        "_gateway_platform": "feishu",
        "_gateway_chat_id": "oc_current",
    }


def test_read_scope_requires_typed_current_chat_and_verified_chat_app() -> None:
    view = valid_view()
    dictionary = {**view, "feishu": view["feishu"].model_dump()}
    for supplied in (view, dictionary):
        scope = resolve_read_scope(supplied)
        assert scope == FeishuReadScope("cli_test", "oc_current")
        assert is_read_available(supplied) is True

    invalid = [
        {},
        {"feishu": {"app_id": "cli_test", "app_secret": "fake-secret", "receive_id": "oc_default"}},
        {**dictionary, "feishu": {**dictionary["feishu"], "connection_verified": False}},
        {**dictionary, "feishu": {**dictionary["feishu"], "app_id": 42}},
        {**dictionary, "feishu": {**dictionary["feishu"], "app_secret": " "}},
        {**dictionary, "feishu": {**dictionary["feishu"], "receive_id_type": "invalid"}},
        {**view, "_gateway_platform": " Feishu "},
        {**view, "_gateway_platform": None},
        {**view, "_gateway_chat_id": 42},
        {**view, "_gateway_chat_id": " "},
    ]
    for supplied in invalid:
        assert is_read_available(supplied) is False
        with pytest.raises(FeishuReadError) as rejected:
            resolve_read_scope(supplied)
        assert rejected.value.code is FeishuReadErrorCode.AUTHORIZATION
        assert rejected.value.args == ("authorization",)
        assert rejected.value.__context__ is None and rejected.value.__cause__ is None

    scope = resolve_read_scope(dictionary)
    dictionary["_gateway_chat_id"] = "oc_changed"
    dictionary["feishu"]["app_id"] = "cli_changed"
    assert scope == FeishuReadScope("cli_test", "oc_current")


def test_runtime_scope_rejects_shell_with_gateway_markers() -> None:
    view = valid_view()
    gateway = ActionPromptContext("gateway", "feishu", frozenset({"feishu_get_message"}))
    assert resolve_runtime_read_scope(view, {ACTION_PROMPT_CONTEXT_RESOURCE: gateway}) == (
        FeishuReadScope("cli_test", "oc_current")
    )
    invalid = (
        {},
        {ACTION_PROMPT_CONTEXT_RESOURCE: {"surface": "gateway", "active_platform": "feishu"}},
        {
            ACTION_PROMPT_CONTEXT_RESOURCE: ActionPromptContext(
                "interactive_shell", None, frozenset()
            )
        },
        {ACTION_PROMPT_CONTEXT_RESOURCE: ActionPromptContext("gateway", "slack", frozenset())},
    )
    for resources in invalid:
        with pytest.raises(FeishuReadError) as rejected:
            resolve_runtime_read_scope(view, resources)
        assert rejected.value.code is FeishuReadErrorCode.AUTHORIZATION
