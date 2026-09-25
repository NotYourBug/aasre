"""Pure Card JSON 2.0 feedback components."""

from __future__ import annotations


def render_feedback_button_elements(token: str) -> list[dict[str, object]]:
    """Return one positive-feedback button carrying only an opaque token."""
    if not token.strip():
        raise ValueError("Feedback token must be nonempty")
    return [
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "✅ 采纳"},
            "type": "default",
            "behaviors": [{"type": "callback", "value": {"feedback_id": token}}],
        }
    ]
