from __future__ import annotations

from core.domain.background_investigations import BackgroundInvestigationRecord
from infrastructure.delivery.notifications.rca_summary import (
    format_background_rca_markdown,
)


def _record() -> BackgroundInvestigationRecord:
    return BackgroundInvestigationRecord(
        task_id="task`id",
        status="completed",
        command="/investigate `checkout`",
        root_cause="The dependency timed out.",
        top_analysis=("The API returned 503.",),
        next_steps=("Roll back the change.",),
        stats={"tool_call_count": 3, "investigation_loop_count": 2, "validity_score": 0.875},
    )


def test_background_summary_is_bounded_markdown_with_all_sections() -> None:
    body = format_background_rca_markdown(_record())

    assert body.startswith("# OpenSRE background investigation completed")
    assert "## Root cause" in body
    assert "## Top analysis" in body
    assert "## What to do next" in body
    assert "## Internal stats" in body
    assert "<b>" not in body
    assert "task`id" not in body
    assert "checkout`" not in body
    assert len(body) <= 4096


def test_background_summary_uses_unavailable_for_empty_item_groups() -> None:
    record = _record()
    record.top_analysis = ()
    record.next_steps = ()

    body = format_background_rca_markdown(record)

    assert body.count("- Unavailable") == 2
