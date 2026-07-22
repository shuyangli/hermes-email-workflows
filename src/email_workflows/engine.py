"""Orchestrate matching, execution, mailbox updates, and notification."""

from __future__ import annotations

from .models import EmailMessage, ProcessResult, Rule, TaskResult


class WorkflowEngine:
    def __init__(self, store, matcher, gmail, runner, notifier, account_email: str):
        self.store = store
        self.matcher = matcher
        self.gmail = gmail
        self.runner = runner
        self.notifier = notifier
        self.account_email = account_email

    def process(self, message: EmailMessage, rules: list[Rule]) -> ProcessResult:
        if not self.store.claim_message(self.account_email, message.gmail_id):
            event = (
                self.store.get_event(self.account_email, message.gmail_id)
                if hasattr(self.store, "get_event")
                else None
            )
            if event and event["status"] == "notification_pending":
                notification = event["notification"]
                self.notifier.send(notification)
                rule_ids = __import__("json").loads(event["matched_rule_ids"])
                self.store.finish_message(
                    self.account_email,
                    message.gmail_id,
                    "completed",
                    rule_ids,
                    notification,
                )
                return ProcessResult("completed", message.gmail_id, rule_ids, notification)
            return ProcessResult("duplicate", message.gmail_id)

        matched = self.matcher.matching_rules(message, rules)
        rule_ids = [rule.id for rule in matched if rule.id is not None]
        if not matched:
            self.store.finish_message(self.account_email, message.gmail_id, "unmatched", [], "")
            return ProcessResult("unmatched", message.gmail_id)

        # User-selected semantic: once any rule matches, mark read before task execution.
        self.gmail.mark_read(message.gmail_id)
        results = [self.runner.run(rule, message) for rule in matched]
        notification = self._format_notification(message, results)
        status = (
            "completed" if all(result.success for result in results) else "completed_with_errors"
        )
        # Persist the complete notification before delivery. A Pub/Sub redelivery can then
        # retry Telegram without re-running Hermes tasks that may have external side effects.
        self.store.finish_message(
            self.account_email,
            message.gmail_id,
            "notification_pending",
            rule_ids,
            notification,
        )
        self.notifier.send(notification)
        self.store.finish_message(
            self.account_email, message.gmail_id, status, rule_ids, notification
        )
        return ProcessResult(status, message.gmail_id, rule_ids, notification)

    @staticmethod
    def _format_notification(message: EmailMessage, results: list[TaskResult]) -> str:
        sections = [
            "📬 Email workflows completed",
            f"From: {message.sender}",
            f"Subject: {message.subject or '(no subject)'}",
        ]
        for result in results:
            icon = "✅" if result.success else "⚠️"
            sections.append(f"{icon} {result.rule_name}\n{result.output.strip()}")
        return "\n\n".join(sections)
