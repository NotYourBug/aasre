"""Search sanitized values without leaking secrets or truncated-body matches."""

import json

import pytest

from infrastructure.safety.masking.policy import MaskingPolicy
from integrations.feishu.search_content import make_search_preview, normalize_search_content


def test_search_matches_only_sanitized_values_and_not_resource_keys() -> None:
    raw = json.dumps(
        {
            "token": "SECRET-CANARY",
            "text": "故障 Bearer abcdefghijklmnopqrstuvwxyz012345",
            "image_key": "img_canary",
            "nested": [{"file_key": {"text": "resource-canary"}, "content": "恢复"}],
        },
        ensure_ascii=False,
    ).replace("Bearer", "\\u0042earer")
    safe = normalize_search_content(raw)
    assert safe.redacted
    assert safe.text is not None and "故障" in safe.text and "恢复" in safe.text
    for query in ("SECRET-CANARY", "abcdefghijkl", "img_canary", "resource-canary", "file_key"):
        assert make_search_preview(safe, query) is None
    assert make_search_preview(safe, "故障") is not None


def test_casefold_preview_keeps_the_original_unicode_match() -> None:
    raw = json.dumps({"text": "ß" * 400 + "Straße" + "尾" * 650}, ensure_ascii=False)
    found = make_search_preview(normalize_search_content(raw), "STRASSE")
    assert found is not None
    preview, shortened = found
    assert "Straße" in preview and len(preview) == 600 and shortened
    assert preview.startswith("ß" * 120 + "Straße")
    assert make_search_preview(normalize_search_content('{"text":"故障"}'), "") == ("故障", False)


def test_rich_text_mentions_cannot_match_or_enter_search_previews() -> None:
    raw = json.dumps(
        {
            "title": "故障",
            "content": [
                [
                    {
                        "tag": "at",
                        "user_id": "ou_ID_CANARY",
                        "user_name": "NAME-CANARY",
                        "text": "NODE-MENTION-CANARY",
                    },
                    {"tag": "text", "text": "database timeout resolved"},
                ]
            ],
            "nested": {
                "user_id": "ou_NESTED_CANARY",
                "user_name": "NESTED-NAME-CANARY",
                "mentions": [{"text": "MENTIONS-CANARY"}],
            },
        },
        ensure_ascii=False,
    )
    safe = normalize_search_content(raw)
    for query in (
        "ou_ID_CANARY",
        "NAME-CANARY",
        "NODE-MENTION-CANARY",
        "ou_NESTED_CANARY",
        "NESTED-NAME-CANARY",
        "MENTIONS-CANARY",
    ):
        assert make_search_preview(safe, query) is None
    preview = make_search_preview(safe, "timeout")
    assert preview is not None and "database timeout resolved" in preview[0]
    assert "CANARY" not in preview[0]


@pytest.mark.parametrize(
    "raw",
    [
        '{"text":"canary",',
        '{"text":"' + "x" * 13000 + '"}',
        json.dumps({"text": "中" * 90000}, ensure_ascii=False),
    ],
    ids=["invalid-json", "json-prefix", "utf8-byte-limit"],
)
def test_incomplete_or_invalid_json_never_becomes_searchable(raw: str) -> None:
    content = normalize_search_content(raw)
    assert content.text is None
    assert make_search_preview(content, "") is None
    assert make_search_preview(content, "canary") is None


def test_search_masking_does_not_share_substitutions_between_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MaskingPolicy._ENV_ENABLED, "true")
    monkeypatch.setenv(MaskingPolicy._ENV_KINDS, "email")
    first = normalize_search_content('{"text":"alice@example.com"}')
    second = normalize_search_content('{"text":"bob@example.com"}')
    assert first.text == second.text == "<EMAIL_0>"
    assert first.redacted and second.redacted
    assert make_search_preview(first, "alice") is None
