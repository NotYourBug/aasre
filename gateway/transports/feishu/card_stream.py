"""Streaming card session for the Feishu transport.

Implements the five-level degradation ladder: a streaming card updated with
monotonic ``sequence`` updates, a byte-and-table budget that closes the stream
and overflows the remainder into further cards, and a fallback to complete
non-streaming cards when the stream itself reports an error. A client below
Feishu 7.20 cannot render any of it, and the server cannot detect that — the
bottom of the ladder is S4's plain-text chunking, not this module.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from config.constants import (
    FEISHU_CARD_BUDGET_BYTES,
    FEISHU_CARD_MAX_TABLES,
    FEISHU_CARD_TRUNCATED_MARKER,
    FEISHU_STREAM_ELEMENT_ID,
    FEISHU_STREAM_MIN_CHARS,
    FEISHU_STREAM_MIN_INTERVAL_SECONDS,
)
from integrations.feishu import paginate, render_card_spec, spec_bytes, table_count
from integrations.feishu.card_client import FeishuStreamRejected

logger = logging.getLogger(__name__)


class CardStreamSession:
    """Render markdown into a CardKit card, degrading when it no longer fits.

    One session drives one reply. ``update`` takes the whole text so far, not a
    delta — CardKit replaces element content rather than appending to it.
    """

    def __init__(
        self,
        *,
        client: Any,
        chat_id: str,
        receive_id_type: str = "chat_id",
        budget: int = FEISHU_CARD_BUDGET_BYTES,
        min_interval: float = FEISHU_STREAM_MIN_INTERVAL_SECONDS,
        min_chars: int = FEISHU_STREAM_MIN_CHARS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._chat_id = chat_id
        self._receive_id_type = receive_id_type
        self._budget = budget
        self._min_interval = min_interval
        self._min_chars = min_chars
        self._clock = clock

        self._card_id = ""
        self._message_id = ""
        self._sequence = 0
        self._rendered = ""
        self._pending = ""
        self._last_flush = 0.0
        self._started = False
        self._closed = False
        self._degraded = False

    @property
    def card_id(self) -> str:
        return self._card_id

    @property
    def message_id(self) -> str:
        return self._message_id

    @property
    def degraded(self) -> bool:
        return self._degraded

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def start(self) -> None:
        """Create the card entity and post it as an interactive message."""
        spec = render_card_spec("", streaming=True)
        self._card_id = self._client.create_card(spec)
        self._message_id = self._client.send_card(
            self._chat_id, self._card_id, receive_id_type=self._receive_id_type
        )
        self._started = True
        self._last_flush = self._clock()

    def update(self, full_text: str) -> None:
        """Render *full_text*, throttled; degrade when it no longer fits."""
        if not self._started or self._closed:
            return
        self._pending = full_text
        if self._card_fits(full_text):
            if self._should_flush(full_text):
                self._flush(full_text)
            return
        self._overflow(full_text)

    def _card_fits(self, text: str) -> bool:
        return (
            spec_bytes(render_card_spec(text, streaming=True)) <= self._budget
            and table_count(text) <= FEISHU_CARD_MAX_TABLES
        )

    def _should_flush(self, text: str) -> bool:
        if not self._rendered:
            return True
        if len(text) - len(self._rendered) >= self._min_chars:
            return True
        return self._clock() - self._last_flush >= self._min_interval

    def _flush(self, text: str) -> None:
        try:
            self._client.update_element(
                self._card_id, FEISHU_STREAM_ELEMENT_ID, text, self._next_sequence()
            )
        except FeishuStreamRejected:
            logger.warning("Feishu card stream rejected; falling back to complete cards")
            self._render_error_fallback(text)
            return
        self._rendered = text
        self._last_flush = self._clock()

    def _safe_cut(self, text: str) -> str:
        """Longest prefix of *text* that still fits the card."""
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if self._card_fits(text[:mid]):
                low = mid
            else:
                high = mid - 1
        return text[:low]

    def _overflow(self, text: str) -> None:
        """Level 1 then level 2: close the card, send the remainder as cards."""
        kept = self._safe_cut(text)
        if not kept:
            self._render_error_fallback(text)
            return
        try:
            self._client.update_element(
                self._card_id,
                FEISHU_STREAM_ELEMENT_ID,
                kept + FEISHU_CARD_TRUNCATED_MARKER,
                self._next_sequence(),
            )
            self._close_current()
        except FeishuStreamRejected:
            self._render_error_fallback(text)
            return
        self._rendered = kept
        self._send_overflow(text[len(kept) :])

    def _send_overflow(self, remainder: str) -> None:
        for page in paginate(remainder, budget=self._budget):
            spec = render_card_spec(page.text, streaming=False)
            card_id = self._client.create_card(spec)
            self._client.send_card(self._chat_id, card_id, receive_id_type=self._receive_id_type)
        self._degraded = True
        self._closed = True

    def _render_error_fallback(self, text: str) -> None:
        """Level 3: the stream is unusable — deliver everything as plain cards."""
        try:
            self._close_current()
        except FeishuStreamRejected:
            logger.warning("Feishu card stream could not even be closed")
        self._send_overflow(text)

    def _close_current(self) -> None:
        if not self._card_id:
            return
        self._client.close_streaming(self._card_id, self._next_sequence())

    def finish(self) -> None:
        """Flush the last text and close streaming, exactly once."""
        if not self._started or self._closed:
            return
        if self._pending and self._pending != self._rendered and self._card_fits(self._pending):
            self._flush(self._pending)
        if self._closed:
            return
        try:
            self._close_current()
        except FeishuStreamRejected:
            logger.warning("Feishu card stream could not be closed")
        self._closed = True
