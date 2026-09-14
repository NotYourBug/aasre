"""Feishu integration env-var names and inbound-resource constants.

The interactive app (``FEISHU_*``) serves the chat transport and everything
delivered into a conversation: investigation reports and scheduled tasks, which
target ``FEISHU_CHAT_RECEIVE_ID``. The dedicated alert-push app
(``ALERTPUSH_*``) is one-way only — watchdog alarms and background-RCA notices,
targeting ``FEISHU_ALARM_RECEIVE_ID``.
"""

FEISHU_APP_ID_ENV = "FEISHU_APP_ID"
FEISHU_APP_SECRET_ENV = "FEISHU_APP_SECRET"
FEISHU_ALLOWED_OPEN_IDS_ENV = "FEISHU_ALLOWED_OPEN_IDS"
FEISHU_CHAT_RECEIVE_ID_ENV = "FEISHU_CHAT_RECEIVE_ID"
FEISHU_CHAT_RECEIVE_ID_TYPE_ENV = "FEISHU_CHAT_RECEIVE_ID_TYPE"
ALERTPUSH_APP_ID_ENV = "ALERTPUSH_APP_ID"
ALERTPUSH_APP_SECRET_ENV = "ALERTPUSH_APP_SECRET"
FEISHU_ALARM_RECEIVE_ID_ENV = "FEISHU_ALARM_RECEIVE_ID"
FEISHU_ALARM_RECEIVE_ID_TYPE_ENV = "FEISHU_ALARM_RECEIVE_ID_TYPE"

#: The ``receive_id_type`` values the Feishu message API accepts. Guided setup
#: collects this as free text, so without the check a typo reaches the SDK and
#: is only rejected at delivery time, long after setup reported success.
#: Mirrors ``lark_oapi``'s ``ReceiveIdType`` literal; this module stays
#: SDK-free, so the list is copied rather than imported.
FEISHU_RECEIVE_ID_TYPES = frozenset({"chat_id", "email", "open_id", "union_id", "user_id"})

#: Feishu serves message resources from its own API hosts only. The download
#: carries a tenant token, so the host is allowlisted before a connection opens.
FEISHU_RESOURCE_HOST_SUFFIXES = ("open.feishu.cn", "open.larksuite.com")

#: The resource endpoint's ``type`` query parameter. Only these two exist; a
#: video's cover frame is fetched as ``image``, never as ``file``.
FEISHU_RESOURCE_TYPE_IMAGE = "image"
FEISHU_RESOURCE_TYPE_FILE = "file"

#: Per-resource download ceilings. An image must arrive whole or not at all — a
#: truncated PNG is not a PNG and vision cannot decode it — so it is dropped past
#: the cap. A text file is the opposite: its head is the useful part, so the read
#: stops at the cap and keeps what it has. 5 MB matches the vision provider's own
#: per-image limit; 2 MB is well past a real log while bounding memory.
FEISHU_IMAGE_MAX_BYTES = 5 * 1024 * 1024
FEISHU_TEXT_FILE_MAX_BYTES = 2 * 1024 * 1024
FEISHU_RESOURCE_TIMEOUT_SECONDS = 10.0

#: Filename suffixes we can read as text, and the ones we know we cannot. An
#: unknown suffix is treated as text — one bounded read then confirms against the
#: response Content-Type, which is cheaper than guessing wrong on ``app.log.1``.
FEISHU_TEXT_FILE_SUFFIXES = frozenset(
    {
        ".conf",
        ".csv",
        ".env",
        ".ini",
        ".json",
        ".log",
        ".md",
        ".ndjson",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
FEISHU_BINARY_FILE_SUFFIXES = frozenset(
    {
        ".7z",
        ".avi",
        ".docx",
        ".gif",
        ".gz",
        ".jpeg",
        ".jpg",
        ".m4a",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".tar",
        ".wav",
        ".webp",
        ".xls",
        ".xlsx",
        ".zip",
    }
)

__all__ = [
    "FEISHU_APP_ID_ENV",
    "FEISHU_APP_SECRET_ENV",
    "FEISHU_ALLOWED_OPEN_IDS_ENV",
    "FEISHU_CHAT_RECEIVE_ID_ENV",
    "FEISHU_CHAT_RECEIVE_ID_TYPE_ENV",
    "ALERTPUSH_APP_ID_ENV",
    "ALERTPUSH_APP_SECRET_ENV",
    "FEISHU_ALARM_RECEIVE_ID_ENV",
    "FEISHU_ALARM_RECEIVE_ID_TYPE_ENV",
    "FEISHU_BINARY_FILE_SUFFIXES",
    "FEISHU_IMAGE_MAX_BYTES",
    "FEISHU_RECEIVE_ID_TYPES",
    "FEISHU_RESOURCE_HOST_SUFFIXES",
    "FEISHU_RESOURCE_TIMEOUT_SECONDS",
    "FEISHU_RESOURCE_TYPE_FILE",
    "FEISHU_RESOURCE_TYPE_IMAGE",
    "FEISHU_TEXT_FILE_MAX_BYTES",
    "FEISHU_TEXT_FILE_SUFFIXES",
]
