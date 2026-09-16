"""The streaming session: throttling, the monotonic sequence, and the ladder."""

from __future__ import annotations

from typing import Any

from config.constants import FEISHU_CARD_TRUNCATED_MARKER
from gateway.transports.feishu.card_stream import CardStreamSession
from integrations.feishu.card_client import FeishuStreamRejected

_TABLE = "| a | b |\n| --- | --- |\n| 1 | 2 |"


class _FakeClient:
    """Records calls and can be told to reject with a given code."""

    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.updates: list[tuple[str, str, str, int]] = []
        self.sent: list[tuple[str, str]] = []
        self.closed: list[tuple[str, int]] = []
        self.received_id_types: list[str] = []
        self.reject_code: int | None = None
        self.reject_close = False
        self.reject_update = False
        self.reject_create = False

    def create_card(self, spec: dict[str, object]) -> str:
        # A stream error code arrives on element updates, never on card creation,
        # so creating the fallback cards must still succeed.
        if self.reject_create:
            raise RuntimeError("cardkit create failed")
        self.created.append(spec)
        return f"c_{len(self.created)}"

    def update_element(self, card_id: str, element_id: str, content: str, sequence: int) -> None:
        if self.reject_code is not None:
            raise FeishuStreamRejected(self.reject_code, "boom")
        if self.reject_update:
            raise RuntimeError("cardkit update failed")
        self.updates.append((card_id, element_id, content, sequence))

    def close_streaming(self, card_id: str, sequence: int) -> None:
        self.closed.append((card_id, sequence))
        if self.reject_close:
            raise FeishuStreamRejected(300309, "boom")

    def send_card(self, chat_id: str, card_id: str, *, receive_id_type: str = "chat_id") -> str:
        self.received_id_types.append(receive_id_type)
        self.sent.append((chat_id, card_id))
        return f"om_{len(self.sent)}"


def _session(client: _FakeClient, **kwargs: Any) -> tuple[CardStreamSession, list[float]]:
    """Build a session whose clock the test can advance."""
    now = [0.0]

    def _clock() -> float:
        return now[0]

    session = CardStreamSession(client=client, chat_id="oc_chat", clock=_clock, **kwargs)
    return session, now


def test_start_creates_a_streaming_card_and_sends_it() -> None:
    client = _FakeClient()
    session, _now = _session(client)
    session.start()

    assert client.created[0]["config"]["streaming_mode"] is True  # type: ignore[index]
    assert client.sent == [("oc_chat", "c_1")]
    assert session.card_id == "c_1"
    assert session.message_id == "om_1"


def test_updates_are_throttled_and_sequence_is_monotonic() -> None:
    client = _FakeClient()
    session, now = _session(client, min_interval=10.0, min_chars=1_000_000)
    session.start()

    session.update("a")
    assert len(client.updates) == 1  # the first content appears immediately

    session.update("ab")
    assert len(client.updates) == 1  # below both thresholds, so throttled

    now[0] = 60.0
    session.update("abc")
    assert len(client.updates) == 2

    sequences = [entry[3] for entry in client.updates]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))


def test_content_is_sent_in_full_not_as_a_delta() -> None:
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1)
    session.start()

    session.update("hello")
    assert client.updates[-1][2] == "hello"


def test_crossing_the_budget_closes_streaming_and_overflows_into_further_cards() -> None:
    """Level 1 then level 2 — the exit criterion for content over 30KB."""
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1, budget=2_000)
    session.start()

    session.update("\n\n".join("x" * 400 for _ in range(40)))

    assert client.closed, "streaming must be closed once the budget is crossed"
    assert len(client.created) > 1, "the remainder must go into further cards"
    assert len(client.sent) > 1
    assert session.degraded is True


def test_overflow_cards_are_not_streaming() -> None:
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1, budget=2_000)
    session.start()

    session.update("\n\n".join("x" * 400 for _ in range(40)))

    assert client.created[1]["config"]["streaming_mode"] is False  # type: ignore[index]


def test_six_tables_overflow_the_card_even_though_the_bytes_fit() -> None:
    """Level 1's table dimension — the byte budget alone would let these through."""
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1)
    session.start()

    session.update("\n\n".join(_TABLE for _ in range(6)))

    assert session.degraded is True
    assert len(client.created) > 1, "the tables must go into further cards"
    assert client.closed, "the streaming card must be closed"


def test_a_stream_error_falls_back_to_a_complete_non_streaming_card() -> None:
    """Level 3: the stream is dead, so deliver the whole text another way."""
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1)
    session.start()
    client.reject_code = 300317

    session.update("final answer")

    assert session.degraded is True
    assert client.closed, "the dead card must still be closed"
    assert len(client.created) > 1, "the text must be re-delivered as fresh cards"


