"""What Feishu needs before it is considered configured.

``app_id`` and ``app_secret`` are the interactive chat app — the one that
serves conversations, investigation reports and scheduled tasks. The separate
alert-push app (``ALERTPUSH_*``) is one-way, has no guided setup, and is
configured by environment variable only.

``receive_id`` is a *fallback* destination for delivery paths that have no
target of their own. ``receive_id_type`` follows it: ``chat_id`` for a group,
``open_id`` for a person.
"""

from __future__ import annotations

from config.constants.feishu import (
    FEISHU_ALLOWED_OPEN_IDS_ENV,
    FEISHU_APP_ID_ENV,
    FEISHU_APP_SECRET_ENV,
    FEISHU_CHAT_RECEIVE_ID_ENV,
    FEISHU_CHAT_RECEIVE_ID_TYPE_ENV,
)
from integrations.feishu.verifier import verify_feishu
from integrations.setup_flow import IntegrationSetupSpec, SetupField

APP_ID_FIELD = "app_id"
APP_SECRET_FIELD = "app_secret"
RECEIVE_ID_FIELD = "receive_id"
RECEIVE_ID_TYPE_FIELD = "receive_id_type"
ALLOWED_OPEN_IDS_FIELD = "allowed_open_ids"

FEISHU_SETUP = IntegrationSetupSpec(
    service="feishu",
    fields=(
        SetupField(
            name=APP_ID_FIELD,
            label="Feishu app ID",
            prompt="Feishu chat app ID (cli_…, from the developer console)",
            env_var=FEISHU_APP_ID_ENV,
        ),
        SetupField(
            name=APP_SECRET_FIELD,
            label="Feishu app secret",
            prompt="Feishu chat app secret",
            env_var=FEISHU_APP_SECRET_ENV,
            secret=True,
        ),
        SetupField(
            name=RECEIVE_ID_FIELD,
            label="Default destination",
            prompt="Default destination (oc_… for a group, ou_… for a person; optional)",
            env_var=FEISHU_CHAT_RECEIVE_ID_ENV,
            required=False,
        ),
        SetupField(
            name=RECEIVE_ID_TYPE_FIELD,
            label="Destination type",
            prompt="Destination type — chat_id or open_id",
            env_var=FEISHU_CHAT_RECEIVE_ID_TYPE_ENV,
            default="chat_id",
            required=False,
        ),
        SetupField(
            name=ALLOWED_OPEN_IDS_FIELD,
            label="Allowed open ids",
            prompt="Comma-separated open ids allowed to talk to the bot (optional)",
            env_var=FEISHU_ALLOWED_OPEN_IDS_ENV,
            required=False,
        ),
    ),
    verify=verify_feishu,
)

__all__ = [
    "ALLOWED_OPEN_IDS_FIELD",
    "APP_ID_FIELD",
    "APP_SECRET_FIELD",
    "FEISHU_SETUP",
    "RECEIVE_ID_FIELD",
    "RECEIVE_ID_TYPE_FIELD",
]
