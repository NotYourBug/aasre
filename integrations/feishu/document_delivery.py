"""Lossless non-streaming Feishu document delivery with plaintext fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from integrations.feishu.card_client import FeishuCardClient
from integrations.feishu.card_document import paginate, render_card_spec
from integrations.feishu.delivery import post_feishu_message
from integrations.feishu.delivery_types import (
    FeishuCardCallError,
    FeishuCardCallStage,
    FeishuDeliveryErrorCategory,
    FeishuDeliveryMode,
    FeishuDeliveryStatus,
    FeishuMessageSendResult,
    FeishuSendCertainty,
    classify_feishu_rejection,
)

logger = logging.getLogger(__name__)

_TEXT_CHUNK_MAX_CODEPOINTS = 4_096
_SUMMARY_LOG = "Feishu document delivery summary"
_DEGRADED_LOG = "Feishu document delivery degraded to text"
_FAILED_LOG = "Feishu document delivery failed"

_SAFE_ERRORS = {
    FeishuDeliveryErrorCategory.CONFIGURATION: "Feishu delivery is not configured",
    FeishuDeliveryErrorCategory.AUTHORIZATION: "Feishu delivery is not authorized",
    FeishuDeliveryErrorCategory.VALIDATION: "Feishu delivery request is invalid",
    FeishuDeliveryErrorCategory.RATE_LIMIT: "Feishu delivery was rate limited",
    FeishuDeliveryErrorCategory.DEFINITE_REJECTION: "Feishu delivery was rejected",
    FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN: "Feishu delivery could not be confirmed",
    FeishuDeliveryErrorCategory.TRANSPORT: "Feishu delivery transport failed",
    FeishuDeliveryErrorCategory.INTERNAL: "Feishu delivery failed",
}


@dataclass(frozen=True)
class FeishuDocumentDeliveryResult:
    status: FeishuDeliveryStatus
    attempted: bool
    confirmed_message_ids: tuple[str, ...]
    delivery_mode: FeishuDeliveryMode
    error_category: FeishuDeliveryErrorCategory | None
    error: str

    @property
    def first_message_id(self) -> str:
        return self.confirmed_message_ids[0] if self.confirmed_message_ids else ""

    @property
    def successful(self) -> bool:
        return self.status in {
            FeishuDeliveryStatus.SUCCESS,
            FeishuDeliveryStatus.DEGRADED_SUCCESS,
        }


@dataclass(frozen=True)
class _TextChunk:
    text: str
    source_start: int
    source_end: int
    index: int


def _text_chunks(markdown: str, source_start: int) -> list[_TextChunk]:
    """Slice original Markdown into contiguous code-point-bounded chunks."""
    chunks: list[_TextChunk] = []
    for index, start in enumerate(
        range(source_start, len(markdown), _TEXT_CHUNK_MAX_CODEPOINTS),
        1,
    ):
        end = min(start + _TEXT_CHUNK_MAX_CODEPOINTS, len(markdown))
        chunks.append(
            _TextChunk(
                text=markdown[start:end],
                source_start=start,
                source_end=end,
                index=index,
            )
        )
    return chunks


def _safe_error(category: FeishuDeliveryErrorCategory) -> str:
    return _SAFE_ERRORS[category]


def _summary(
    result: FeishuDocumentDeliveryResult,
    *,
    receive_id_type: str,
    card_page_total: int,
    fallback_chunk_total: int,
) -> FeishuDocumentDeliveryResult:
    logger.info(
        _SUMMARY_LOG,
        extra={
            "status": result.status.value,
            "delivery_mode": result.delivery_mode.value,
            "receive_id_type": receive_id_type,
            "card_page_total": card_page_total,
            "fallback_chunk_total": fallback_chunk_total,
            "confirmed_message_count": len(result.confirmed_message_ids),
            "error_category": (
                result.error_category.value if result.error_category is not None else ""
            ),
        },
    )
    return result


def _card_error_category(
    error: Exception,
    *,
    stage: FeishuCardCallStage,
) -> FeishuDeliveryErrorCategory:
    if isinstance(error, FeishuCardCallError):
        if error.stage is FeishuCardCallStage.SEND_CARD and error.code == 0:
            return FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN
        return classify_feishu_rejection(error.code)
    if stage is FeishuCardCallStage.SEND_CARD:
        return FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN
    return FeishuDeliveryErrorCategory.TRANSPORT


def _confirmed_text_send(result: FeishuMessageSendResult) -> bool:
    return (
        result.accepted
        and bool(result.message_id)
        and result.certainty is FeishuSendCertainty.CONFIRMED_SENT
    )


def _fallback_to_text(
    *,
    app_id: str,
    app_secret: str,
    receive_id: str,
    receive_id_type: str,
    markdown: str,
    source_cursor: int,
    card_page_index: int,
    card_page_total: int,
    trigger_category: FeishuDeliveryErrorCategory,
    confirmed_message_ids: list[str],
) -> FeishuDocumentDeliveryResult:
    chunks = _text_chunks(markdown, source_cursor)
    logger.warning(
        _DEGRADED_LOG,
        extra={
            "delivery_mode": FeishuDeliveryMode.TEXT_FALLBACK.value,
            "receive_id_type": receive_id_type,
            "card_page_index": card_page_index,
            "card_page_total": card_page_total,
            "confirmed_message_count": len(confirmed_message_ids),
            "error_category": trigger_category.value,
        },
    )

    for chunk in chunks:
        try:
            send_result = post_feishu_message(
                app_id,
                app_secret,
                receive_id,
                receive_id_type,
                chunk.text,
            )
        except Exception:
            send_result = FeishuMessageSendResult(
                accepted=False,
                message_id="",
                error_category=FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN,
                certainty=FeishuSendCertainty.MAYBE_SENT,
            )
        if _confirmed_text_send(send_result):
            confirmed_message_ids.append(send_result.message_id)
            source_cursor = chunk.source_end
            continue

        terminal_category = (
            send_result.error_category or FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN
        )
        logger.warning(
            _FAILED_LOG,
            extra={
                "status": FeishuDeliveryStatus.FAILED.value,
                "delivery_mode": FeishuDeliveryMode.TEXT_FALLBACK.value,
                "receive_id_type": receive_id_type,
                "fallback_chunk_index": chunk.index,
                "fallback_chunk_total": len(chunks),
                "confirmed_message_count": len(confirmed_message_ids),
                "error_category": terminal_category.value,
            },
        )
        return _summary(
            FeishuDocumentDeliveryResult(
                status=FeishuDeliveryStatus.FAILED,
                attempted=True,
                confirmed_message_ids=tuple(confirmed_message_ids),
                delivery_mode=FeishuDeliveryMode.TEXT_FALLBACK,
                error_category=terminal_category,
                error=_safe_error(terminal_category),
            ),
            receive_id_type=receive_id_type,
            card_page_total=card_page_total,
            fallback_chunk_total=len(chunks),
        )

    return _summary(
        FeishuDocumentDeliveryResult(
            status=FeishuDeliveryStatus.DEGRADED_SUCCESS,
            attempted=True,
            confirmed_message_ids=tuple(confirmed_message_ids),
            delivery_mode=FeishuDeliveryMode.TEXT_FALLBACK,
            error_category=trigger_category,
            error="",
        ),
        receive_id_type=receive_id_type,
        card_page_total=card_page_total,
        fallback_chunk_total=len(chunks),
    )


def deliver_feishu_document(
    *,
    app_id: str,
    app_secret: str,
    receive_id: str,
    receive_id_type: str,
    markdown: str,
) -> FeishuDocumentDeliveryResult:
    """Deliver complete Markdown as cards, then lossless plaintext if needed."""
    app_id = app_id.strip()
    app_secret = app_secret.strip()
    receive_id = receive_id.strip()
    receive_id_type = receive_id_type.strip()
    if not all((app_id, app_secret, receive_id, receive_id_type)):
        return _summary(
            FeishuDocumentDeliveryResult(
                status=FeishuDeliveryStatus.SKIPPED,
                attempted=False,
                confirmed_message_ids=(),
                delivery_mode=FeishuDeliveryMode.NONE,
                error_category=FeishuDeliveryErrorCategory.CONFIGURATION,
                error=_safe_error(FeishuDeliveryErrorCategory.CONFIGURATION),
            ),
            receive_id_type=receive_id_type,
            card_page_total=0,
            fallback_chunk_total=0,
        )
    if not markdown.strip():
        return _summary(
            FeishuDocumentDeliveryResult(
                status=FeishuDeliveryStatus.SKIPPED,
                attempted=False,
                confirmed_message_ids=(),
                delivery_mode=FeishuDeliveryMode.NONE,
                error_category=FeishuDeliveryErrorCategory.VALIDATION,
                error=_safe_error(FeishuDeliveryErrorCategory.VALIDATION),
            ),
            receive_id_type=receive_id_type,
            card_page_total=0,
            fallback_chunk_total=0,
        )

    try:
        pages = paginate(markdown)
    except Exception:
        return _fallback_to_text(
            app_id=app_id,
            app_secret=app_secret,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            markdown=markdown,
            source_cursor=0,
            card_page_index=1,
            card_page_total=0,
            trigger_category=FeishuDeliveryErrorCategory.INTERNAL,
            confirmed_message_ids=[],
        )

    if not pages:
        return _fallback_to_text(
            app_id=app_id,
            app_secret=app_secret,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            markdown=markdown,
            source_cursor=0,
            card_page_index=1,
            card_page_total=0,
            trigger_category=FeishuDeliveryErrorCategory.INTERNAL,
            confirmed_message_ids=[],
        )

    try:
        client = FeishuCardClient(app_id, app_secret)
    except Exception:
        return _fallback_to_text(
            app_id=app_id,
            app_secret=app_secret,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            markdown=markdown,
            source_cursor=0,
            card_page_index=1,
            card_page_total=len(pages),
            trigger_category=FeishuDeliveryErrorCategory.TRANSPORT,
            confirmed_message_ids=[],
        )

    confirmed_message_ids: list[str] = []
    source_cursor = 0
    failure_category: FeishuDeliveryErrorCategory | None = None
    failure_page_index = 1

    for page in pages:
        failure_page_index = page.index
        if page.source_start != source_cursor:
            failure_category = FeishuDeliveryErrorCategory.INTERNAL
            break
        try:
            spec = render_card_spec(page.text, streaming=False)
        except Exception:
            failure_category = FeishuDeliveryErrorCategory.INTERNAL
            break
        try:
            card_id = client.create_card(spec)
        except Exception as error:
            failure_category = _card_error_category(
                error,
                stage=FeishuCardCallStage.CREATE_CARD,
            )
            break
        if not card_id:
            failure_category = FeishuDeliveryErrorCategory.DEFINITE_REJECTION
            break
        try:
            message_id = client.send_card(
                receive_id,
                card_id,
                receive_id_type=receive_id_type,
            )
        except Exception as error:
            failure_category = _card_error_category(
                error,
                stage=FeishuCardCallStage.SEND_CARD,
            )
            break
        if not message_id:
            failure_category = FeishuDeliveryErrorCategory.DELIVERY_UNCERTAIN
            break
        confirmed_message_ids.append(message_id)
        source_cursor = page.source_end
    else:
        return _summary(
            FeishuDocumentDeliveryResult(
                status=FeishuDeliveryStatus.SUCCESS,
                attempted=True,
                confirmed_message_ids=tuple(confirmed_message_ids),
                delivery_mode=FeishuDeliveryMode.CARDS,
                error_category=None,
                error="",
            ),
            receive_id_type=receive_id_type,
            card_page_total=len(pages),
            fallback_chunk_total=0,
        )

    return _fallback_to_text(
        app_id=app_id,
        app_secret=app_secret,
        receive_id=receive_id,
        receive_id_type=receive_id_type,
        markdown=markdown,
        source_cursor=source_cursor,
        card_page_index=failure_page_index,
        card_page_total=len(pages),
        trigger_category=failure_category or FeishuDeliveryErrorCategory.INTERNAL,
        confirmed_message_ids=confirmed_message_ids,
    )


__all__ = ["FeishuDocumentDeliveryResult", "deliver_feishu_document"]
