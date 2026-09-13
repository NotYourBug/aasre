"""Feishu integration env-var names.

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
FEISHU_RECEIVE_ID_TYPES = frozenset({"chat_id", "email", "open_id", "union_id", "user_id"})

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
    "FEISHU_RECEIVE_ID_TYPES",
]
