from __future__ import annotations

import json

from email_workflows.engine import WorkflowEngine
from email_workflows.models import EmailMessage, Rule, TaskResult
from email_workflows.store import Store
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


def test_recovery_redrives_stamped_but_incomplete_messages(tmp_path):
    # A message left `retryable` was already stamped processed, so it never appears in the
    # unprocessed inbox. Stale-history recovery must still re-drive it (regression:
    # previously abandoned).
    store = Store(tmp_path / "app.db")
    account = "me@example.com"
    store.set_setting("account_email", account)
    store.set_setting("topic_path", "projects/p/topics/t")
    store.claim_message(account, "stuck")
    store.finish_message(account, "stuck", "retryable", [1], "")

    processed: list[str] = []

    class Gmail:
        processed_label_id = "HEWPROC"

        def start_watch(self, topic):
            return {"historyId": "99", "expiration": "1"}

        def unprocessed_inbox_message_ids(self):
            return ["fresh"]  # note: "stuck" is stamped, absent here

        def fetch_message(self, message_id):
            labels = ["HEWPROC"] if message_id == "stuck" else []
            return EmailMessage(message_id, "t", None, "a", "b", "s", "body", 1, labels)

    class Engine:
        def process(self, message, rules, allow_rematch=False):
            processed.append(message.gmail_id)

    worker = WorkflowWorker(store, Gmail(), Engine())
    worker._recover_stale_history(account)

    assert "stuck" in processed and "fresh" in processed


def test_pubsub_notification_processes_each_unstamped_message_and_advances_cursor():
    class Store:
        values = {"account_email": "me@example.com", "history_id": "10"}

        def get_setting(self, key, default=None):
            return self.values.get(key, default)

        def set_setting(self, key, value):
            self.values[key] = value

        def list_rules(self, account_email=None):
            return ["rule"]

    class Gmail:
        processed_label_id = "HEWPROC"

        def history_message_ids(self, cursor):
            return ["m1", "m2"], "12"

        def fetch_message(self, message_id):
            labels = [] if message_id == "m1" else ["HEWPROC"]
            return EmailMessage(message_id, "t", None, "a", "b", "s", "body", 1, labels)

    class Engine:
        def __init__(self):
            self.processed = []

        def process(self, message, rules, allow_rematch=False):
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


def test_malformed_notification_is_acked_as_poison_message():
    class Store:
        values = {}

        def set_setting(self, key, value):
            self.values[key] = value

    class Never:
        def __getattr__(self, name):
            raise AssertionError("must not use")

    event = PubSubMessage({"historyId": "12"})
    store = Store()
    WorkflowWorker(store, Never(), Never()).handle_message(event)
    assert event.acked is True and event.nacked is False
    assert store.values["last_worker_error"] == "Malformed Pub/Sub notification"


def test_stale_history_recovers_from_current_unprocessed_mail():
    class Response:
        status = 404

    class StaleHistory(RuntimeError):
        resp = Response()

    class Store:
        values = {
            "account_email": "me@example.com",
            "history_id": "old",
            "topic_path": "projects/p/topics/t",
        }

        def get_setting(self, key, default=None):
            return self.values.get(key, default)

        def set_setting(self, key, value):
            self.values[key] = value

        def list_rules(self, account_email=None):
            return ["rule"]

    class Gmail:
        processed_label_id = "HEWPROC"

        def history_message_ids(self, cursor):
            raise StaleHistory()

        def start_watch(self, topic):
            return {"historyId": "fresh", "expiration": "999"}

        def unprocessed_inbox_message_ids(self):
            return ["m1"]

        def fetch_message(self, message_id):
            return EmailMessage(message_id, "t", None, "a", "b", "s", "body", 1, [])

    class Engine:
        processed = []

        def process(self, message, rules, allow_rematch=False):
            self.processed.append(message.gmail_id)

    store, engine = Store(), Engine()
    event = PubSubMessage({"emailAddress": "me@example.com", "historyId": "20"})
    WorkflowWorker(store, Gmail(), engine).handle_message(event)
    assert event.acked is True
    assert engine.processed == ["m1"]
    assert store.values["history_id"] == "fresh"


def test_worker_redelivery_retries_saved_notification_after_message_is_stamped(tmp_path):
    store = Store(tmp_path / "app.db")
    store.set_setting("account_email", "me@example.com")
    store.set_setting("history_id", "10")
    store.create_rule(Rule(None, "rule", "from:a", "summarize"))
    calls = {"run": 0, "send": 0}

    class Gmail:
        processed_label_id = "HEWPROC"
        labels: list[str] = []

        def history_message_ids(self, cursor):
            return ["m1"], "12"

        def fetch_message(self, message_id):
            return EmailMessage(message_id, "t", None, "a", "b", "s", "body", 1, list(self.labels))

        def mark_read(self, message_id):
            pass

        def add_processed_label(self, message_id):
            self.labels = ["HEWPROC"]

    class Matcher:
        def matching_rules(self, message, rules):
            return rules

    class Runner:
        def run(self, rule, message):
            calls["run"] += 1
            return TaskResult(rule.id, rule.name, True, "done")

    class Notifier:
        def send(self, text):
            calls["send"] += 1
            if calls["send"] == 1:
                raise RuntimeError("telegram unavailable")

    gmail = Gmail()
    engine = WorkflowEngine(store, Matcher(), gmail, Runner(), Notifier(), "me@example.com")
    worker = WorkflowWorker(store, gmail, engine)

    first = PubSubMessage({"emailAddress": "me@example.com", "historyId": "12"})
    worker.handle_message(first)
    assert first.nacked is True
    assert store.get_event("me@example.com", "m1")["status"].startswith("notification_pending:")

    second = PubSubMessage({"emailAddress": "me@example.com", "historyId": "12"})
    worker.handle_message(second)
    assert second.acked is True
    assert store.get_event("me@example.com", "m1")["status"] == "completed"
    assert calls == {"run": 1, "send": 2}


def test_background_sweep_stamps_terminal_unlabeled_messages(tmp_path):
    # A ledger row that is already terminal (completed, or unmatched past its rematch
    # window) but whose message never received the processed label — e.g. rows predating
    # the label — must be stamped by the sweep so it stops being listed.
    store = Store(tmp_path / "app.db")
    account = "me@example.com"
    store.set_setting("account_email", account)
    store.claim_message(account, "done")
    store.finish_message(account, "done", "completed", [1], "notified")
    stamped: list[str] = []

    class Gmail:
        processed_label_id = "HEWPROC"

        def fetch_message(self, message_id):
            return EmailMessage(message_id, "t", None, "a", "b", "s", "body", 1, [])

        def add_processed_label(self, message_id):
            stamped.append(message_id)

    engine = WorkflowEngine(store, None, Gmail(), None, None, account)
    worker = WorkflowWorker(store, Gmail(), engine)
    worker._process_ids(account, ["done"], background=True)
    assert stamped == ["done"]
    assert store.get_event(account, "done")["status"] == "completed"
