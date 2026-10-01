"""The final action-driver gate applies even to precomputed tool providers."""

from dataclasses import replace
from typing import Any

import pytest

from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.action_driver import ActionTurnRunner, _StaticToolCallLLM
from core.agent_harness.turns.headless_adapters import BufferOutputSink, InMemorySessionState
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.domain.types.tools import ToolSurface
from core.llm.types import AgentLLMResponse
from core.tool import RegisteredTool
from infrastructure.harness_providers import ToolSources
from integrations.harness_adapters import register_harness_adapters


class RecordingLLM(_StaticToolCallLLM):
    def __init__(self) -> None:
        super().__init__([])
        self.offered: list[str] = []
        self.systems: list[str] = []

    def tool_schemas(self, tools: Any) -> list[dict[str, Any]]:
        self.offered = [tool.name for tool in tools]
        return []

    def invoke(
        self, _messages: Any, *, system: str | None = None, tools: Any = None
    ) -> AgentLLMResponse:
        _ = tools
        self.systems.append(system or "")
        return AgentLLMResponse(content="Recorded answer.", tool_calls=[], raw_content=None)


def candidate(name: str, source: str) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        source=source,
        description="Offline recording tool",
        input_schema={"type": "object", "properties": {}},
        run=lambda: {"ok": True},
    )


def snapshot(session: InMemorySessionState, *, surface: str, platform: object) -> TurnSnapshot:
    return replace(
        TurnSnapshot.from_session("record", session, surface=surface),
        resolved_integrations={"_gateway_platform": platform, "_gateway_chat_id": "private-chat"},
    )


@pytest.mark.parametrize(
    "surface,platform,expected",
    [
        ("gateway", "feishu", ["opaque-a", "ordinary"]),
        ("gateway", "unknown", ["ordinary"]),
        ("interactive_shell", "feishu", ["opaque-a", "opaque-b", "opaque-c", "ordinary"]),
    ],
)
def test_precomputed_provider_cannot_bypass_final_gate(
    surface: str, platform: str, expected: list[str]
) -> None:
    register_harness_adapters()
    session = InMemorySessionState()
    candidates = [
        candidate("opaque-a", "feishu"),
        candidate("opaque-b", "slack"),
        candidate("opaque-c", "rocketchat"),
        candidate("ordinary", "github"),
    ]
    provider = DefaultToolProvider(session, None, precomputed_action_tools=candidates)
    llm = RecordingLLM()
    result = ActionTurnRunner(BufferOutputSink(), provider, lambda: llm).run(
        "record", session, turn_plan=TurnPlan(snapshot(session, surface=surface, platform=platform))
    )
    assert result.accounting_status == "completed", result.response_text
    assert llm.offered == expected
    assert [tool.name for tool in candidates] == ["opaque-a", "opaque-b", "opaque-c", "ordinary"]


def test_normal_provider_keeps_availability_checks_before_channel_gate() -> None:
    register_harness_adapters()
    seen: list[dict[str, Any]] = []

    def unavailable(sources: dict[str, Any]) -> bool:
        seen.append(sources)
        return False

    candidates = [
        candidate("active", "feishu"),
        candidate("inactive", "slack"),
        replace(candidate("withheld", "feishu"), is_available=unavailable),
        candidate("ordinary", "github"),
    ]

    class OfflineRegistry:
        def tools_for_surface(self, _surface: ToolSurface) -> list[RegisteredTool]:
            return candidates

        def tool_map_for_surface(self, _surface: ToolSurface) -> dict[str, RegisteredTool]:
            return {tool.name: tool for tool in candidates}

    ToolSources(registry=OfflineRegistry()).install()
    session = InMemorySessionState()
    turn = replace(
        snapshot(session, surface="gateway", platform="feishu"),
        resolved_integrations={
            "_gateway_platform": "feishu",
            "_gateway_chat_id": "private-chat",
            "feishu": {"connection_verified": True},
            "slack": {"connection_verified": True},
        },
    )
    llm = RecordingLLM()
    provider = DefaultToolProvider(session, None)
    result = ActionTurnRunner(BufferOutputSink(), provider, lambda: llm).run(
        "record", session, turn_plan=TurnPlan(turn)
    )
    assert result.accounting_status == "completed", result.response_text
    assert len(seen) == 1
    assert "feishu" in seen[0] and "slack" in seen[0]
    assert llm.offered == ["active", "ordinary"]


