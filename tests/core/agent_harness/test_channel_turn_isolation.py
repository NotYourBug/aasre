"""Frozen prompt and tool-resource facts travel through the real action runner."""

import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from typing import Any

import pytest

from core.agent_harness.tools import ActionToolScope
from core.agent_harness.tools.tool_context import ACTION_TOOL_CONTEXT_RESOURCE_KEY
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns import action_driver
from core.agent_harness.turns.headless_adapters import BufferOutputSink, InMemorySessionState
from core.agent_harness.turns.turn_plan import TurnPlan
from core.llm.types import AgentLLMResponse, ToolCall
from core.tool import AgentToolContext, RegisteredTool
from infrastructure.harness_providers import register_action_prompt_fragment
from infrastructure.harness_providers.prompt_context import (
    ACTION_PROMPT_CONTEXT_RESOURCE,
    ActionPromptContext,
)
from integrations.harness_adapters import register_harness_adapters
from tests.core.agent_harness.test_gateway_channel_tools import RecordingLLM, candidate, snapshot
from tools.interactive_shell.actions.skill_view import run_skill_view, skill_view_tool


@pytest.fixture
def offline_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(action_driver, "build_goal_reviewer", lambda *_args: None)

    def deny_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Channel runner tests must stay offline")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(action_driver, "resolve_and_cache_integrations", deny_network)


