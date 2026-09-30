"""Shared prompt types: envelope, tiers, and surface Strategy — no agent-path knowledge."""

from __future__ import annotations

from core.agent_harness.prompts.kernel.channel_context import build_action_prompt_context
from core.agent_harness.prompts.kernel.envelope import (
    PromptBlock,
    PromptBlockId,
    PromptBlockKind,
    PromptEnvelope,
    PromptTier,
)
from core.agent_harness.prompts.kernel.surfaces import (
    PromptSurface,
    SurfaceProfile,
    profile_for,
)

__all__ = [
    "build_action_prompt_context",
    "PromptBlock",
    "PromptBlockId",
    "PromptBlockKind",
    "PromptEnvelope",
    "PromptSurface",
    "PromptTier",
    "SurfaceProfile",
    "profile_for",
]
