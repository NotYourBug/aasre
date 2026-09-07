"""Feishu watchdog alarm dispatcher via im.message.create.

Credential resolution lives in :mod:`integrations.feishu.credentials`; raw
transport in :mod:`integrations.feishu.delivery`. This module owns throttling +
dispatch policy (mirrors :mod:`integrations.rocketchat.alarms`).
"""

from __future__ import annotations

import logging
import time

from infrastructure.delivery.notifications.cooldown import CooldownGate
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.text.truncation import truncate
from integrations.feishu.credentials import FeishuAlarmCredentials
from integrations.feishu.delivery import post_feishu_message

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 300.0


class FeishuAlarmDispatcher:
    """Deliver watchdog threshold alarms to a Feishu chat."""

    def __init__(
        self,
        creds: FeishuAlarmCredentials,
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._creds = creds
        self._gate = CooldownGate(cooldown_seconds)

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Send to Feishu unless this threshold is in cooldown."""
        now = self._now()

        remaining = self._gate.try_reserve(threshold_name, now)
        if remaining is not None:
            logger.debug(
                "alarm suppressed by cooldown: name=%s remaining=%.1fs",
                threshold_name,
                remaining,
            )
            return False

        text = truncate(f"[{threshold_name}] {message}", MAX_MESSAGE_SIZE, suffix="…")

        # The cooldown slot was reserved before this network call. If the
        # delivery returns ok=False, the slot stays armed for the cooldown
        # window and the next caller for the same key is silently suppressed —
        # emit the same warning in both paths so operators see the original
        # failure instead of only the suppression debug line. Transport and
        # construction failures fold into ok=False inside the helper; the
        # try/except below is defense-in-depth so a contract-violating raise
        # still returns False instead of escaping the watchdog runner.
        try:
            ok, error, _message_id = post_feishu_message(
                self._creds.app_id,
                self._creds.app_secret,
                self._creds.receive_id,
                self._creds.receive_id_type,
                text,
            )
        except Exception as exc:
            logger.warning(
                "alarm delivery raised and cooldown remains armed: name=%s error=%s",
                threshold_name,
                exc,
                exc_info=True,
            )
            return False

        if ok:
            return True

        logger.warning(
            "alarm delivery failed and cooldown remains armed: name=%s error=%s",
            threshold_name,
            error,
        )
        return False

    @staticmethod
    def _now() -> float:
        return time.monotonic()