def test_a_plain_cardkit_error_degrades_instead_of_escaping() -> None:
    """An oversize card or Feishu's table cap 11310 is a plain error, not a stream code."""
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1)
    session.start()
    client.reject_update = True

    session.update("final answer")

    assert session.degraded is True
    assert client.closed, "the stream must still be closed"
    assert len(client.created) > 1, "the text must be re-delivered as fresh cards"


def test_a_failed_fallback_card_still_leaves_the_session_terminal() -> None:
    """Losing the last-resort card must not leave a session that looks alive."""
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1, budget=2_000)
    session.start()
    client.reject_create = True

    session.update("\n\n".join("x" * 400 for _ in range(40)))

    assert session.degraded is True
    assert client.closed, "the streaming card was already closed before the overflow"
    del client.updates[:]
    session.update("more")
    assert not client.updates, "a terminal session must not stream again"


def test_an_oversize_code_block_first_still_loses_nothing() -> None:
    """A fence too big to cut literally must reach the cards whole.

    Level 1 gets no prefix for it, so the session drops to level 3 and the
    paginator reopens the fence on each card instead of shipping a broken one.
    """
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1, budget=2_000)
    session.start()
    code = [f"line_{i} = {i}" for i in range(600)]
    session.update("```python\n" + "\n".join(code) + "\n```")

    assert session.degraded is True
    assert client.closed, "the streaming card must be closed"
    cards = [spec["body"]["elements"][0]["content"] for spec in client.created[1:]]  # type: ignore[index]
    assert len(cards) > 1
    for card in cards:
        lines = card.splitlines()
        assert lines[0] == "```python", "every card must reopen the fence"
        assert lines[-1] == "```", "every card must close its own fence"
    assert [line for card in cards for line in card.splitlines()[1:-1]] == code


def test_text_arriving_after_the_budget_trips_still_reaches_the_user() -> None:
    """A turn streams tokens, so most of a long answer arrives *after* the trip.

    Every other test here hands `update` the whole document at once — the one
    shape where nothing is left to arrive once the card fills.
    """
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1, budget=2_000)
    session.start()
    text = "\n\n".join("x" * 400 for _ in range(40))

    for end in range(400, len(text), 400):
        session.update(text[:end])
    session.update(text)
    session.finish()

    streamed = client.updates[-1][2] if client.updates else ""
    body = streamed.replace(FEISHU_CARD_TRUNCATED_MARKER, "")
    overflow = "".join(
        spec["body"]["elements"][0]["content"]
        for spec in client.created[1:]  # type: ignore[index]
    )
    assert "".join((body + overflow).split()) == "".join(text.split())


def test_the_overflow_continuation_is_paginated_not_dribbled() -> None:
    """Delivering each delta as its own card would work and read terribly.

    The text is 40 deltas against a 2,000-byte budget, so one card per delta
    would be ~40 cards. Whole cards are an order of magnitude fewer, and the
    bound is loose on purpose — this pins the shape, not the exact packing.
    """
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1, budget=2_000)
    session.start()
    text = "\n\n".join("x" * 400 for _ in range(40))

    for end in range(400, len(text), 400):
        session.update(text[:end])
    session.update(text)
    session.finish()

    assert len(client.created) < 20, f"continuation dribbled into {len(client.created)} cards"


def test_finish_closes_streaming_exactly_once() -> None:
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1)
    session.start()
    session.update("done")

    session.finish()
    session.finish()

    assert len(client.closed) == 1


def test_a_close_that_fails_at_the_end_does_not_raise_or_redeliver() -> None:
    """A stream that dies before finish() is cosmetic — never re-send the text."""
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1)
    session.start()
    session.update("done")
    client.reject_close = True

    session.finish()
    session.finish()

    assert len(client.closed) == 1, "a failed close must terminate, not be retried"
    assert session.degraded is False, "the text is already on screen; do not re-deliver"
    assert len(client.created) == 1, "no fallback cards after a failed close"


def test_text_survives_the_ladder_without_loss() -> None:
    """Whatever the path, every character must reach some card."""
    client = _FakeClient()
    session, _now = _session(client, min_interval=0.0, min_chars=1, budget=2_000)
    session.start()
    text = "\n\n".join(f"paragraph {i} " + "z" * 300 for i in range(40))

    session.update(text)

    rendered = "".join(entry[2] for entry in client.updates)
    overflow = "".join(spec["body"]["elements"][0]["content"] for spec in client.created[1:])  # type: ignore[index]
    body = rendered.replace(FEISHU_CARD_TRUNCATED_MARKER, "")
    assert text.startswith(body), "the streamed text must be a prefix of the source"
    # Whitespace-free: paginate drops the "\n\n" between pages, so a token
    # comparison would see merged neighbours. This form catches loss *and*
    # duplication, which the old length check could not.
    assert "".join((body + overflow).split()) == "".join(text.split())
