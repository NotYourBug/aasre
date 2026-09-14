"""Feishu inbound content parsing, pinned to payloads captured from the live app."""

from __future__ import annotations

from integrations.feishu import (
    ResourceRef,
    classify_file,
    flatten_post,
    resource_refs,
    resource_url,
)

# Captured 2026-09-14: a screenshot sent in a group, where the required @-mention
# makes the client compose a rich-text message rather than an image message.
SCREENSHOT_POST = {
    "title": "",
    "content": [
        [
            {"tag": "at", "user_id": "@_user_1", "user_name": "aasre", "style": []},
            {"tag": "text", "text": " ", "style": []},
        ],
        [{"tag": "img", "image_key": "img_v3_0215h_c80616f8", "width": 1247, "height": 984}],
    ],
}

RICH_POST = {
    "title": "Test",
    "content": [
        [
            {"tag": "at", "user_id": "@_user_1", "user_name": "aasre", "style": []},
            {"tag": "text", "text": " 早上好", "style": []},
        ],
        [
            {"tag": "text", "text": "1. ", "style": []},
            {"tag": "text", "text": "test1", "style": []},
        ],
        [],
        [{"tag": "code_block", "language": "SQL", "text": "SELECT 1"}],
        [{"tag": "img", "image_key": "img_v3_cb6cca06", "width": 1220, "height": 1083}],
    ],
}


def test_flatten_keeps_every_text_run_and_drops_the_bot_mention() -> None:
    text = flatten_post(RICH_POST, bot_keys=frozenset({"@_user_1"}))
    assert "早上好" in text
    assert "test1" in text
    assert "Test" in text
    assert "@_user_1" not in text
    assert "aasre" not in text


def test_flatten_renders_a_code_block_instead_of_dropping_it() -> None:
    assert "SELECT 1" in flatten_post(RICH_POST, bot_keys=frozenset({"@_user_1"}))


def test_flatten_keeps_another_members_mention() -> None:
    """Only the bot's own mention is dropped; a colleague's must survive."""
    post = {
        "content": [
            [
                {"tag": "at", "user_id": "@_user_1", "user_name": "aasre"},
                {"tag": "at", "user_id": "@_user_2", "user_name": "Alice"},
                {"tag": "text", "text": " please look"},
            ]
        ]
    }
    flat = flatten_post(post, bot_keys=frozenset({"@_user_1"}))
    assert "Alice" in flat
    assert "aasre" not in flat


def test_flatten_ignores_an_unknown_tag_without_losing_its_neighbours() -> None:
    post = {
        "content": [
            [
                {"tag": "text", "text": "before"},
                {"tag": "nope"},
                {"tag": "text", "text": "after"},
            ]
        ]
    }
    flat = flatten_post(post, bot_keys=frozenset())
    assert "before" in flat
    assert "after" in flat


def test_flatten_survives_a_malformed_node_tree() -> None:
    assert flatten_post({"content": "not-a-list"}, bot_keys=frozenset()) == ""


def test_post_yields_both_its_text_and_its_images() -> None:
    refs = resource_refs("post", SCREENSHOT_POST)
    assert refs == (ResourceRef(kind="image", key="img_v3_0215h_c80616f8", name=""),)


def test_a_media_message_exposes_its_cover_frame() -> None:
    content = {
        "file_key": "file_v3_x",
        "file_name": "IMG_0404.MOV",
        "image_key": "img_v3_cover",
        "duration": 8000,
    }
    assert resource_refs("media", content) == (
        ResourceRef(kind="image", key="img_v3_cover", name=""),
    )


def test_a_file_message_names_itself_but_an_image_message_cannot() -> None:
    assert resource_refs("file", {"file_key": "file_v3_x", "file_name": "test.txt"}) == (
        ResourceRef(kind="file", key="file_v3_x", name="test.txt"),
    )
    assert resource_refs("image", {"image_key": "img_v3_y"}) == (
        ResourceRef(kind="image", key="img_v3_y", name=""),
    )


def test_sticker_and_unknown_types_yield_nothing() -> None:
    assert resource_refs("sticker", {"file_key": "st-1"}) == ()
    assert resource_refs("merge_forward", {}) == ()


def test_resource_url_carries_the_type_param() -> None:
    url = resource_url("om_abc", ResourceRef(kind="file", key="file_v3_x", name="a.log"))
    assert url.startswith("https://open.feishu.cn/open-apis/im/v1/messages/om_abc/resources/")
    assert "type=file" in url


def test_filename_classification_routes_the_captured_samples() -> None:
    assert classify_file("test.txt") == "text"
    assert classify_file("app.log") == "text"
    assert classify_file("screenshot.png") == "image"
    assert classify_file("test.pdf") == "binary"
    assert classify_file("26.mp4") == "binary"
    assert classify_file("unforgettable experience.m4a") == "binary"
    assert classify_file("") == "text"  # unknown -> attempt text, confirm by response
