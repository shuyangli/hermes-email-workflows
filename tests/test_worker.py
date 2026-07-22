from __future__ import annotations

import json

from email_workflows.models import EmailMessage
from email_workflows.worker import WorkflowWorker


class PubSubMessage:
    def __init__(self, payload):
        self.data = json.dumps(payload).encode()
        self.acked = False
        self.nacked = False

    def ack(self):
        self.acked = True

    def nack(self):
        self.nacked = True


def test_pubsub_notification_processes_each_new_unread_message_and_advances_cursor():
    class Store:
        values = {"account_email": "me@example.com", "history_id": "10"}

        def get_setting(self, key, default=None):
            return self.values.get(key, default)

        def set_setting(self, key, value):
            self.values[key] = value

        def list_rules(self, account_email=None):
            return ["rule"]

    class Gmail:
        def history_message_ids(self, cursor):
            return ["m1", "m2"], "12"

        def fetch_message(self, message_id):
            labels = ["UNREAD"] if message_id == "m1" else []
            return EmailMessage(message_id, "t", None, "a", "b", "s", "body", 1, labels)

    class Engine:
        def __init__(self):
            self.processed = []

        def process(self, message, rules):
            self.processed.append(message.gmail_id)

    store, engine = Store(), Engine()
    worker = WorkflowWorker(store=store, gmail=Gmail(), engine=engine)
    event = PubSubMessage({"emailAddress": "me@example.com", "historyId": "12"})

    worker.handle_message(event)

    assert engine.processed == ["m1"]
    assert store.values["history_id"] == "12"
    assert event.acked is True and event.nacked is False


def test_notification_for_inactive_account_is_acked_without_processing():
    class Store:
        def get_setting(self, key, default=None):
            return "active@example.com" if key == "account_email" else default

    class Never:
        def __getattr__(self, name):
            raise AssertionError("must not use")

    event = PubSubMessage({"emailAddress": "old@example.com", "historyId": "12"})
    WorkflowWorker(Store(), Never(), Never()).handle_message(event)
    assert event.acked is True
