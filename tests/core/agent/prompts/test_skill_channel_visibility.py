"""All skill catalog and body entrypoints obey the frozen channel context."""

from pathlib import Path

import pytest

from core.agent_harness.prompts.skills import loader
from core.agent_harness.prompts.skills.loader import (
    clear_skills_caches,
    list_action_skills,
    load_skill_body,
    load_skills_demo_block,
    load_skills_index,
)
from core.agent_harness.tools import ActionToolScope
from core.agent_harness.tools.tool_context import ACTION_TOOL_CONTEXT_RESOURCE_KEY
from core.tool import AgentToolContext
from infrastructure.harness_providers.prompt_context import (
    ACTION_PROMPT_CONTEXT_RESOURCE,
    ActionPromptContext,
)
from tools.interactive_shell.actions.skill_view import run_skill_view


def runtime_context(resource: object) -> AgentToolContext:
    scope = ActionToolScope(session=None, console=None)
    return AgentToolContext(
        resolved_integrations={"_gateway_platform": "slack"},
        resources={
            ACTION_TOOL_CONTEXT_RESOURCE_KEY: scope,
            ACTION_PROMPT_CONTEXT_RESOURCE: resource,
        },
    )


@pytest.mark.parametrize(
    "resource",
    [
        ActionPromptContext("gateway", "feishu", frozenset({"skill_view"})),
        ActionPromptContext("gateway", None, frozenset({"skill_view"})),
        None,
        {"surface": "interactive_shell"},
    ],
)
def test_direct_lookup_and_error_lists_never_reveal_hidden_skill(resource: object) -> None:
    context = runtime_context(resource)
    result = run_skill_view(name="morning-report", context=context)
    assert result["ok"] is False
    assert "content" not in result
    assert "morning-report" not in result["available"]
    assert "ALWAYS DELIVER TO SLACK" not in str(result)
    for name in ("", "unknown-skill"):
        error = run_skill_view(name=name, context=context)
        assert "morning-report" not in error["available"]


def test_cache_isolation_across_gateway_and_shell_catalogs() -> None:
    clear_skills_caches()
    try:
        for surface, platform, visible in (
            ("gateway", "slack", True),
            ("gateway", "feishu", False),
            ("gateway", "discord", False),
            ("gateway", None, False),
            ("interactive_shell", None, True),
            ("gateway", "slack", True),
        ):
            context = ActionPromptContext(surface, platform, frozenset({"skill_view"}))
            names = {skill.name for skill in list_action_skills(context)}
            assert ("morning-report" in names) is visible
            assert ("morning-report" in load_skills_index(context)) is visible
            assert ("weekday morning briefing" in load_skills_demo_block(context)) is visible
            assert bool(load_skill_body("morning-report", context=context)) is visible
    finally:
        clear_skills_caches()


@pytest.mark.parametrize(
    "frontmatter", ["gateway_platforms: slack", "gateway_platforms: [slack, 7]", "[", "[]"]
)
def test_malformed_frontmatter_hides_skill_from_gateways(
    frontmatter: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "restricted.md").write_text(
        f"---\n{frontmatter}\n---\nPrivate recipe", encoding="utf-8"
    )
    monkeypatch.setattr(loader, "skills_dir", lambda: tmp_path)
    clear_skills_caches()
    try:
        gateway = ActionPromptContext("gateway", "slack", frozenset({"skill_view"}))
        assert not list_action_skills(gateway)
        assert load_skill_body("restricted", context=gateway) == ""
        assert load_skill_body("restricted").endswith("Private recipe")
    finally:
        clear_skills_caches()


def test_clearing_caches_refreshes_every_channel_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "restricted.md"
    monkeypatch.setattr(loader, "skills_dir", lambda: tmp_path)
    clear_skills_caches()
    try:
        for allowed in ("slack", "feishu"):
            path.write_text(
                f"---\nname: restricted\ndemo: restricted-demo\ngateway_platforms: [{allowed}]\n---\nPrivate recipe",
                encoding="utf-8",
            )
            clear_skills_caches()
            for platform in ("slack", "feishu"):
                context = ActionPromptContext("gateway", platform, frozenset({"skill_view"}))
                visible = platform == allowed
                assert bool(list_action_skills(context)) is visible
                assert ("restricted" in load_skills_index(context)) is visible
                assert ("restricted-demo" in load_skills_demo_block(context)) is visible
                assert bool(load_skill_body("restricted", context=context)) is visible
    finally:
        clear_skills_caches()
