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
from dataclasses import dataclass
from typing import Any

from config.constants import (
    FEISHU_CARD_BUDGET_BYTES,
    FEISHU_CARD_MAX_TABLES,
    FEISHU_CARD_TRUNCATED_MARKER,
    FEISHU_STREAM_ELEMENT_ID,
    FEISHU_STREAM_MIN_CHARS,
    FEISHU_STREAM_MIN_INTERVAL_SECONDS,
)
from integrations.feishu import (
    paginate,
    render_card_spec,
    safe_prefix,
    spec_bytes,
    table_count,
)
from integrations.feishu.card_client import FeishuStreamRejected

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FinalCardTarget:
    """A completely delivered card and its next mutation sequence."""

    card_id: str
    message_id: str
    append_sequence: int


class CardStreamSession:
    """Render markdown into a CardKit card, degrading when it no longer fits.

    One session drives one reply. ``update`` takes the whole text so far, not a
    delta — CardKit replaces element content rather than appending to it.

    A cardkit failure never propagates out of ``update``: whatever the API
    rejects, the session closes the stream and re-delivers the text as complete
    non-streaming cards, reporting ``degraded``. ``finish()`` stays the caller's
    obligation — nothing here closes a healthy stream on its own.
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
        self._delivered = 0
        self._last_flush = 0.0
        self._started = False
        self._closed = False
        self._degraded = False
        self._delivery_failed = False
        self._close_failed = False
        self._last_complete: FinalCardTarget | None = None
        self._finished = False
        self._final_target: FinalCardTarget | None = None

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
        if not self._card_id or not self._message_id:
            raise ValueError("Card delivery requires nonempty IDs")
        self._started = True
        self._last_flush = self._clock()

    def update(self, full_text: str) -> None:
        """Render *full_text*, throttled; degrade when it no longer fits.

        After the card fills, later text is not dropped — a turn keeps producing
        past the trip, and that is most of a long answer. It is delivered as
        further cards instead, in whole-card batches.
        """
        if not self._started or self._finished:
            return
        self._pending = full_text
        if self._closed:
            self._drain(full_text)
            return
        if self._card_fits(full_text):
            if self._should_flush(full_text):
                self._flush(full_text)
            return
        self._overflow(full_text)

    def _drain(self, full_text: str) -> None:
        """Send text that arrived after the stream was closed, once a card is worth it.

        Waiting for a whole budget keeps the continuation page-sized rather than
        one small card per delta. Whatever is left over at the end is not
        stranded: ``finish()`` flushes it.
        """
        if self._delivery_failed:
            return
        tail = full_text[self._delivered :]
        if spec_bytes(render_card_spec(tail, streaming=False)) < self._budget:
            return
        self._send_overflow(tail, upto=len(full_text))

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
        except Exception:
            logger.exception("Feishu card update failed; falling back to complete cards")
            self._render_error_fallback(text)
            return
        self._rendered = text
        # The card now shows this much, which is what the cursor tracks: a plain
        # close delivers everything on the card, so `_flush_tail` must find
        # nothing left once it lands.
        self._delivered = len(text)
        self._last_flush = self._clock()

    def _safe_cut(self, text: str) -> str:
        """Longest prefix of *text* that fits, backed off to a markdown block boundary."""
        return safe_prefix(
            text,
            budget=self._budget,
            max_tables=FEISHU_CARD_MAX_TABLES,
            suffix=FEISHU_CARD_TRUNCATED_MARKER,
            streaming=True,
        )

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
        except Exception:
            logger.exception("Feishu card overflow failed; falling back to complete cards")
            self._render_error_fallback(text)
            return
        self._rendered = kept
        self._send_overflow(text[len(kept) :], upto=len(text))

    def _send_overflow(self, remainder: str, *, upto: int) -> None:
        """Deliver *remainder* as complete cards and advance the delivery cursor.

        The stream is already closed by the time this runs, so the session is
        over whatever happens next — a page that cannot be created is logged,
        not retried, and never leaves the session looking alive. ``upto`` is how
        much of the document this call settles; the cursor moves only once every
        page has landed, so a half-delivered batch is never mistaken for a
        delivered one and sliced past on the next drain.
        """
        try:
            for page in paginate(remainder, budget=self._budget):
                spec = render_card_spec(page.text, streaming=False)
                card_id = self._client.create_card(spec)
                message_id = self._client.send_card(
                    self._chat_id, card_id, receive_id_type=self._receive_id_type
                )
                if not card_id or not message_id:
                    raise ValueError("Card delivery requires nonempty IDs")
                self._last_complete = FinalCardTarget(card_id, message_id, 1)
        except Exception:
            logger.exception("Feishu overflow cards could not all be delivered")
            self._delivery_failed = True
        else:
            self._delivered = upto
        finally:
            self._degraded = True
            self._closed = True

    def _render_error_fallback(self, text: str) -> None:
        """Level 3: the stream is unusable — deliver everything as plain cards."""
        try:
            self._close_current()
        except Exception:
            logger.exception("Feishu card stream could not even be closed")
        self._send_overflow(text, upto=len(text))

    def _close_current(self) -> None:
        if not self._card_id:
            return
        try:
            self._client.close_streaming(self._card_id, self._next_sequence())
        except Exception:
            self._close_failed = True
            raise

    def finish(self) -> FinalCardTarget | None:
        """Finish delivery once and expose only a certain final card."""
        if self._finished:
            return self._final_target
        self._finish_delivery()
        self._finished = True
        if (
            self._started
            and not self._delivery_failed
            and not self._close_failed
            and self._pending.strip()
            and self._delivered >= len(self._pending.rstrip())
        ):
            self._final_target = self._last_complete or FinalCardTarget(
                self._card_id, self._message_id, self._sequence + 1
            )
        return self._final_target

    def _finish_delivery(self) -> None:
        """Flush the last text and close streaming, exactly once."""
        if not self._started:
            return
        if self._closed:
            self._flush_tail()
            return
        if self._pending and self._pending != self._rendered and self._card_fits(self._pending):
            self._flush(self._pending)
        if self._closed:
            self._flush_tail()
            return
        try:
            self._close_current()
        except Exception:
            logger.exception("Feishu card stream could not be closed")
        finally:
            self._closed = True

    def _flush_tail(self) -> None:
        """Deliver the sub-card remainder the drain deliberately held back.

        Without this the last few kilobytes of a long answer — too small to have
        triggered a drain — would never be sent at all.
        """
        if self._delivery_failed:
            return
        tail = self._pending[self._delivered :]
        if tail.strip():
            self._send_overflow(tail, upto=len(self._pending))
