"""Feishu integration: chat-app delivery and alert-push delivery.

The interactive app serves everything that belongs in a conversation — the chat
transport, investigation reports, and scheduled tasks. The alert-push app is
one-way only: watchdog alarms and background-RCA notices. The package also
parses inbound message content into text and resource references, and builds
CardKit cards from markdown.
"""

from integrations.feishu.card_document import (
    CardPage,
    paginate,
    render_card_spec,
    spec_bytes,
)
from integrations.feishu.credentials import (
    FeishuAlarmCredentials,
    FeishuChatCredentials,
    load_chat_credentials_from_env,
    load_credentials_from_env,
)
from integrations.feishu.inbound_content import (
    TRACKED_MESSAGE_TYPES,
    ResourceRef,
    classify_file,
    flatten_post,
    is_known_text_file,
    resource_refs,
    resource_url,
)

__all__ = [
    "CardPage",
    "FeishuAlarmCredentials",
    "FeishuChatCredentials",
    "ResourceRef",
    "TRACKED_MESSAGE_TYPES",
    "classify_file",
    "flatten_post",
    "is_known_text_file",
    "load_chat_credentials_from_env",
    "load_credentials_from_env",
    "paginate",
    "render_card_spec",
    "resource_refs",
    "resource_url",
    "spec_bytes",
]
