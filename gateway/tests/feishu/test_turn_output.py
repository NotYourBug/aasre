"""Tests for the Feishu turn output."""

from gateway.transports.feishu.turn_output import FeishuTurnOutput


def test_finalize_sends_text(monkeypatch):
    sent: list[str] = []

    def _fake_send(_app_id: str, _app_secret: str, _chat_id: str, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", _fake_send)
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")
    out.finalize("hello world")
    assert sent == ["hello world"]


def test_finalize_blank_answer_does_not_send(monkeypatch):
    calls: list[str] = []

    def _fake_send(_app_id: str, _app_secret: str, _chat_id: str, text: str) -> None:
        calls.append(text)

    monkeypatch.setattr("gateway.transports.feishu.turn_output._send_text", _fake_send)
    out = FeishuTurnOutput(app_id="a", app_secret="s", chat_id="oc_x")
    out.finalize("")
    out.finalize("   ")
    assert calls == []
