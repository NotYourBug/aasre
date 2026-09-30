"""Reset orchestration for harness and integration-populated core registries."""

from core.domain.alerts.alert_source import (
    clear_alert_source_detectors,
    clear_alert_source_routing,
    clear_secondary_tool_sources,
    clear_source_aliases,
)
from core.domain.alerts.extraction import clear_alert_detail_fields
from core.domain.diagnosis.taxonomy_registry import clear_taxonomy_profiles
from core.domain.types.incident_anchors import clear_anchor_parsers
from infrastructure.harness_providers.cli_llm import reset as reset_cli_llm
from infrastructure.harness_providers.evidence_sources import reset as reset_evidence_sources
from infrastructure.harness_providers.integration_resolution import (
    reset as reset_integration_resolution,
)
from infrastructure.harness_providers.message_context import reset as reset_message_context
from infrastructure.harness_providers.messaging_sources import reset as reset_messaging_sources
from infrastructure.harness_providers.prompt_fragments import reset as reset_prompt_fragments
from infrastructure.harness_providers.repo_scope import reset as reset_repo_scope
from infrastructure.harness_providers.subprocess_presenter import (
    reset as reset_subprocess_presenter,
)
from infrastructure.harness_providers.tool_registry import reset as reset_tool_registry


def reset_harness_providers() -> None:
    """Restore harness providers and integration-populated core leaves to defaults."""
    reset_integration_resolution()
    reset_tool_registry()
    reset_cli_llm()
    reset_repo_scope()
    reset_prompt_fragments()
    reset_message_context()
    reset_evidence_sources()
    reset_subprocess_presenter()
    reset_messaging_sources()
    clear_alert_source_detectors()
    clear_alert_source_routing()
    clear_source_aliases()
    clear_secondary_tool_sources()
    clear_alert_detail_fields()
    clear_taxonomy_profiles()
    clear_anchor_parsers()
