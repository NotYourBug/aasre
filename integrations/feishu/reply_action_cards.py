"""Pure Card JSON 2.0 components for final reply actions."""

from __future__ import annotations


def _action_button(label: str, key: str, token: str) -> dict[str, object]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "default",
        "behaviors": [{"type": "callback", "value": {key: token}}],
    }


def render_reply_action_elements(
    *, feedback_token: str, retry_token: str
) -> list[dict[str, object]]:
    """Return final-answer adoption and retry buttons with opaque callback values."""
    if not feedback_token.strip() or not retry_token.strip():
        raise ValueError("Reply action tokens must be nonempty")
    if feedback_token == retry_token:
        raise ValueError("Reply action tokens must be distinct")
    return [
        _action_button("✅ 采纳", "feedback_id", feedback_token),
        _action_button("🔄 重试", "retry_id", retry_token),
    ]


__all__ = ["render_reply_action_elements"]
