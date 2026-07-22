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
