"""Pin content release bounds and sanitization before truncation."""

import json
from typing import Any

import pytest

from infrastructure.safety.masking.policy import MaskingPolicy
from integrations.feishu.message_content import normalize_read_content
from integrations.feishu.read_types import FeishuReadError, FeishuReadErrorCode


def test_content_sanitizes_decoded_json_before_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.dumps(
        {
            "token": "secret-key-value",
            "text": "x" * 11_950 + " Bearer abcdefghijklmnopqrstuvwxyz012345 " + "x" * 500,
            "private": "-----BEGIN PRIVATE KEY-----\nsecret-material\n-----END PRIVATE KEY-----",
        }
    ).replace("Bearer", "\\u0042earer")
    dumps = json.dumps
    calls: list[object] = []

    def record_serialization(value: object, **kwargs: Any) -> str:
        calls.append(value)
        return dumps(value, **kwargs)

    monkeypatch.setattr(json, "dumps", record_serialization)
    result = normalize_read_content(raw)
    assert len(calls) == 1
    assert len(result.body_content) == 12_000
    assert result.truncated is True and result.body_format == "json_prefix"
    assert result.redacted is True
    for secret in ("secret-key-value", "abcdefghijkl", "secret-material"):
        assert secret not in result.body_content
    assert "Bearer [REDACTED]" in result.body_content


def test_content_preserves_rich_text_and_card_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MaskingPolicy._ENV_ENABLED, "false")
    bodies = (
        {"zh_cn": {"title": "调查", "content": [[{"tag": "text", "text": "日志"}]]}},
        {
            "schema": "2.0",
            "body": {"elements": [{"tag": "markdown", "content": "**ok**"}]},
            "future_node": {"custom": [True, None, 42]},
            "image_key": "img_fake_resource",
        },
    )
    for body in bodies:
        result = normalize_read_content(json.dumps(body, ensure_ascii=False))
        assert json.loads(result.body_content) == body
        assert result.body_format == "json" and result.truncated is False
        assert result.redacted is False


def test_content_bounds_use_utf8_bytes_and_count_json_values() -> None:
    assert normalize_read_content('{"text":"' + "x" * (256 * 1024 - 11) + '"}').truncated
    assert normalize_read_content('{"x":' + "[" * 62 + "0" + "]" * 62 + "}").body_format == "json"
    assert normalize_read_content(json.dumps({"x": [0] * 8_190})).truncated
    bad = (
        (
            json.dumps({"text": "中" * 90_000}, ensure_ascii=False),
            FeishuReadErrorCode.CONTENT_TOO_LARGE,
        ),
        ('{"text":"' + "x" * (256 * 1024 - 10) + '"}', FeishuReadErrorCode.CONTENT_TOO_LARGE),
        ('{"x":' + "[" * 63 + "0" + "]" * 63 + "}", FeishuReadErrorCode.CONTENT_TOO_COMPLEX),
        (json.dumps({"x": [0] * 8_191}), FeishuReadErrorCode.CONTENT_TOO_COMPLEX),
        ('{"x":' + "[" * 2_000 + "0" + "]" * 2_000 + "}", FeishuReadErrorCode.CONTENT_TOO_COMPLEX),
        ('{"text":"diagnostic-canary",', FeishuReadErrorCode.UNSUPPORTED_CONTENT),
        ('["diagnostic-canary"]', FeishuReadErrorCode.UNSUPPORTED_CONTENT),
        ('{"text":"\\ud800"}', FeishuReadErrorCode.UNSUPPORTED_CONTENT),
        ('{"number":NaN}', FeishuReadErrorCode.UNSUPPORTED_CONTENT),
        ('{"number":1e10000}', FeishuReadErrorCode.UNSUPPORTED_CONTENT),
    )
    for raw, expected in bad:
        with pytest.raises(FeishuReadError) as rejected:
            normalize_read_content(raw)
        assert rejected.value.code is expected
        assert rejected.value.__cause__ is None and rejected.value.__context__ is None
        assert "diagnostic-canary" not in str(rejected.value)


def test_configured_masking_uses_a_fresh_map_per_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MaskingPolicy._ENV_ENABLED, "true")
    monkeypatch.setenv(MaskingPolicy._ENV_KINDS, "email")
    first = normalize_read_content('{"text":"alice@example.com"}')
    second = normalize_read_content('{"text":"bob@example.com"}')
    assert first.body_content == '{"text":"<EMAIL_0>"}'
    assert second.body_content == '{"text":"<EMAIL_0>"}'
    assert first.redacted and second.redacted
