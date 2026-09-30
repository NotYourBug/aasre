"""Vendor factories render action recipes and personas from frozen turn facts.

Historical gather/assistant factories retain their independent zero-argument API.
"""

from __future__ import annotations

from collections.abc import Callable

from infrastructure.harness_providers.prompt_context import ActionPromptContext

PromptFragmentFn = Callable[[], str]
ActionPromptFragmentFn = Callable[[ActionPromptContext], str]

_gather_prompt_fragments: list[PromptFragmentFn] = []
_action_prompt_fragments: list[ActionPromptFragmentFn] = []
_assistant_prompt_fragments: list[PromptFragmentFn] = []
_gateway_persona_fragments: list[ActionPromptFragmentFn] = []


def register_gather_prompt_fragment(fn: PromptFragmentFn) -> None:
    _gather_prompt_fragments.append(fn)


def gather_prompt_vendor_fragments() -> str:
    return "\n".join(fn() for fn in _gather_prompt_fragments)


def clear_gather_prompt_fragments() -> None:
    _gather_prompt_fragments.clear()


def register_action_prompt_fragment(fn: ActionPromptFragmentFn) -> None:
    _action_prompt_fragments.append(fn)


def action_prompt_vendor_fragments(context: ActionPromptContext) -> str:
    return "\n\n".join(filter(None, (fn(context) for fn in _action_prompt_fragments)))


def clear_action_prompt_fragments() -> None:
    _action_prompt_fragments.clear()


def register_assistant_prompt_fragment(fn: PromptFragmentFn) -> None:
    _assistant_prompt_fragments.append(fn)


def assistant_prompt_vendor_fragments() -> str:
    return "\n\n".join(fn() for fn in _assistant_prompt_fragments)


def clear_assistant_prompt_fragments() -> None:
    _assistant_prompt_fragments.clear()


def register_gateway_persona_fragment(fn: ActionPromptFragmentFn) -> None:
    _gateway_persona_fragments.append(fn)


def gateway_persona_fragments(context: ActionPromptContext) -> str:
    return "\n\n".join(filter(None, (fn(context) for fn in _gateway_persona_fragments)))


def clear_gateway_persona_fragments() -> None:
    _gateway_persona_fragments.clear()


def reset() -> None:
    """Clear all registered prompt/persona fragments (tests)."""
    clear_gather_prompt_fragments()
    clear_action_prompt_fragments()
    clear_assistant_prompt_fragments()
    clear_gateway_persona_fragments()


__all__ = [
    "ActionPromptFragmentFn",
    "PromptFragmentFn",
    "action_prompt_vendor_fragments",
    "assistant_prompt_vendor_fragments",
    "clear_action_prompt_fragments",
    "clear_assistant_prompt_fragments",
    "clear_gateway_persona_fragments",
    "clear_gather_prompt_fragments",
    "gateway_persona_fragments",
    "gather_prompt_vendor_fragments",
    "register_action_prompt_fragment",
    "register_assistant_prompt_fragment",
    "register_gateway_persona_fragment",
    "register_gather_prompt_fragment",
]
