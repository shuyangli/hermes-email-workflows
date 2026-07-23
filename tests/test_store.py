from __future__ import annotations

import stat
from pathlib import Path

from email_workflows.models import Rule
from email_workflows.store import Store


def test_rule_crud_and_order(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    first = store.create_rule(Rule(None, "first", "from:a", "A ${subject}", priority=20))
    second = store.create_rule(Rule(None, "second", "from:b", "B ${body}", priority=10))

    assert [rule.name for rule in store.list_rules()] == ["second", "first"]
    updated = Rule(first.id, "renamed", "from:c", "C", enabled=False, priority=5)
    store.update_rule(updated)
    assert store.get_rule(first.id).name == "renamed"
    assert store.get_rule(first.id).enabled is False
    store.delete_rule(second.id)
    assert [rule.id for rule in store.list_rules()] == [first.id]


def test_rule_destination_round_trips(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    created = store.create_rule(
        Rule(None, "group", "from:a", "A", destination="telegram:-1001234567890")
    )

    assert store.get_rule(created.id).destination == "telegram:-1001234567890"

    created.destination = "telegram"
    store.update_rule(created)
    assert store.get_rule(created.id).destination == "telegram"


def test_existing_database_adds_destination_column(tmp_path: Path):
    import sqlite3

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            gmail_query TEXT NOT NULL,
            prompt_template TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 100,
            account_email TEXT,
            toolsets TEXT NOT NULL DEFAULT 'web',
            skills TEXT NOT NULL DEFAULT '',
            timeout_seconds INTEGER NOT NULL DEFAULT 300,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """INSERT INTO rules
            (name,gmail_query,prompt_template,created_at,updated_at)
            VALUES ('existing','from:a','A','now','now')"""
        )

    store = Store(path)
    assert store.list_rules()[0].destination == "telegram"
    created = store.create_rule(Rule(None, "legacy", "from:a", "A"))
    assert store.get_rule(created.id).destination == "telegram"


def test_database_is_private(tmp_path: Path):
    path = tmp_path / "app.db"
    Store(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_claim_message_is_idempotent(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    assert store.claim_message("me@example.com", "m1") is True
    assert store.claim_message("me@example.com", "m1") is False


def test_settings_round_trip(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.set_setting("project_id", "example-project")
    assert store.get_setting("project_id") == "example-project"
    assert store.get_setting("missing", "fallback") == "fallback"


def test_restart_recovers_interrupted_processing_claim(tmp_path: Path):
    path = tmp_path / "app.db"
    assert Store(path).claim_message("me@example.com", "m1") is True
    restarted = Store(path)
    assert restarted.get_event("me@example.com", "m1")["status"] == "retryable"
    assert restarted.claim_message("me@example.com", "m1") is True


def test_resumable_message_ids_returns_mid_flight_messages(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    account = "me@example.com"
    # A retryable message (task failed) and a notification-pending message (task ran,
    # delivery not confirmed) are both already marked read, so recovery must find them.
    store.claim_message(account, "retry")
    store.finish_message(account, "retry", "retryable", [1], "")
    store.claim_message(account, "pending")
    store.finish_message(account, "pending", "notification_pending:completed", [2], "note")
    store.claim_message(account, "done")
    store.finish_message(account, "done", "completed", [3], "note")
    store.claim_message(account, "nope")
    store.finish_message(account, "nope", "unmatched", [], "")

    assert set(store.resumable_message_ids(account)) == {"retry", "pending"}


def test_claim_for_rematch_only_reclaims_recent_unmatched(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    account = "me@example.com"
    store.claim_message(account, "fresh")
    store.finish_message(account, "fresh", "unmatched", [], "")

    # Recent unmatched → reclaimable for re-evaluation.
    assert store.claim_for_rematch(account, "fresh", within_seconds=3600) is True
    assert store.get_event(account, "fresh")["status"] == "processing"

    # A completed message is never reclaimed for re-match.
    store.claim_message(account, "done")
    store.finish_message(account, "done", "completed", [1], "note")
    assert store.claim_for_rematch(account, "done", within_seconds=3600) is False

    # Outside the recency window (window=0) an unmatched message is not reclaimed.
    store.claim_message(account, "stale")
    store.finish_message(account, "stale", "unmatched", [], "")
    assert store.claim_for_rematch(account, "stale", within_seconds=0) is False


def test_existing_data_permissions_are_repaired(tmp_path: Path):
    directory = tmp_path / "data"
    directory.mkdir(mode=0o755)
    path = directory / "app.db"
    Store(path)
    path.chmod(0o644)
    directory.chmod(0o755)
    wal = Path(f"{path}-wal")
    shm = Path(f"{path}-shm")
    wal.touch(mode=0o644)
    shm.touch(mode=0o644)

    Store(path)

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for sidecar in (wal, shm):
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
