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
