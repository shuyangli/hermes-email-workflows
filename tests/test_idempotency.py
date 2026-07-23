from __future__ import annotations

from pathlib import Path

import pytest

from email_workflows.engine import WorkflowEngine
from email_workflows.models import EmailMessage, Rule, TaskResult
from email_workflows.store import Store


def test_redelivery_after_telegram_failure_retries_notification_without_rerunning_task(
    tmp_path: Path,
):
    store = Store(tmp_path / "app.db")
    email = EmailMessage("m1", "t", None, "a", "b", "s", "body", 1)
    rule = Rule(1, "rule", "x", "x")
    calls = {"run": 0, "read": 0, "send": 0}

    class Matcher:
        def matching_rules(self, message, rules):
            return [rule]

    class Gmail:
        def mark_read(self, message_id):
            calls["read"] += 1

    class Runner:
        def run(self, rule, message):
            calls["run"] += 1
            return TaskResult(rule.id, rule.name, False, "task failed")

    class Notifier:
        def send(self, text):
            calls["send"] += 1
            if calls["send"] == 1:
                raise RuntimeError("telegram unavailable")

    engine = WorkflowEngine(store, Matcher(), Gmail(), Runner(), Notifier(), "me")
    with pytest.raises(RuntimeError, match="telegram unavailable"):
        engine.process(email, [rule])

    assert store.get_event("me", "m1")["status"] == "notification_pending:completed_with_errors"
    result = engine.process(email, [rule])
    assert result.status == "completed_with_errors"
    assert calls == {"run": 1, "read": 1, "send": 2}


def test_processing_failure_is_retryable(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    email = EmailMessage("m1", "t", None, "a", "b", "s", "body", 1)

    class Matcher:
        calls = 0

        def matching_rules(self, message, rules):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("gmail unavailable")
            return []

    class Never:
        def __getattr__(self, name):
            raise AssertionError("must not use")

    engine = WorkflowEngine(store, Matcher(), Never(), Never(), Never(), "me")
    with pytest.raises(RuntimeError, match="gmail unavailable"):
        engine.process(email, [])
    assert store.get_event("me", "m1")["status"] == "retryable"
    assert engine.process(email, []).status == "unmatched"


def test_redelivery_retries_only_destinations_not_already_sent(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    email = EmailMessage("m1", "t", None, "a", "b", "s", "body", 1)
    rules = [
        Rule(1, "home", "x", "x"),
        Rule(2, "group", "x", "x", destination="telegram:-1001234567890"),
    ]
    calls = []

    class Matcher:
        def matching_rules(self, message, candidates):
            return candidates

    class Gmail:
        def mark_read(self, message_id):
            pass

    class Runner:
        def run(self, rule, message):
            return TaskResult(rule.id, rule.name, True, rule.name)

    class Notifier:
        def send(self, text, destination="telegram"):
            calls.append(destination)
            if destination != "telegram" and calls.count(destination) == 1:
                raise RuntimeError("group unavailable")

    engine = WorkflowEngine(store, Matcher(), Gmail(), Runner(), Notifier(), "me")
    with pytest.raises(RuntimeError, match="group unavailable"):
        engine.process(email, rules)

    assert calls == ["telegram", "telegram:-1001234567890"]
    result = engine.process(email, rules)
    assert result.status == "completed"
    assert calls == ["telegram", "telegram:-1001234567890", "telegram:-1001234567890"]


def test_legacy_plaintext_pending_notification_retries_to_home(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    store.claim_message("me", "m1")
    store.finish_message("me", "m1", "notification_pending:completed", [1], "legacy")
    deliveries = []

    class Notifier:
        def send(self, text):
            deliveries.append(text)

    class Never:
        def __getattr__(self, name):
            raise AssertionError("pending retry must not rerun workflow")

    email = EmailMessage("m1", "t", None, "a", "b", "s", "body", 1)
    result = WorkflowEngine(store, Never(), Never(), Never(), Notifier(), "me").process(email, [])

    assert result.status == "completed"
    assert deliveries == ["legacy"]
