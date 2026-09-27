"""Opaque feedback capability persistence and expiry."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gateway.transports.feishu.feedback_authority import FeedbackAuthorityStore


def test_restart_digest_only_and_concurrent_consumption(tmp_path: Path) -> None:
    path = tmp_path / "authority.jsonl"
    store = FeedbackAuthorityStore(path)
    store.register(
        token="opaque-token", requester_open_id="actor", chat_id="chat", message_id="final"
    )
    raw = path.read_text()
    assert "opaque-token" not in raw
    assert hashlib.sha256(b"opaque-token").hexdigest() in raw
    restarted = FeedbackAuthorityStore(path)
    authority = restarted.find("opaque-token")
    assert authority is not None and authority.message_id == "final"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: restarted.consume("opaque-token"), range(20)))
    assert sum(results) == 1
    assert restarted.find("opaque-token") is None


def test_expiry_and_invalidated_token_fail_closed(tmp_path: Path) -> None:
    now = [100.0]
    store = FeedbackAuthorityStore(tmp_path / "authority.jsonl", clock=lambda: now[0])
    authority = store.register(
        token="token", requester_open_id="actor", chat_id="chat", message_id="final"
    )
    now[0] = authority.expires_at
    assert store.find("token") is None
    store.register(token="other", requester_open_id="actor", chat_id="chat", message_id="second")
    assert store.invalidate("other")
    assert store.find("other") is None


def test_corrupt_authority_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "authority.jsonl"
    path.write_text('{"partial":')
    store = FeedbackAuthorityStore(path)
    with pytest.raises(ValueError):
        store.find("token")
    with pytest.raises(ValueError):
        store.register(token="token", requester_open_id="actor", chat_id="chat", message_id="final")
    assert path.read_text() == '{"partial":'
