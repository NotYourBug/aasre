"""Feishu attachment routing: what the agent is told about what the user sent."""

from __future__ import annotations

from config.constants.feishu import FEISHU_IMAGE_MAX_BYTES, FEISHU_TEXT_FILE_MAX_BYTES
from gateway.core.attachments.fetch import DownloadedAttachment
from gateway.transports.feishu.attachments import Downloader, build_attachments_context
from integrations.feishu import ResourceRef

IMG = ResourceRef(kind="image", key="img_1")
TXT = ResourceRef(kind="file", key="file_1", name="app.log")
PDF = ResourceRef(kind="file", key="file_2", name="report.pdf")


def _returns(payload: dict[str, bytes], content_type: str) -> Downloader:
    """A downloader that serves ``payload`` for any URL mentioning its key."""

    def _download(url: str, _max_bytes: int, _keep_partial: bool) -> DownloadedAttachment | None:
        for key, body in payload.items():
            if key in url:
                return DownloadedAttachment(data=body, content_type=content_type, truncated=False)
        return None

    return _download


def _recording_downloader(calls: list[tuple[int, bool]]) -> Downloader:
    """A downloader that records the (cap, keep_partial) policy it was handed."""

    def _record(_url: str, max_bytes: int, keep_partial: bool) -> DownloadedAttachment | None:
        calls.append((max_bytes, keep_partial))
        return None

    return _record


def _never_downloads(
    _url: str, _max_bytes: int, _keep_partial: bool
) -> DownloadedAttachment | None:
    return None


def test_an_image_becomes_a_vision_description() -> None:
    ctx = build_attachments_context(
        "om_1",
        (IMG,),
        downloader=_returns({"img_1": b"\x89PNG"}, "image/png"),
        describer=lambda _data, _mime: "a stack trace on a dark terminal",
    )

    assert "a stack trace on a dark terminal" in ctx


def test_a_text_file_is_inlined_with_its_header() -> None:
    ctx = build_attachments_context(
        "om_1",
        (TXT,),
        downloader=_returns({"file_1": b"ERROR boom\n"}, "text/plain"),
        describer=lambda _d, _m: None,
    )

    assert "app.log" in ctx
    assert "ERROR boom" in ctx


def test_a_text_file_is_inlined_even_when_the_server_calls_it_binary() -> None:
    """The exit criterion: a log file must reach the agent whatever the header says."""
    ctx = build_attachments_context(
        "om_1",
        (TXT,),
        downloader=_returns({"file_1": b"ERROR boom\n"}, "application/octet-stream"),
        describer=lambda _d, _m: None,
    )

    assert "ERROR boom" in ctx


def test_a_binary_file_is_named_and_never_downloaded() -> None:
    def _explode(_url: str, _max_bytes: int, _keep_partial: bool) -> DownloadedAttachment | None:
        raise AssertionError("a PDF must not be downloaded")

    ctx = build_attachments_context(
        "om_1", (PDF,), downloader=_explode, describer=lambda _d, _m: None
    )

    assert "report.pdf" in ctx


def test_a_failed_download_becomes_a_line_not_a_silent_gap() -> None:
    ctx = build_attachments_context(
        "om_1", (IMG,), downloader=_never_downloads, describer=lambda _d, _m: None
    )

    assert "could not be downloaded" in ctx


def test_an_undescribable_image_says_so() -> None:
    ctx = build_attachments_context(
        "om_1",
        (IMG,),
        downloader=_returns({"img_1": b"x"}, "image/png"),
        describer=lambda _d, _m: None,
    )

    assert "could not be described" in ctx


def test_an_image_is_fetched_whole_or_not_at_all() -> None:
    """A truncated PNG is not a PNG, so an image takes the drop-don't-cut cap."""
    calls: list[tuple[int, bool]] = []

    build_attachments_context(
        "om_1", (IMG,), downloader=_recording_downloader(calls), describer=lambda _d, _m: None
    )

    assert calls == [(FEISHU_IMAGE_MAX_BYTES, False)]


def test_a_text_file_keeps_its_head_when_it_is_oversized() -> None:
    calls: list[tuple[int, bool]] = []

    build_attachments_context(
        "om_1", (TXT,), downloader=_recording_downloader(calls), describer=lambda _d, _m: None
    )

    assert calls == [(FEISHU_TEXT_FILE_MAX_BYTES, True)]


def test_the_download_targets_the_messages_own_resource_endpoint() -> None:
    seen: list[str] = []

    def _record(url: str, _max_bytes: int, _keep_partial: bool) -> DownloadedAttachment | None:
        seen.append(url)
        return None

    build_attachments_context("om_1", (TXT,), downloader=_record, describer=lambda _d, _m: None)

    assert seen == [
        "https://open.feishu.cn/open-apis/im/v1/messages/om_1/resources/file_1?type=file"
    ]


def test_an_unrecognised_suffix_needs_the_server_to_call_it_text() -> None:
    """An unknown extension is only a guess, so the response has to agree."""
    ctx = build_attachments_context(
        "om_1",
        (ResourceRef(kind="file", key="file_3", name="dump.dat"),),
        downloader=_returns({"file_3": b"\x00\x01\x02\xff\xfe"}, "application/octet-stream"),
        describer=lambda _d, _m: None,
    )

    assert "dump.dat" in ctx
    assert "\x00" not in ctx


def test_an_unrecognised_suffix_is_inlined_when_the_server_calls_it_text() -> None:
    ctx = build_attachments_context(
        "om_1",
        (ResourceRef(kind="file", key="file_3", name="dump.dat"),),
        downloader=_returns({"file_3": b"connection refused"}, "text/plain"),
        describer=lambda _d, _m: None,
    )

    assert "connection refused" in ctx


def test_a_cap_cut_mid_character_does_not_garble_the_whole_file() -> None:
    """The byte cap lands wherever it lands; the rest of the log must still read."""
    ctx = build_attachments_context(
        "om_1",
        (TXT,),
        downloader=_returns({"file_1": "错误日志: 数据库连接失败".encode()[:-1]}, "text/plain"),
        describer=lambda _d, _m: None,
    )

    assert "错误日志" in ctx
