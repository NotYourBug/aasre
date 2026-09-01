"""Feishu session binding key isolation."""

from __future__ import annotations

from gateway.transports.feishu.events import FeishuInboundMessage
from gateway.transports.feishu.session_rotation import conversation_key


def test_conversation_key_isolates_senders_in_one_chat() -> None:
    alice = FeishuInboundMessage(chat_id="oc_chat", open_id="ou_alice", message_id="m1", text="hi")
    bob = FeishuInboundMessage(chat_id="oc_chat", open_id="ou_bob", message_id="m2", text="hi")
    assert conversation_key(alice) == "oc_chat:ou_alice"
    assert conversation_key(bob) == "oc_chat:ou_bob"
    assert conversation_key(alice) != conversation_key(bob)