class ScriptedLLM(RecordingLLM):
    def __init__(self, name: str, arguments: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.name = name
        self.arguments = arguments or {}
        self.called = False

    def invoke(
        self, _messages: Any, *, system: str | None = None, tools: Any = None
    ) -> AgentLLMResponse:
        _ = tools
        self.systems.append(system or "")
        if not self.called:
            self.called = True
            return AgentLLMResponse(
                content="",
                tool_calls=[ToolCall(id="offline", name=self.name, input=self.arguments)],
                raw_content=None,
            )
        return AgentLLMResponse(content="Recorded answer.", tool_calls=[], raw_content=None)


class SharedResourceProvider:
    def __init__(self, tools: list[Any], resources: dict[str, Any] | None = None) -> None:
        self.tools = tools
        self.resources = resources if resources is not None else {"marker": object()}

    def action_tools(self, **_kwargs: Any) -> list[Any]:
        return self.tools

    def tool_resources(self) -> dict[str, Any]:
        return self.resources

    def observer(self, **_kwargs: Any) -> None:
        return None


def test_offered_tools_are_frozen_without_mutating_provider_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_harness_adapters()
    monkeypatch.setattr(action_driver, "build_goal_reviewer", lambda *_args: None)
    observed: list[AgentToolContext] = []
    prompt_contexts: list[ActionPromptContext] = []

    def record_prompt(context: ActionPromptContext) -> str:
        prompt_contexts.append(context)
        return "OFFERED FACTS: " + ",".join(sorted(context.offered_tool_names))

    register_action_prompt_fragment(record_prompt)

    def probe(*, context: AgentToolContext) -> dict[str, bool]:
        observed.append(context)
        return {"ok": True}

    tool = RegisteredTool(
        name="probe",
        description="Record context",
        input_schema={"type": "object"},
        source="interactive_shell",
        run=probe,
        accepts_runtime_context=True,
    )
    provider = SharedResourceProvider(
        [candidate("feishu_send_message", "feishu"), candidate("slack_send_message", "slack"), tool]
    )
    marker = provider.resources["marker"]
    llm = ScriptedLLM("probe")
    session = InMemorySessionState()
    result = action_driver.ActionTurnRunner(BufferOutputSink(), provider, lambda: llm).run(
        "record",
        session,
        turn_plan=TurnPlan(snapshot(session, surface="gateway", platform="feishu")),
    )
    assert result.executed_success_count == 1, result.response_text
    assert len(observed) == 1
    context = observed[0].resources.get(ACTION_PROMPT_CONTEXT_RESOURCE)
    assert context == ActionPromptContext(
        "gateway", "feishu", frozenset({"probe", "feishu_send_message"})
    )
    assert context is prompt_contexts[0]
    assert "OFFERED FACTS: feishu_send_message,probe" in llm.systems[0]
    assert observed[0].resources["marker"] is marker
    assert provider.resources == {"marker": marker}


def test_sequential_reused_runner_rebuilds_channel_and_skill_facts(offline_runner: None) -> None:
    register_harness_adapters()
    observed: list[tuple[AgentToolContext, dict[str, Any]]] = []

    def record_skill_view(*, name: str, context: AgentToolContext) -> dict[str, Any]:
        result = run_skill_view(name=name, context=context)
        observed.append((context, result))
        return result

    candidates = [
        candidate("feishu_send_message", "feishu"),
        candidate("slack_send_message", "slack"),
        replace(skill_view_tool, run=record_skill_view),
    ]
    session = InMemorySessionState()
    provider = DefaultToolProvider(session, None, precomputed_action_tools=candidates)
    llms = [ScriptedLLM("skill_view", {"name": "morning-report"}) for _ in range(3)]
    pending = iter(llms)
    runner = action_driver.ActionTurnRunner(BufferOutputSink(), provider, lambda: next(pending))
    for index, platform in enumerate(("feishu", "slack", "feishu")):
        session = InMemorySessionState()
        plan = TurnPlan(snapshot(session, surface="gateway", platform=platform))
        session.resolved_integrations_cache = {"_gateway_platform": "telegram"}
        provider.bind_session(session)
        runner.run("load the skill", session, turn_plan=plan)
        context, result = observed[index]
        expected_names = frozenset({"skill_view", f"{platform}_send_message"})
        assert context.resources[ACTION_PROMPT_CONTEXT_RESOURCE] == ActionPromptContext(
            "gateway", platform, expected_names
        )
        assert frozenset(llms[index].offered) == expected_names
        assert result["ok"] is (platform == "slack")
        assert ("morning-report" in llms[index].systems[0]) is (platform == "slack")
        assert ("ALWAYS DELIVER TO SLACK" in str(result)) is (platform == "slack")
    assert (
        observed[0][0].resources[ACTION_PROMPT_CONTEXT_RESOURCE]
        is not observed[2][0].resources[ACTION_PROMPT_CONTEXT_RESOURCE]
    )


def test_concurrent_channels_share_boot_registry_without_sharing_context(
    offline_runner: None,
) -> None:
    register_harness_adapters()
    rendezvous = Barrier(2)
    observed: dict[str, tuple[AgentToolContext, dict[str, Any]]] = {}

    class SynchronizedLLM(ScriptedLLM):
        def tool_schemas(self, tools: Any) -> list[dict[str, Any]]:
            schemas = super().tool_schemas(tools)
            rendezvous.wait(timeout=10)
            return schemas

    def record_skill_view(*, name: str, context: AgentToolContext) -> dict[str, Any]:
        frozen = context.resources[ACTION_PROMPT_CONTEXT_RESOURCE]
        assert isinstance(frozen, ActionPromptContext)
        result = run_skill_view(name=name, context=context)
        observed[frozen.active_platform or ""] = (context, result)
        return result

    shared_resources = {
        ACTION_TOOL_CONTEXT_RESOURCE_KEY: ActionToolScope(session=None, console=None)
    }
    provider = SharedResourceProvider(
        [
            candidate("feishu_send_message", "feishu"),
            candidate("slack_send_message", "slack"),
            replace(skill_view_tool, run=record_skill_view),
        ],
        shared_resources,
    )
    llms = {
        platform: SynchronizedLLM("skill_view", {"name": "morning-report"})
        for platform in ("feishu", "slack")
    }

    def run(platform: str) -> Any:
        session = InMemorySessionState()
        llm = llms[platform]
        return action_driver.ActionTurnRunner(BufferOutputSink(), provider, lambda: llm).run(
            "load the skill",
            session,
            turn_plan=TurnPlan(snapshot(session, surface="gateway", platform=platform)),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, platform) for platform in llms]
        try:
            results = [future.result(timeout=30) for future in futures]
        finally:
            rendezvous.abort()
            for future in futures:
                future.cancel()
    assert len(results) == 2
    assert set(observed) == {"slack", "feishu"}
    for platform, (context, result) in observed.items():
        assert context.resources[ACTION_PROMPT_CONTEXT_RESOURCE] == ActionPromptContext(
            "gateway", platform, frozenset({"skill_view", f"{platform}_send_message"})
        )
        assert set(llms[platform].offered) == {"skill_view", f"{platform}_send_message"}
        assert ("morning-report" in llms[platform].systems[0]) is (platform == "slack")
        assert ("ALWAYS DELIVER TO SLACK" in str(result)) is (platform == "slack")
    assert provider.resources is shared_resources
    assert ACTION_PROMPT_CONTEXT_RESOURCE not in shared_resources
