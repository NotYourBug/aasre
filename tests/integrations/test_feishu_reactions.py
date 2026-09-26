"""Reaction request ownership and safe vendor failures."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from integrations.feishu.reactions import FeishuReactionClient, ReactionCallError


def _response(*, code: int = 0, **data: object) -> MagicMock:
    response = MagicMock()
    response.success.return_value = code == 0
    response.code = code
    response.msg = "sensitive-vendor-detail"
    response.data = SimpleNamespace(**data)
    return response


def test_add_and_delete_preserve_exact_identity() -> None:
    sdk = MagicMock()
    sdk.im.v1.message_reaction.create.return_value = _response(
        reaction_id="reaction", operator=SimpleNamespace(operator_id="app", operator_type="app")
    )
    sdk.im.v1.message_reaction.delete.return_value = _response()
    client = FeishuReactionClient("app", "secret", sdk_client=sdk)
    reaction = client.add_eye("message")
    assert (reaction.reaction_id, reaction.operator_id, reaction.operator_type) == (
        "reaction",
        "app",
        "app",
    )
    request = sdk.im.v1.message_reaction.create.call_args.args[0]
    assert request.message_id == "message"
    assert request.request_body.reaction_type.emoji_type == "EYE"
    client.delete("message", reaction.reaction_id)
    request = sdk.im.v1.message_reaction.delete.call_args.args[0]
    assert (request.message_id, request.reaction_id) == ("message", "reaction")


def test_list_preserves_operator_and_follows_pages() -> None:
    sdk = MagicMock()
    item = SimpleNamespace(
        reaction_id="r", operator=SimpleNamespace(operator_id="user", operator_type="user")
    )
    sdk.im.v1.message_reaction.list.side_effect = [
        _response(items=[item], has_more=True, page_token="next"),
        _response(items=[], has_more=False, page_token=""),
    ]
    client = FeishuReactionClient("app", "secret", sdk_client=sdk)
    assert client.list_eye("m")[0].operator_type == "user"
    requests = [call.args[0] for call in sdk.im.v1.message_reaction.list.call_args_list]
    assert all(request.reaction_type == "EYE" for request in requests)
    assert requests[1].page_token == "next"


def test_authorization_error_is_not_success_or_vendor_detail() -> None:
    sdk = MagicMock()
    sdk.im.v1.message_reaction.delete.return_value = _response(code=231007)
    with pytest.raises(ReactionCallError) as caught:
        FeishuReactionClient("app", "secret", sdk_client=sdk).delete("m", "r")
    assert "sensitive" not in str(caught.value)
    assert sdk.im.v1.message_reaction.delete.call_count == 1
