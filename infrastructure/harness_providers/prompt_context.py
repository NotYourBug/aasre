"""Immutable model and tool visibility facts for one action turn."""

from dataclasses import dataclass
from typing import Literal

ACTION_PROMPT_CONTEXT_RESOURCE = "action_prompt_context"


@dataclass(frozen=True)
class ActionPromptContext:
    """Carry only surface, platform and the final offered tool names."""

    surface: Literal["interactive_shell", "gateway"]
    active_platform: str | None
    offered_tool_names: frozenset[str]
