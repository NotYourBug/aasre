"""Tests for integrations.feishu.scheduled_delivery."""

from __future__ import annotations

import pytest

from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.feishu.credentials import FeishuChatCredentials
from integrations.feishu.delivery_types import (
    FeishuDeliveryErrorCategory,
    FeishuDeliveryMode,
    FeishuDeliveryStatus,
)
from integrations.feishu.document_delivery import FeishuDocumentDeliveryResult
from integrations.feishu.scheduled_delivery import FeishuScheduledDelivery


def _task(*, chat_id: str = "", params: dict[str, str] | None = None) -> ScheduledTask:
    return ScheduledTask(
        kind=TaskKind.DAILY_SUMMARY,
        cron="0 9 * * *",
        provider=Provider.FEISHU,
        chat_id=chat_id,
        params=params or {},
    )


def _chat_creds(**overrides: str) -> FeishuChatCredentials:
    values = {
        "app_id": "cli_chat",
        "app_secret": "s_chat",
        "receive_id": "",
        "receive_id_type": "chat_id",
    }
    values.update(overrides)
    return FeishuChatCredentials(**values)


def _result(
    status: FeishuDeliveryStatus,
    *,
    ids: tuple[str, ...] = (),
) -> FeishuDocumentDeliveryResult:
    successful = status in {
        FeishuDeliveryStatus.SUCCESS,
        FeishuDeliveryStatus.DEGRADED_SUCCESS,
    }
    return FeishuDocumentDeliveryResult(
        status=status,
        attempted=status is not FeishuDeliveryStatus.SKIPPED,
        confirmed_message_ids=ids,
        delivery_mode=FeishuDeliveryMode.CARDS if successful else FeishuDeliveryMode.NONE,
        error_category=None if successful else FeishuDeliveryErrorCategory.TRANSPORT,
        error="" if successful else "safe owner error",
    )


def _install_fake_document_delivery(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: FeishuDocumentDeliveryResult | None = None,
) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    def _fake_deliver(**kwargs: str) -> FeishuDocumentDeliveryResult:
        calls.append(kwargs)
        return result or _result(FeishuDeliveryStatus.SUCCESS, ids=("om_first",))

    monkeypatch.setattr(
        "integrations.feishu.document_delivery.deliver_feishu_document",
        _fake_deliver,
    )
    return calls


@pytest.mark.parametrize(
    ("task_chat_id", "params", "base", "expected_target"),
    [
        (
            "oc_task",
            {"receive_id": "ou_param", "receive_id_type": "open_id"},
            _chat_creds(receive_id="ou_config", receive_id_type="open_id"),
            ("oc_task", "chat_id"),
        ),
        (
            "",
            {"receive_id": "ou_param", "receive_id_type": "open_id"},
            _chat_creds(),
            ("ou_param", "open_id"),
        ),
        (
            "",
            {"receive_id": "oc_param"},
            _chat_creds(receive_id="ou_config", receive_id_type="open_id"),
            ("oc_param", "chat_id"),
        ),
        (
            "",
            {},
            _chat_creds(receive_id="ou_config", receive_id_type="open_id"),
            ("ou_config", "open_id"),
        ),
    ],
)
def test_destination_and_type_are_resolved_as_an_atomic_pair(
    monkeypatch: pytest.MonkeyPatch,
    task_chat_id: str,
    params: dict[str, str],
    base: FeishuChatCredentials,
    expected_target: tuple[str, str],
) -> None:
    monkeypatch.setattr("integrations.feishu.load_chat_credentials_from_env", lambda: base)
    calls = _install_fake_document_delivery(monkeypatch)

    outcome = FeishuScheduledDelivery().deliver(
        _task(chat_id=task_chat_id, params=params),
        "scheduled **Markdown**",
    )

    assert outcome == (True, "", "om_first")
    assert len(calls) == 1
    assert (calls[0]["receive_id"], calls[0]["receive_id_type"]) == expected_target


