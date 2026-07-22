from __future__ import annotations

import base64

from email_workflows.gmail import GmailClient


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class Messages:
    def __init__(self):
        self.modified = []

    def get(self, **kwargs):
        body = base64.urlsafe_b64encode(b"hello world").decode().rstrip("=")
        return Request(
            {
                "id": kwargs["id"],
                "threadId": "t1",
                "internalDate": "1700000000000",
                "labelIds": ["INBOX", "UNREAD"],
                "payload": {
                    "mimeType": "multipart/alternative",
                    "headers": [
                        {"name": "Message-ID", "value": "<abc@example.com>"},
                        {"name": "From", "value": "Alerts <alerts@example.com>"},
                        {"name": "To", "value": "me@example.com"},
                        {"name": "Subject", "value": "Notice"},
                    ],
                    "parts": [{"mimeType": "text/plain", "body": {"data": body}}],
                },
            }
        )

    def modify(self, **kwargs):
        self.modified.append(kwargs)
        return Request({"id": kwargs["id"]})

    def list(self, **kwargs):
        return Request({"messages": [{"id": "m1"}, {"id": "m2"}]})


class History:
    def list(self, **kwargs):
        return Request(
            {
                "history": [
                    {
                        "id": "11",
                        "messagesAdded": [{"message": {"id": "m1"}}, {"message": {"id": "m2"}}],
                    },
                    {"id": "12", "messagesAdded": [{"message": {"id": "m1"}}]},
                ],
                "historyId": "12",
            }
        )


class Users:
    def __init__(self):
        self.msgs = Messages()
        self.hist = History()

    def messages(self):
        return self.msgs

    def history(self):
        return self.hist

    def getProfile(self, **kwargs):
        return Request({"emailAddress": "me@example.com", "historyId": "10"})


class Service:
    def __init__(self):
        self.u = Users()

    def users(self):
        return self.u


def test_fetch_message_extracts_headers_and_plain_text():
    service = Service()
    message = GmailClient(service).fetch_message("m1")
    assert message.sender == "Alerts <alerts@example.com>"
    assert message.subject == "Notice"
    assert message.body == "hello world"
    assert message.rfc822_message_id == "<abc@example.com>"


def test_mark_read_removes_unread_label():
    service = Service()
    GmailClient(service).mark_read("m1")
    assert service.u.msgs.modified[0]["body"] == {"removeLabelIds": ["UNREAD"]}


def test_history_message_ids_are_deduplicated_and_cursor_returned():
    ids, cursor = GmailClient(Service()).history_message_ids("10")
    assert ids == ["m1", "m2"]
    assert cursor == "12"


def test_lists_current_unread_inbox_messages():
    assert GmailClient(Service()).unread_inbox_message_ids() == ["m1", "m2"]
