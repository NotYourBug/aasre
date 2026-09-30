"""Exact outbound target authority for Feishu agent writes."""

from __future__ import annotations

import pytest

from integrations.feishu.outbound_targets import parse_outbound_targets, resolve_outbound_target


def test_malformed_entry_is_rejected_instead_of_dropped() -> None:
    with pytest.raises(ValueError, match="Invalid Feishu outbound target"):
        parse_outbound_targets("chat_id:oc_ops,not-a-capability")


def test_exact_targets_are_deduplicated_and_case_sensitive() -> None:
    targets = parse_outbound_targets(" chat_id:oc_ops,chat_id:oc_ops,open_id:ou_owner, ")

    assert targets == frozenset({("chat_id", "oc_ops"), ("open_id", "ou_owner")})
    with pytest.raises(PermissionError):
        resolve_outbound_target(
            "chat_id:OC_OPS",
            default_receive_id_type="chat_id",
            default_receive_id="",
            allowed_targets=targets,
        )


def test_current_is_only_the_actual_feishu_turn_chat() -> None:
    allowed = frozenset({("chat_id", "oc_other")})

    resolved = resolve_outbound_target(
        "current",
        default_receive_id_type="chat_id",
        default_receive_id="",
        allowed_targets=allowed,
        gateway_platform="feishu",
        gateway_chat_id="oc_current",
    )
    assert resolved.canonical == "chat_id:oc_current"

    with pytest.raises(PermissionError):
        resolve_outbound_target(
            "current",
            default_receive_id_type="chat_id",
            default_receive_id="",
            allowed_targets=allowed,
            gateway_platform="slack",
            gateway_chat_id="oc_current",
        )


def test_reply_needs_a_chat_target_and_default_revocation_fails_closed() -> None:
    with pytest.raises(ValueError, match="chat_id"):
        resolve_outbound_target(
            "default",
            default_receive_id_type="open_id",
            default_receive_id="ou_owner",
            allowed_targets=frozenset(),
            require_chat_id=True,
        )

    with pytest.raises(PermissionError):
        resolve_outbound_target(
            "chat_id:oc_old",
            default_receive_id_type="chat_id",
            default_receive_id="oc_new",
            allowed_targets=frozenset(),
        )
