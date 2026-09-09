"""Feishu worker stop: a stop watcher unblocks Client.start() so the thread exits.

``lark_oapi.ws.Client.start()`` blocks forever in ``run_until_complete(_select())``
and exposes no stop method, so without this the worker can never be joined and
its ``finally`` (which denies pending approvals) never runs.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from gateway.transports.feishu import worker as worker_mod
from gateway.transports.feishu.worker import _ReadyOnConnectClient


async def _fake_connect(self: _ReadyOnConnectClient) -> None:
    self._ready_event.set()


def test_watch_stop_sets_stopped_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fresh_loop = asyncio.new_event_loop()
    monkeypatch.setattr(worker_mod, "_ws_loop", fresh_loop)
    stop = threading.Event()
    stop.set()
    client = _ReadyOnConnectClient(
        "cli_app", "s_secret", ready_event=threading.Event(), stop_event=stop
    )
    try:
        fresh_loop.run_until_complete(client._watch_stop())
        assert client._stopped is True
    finally:
        fresh_loop.close()


def test_start_swallows_runtime_error_only_after_clean_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SinkLoop:
        def create_task(self, coro: object) -> object:
            coro.close()  # type: ignore[attr-defined]
            return coro

    monkeypatch.setattr(worker_mod, "_ws_loop", _SinkLoop())
    client = _ReadyOnConnectClient(
        "cli_app", "s_secret", ready_event=threading.Event(), stop_event=threading.Event()
    )

    def _boom(_self: object) -> None:
        raise RuntimeError("Event loop stopped before Future completed.")

    monkeypatch.setattr(worker_mod.Client, "start", _boom)

    client._stopped = True
    client.start()  # a deliberate stop is not an error

    client._stopped = False
    with pytest.raises(RuntimeError):
        client.start()


def test_start_returns_after_stop_event_set(monkeypatch: pytest.MonkeyPatch) -> None:
    fresh_loop = asyncio.new_event_loop()
    monkeypatch.setattr(worker_mod, "_ws_loop", fresh_loop)
    monkeypatch.setattr("lark_oapi.ws.client.loop", fresh_loop)
    monkeypatch.setattr(_ReadyOnConnectClient, "_connect", _fake_connect)

    ready = threading.Event()
    stop = threading.Event()
    client = _ReadyOnConnectClient("cli_app", "s_secret", ready_event=ready, stop_event=stop)
    finished = threading.Event()

    def _run() -> None:
        try:
            client.start()
        finally:
            finished.set()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        assert ready.wait(timeout=5.0), "client never connected"
        stop.set()
        assert finished.wait(timeout=10.0), "start() did not return after stop"
    finally:
        stop.set()
        thread.join(timeout=5.0)

    assert not thread.is_alive()

    # Reap the pending ``_select``/``_ping_loop`` tasks so the loop closes cleanly.
    tasks = asyncio.all_tasks(fresh_loop)
    for task in tasks:
        task.cancel()
    fresh_loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
    fresh_loop.close()
