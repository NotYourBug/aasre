"""Feishu worker stop: a stop watcher unblocks Client.start() so the thread exits.

``lark_oapi.ws.Client.start()`` blocks forever in ``run_until_complete(_select())``
and exposes no stop method, so without this the worker can never be joined and
its ``finally`` (which denies pending approvals) never runs.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.feishu import worker as worker_mod
from gateway.transports.feishu.pending_approvals import PendingApprovals
from gateway.transports.feishu.settings import FeishuGatewaySettings
from gateway.transports.feishu.worker import _ReadyOnConnectClient


async def _fake_connect(self: _ReadyOnConnectClient) -> None:
    self._ready_event.set()


class _ReturningClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def start(self) -> None:
        return None


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


def test_gateway_shutdown_drains_transport_before_closing_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = ApprovalBroker()
    pending = PendingApprovals()
    approval_id = broker.create(platform="feishu", chat_id="oc_chat-1")
    pending.register(
        broker_approval_id=approval_id,
        approve_token="approve-token",
        deny_token="deny-token",
        requester_open_id="ou_user-1",
        chat_id="oc_chat-1",
        tool_name="write_tool",
        expires_at=float("inf"),
    )
    order: list[str] = []
    original_drain = pending.drain
    original_close = broker.close

    def _drain() -> list[str]:
        order.append("drain")
        return original_drain()

    def _close() -> int:
        order.append("close")
        return original_close()

    def _broker_factory() -> ApprovalBroker:
        return broker

    def _pending_factory() -> PendingApprovals:
        return pending

    monkeypatch.setattr(pending, "drain", _drain)
    monkeypatch.setattr(broker, "close", _close)
    monkeypatch.setattr(worker_mod, "ApprovalBroker", _broker_factory)
    monkeypatch.setattr(worker_mod, "PendingApprovals", _pending_factory)
    monkeypatch.setattr(worker_mod, "_ReadyOnConnectClient", _ReturningClient)

    worker_mod.run_feishu_gateway_thread(
        settings=FeishuGatewaySettings(
            app_id="app", app_secret="secret", allowed_open_ids=["ou_user-1"]
        ),
        logger=logging.getLogger("gateway.test"),
        handler=MagicMock(),
        bindings=MagicMock(),
        executor=MagicMock(spec=ThreadPoolExecutor),
        stop_event=threading.Event(),
        ready_event=threading.Event(),
        output_registry=MagicMock(),
    )

    assert order == ["drain", "close"]
    assert broker.wait(approval_id, timeout=0.0) == (False, "")
