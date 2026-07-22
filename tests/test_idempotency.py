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
            return TaskResult(rule.id, rule.name, True, "result")

    class Notifier:
        def send(self, text):
            calls["send"] += 1
            if calls["send"] == 1:
                raise RuntimeError("telegram unavailable")

    engine = WorkflowEngine(store, Matcher(), Gmail(), Runner(), Notifier(), "me")
    with pytest.raises(RuntimeError, match="telegram unavailable"):
        engine.process(email, [rule])

    assert store.get_event("me", "m1")["status"] == "notification_pending"
    result = engine.process(email, [rule])
    assert result.status == "completed"
    assert calls == {"run": 1, "read": 1, "send": 2}