def test_discovery_and_provider_paths_enforce_read_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    from lark_oapi.api.im.v1 import GetMessageRequest

    from integrations.feishu.tools.feishu_get_message_tool import tool as read_module
    from integrations.feishu.tools.feishu_search_messages_tool import tool as search_module
    from tests.integrations.feishu_read_support import install_read_transport
    from tests.integrations.test_feishu_read_scope import valid_view
    from tools.registry import get_registered_tools

    register_harness_adapters()
    discovered = {tool.name: tool for tool in get_registered_tools(ToolSurface.ACTION)}
    reader = discovered["feishu_get_message"]
    assert reader.surfaces == (ToolSurface.ACTION,)
    assert reader.is_available(valid_view()) and not reader.is_available({})
    assert all(tool.name != reader.name for tool in get_registered_tools(ToolSurface.INVESTIGATION))
    candidates = [
        discovered[name]
        for name in (
            "feishu_get_message",
            "feishu_search_messages",
            "feishu_send_message",
            "feishu_reply_message",
        )
    ]

    def never_get(_request: GetMessageRequest) -> dict[str, Any]:
        raise AssertionError("No real read requested in recording turns")

    def never_load() -> Any:
        raise AssertionError("Invalid runtime scope must precede credential loading")

    probe = install_read_transport(monkeypatch, never_get)
    monkeypatch.setattr(read_module, "load_chat_credentials_from_env", never_load)
    monkeypatch.setattr(search_module, "load_chat_credentials_from_env", never_load)
    searcher = discovered["feishu_search_messages"]
    assert searcher.is_available(valid_view()) and not searcher.is_available({})
    assert all(
        tool.name != searcher.name for tool in get_registered_tools(ToolSurface.INVESTIGATION)
    )

    class OfflineRegistry:
        def tools_for_surface(self, _surface: ToolSurface) -> list[RegisteredTool]:
            return candidates

        def tool_map_for_surface(self, _surface: ToolSurface) -> dict[str, RegisteredTool]:
            return {tool.name: tool for tool in candidates}

    class CustomProvider(DefaultToolProvider):
        def action_tools(self, **_kwargs: Any) -> list[RegisteredTool]:
            return candidates

    ToolSources(registry=OfflineRegistry()).install()
    for provider_kind in ("normal", "precomputed", "custom"):
        for surface, platform in (
            ("gateway", "feishu"),
            ("interactive_shell", "feishu"),
            ("gateway", "slack"),
        ):
            session = InMemorySessionState()
            view = {**valid_view(), "_gateway_platform": platform}
            turn = replace(
                snapshot(session, surface=surface, platform=platform), resolved_integrations=view
            )
            provider = (
                CustomProvider(session, None)
                if provider_kind == "custom"
                else DefaultToolProvider(
                    session,
                    None,
                    precomputed_action_tools=candidates if provider_kind == "precomputed" else None,
                )
            )
            llm = RecordingLLM()
            result = ActionTurnRunner(BufferOutputSink(), provider, lambda llm=llm: llm).run(
                "record", session, turn_plan=TurnPlan(turn)
            )
            assert result.accounting_status == "completed", result.response_text
            names = set(llm.offered)
            assert ("feishu_get_message" in names) is (
                surface == "gateway" and platform == "feishu"
            )
            assert ("feishu_get_message" in llm.systems[0]) is ("feishu_get_message" in names)
            assert ("feishu_search_messages" in names) is (
                surface == "gateway" and platform == "feishu"
            )
            assert ("feishu_search_messages" in llm.systems[0]) is (
                "feishu_search_messages" in names
            )
            if surface == "interactive_shell":
                assert {"feishu_send_message", "feishu_reply_message"} <= names
            if platform == "slack":
                assert not names
    # A provider can offer an unavailable tool; direct runtime authority still fails closed.
    from core.llm.types import ToolCall

    invalid_session = InMemorySessionState()
    invalid_turn = replace(
        snapshot(invalid_session, surface="gateway", platform="feishu"),
        resolved_integrations={**valid_view(), "_gateway_chat_id": 42},
    )
    forced_llm = _StaticToolCallLLM(
        [ToolCall(id="forced-read", name=reader.name, input={"message_id": "om_known"})]
    )
    ActionTurnRunner(
        BufferOutputSink(), CustomProvider(invalid_session, None), lambda: forced_llm
    ).run("read", invalid_session, turn_plan=TurnPlan(invalid_turn))
    forced_search = _StaticToolCallLLM(
        [
            ToolCall(
                id="forced-search", name=searcher.name, input={"start_time": 1000, "end_time": 2000}
            )
        ]
    )
    ActionTurnRunner(
        BufferOutputSink(), CustomProvider(invalid_session, None), lambda: forced_search
    ).run("search", invalid_session, turn_plan=TurnPlan(invalid_turn))
    assert not probe.requests


def test_reader_requires_explicit_frozen_gateway_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.agent_harness.turns import action_driver
    from core.llm.types import ToolCall
    from integrations.feishu.tools.feishu_get_message_tool.tool import FeishuGetMessageTool
    from tests.integrations.test_feishu_read_scope import valid_view

    register_harness_adapters()
    reader = RegisteredTool.from_base_tool(FeishuGetMessageTool())
    write = candidate("feishu_send_message", "feishu")
    calls: list[str] = []

    def record_read(*, message_id: str, context: Any) -> Any:
        calls.append(message_id)
        raise AssertionError("Unknown host must not dispatch the reader")

    reader = replace(reader, run=record_read)

    def resolved(_session: Any) -> dict[str, Any]:
        return valid_view()

    monkeypatch.setattr(action_driver, "resolve_and_cache_integrations", resolved)

    class ForcedReadLLM(RecordingLLM):
        def __init__(self) -> None:
            super().__init__()
            self.called = False

        def invoke(
            self, messages: Any, *, system: str | None = None, tools: Any = None
        ) -> AgentLLMResponse:
            if not self.called:
                self.called = True
                self.systems.append(system or "")
                return AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(id="forced", name=reader.name, input={"message_id": "om_known"})
                    ],
                    raw_content=None,
                )
            return super().invoke(messages, system=system, tools=tools)

    from integrations.feishu.tools.feishu_search_messages_tool.tool import FeishuSearchMessagesTool

    searcher = replace(RegisteredTool.from_base_tool(FeishuSearchMessagesTool()), run=record_read)
    for has_plan in (False, True):
        session = InMemorySessionState()
        turn = replace(
            snapshot(session, surface="gateway", platform="feishu"),
            prompt_surface=None,
            resolved_integrations=valid_view(),
        )
        plan = TurnPlan(turn) if has_plan else None
        llm = ForcedReadLLM()
        provider = DefaultToolProvider(
            session, None, precomputed_action_tools=[reader, searcher, write]
        )
        ActionTurnRunner(BufferOutputSink(), provider, lambda llm=llm: llm).run(
            "read", session, turn_plan=plan
        )
        assert llm.offered == ["feishu_send_message"]
        assert "feishu_get_message" not in llm.systems[0]
        assert "feishu_search_messages" not in llm.systems[0]
    assert not calls
