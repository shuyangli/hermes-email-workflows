from __future__ import annotations

import base64

from email_workflows.gmail import PROCESSED_LABEL_NAME, GmailClient


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class Messages:
    def __init__(self):
        self.modified = []
        self.list_queries = []

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
        self.list_queries.append(kwargs.get("q", ""))
        return Request({"messages": [{"id": "m1"}, {"id": "m2"}]})


class Labels:
    def __init__(self):
        self.existing = []
        self.created = []

    def list(self, **kwargs):
        return Request({"labels": self.existing})

    def create(self, **kwargs):
        self.created.append(kwargs["body"])
        return Request({"id": "Label_77", "name": kwargs["body"]["name"]})


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
        self.lbls = Labels()

    def messages(self):
        return self.msgs

    def history(self):
        return self.hist

    def labels(self):
        return self.lbls

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


def test_lists_current_unprocessed_inbox_messages():
    service = Service()
    assert GmailClient(service).unprocessed_inbox_message_ids() == ["m1", "m2"]
    assert service.u.msgs.list_queries == [f"in:inbox -label:{PROCESSED_LABEL_NAME}"]


def test_ensure_processed_label_reuses_existing_label():
    service = Service()
    service.u.lbls.existing = [{"id": "Label_5", "name": PROCESSED_LABEL_NAME}]
    client = GmailClient(service)
    assert client.ensure_processed_label() == "Label_5"
    assert service.u.lbls.created == []


def test_ensure_processed_label_creates_label_when_missing():
    service = Service()
    client = GmailClient(service)
    assert client.ensure_processed_label() == "Label_77"
    assert service.u.lbls.created[0]["name"] == PROCESSED_LABEL_NAME


def test_add_processed_label_stamps_message():
    service = Service()
    client = GmailClient(service, processed_label_id="Label_5")
    client.add_processed_label("m1")
    assert service.u.msgs.modified[0]["body"] == {"addLabelIds": ["Label_5"]}
