"""Durable admission and terminal-before-create cleanup races."""

import json
from pathlib import Path
from threading import Event

import pytest

from gateway.transports.feishu.reaction_ledger import ReactionLedger
from gateway.transports.feishu.reaction_lifecycle import (
    AckAdmission,
    AckOutcome,
    ReactionLifecycleManager,
)
from integrations.feishu.reactions import FeishuReaction


class ReactionClient:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.deleted = Event()
        self.added: list[str] = []
        self.removed: list[tuple[str, str]] = []
        self.reactions: tuple[FeishuReaction, ...] = ()
        self.fail_delete = False

    def add_eye(self, message_id: str) -> FeishuReaction:
        self.added.append(message_id)
        self.entered.set()
        assert self.release.wait(5)
        return FeishuReaction("reaction", "app", "app")

    def delete(self, message_id: str, reaction_id: str) -> None:
        if self.fail_delete:
            raise RuntimeError("sensitive detail")
        self.removed.append((message_id, reaction_id))
        self.deleted.set()

    def list_eye(self, message_id: str) -> tuple[FeishuReaction, ...]:
        assert message_id
        return self.reactions


def test_terminal_before_add_and_replay(tmp_path: Path) -> None:
    path = tmp_path / "ack.jsonl"
    client = ReactionClient()
    manager = ReactionLifecycleManager(path=path, client=client, app_id="app")
    try:
        assert manager.begin("message") == AckAdmission.ADMITTED
        assert client.entered.wait(5)
        assert manager.begin("message") == AckAdmission.DUPLICATE
        manager.finish("message", AckOutcome.CANCELLED)
        client.release.set()
        assert client.deleted.wait(5)
    finally:
        client.release.set()
        manager.shutdown(timeout_seconds=5)
    restarted = ReactionLifecycleManager(path=path, client=client, app_id="app")
    try:
        assert restarted.begin("message") == AckAdmission.DUPLICATE
        assert client.added == ["message"]
        assert client.removed == [("message", "reaction")]
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert rows[-1]["state"] == "removed"
        assert rows[-1]["outcome"] == "cancelled"
    finally:
        restarted.shutdown(timeout_seconds=5)


def test_queue_is_bounded_and_corrupt_ledger_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "ack.jsonl"
    client = ReactionClient()
    manager = ReactionLifecycleManager(path=path, client=client, app_id="app", queue_limit=1)
    try:
        assert manager.begin("first") == AckAdmission.ADMITTED
        assert client.entered.wait(5)
        assert manager.begin("second") == AckAdmission.UNTRACKED
        manager.shutdown(timeout_seconds=0)
        assert manager.begin("third") == AckAdmission.UNTRACKED
        client.release.set()
        assert client.deleted.wait(5)
    finally:
        client.release.set()
        manager.shutdown(timeout_seconds=5)
    corrupt = tmp_path / "corrupt.jsonl"
    corrupt.write_text('{"partial":', encoding="utf-8")
    manager = ReactionLifecycleManager(path=corrupt, client=client, app_id="app")
    try:
        assert manager.begin("new") == AckAdmission.UNTRACKED
        assert corrupt.read_text() == '{"partial":'
    finally:
        manager.shutdown(timeout_seconds=5)


