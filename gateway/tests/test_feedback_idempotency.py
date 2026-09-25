"""Durable feedback deduplication under retries and competing processes."""

from __future__ import annotations

import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gateway.core.storage.feedback import FeedbackWriteResult, append_feedback_entry_once


def _write(path: Path, actor: str = "actor") -> FeedbackWriteResult:
    return append_feedback_entry_once(
        {"message_id": "answer", "user_id": actor, "verdict": "good"},
        idempotency_fields=("message_id", "user_id"),
        path=path,
        lock_timeout_seconds=2.0,
    )


def test_retry_threads_and_restart_share_durable_key(tmp_path: Path) -> None:
    path = tmp_path / "feedback.jsonl"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_write, [path] * 20))
    assert results.count(FeedbackWriteResult.WRITTEN) == 1
    assert results.count(FeedbackWriteResult.DUPLICATE) == 19
    assert _write(path) is FeedbackWriteResult.DUPLICATE
    assert _write(path, "other") is FeedbackWriteResult.WRITTEN
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_spawned_processes_share_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "feedback.jsonl"
    context = multiprocessing.get_context("spawn")
    with context.Pool(2) as pool:
        results = pool.map(_write, [path] * 4)
    assert results.count(FeedbackWriteResult.WRITTEN) == 1
    assert results.count(FeedbackWriteResult.DUPLICATE) == 3
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize("content", ['{"message_id":', "[]\n", '{"message_id":"a"}\n'])
def test_corruption_never_becomes_an_empty_store(tmp_path: Path, content: str) -> None:
    path = tmp_path / "feedback.jsonl"
    path.write_text(content, encoding="utf-8")
    assert _write(path) is FeedbackWriteResult.FAILED
    assert path.read_text(encoding="utf-8") == content


def test_uncertain_fsync_is_not_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gateway.core.storage.feedback.idempotent as module

    def fail_sync(_fd: int) -> None:
        raise OSError("sensitive detail")

    monkeypatch.setattr(module.os, "fsync", fail_sync)
    assert _write(tmp_path / "feedback.jsonl") is FeedbackWriteResult.FAILED
