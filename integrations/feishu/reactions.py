"""Exact-identity operations for the bot's processing reaction."""

from dataclasses import dataclass
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    DeleteMessageReactionRequest,
    Emoji,
    ListMessageReactionRequest,
)

from config.constants.feishu import FEISHU_PROCESSING_EMOJI


class ReactionCallError(RuntimeError):
    """A reaction operation failed without exposing vendor response content."""

    def __init__(self, operation: str, code: int = 0) -> None:
        super().__init__("Feishu reaction call failed")
        self.operation = operation
        self.code = code


@dataclass(frozen=True, slots=True)
class FeishuReaction:
    """Reaction identity and its platform-reported owner."""

    reaction_id: str
    operator_id: str
    operator_type: str


def _parse(data: Any) -> FeishuReaction:
    reaction_id = getattr(data, "reaction_id", None)
    if not isinstance(reaction_id, str) or not reaction_id:
        raise ReactionCallError("response")
    operator = getattr(data, "operator", None)
    operator_id = getattr(operator, "operator_id", "")
    operator_type = getattr(operator, "operator_type", "")
    return FeishuReaction(
        reaction_id,
        operator_id if isinstance(operator_id, str) else "",
        operator_type if isinstance(operator_type, str) else "",
    )


class FeishuReactionClient:
    """Perform bounded individual requests without implicit reaction retries."""

    def __init__(self, app_id: str, app_secret: str, *, sdk_client: Any = None) -> None:
        self._client = sdk_client or (
            lark.Client.builder().app_id(app_id).app_secret(app_secret).timeout(2).build()
        )

    @staticmethod
    def _check(response: Any, operation: str) -> None:
        if not response.success():
            raise ReactionCallError(operation, int(response.code or 0))

    def add_eye(self, message_id: str) -> FeishuReaction:
        """Add EYE and retain the exact identity returned by Feishu."""
        request = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(
                CreateMessageReactionRequestBody.builder()
                .reaction_type(Emoji.builder().emoji_type(FEISHU_PROCESSING_EMOJI).build())
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message_reaction.create(request)
        self._check(response, "add")
        return _parse(response.data)

    def delete(self, message_id: str, reaction_id: str) -> None:
        """Delete only the specified reaction on the specified message."""
        request = (
            DeleteMessageReactionRequest.builder()
            .message_id(message_id)
            .reaction_id(reaction_id)
            .build()
        )
        response = self._client.im.v1.message_reaction.delete(request)
        self._check(response, "delete")

    def list_eye(self, message_id: str) -> tuple[FeishuReaction, ...]:
        """List EYE identities without discarding ownership information."""
        reactions: list[FeishuReaction] = []
        page_token = ""
        seen: set[str] = set()
        while True:
            request = (
                ListMessageReactionRequest.builder()
                .message_id(message_id)
                .reaction_type(FEISHU_PROCESSING_EMOJI)
                .page_size(50)
                .page_token(page_token)
                .build()
            )
            response = self._client.im.v1.message_reaction.list(request)
            self._check(response, "list")
            reactions.extend(_parse(item) for item in response.data.items or ())
            if not response.data.has_more:
                return tuple(reactions)
            page_token = response.data.page_token
            if not page_token or page_token in seen:
                raise ReactionCallError("pagination")
            seen.add(page_token)
