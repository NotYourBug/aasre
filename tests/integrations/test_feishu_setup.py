"""Vendor-specific behavior of the Feishu guided setup.

The prompt loop itself — that a rejected answer is re-asked rather than saved —
is shared handler behavior and is pinned in
:mod:`tests.integrations.test_cli_spec_setup`. What belongs here is the Feishu
check the loop calls.
"""

from __future__ import annotations

from integrations.feishu.setup import FEISHU_SETUP, RECEIVE_ID_TYPE_FIELD
from integrations.setup_flow import SetupField


def _receive_id_type_field() -> SetupField:
    return next(field for field in FEISHU_SETUP.fields if field.name == RECEIVE_ID_TYPE_FIELD)


def test_destination_type_is_checked_at_the_prompt() -> None:
    """The verifier probes only the app credentials, so a typo must not get past setup.

    Checked here rather than only in the classifier: a value that failed later
    would already be written to ``.env`` and the store, leaving the repository
    showing the integration as active while its verification reports it missing.
    """
    validate = _receive_id_type_field().validate

    assert validate is not None
    assert validate("chatid") is not None
    assert validate("chat_id") is None


def test_destination_type_check_accepts_every_value_the_api_takes() -> None:
    """Guards against the check being narrowed to the two values the prompt names."""
    validate = _receive_id_type_field().validate

    assert validate is not None
    for name in ("chat_id", "email", "open_id", "union_id", "user_id"):
        assert validate(name) is None, name
