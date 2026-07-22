"""Small Gmail API facade used by the worker and rule engine."""

from __future__ import annotations

import base64
import html
import re

from .models import EmailMessage


class GmailClient:
    def __init__(self, service):
        self.service = service

    def profile(self) -> dict:
        return self.service.users().getProfile(userId="me").execute()

    def fetch_message(self, message_id: str) -> EmailMessage:
        raw = (
            self.service.users().messages().get(userId="me", id=message_id, format="full").execute()
        )
        headers = {
            item.get("name", "").lower(): item.get("value", "")
            for item in raw.get("payload", {}).get("headers", [])
        }
        body = _extract_body(raw.get("payload", {}))[:100_000]
        return EmailMessage(
            gmail_id=raw["id"],
            thread_id=raw.get("threadId", ""),
            rfc822_message_id=headers.get("message-id") or None,
            sender=headers.get("from", ""),
            recipients=headers.get("to", ""),
            subject=headers.get("subject", ""),
            body=body,
            internal_date_ms=int(raw.get("internalDate", 0)),
            labels=list(raw.get("labelIds", [])),
        )

    def mark_read(self, message_id: str) -> None:
        self.service.users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()

    def start_watch(self, topic_path: str) -> dict:
        return (
            self.service.users()
            .watch(
                userId="me",
                body={"topicName": topic_path},
            )
            .execute()
        )

    def stop_watch(self) -> None:
        self.service.users().stop(userId="me").execute()

    def history_message_ids(self, start_history_id: str) -> tuple[list[str], str]:
        request = (
            self.service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                labelId="INBOX",
                maxResults=500,
            )
        )
        ids: list[str] = []
        seen: set[str] = set()
        latest = start_history_id
        while request is not None:
            response = request.execute()
            latest = str(response.get("historyId", latest))
            for history in response.get("history", []):
                latest = str(max(int(latest), int(history.get("id", latest))))
                for added in history.get("messagesAdded", []):
                    message_id = added.get("message", {}).get("id")
                    if message_id and message_id not in seen:
                        seen.add(message_id)
                        ids.append(message_id)
            token = response.get("nextPageToken")
            if not token:
                break
            request = (
                self.service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    labelId="INBOX",
                    maxResults=500,
                    pageToken=token,
                )
            )
        return ids, latest


def _decode(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")


def _extract_body(payload: dict) -> str:
    plain: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            html_parts.append(_decode(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    if plain:
        return "\n\n".join(text.strip() for text in plain if text.strip())[:200_000]
    if html_parts:
        text = "\n".join(html_parts)
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        return html.unescape(" ".join(text.split()))[:200_000]
    return ""
