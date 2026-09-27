"""Transport-neutral feedback persistence."""

from gateway.core.storage.feedback.idempotent import FeedbackWriteResult, append_feedback_entry_once
from gateway.core.storage.feedback.jsonl import append_feedback_entry

__all__ = ["FeedbackWriteResult", "append_feedback_entry", "append_feedback_entry_once"]
