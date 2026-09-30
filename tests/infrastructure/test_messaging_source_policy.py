"""Gateway messaging-source classification must fail closed."""

from collections.abc import Iterator

import pytest

from infrastructure.harness_providers import messaging_sources as policy


@pytest.fixture(autouse=True)
def _registry() -> Iterator[None]:
    policy.reset()
    policy.register_messaging_source("feishu", inbound_platform="feishu")
    policy.register_messaging_source("slack", inbound_platform="slack")
    policy.register_messaging_source("rocketchat")
    yield
    policy.reset()


@pytest.mark.parametrize("platform", [None, "unknown", "rocketchat"])
def test_unknown_platform_withholds_all_messaging_sources(platform: str | None) -> None:
    assert policy.recognized_messaging_platform(platform) is None
    assert not policy.messaging_source_allowed(
        "feishu", surface="gateway", active_platform=platform
    )
    assert not policy.messaging_source_allowed(
        "rocketchat", surface="gateway", active_platform=platform
    )
    assert policy.messaging_source_allowed("github", surface="gateway", active_platform=platform)


def test_registry_covers_inbound_and_delivery_vocabularies() -> None:
    from infrastructure.scheduling.scheduler.types import Provider
    from integrations.harness_adapters import register_harness_adapters
    from integrations.messaging_security import MessagingPlatform

    register_harness_adapters()
    register_harness_adapters()
    inbound = {platform.value for platform in MessagingPlatform}
    delivery = {
        provider.value for provider in Provider if provider is not Provider.INTERACTIVE_SHELL
    }
    entries = policy.registered_messaging_sources()
    assert {entry.source for entry in entries} == inbound | delivery
    assert len(entries) == len(inbound | delivery)
    assert {entry.inbound_platform for entry in entries if entry.inbound_platform} == inbound
    assert policy.recognized_messaging_platform(" FEISHU ") == "feishu"
    assert policy.inactive_messaging_sources(surface="gateway", active_platform="feishu") == (
        frozenset(inbound | delivery) - {"feishu"}
    )
    policy.register_messaging_source("future-delivery")
    assert not policy.messaging_source_allowed(
        "future-delivery", surface="gateway", active_platform="feishu"
    )
    assert policy.messaging_source_allowed(
        "slack", surface="interactive_shell", active_platform=None
    )
