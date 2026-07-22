"""Evaluate dashboard rules using Gmail's own search language."""

from __future__ import annotations

from .models import EmailMessage, Rule


class GmailQueryMatcher:
    def __init__(self, gmail_service):
        self.gmail = gmail_service

    def matching_rules(self, message: EmailMessage, rules: list[Rule]) -> list[Rule]:
        return [rule for rule in rules if rule.enabled and self.matches(message, rule.gmail_query)]

    def matches(self, message: EmailMessage, gmail_query: str) -> bool:
        query = self._scoped_query(message, gmail_query)
        request = (
            self.gmail.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=10,
                includeSpamTrash=False,
            )
        )
        while request is not None:
            response = request.execute()
            if any(item.get("id") == message.gmail_id for item in response.get("messages", [])):
                return True
            token = response.get("nextPageToken")
            if not token:
                break
            request = (
                self.gmail.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=100,
                    includeSpamTrash=False,
                    pageToken=token,
                )
            )
        return False

    @staticmethod
    def _scoped_query(message: EmailMessage, gmail_query: str) -> str:
        if message.rfc822_message_id:
            message_id = message.rfc822_message_id.strip().strip("<>")
            return f"({gmail_query}) rfc822msgid:{message_id} is:unread"
        seconds = max(message.internal_date_ms // 1000, 1)
        return f"({gmail_query}) after:{seconds - 86400} before:{seconds + 86400} is:unread"