@pytest.mark.parametrize(
    ("task", "base"),
    [
        (
            _task(params={"receive_id_type": "open_id"}),
            _chat_creds(receive_id="ou_config", receive_id_type="open_id"),
        ),
        (_task(), _chat_creds()),
        (_task(), _chat_creds(receive_id="oc_config", receive_id_type="   ")),
        (
            _task(chat_id="   ", params={"receive_id": "   ", "receive_id_type": "   "}),
            _chat_creds(receive_id="   ", receive_id_type="   "),
        ),
    ],
    ids=[
        "task-type-without-task-target",
        "missing-configured-target",
        "blank-configured-type",
        "whitespace-only-values",
    ],
)
def test_incomplete_destination_pair_skips_document_delivery(
    monkeypatch: pytest.MonkeyPatch,
    task: ScheduledTask,
    base: FeishuChatCredentials,
) -> None:
    monkeypatch.setattr("integrations.feishu.load_chat_credentials_from_env", lambda: base)
    calls = _install_fake_document_delivery(monkeypatch)

    ok, error, message_id = FeishuScheduledDelivery().deliver(task, "digest")

    assert ok is False
    assert error == "Missing Feishu delivery target"
    assert message_id == ""
    assert calls == []


def test_missing_app_credentials_refuses_document_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.feishu.scheduled_delivery.resolve_feishu_credentials",
        lambda _params: {"receive_id": "oc_x", "receive_id_type": "chat_id"},
    )
    calls = _install_fake_document_delivery(monkeypatch)

    outcome = FeishuScheduledDelivery().deliver(_task(), "digest")

    assert outcome == (False, "Missing app_id or app_secret for Feishu", "")
    assert calls == []


@pytest.mark.parametrize(
    ("status", "ids", "expected"),
    [
        (FeishuDeliveryStatus.SUCCESS, ("om_first", "om_second"), (True, "", "om_first")),
        (
            FeishuDeliveryStatus.DEGRADED_SUCCESS,
            ("om_first", "om_second"),
            (True, "", "om_first"),
        ),
        (
            FeishuDeliveryStatus.FAILED,
            ("om_partial",),
            (False, "Feishu delivery failed", ""),
        ),
        (FeishuDeliveryStatus.SKIPPED, (), (False, "Feishu delivery failed", "")),
    ],
)
def test_document_status_maps_to_whole_delivery_outcome(
    monkeypatch: pytest.MonkeyPatch,
    status: FeishuDeliveryStatus,
    ids: tuple[str, ...],
    expected: tuple[bool, str, str],
) -> None:
    monkeypatch.setattr(
        "integrations.feishu.scheduled_delivery.resolve_feishu_credentials",
        lambda _params: {
            "app_id": "cli_chat",
            "app_secret": "s_chat",
            "receive_id": "oc_x",
            "receive_id_type": "chat_id",
        },
    )
    _install_fake_document_delivery(monkeypatch, result=_result(status, ids=ids))

    assert FeishuScheduledDelivery().deliver(_task(), "digest") == expected


@pytest.mark.parametrize("message", ["  # Daily\n\n<b>literal</b>  ", ""])
def test_canonical_markdown_is_forwarded_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    monkeypatch.setattr(
        "integrations.feishu.scheduled_delivery.resolve_feishu_credentials",
        lambda _params: {
            "app_id": "cli_chat",
            "app_secret": "s_chat",
            "receive_id": "oc_x",
            "receive_id_type": "chat_id",
        },
    )
    result = (
        _result(FeishuDeliveryStatus.SKIPPED)
        if not message
        else _result(FeishuDeliveryStatus.SUCCESS, ids=("om_first",))
    )
    calls = _install_fake_document_delivery(monkeypatch, result=result)

    FeishuScheduledDelivery().deliver(_task(), message)

    assert calls[0]["markdown"] == message
