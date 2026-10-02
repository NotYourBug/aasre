"""Select Feishu application records without discarding stored identity policy."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from integrations.feishu.classify import classify
from integrations.messaging_security import MessagingIdentityPolicy


def _is_policy_only_default(record: dict[str, Any]) -> bool:
    if set(record) != {"id", "service", "status", "instances"} or record["status"] != "active":
        return False
    instances = record["instances"]
    if not isinstance(instances, list) or len(instances) != 1:
        return False
    instance = instances[0]
    if (
        not isinstance(instance, dict)
        or set(instance) != {"name", "tags", "credentials"}
        or instance["name"] != "default"
        or not isinstance(instance["tags"], dict)
    ):
        return False
    credentials = instance["credentials"]
    if not isinstance(credentials, dict) or set(credentials) != {"identity_policy"}:
        return False
    policy = credentials["identity_policy"]
    if not isinstance(policy, dict):
        return False
    try:
        MessagingIdentityPolicy.model_validate(policy)
    except ValidationError:
        return False
    return True


def feishu_credential_records(
    store_records: list[dict[str, Any]], env_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep env application config beside one active default identity-only record.

    Returns a new list for credential resolution; records and persisted identity
    policy remain untouched. Explicit configuration and ambiguous records retain
    store precedence.
    """
    feishu_records = [r for r in store_records if str(r.get("service", "")).strip() == "feishu"]
    if len(feishu_records) != 1 or not _is_policy_only_default(feishu_records[0]):
        return list(store_records)
    # Match the last env record, as the generic later-group-wins merge does.
    env_record = next(
        (r for r in reversed(env_records) if str(r.get("service", "")).strip() == "feishu"),
        None,
    )
    if env_record is None or env_record.get("status") != "active":
        return list(store_records)
    credentials = env_record.get("credentials")
    if not isinstance(credentials, dict):
        return list(store_records)
    config, _service = classify(credentials, str(env_record.get("id", "")))
    if config is None or not config["app_id"] or not config["app_secret"]:
        return list(store_records)
    return [r for r in store_records if r is not feishu_records[0]]


__all__ = ["feishu_credential_records"]
