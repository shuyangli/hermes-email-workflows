"""Orchestrate matching, execution, mailbox updates, and notification."""

from __future__ import annotations

import json

from .models import EmailMessage, ProcessResult, Rule, TaskResult


class WorkflowEngine:
    # Window during which an ``unmatched`` message stays eligible for re-evaluation, to
    # absorb Gmail search-index lag and rules added shortly after the message arrived.
    REMATCH_WINDOW_SECONDS = 60 * 60

    def __init__(self, store, matcher, gmail, runner, notifier, account_email: str):
        self.store = store
        self.matcher = matcher
        self.gmail = gmail
        self.runner = runner
        self.notifier = notifier
        self.account_email = account_email

    def process(
        self, message: EmailMessage, rules: list[Rule], allow_rematch: bool = False
    ) -> ProcessResult:
        if not self.store.claim_message(self.account_email, message.gmail_id):
            event = (
                self.store.get_event(self.account_email, message.gmail_id)
                if hasattr(self.store, "get_event")
                else None
            )
            if event and event["status"].startswith("notification_pending:"):
                deliveries = self._decode_deliveries(event["notification"])
                rule_ids = json.loads(event["matched_rule_ids"])
                final_status = event["status"].split(":", 1)[1]
                notification = self._deliver_pending(
                    message.gmail_id, final_status, rule_ids, deliveries
                )
                return ProcessResult(final_status, message.gmail_id, rule_ids, notification)
            reclaimed = (
                allow_rematch
                and event
                and event["status"] == "unmatched"
                and hasattr(self.store, "claim_for_rematch")
                and self.store.claim_for_rematch(
                    self.account_email, message.gmail_id, self.REMATCH_WINDOW_SECONDS
                )
            )
            if not reclaimed:
                return ProcessResult("duplicate", message.gmail_id)

        rule_ids: list[int] = []
        notification = ""
        try:
            matched = self.matcher.matching_rules(message, rules)
            rule_ids = [rule.id for rule in matched if rule.id is not None]
            if not matched:
                self.store.finish_message(self.account_email, message.gmail_id, "unmatched", [], "")
                return ProcessResult("unmatched", message.gmail_id)

            # User-selected semantic: once any rule matches, mark read before task execution.
            self.gmail.mark_read(message.gmail_id)
            rule_results = [(rule, self.runner.run(rule, message)) for rule in matched]
            visible_rule_results = [
                (rule, result)
                for rule, result in rule_results
                if not (result.success and result.output.strip() == "NO_NOTIFICATION")
            ]
            if not visible_rule_results:
                status = "completed_silent"
                self.store.finish_message(
                    self.account_email,
                    message.gmail_id,
                    status,
                    rule_ids,
                    "",
                )
                return ProcessResult(status, message.gmail_id, rule_ids, "")

            grouped: dict[str, list[TaskResult]] = {}
            for rule, result in visible_rule_results:
                grouped.setdefault(rule.destination or "telegram", []).append(result)
            deliveries = [
                {
                    "destination": destination,
                    "message": self._format_notification(message, destination_results),
                    "sent": False,
                }
                for destination, destination_results in grouped.items()
            ]
            notification = self._combine_delivery_messages(deliveries)
            status = (
                "completed"
                if all(result.success for _, result in rule_results)
                else "completed_with_errors"
            )
            # Persist before delivery so Telegram can retry without rerunning Hermes tasks.
            self.store.finish_message(
                self.account_email,
                message.gmail_id,
                f"notification_pending:{status}",
                rule_ids,
                self._encode_deliveries(deliveries),
            )
        except Exception:
            self.store.finish_message(
                self.account_email, message.gmail_id, "retryable", rule_ids, notification
            )
            raise
        notification = self._deliver_pending(message.gmail_id, status, rule_ids, deliveries)
        return ProcessResult(status, message.gmail_id, rule_ids, notification)

    def _deliver_pending(
        self,
        message_id: str,
        final_status: str,
        rule_ids: list[int],
        deliveries: list[dict],
    ) -> str:
        pending_status = f"notification_pending:{final_status}"
        for delivery in deliveries:
            if delivery.get("sent"):
                continue
            destination = delivery.get("destination") or "telegram"
            if destination == "telegram":
                self.notifier.send(delivery["message"])
            else:
                self.notifier.send(delivery["message"], destination)
            delivery["sent"] = True
            self.store.finish_message(
                self.account_email,
                message_id,
                pending_status,
                rule_ids,
                self._encode_deliveries(deliveries),
            )
        notification = self._combine_delivery_messages(deliveries)
        self.store.finish_message(
            self.account_email, message_id, final_status, rule_ids, notification
        )
        return notification

    @staticmethod
    def _encode_deliveries(deliveries: list[dict]) -> str:
        return "__DELIVERIES_V1__" + json.dumps(deliveries, separators=(",", ":"))

    @staticmethod
    def _decode_deliveries(value: str) -> list[dict]:
        prefix = "__DELIVERIES_V1__"
        if value.startswith(prefix):
            decoded = json.loads(value[len(prefix) :])
            if isinstance(decoded, list):
                return decoded
        # Backward compatibility for notification-pending rows from older releases.
        return [{"destination": "telegram", "message": value, "sent": False}]

    @staticmethod
    def _combine_delivery_messages(deliveries: list[dict]) -> str:
        return "\n\n---\n\n".join(delivery["message"] for delivery in deliveries)

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
