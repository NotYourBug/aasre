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

#: Resources fetched for one message at most. The character budget bounds the
#: prompt, not the work: a resource that fails or does not render costs no
#: budget, so a message carrying many of them would otherwise drive an unbounded
#: sequence of downloads and vision calls while holding a worker slot and the
#: conversation lock. Real messages carry a handful; this only stops the
#: pathological case.
FEISHU_MAX_RESOURCES_PER_MESSAGE = 10

#: Filename suffixes we can read as text, and the ones we know we cannot. An
#: unknown suffix is treated as text — one bounded read then confirms against the
#: response Content-Type, which is cheaper than guessing wrong on ``app.log.1``.
#:
#: ``.env`` is deliberately absent. A trusted suffix skips that corroboration, and
#: ``.env`` is the one file whose contents are almost entirely credentials — the
#: shapes the scrubber does not recognise (connection URIs, ``AWS_SECRET_ACCESS_KEY``,
#: provider-prefixed keys). It still reads as text; it just has to earn it.
FEISHU_TEXT_FILE_SUFFIXES = frozenset(
    {
        ".conf",
        ".csv",
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

#: CardKit 2.0 is the only schema version that renders GFM tables; 1.0 silently
#: drops them.
FEISHU_CARD_SCHEMA = "2.0"

#: The platform applies **two** ceilings, measured against the live API
#: (2026-09-16), and they are in *different units* — a card can clear one and
#: hit the other:
#:
#: - Creating a card entity rejects card JSON past ~150 KiB with ``200860``
#:   ("card over max size"): 148 KiB passed, 152 KiB failed.
#: - Updating a streaming element rejects content past 100_000 **characters**
#:   with ``99992402``: 100,000 ASCII and 100,000 CJK (300 KB of them) both
#:   passed; 100,001 of either failed. So this ceiling is not a byte count — a
#:   CJK character spends three bytes of one budget and one unit of the other.
FEISHU_CARD_MAX_BYTES = 150 * 1024

#: What every card ``paginate`` emits and every streamed element stays under.
#: 64 KiB sits ~57% below the create ceiling and, counted in characters, ~35%
#: below the element ceiling for ASCII and ~78% for CJK. ASCII is the tight
#: side: one character is one byte, so the byte budget *is* the worst-case
#: character count.
FEISHU_CARD_BUDGET_BYTES = 64 * 1024

#: Tables per card. Exceeding it fails the whole update with
#: ``11310 card table number over limit`` — a byte-only budget would miss this.
FEISHU_CARD_MAX_TABLES = 5

#: The markdown element the streaming session updates. CardKit requires the id
#: to start with a letter and use only alphanumerics/underscores, max 20 chars.
FEISHU_STREAM_ELEMENT_ID = "stream_md"

#: Streaming failures that mean "stop streaming, deliver the rest another way":
#: 200850 stream timed out, 300309 streaming already closed, 300317 sequence
#: out of order. None is worth retrying on the same card.
FEISHU_STREAM_ERROR_CODES = frozenset({200850, 300309, 300317})

#: Verified stable business codes used to classify message and CardKit
#: rejections without retaining vendor response text.
FEISHU_RATE_LIMIT_ERROR_CODES = frozenset({230020, 99991400})
FEISHU_VALIDATION_ERROR_CODES = frozenset({11310, 200860, 230001, 230025})
FEISHU_AUTHORIZATION_ERROR_CODES = frozenset({230002, 230006, 230013, 230017, 230018, 230027})

#: Streaming is throttled well under the endpoint's 50/s: one flush per interval,
#: or sooner once this many characters have accumulated.
FEISHU_STREAM_MIN_INTERVAL_SECONDS = 1.5
FEISHU_STREAM_MIN_CHARS = 200

#: Level 1 marks the card as continuing elsewhere. A bare "…" would read as
#: "the answer ended here", which is the opposite of the truth.
FEISHU_CARD_TRUNCATED_MARKER = "\n\n> _内容较长，后续见下方卡片。_"

__all__ = [
    "FEISHU_APP_ID_ENV",
    "FEISHU_APP_SECRET_ENV",
    "FEISHU_AUTHORIZATION_ERROR_CODES",
    "FEISHU_ALLOWED_OPEN_IDS_ENV",
    "FEISHU_CHAT_RECEIVE_ID_ENV",
    "FEISHU_CHAT_RECEIVE_ID_TYPE_ENV",
    "ALERTPUSH_APP_ID_ENV",
    "ALERTPUSH_APP_SECRET_ENV",
    "FEISHU_ALARM_RECEIVE_ID_ENV",
    "FEISHU_ALARM_RECEIVE_ID_TYPE_ENV",
    "FEISHU_BINARY_FILE_SUFFIXES",
    "FEISHU_CARD_BUDGET_BYTES",
    "FEISHU_CARD_MAX_BYTES",
    "FEISHU_CARD_MAX_TABLES",
    "FEISHU_CARD_SCHEMA",
    "FEISHU_CARD_TRUNCATED_MARKER",
    "FEISHU_IMAGE_MAX_BYTES",
    "FEISHU_MAX_RESOURCES_PER_MESSAGE",
    "FEISHU_RECEIVE_ID_TYPES",
    "FEISHU_RATE_LIMIT_ERROR_CODES",
    "FEISHU_RESOURCE_HOST_SUFFIXES",
    "FEISHU_RESOURCE_TIMEOUT_SECONDS",
    "FEISHU_RESOURCE_TYPE_FILE",
    "FEISHU_RESOURCE_TYPE_IMAGE",
    "FEISHU_STREAM_ELEMENT_ID",
    "FEISHU_STREAM_ERROR_CODES",
    "FEISHU_STREAM_MIN_CHARS",
    "FEISHU_STREAM_MIN_INTERVAL_SECONDS",
    "FEISHU_TEXT_FILE_MAX_BYTES",
    "FEISHU_TEXT_FILE_SUFFIXES",
    "FEISHU_VALIDATION_ERROR_CODES",
]
