"""Feishu watchdog alarm dispatcher via document delivery.

Credential resolution lives in :mod:`integrations.feishu.credentials`. This
module owns throttling and dispatch policy (mirrors
:mod:`integrations.rocketchat.alarms`).
"""

from __future__ import annotations

import logging
import time

from infrastructure.delivery.notifications.cooldown import CooldownGate
from integrations.feishu.credentials import FeishuAlarmCredentials

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
        markdown = f"**[{threshold_name}]**\n{message}"
        if not all(
            (
                self._creds.app_id.strip(),
                self._creds.app_secret.strip(),
                self._creds.receive_id.strip(),
                self._creds.receive_id_type.strip(),
                message.strip(),
            )
        ):
            logger.debug("Feishu alarm delivery skipped before cooldown reservation")
            return False

        now = self._now()

        remaining = self._gate.try_reserve(threshold_name, now)
        if remaining is not None:
            logger.debug(
                "alarm suppressed by cooldown: name=%s remaining=%.1fs",
                threshold_name,
                remaining,
            )
            return False

        # Every attempted delivery keeps the reservation, including FAILED and
        # a contract-violating raise, so retries cannot bypass the cooldown.
        try:
            from integrations.feishu.document_delivery import deliver_feishu_document

            result = deliver_feishu_document(
                app_id=self._creds.app_id,
                app_secret=self._creds.app_secret,
                receive_id=self._creds.receive_id,
                receive_id_type=self._creds.receive_id_type,
                markdown=markdown,
            )
        except Exception:  # noqa: BLE001
            logger.warning("Feishu alarm delivery raised; cooldown remains armed")
            return False

        if result.successful:
            return True

        logger.warning("Feishu alarm delivery failed; cooldown remains armed")
        return False

    @staticmethod
    def _now() -> float:
        return time.monotonic()
