"""Feishu background shutdown closes active turn outputs."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from gateway.transports.feishu import turn_output
from gateway.transports.feishu.background import FeishuGatewayBackground
from gateway.transports.feishu.turn_output import FeishuTurnOutput, FeishuTurnOutputRegistry


def test_background_stop_cancels_and_finishes_an_active_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Session:
        def __init__(self, **_kwargs: Any) -> None:
            self.finished = 0

        def start(self) -> None:
            pass

        def update(self, _text: str) -> None:
            pass

        def finish(self) -> None:
            self.finished += 1

    class _Thread:
        def join(self, timeout: float) -> None:
            _ = timeout

        def is_alive(self) -> bool:
            return False

    class _Closeable:
        def close(self) -> None:
            pass

    class _Executor:
        def shutdown(self, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(turn_output, "FeishuCardClient", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(turn_output, "CardStreamSession", _Session)
    registry = FeishuTurnOutputRegistry()
    output = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x", output_registry=registry)
    cancel = threading.Event()
    output.turn_cancel = cancel
    output.set_tool_status("working")
    background = FeishuGatewayBackground(
        thread=_Thread(),  # type: ignore[arg-type]
        stop_event=threading.Event(),
        ready_event=threading.Event(),
        bindings=_Closeable(),  # type: ignore[arg-type]
        executor=_Executor(),  # type: ignore[arg-type]
        output_registry=registry,
    )

    assert background.stop(timeout=0.1) is True
    assert cancel.is_set()
    assert output._session is not None
    assert output._session.finished == 1