def test_restart_reconciles_only_proven_app_owned_reaction(tmp_path: Path) -> None:
    path = tmp_path / "ack.jsonl"
    path.write_text(
        json.dumps(
            {
                "message_id": "message",
                "reaction_id": "",
                "operator_id": "",
                "operator_type": "",
                "state": "adding",
                "outcome": "",
                "created_at": 1.0,
                "updated_at": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    client = ReactionClient()
    client.reactions = (
        FeishuReaction("user-reaction", "user", "user"),
        FeishuReaction("other-app", "other", "app"),
        FeishuReaction("ours", "app", "app"),
    )
    manager = ReactionLifecycleManager(path=path, client=client, app_id="app")
    try:
        manager.reconcile(budget_seconds=5, record_limit=10)
        assert client.deleted.wait(5)
    finally:
        manager.shutdown(timeout_seconds=5)
    assert client.removed == [("message", "ours")]


@pytest.mark.parametrize("outcome", list(AckOutcome))
def test_terminal_outcomes_remove_once(tmp_path: Path, outcome: AckOutcome) -> None:
    client = ReactionClient()
    client.release.set()
    manager = ReactionLifecycleManager(path=tmp_path / "ack.jsonl", client=client, app_id="app")
    try:
        assert manager.begin("m") == AckAdmission.ADMITTED
        manager.finish("m", outcome)
        manager.finish("m", AckOutcome.FAILURE)
        assert client.deleted.wait(5)
    finally:
        manager.shutdown(timeout_seconds=5)
    assert client.removed == [("m", "reaction")]


def test_failed_cleanup_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "ack.jsonl"
    client = ReactionClient()
    client.release.set()
    client.fail_delete = True
    client.reactions = (FeishuReaction("reaction", "app", "app"),)
    manager = ReactionLifecycleManager(path=path, client=client, app_id="app")
    manager.begin("m")
    manager.finish("m", AckOutcome.SUCCESS)
    manager.shutdown(timeout_seconds=5)
    record = ReactionLedger(path).find("m")
    assert record is not None and record.state == "remove_failed"
    client.fail_delete = False
    restarted = ReactionLifecycleManager(path=path, client=client, app_id="app")
    try:
        restarted.reconcile(budget_seconds=5, record_limit=10)
        assert client.deleted.wait(5)
    finally:
        restarted.shutdown(timeout_seconds=5)
    assert client.added == ["m"]


def test_compaction_only_expires_completed_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "ack.jsonl"
    records = [
        {
            "message_id": message_id,
            "state": state,
            "updated_at": 1,
            "created_at": 1,
            "outcome": "success",
        }
        for message_id, state in (("cleaned", "removed"), ("pending", "remove_failed"))
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    ledger = ReactionLedger(path)
    ledger.compact()
    assert ledger.find("cleaned") is None
    assert ledger.find("pending") is not None


def test_restart_confirms_already_deleted_reaction_without_recreating(tmp_path: Path) -> None:
    from gateway.transports.feishu.reaction_ledger import AckRecord

    path = tmp_path / "ack.jsonl"
    ledger = ReactionLedger(path)
    ledger.change(
        "m",
        lambda _previous: AckRecord(
            message_id="m",
            reaction_id="gone",
            operator_id="app",
            operator_type="app",
            state="removing",
            outcome="success",
            created_at=1,
            updated_at=1,
        ),
    )
    client = ReactionClient()
    client.fail_delete = True
    manager = ReactionLifecycleManager(path=path, client=client, app_id="app")
    manager.reconcile(budget_seconds=5, record_limit=10)
    manager.shutdown(timeout_seconds=5)
    record = ledger.find("m")
    assert record is not None and record.state == "removed"
    assert not client.added


def test_recovery_limit_counts_pending_records_not_completed_history(tmp_path: Path) -> None:
    import time

    from gateway.transports.feishu.reaction_ledger import AckRecord

    path = tmp_path / "ack.jsonl"
    ledger = ReactionLedger(path)
    ledger.change(
        "done",
        lambda _previous: AckRecord(
            message_id="done",
            state="removed",
            outcome="success",
            updated_at=time.time(),
        ),
    )
    ledger.change(
        "pending",
        lambda _previous: AckRecord(
            message_id="pending",
            reaction_id="r",
            state="active",
            updated_at=time.time(),
        ),
    )
    client = ReactionClient()
    manager = ReactionLifecycleManager(path=path, client=client, app_id="app")
    manager.reconcile(budget_seconds=5, record_limit=1)
    manager.shutdown(timeout_seconds=5)
    assert client.removed == [("pending", "r")]


def test_empty_inbound_id_cannot_poison_admission_ledger(tmp_path: Path) -> None:
    path = tmp_path / "ack.jsonl"
    client = ReactionClient()
    client.release.set()
    manager = ReactionLifecycleManager(path=path, client=client, app_id="app")
    try:
        assert manager.begin("") == AckAdmission.UNTRACKED
        assert not path.exists()
    finally:
        manager.shutdown(timeout_seconds=5)
