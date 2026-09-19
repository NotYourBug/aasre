"""Pure Card JSON 2.0 construction for Feishu write-tool approvals."""

from __future__ import annotations

from config.constants import FEISHU_CARD_SCHEMA


def _callback_button(label: str, token: str, *, kind: str) -> dict[str, object]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": kind,
        "width": "fill",
        "behaviors": [{"type": "callback", "value": {"approval_id": token}}],
    }


def render_approval_prompt_card(
    *,
    approve_token: str,
    deny_token: str,
    tool_name: str,
    reason: str,
    arguments_preview: str,
) -> dict[str, object]:
    """Return a Card JSON 2.0 approval prompt with two opaque actions."""
    details = [f"**Tool:** {tool_name}"]
    if reason.strip():
        details.append(f"**Reason:** {reason.strip()}")
    if arguments_preview:
        details.append(f"**Arguments:**\n```json\n{arguments_preview}\n```")
    return {
        "schema": FEISHU_CARD_SCHEMA,
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "Approval required"},
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n\n".join(details)},
                {
                    "tag": "column_set",
                    "horizontal_spacing": "8px",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                _callback_button("Approve", approve_token, kind="primary_filled")
                            ],
                        },
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                _callback_button("Deny", deny_token, kind="danger_filled")
                            ],
                        },
                    ],
                },
            ]
        },
    }


def render_approval_result_card(*, tool_name: str, approved: bool) -> dict[str, object]:
    """Return a button-free Card JSON 2.0 approval result."""
    outcome = "Approved" if approved else "Denied"
    icon = "✅" if approved else "🚫"
    return {
        "schema": FEISHU_CARD_SCHEMA,
        "header": {
            "template": "green" if approved else "red",
            "title": {"tag": "plain_text", "content": outcome},
        },
        "body": {
            "elements": [{"tag": "markdown", "content": f"{icon} **{outcome}** — `{tool_name}`"}]
        },
    }


__all__ = ["render_approval_prompt_card", "render_approval_result_card"]
