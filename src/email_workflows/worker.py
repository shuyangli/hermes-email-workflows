"""Long-running Pub/Sub pull worker."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from google.cloud import pubsub_v1

from .auth import build_services, load_credentials
from .engine import WorkflowEngine
from .gmail import GmailClient
from .notifier import TelegramNotifier
from .rules import GmailQueryMatcher
from .runner import HermesRunner

logger = logging.getLogger(__name__)


class WorkflowWorker:
    def __init__(self, store, gmail, engine, subscriber=None):
        self.store = store
        self.gmail = gmail
        self.engine = engine
        self.subscriber = subscriber
        self._future = None
        self._stop = threading.Event()
        self._renewer: threading.Thread | None = None

    @classmethod
    def from_store(cls, store):
        token_path = store.get_setting("token_path", "") or ""
        subscription = store.get_setting("subscription_path", "") or ""
        if not token_path or not Path(token_path).expanduser().exists() or not subscription:
            return cls(store, None, None)
        credentials = load_credentials(token_path)
        gmail_service, _ = build_services(credentials)
        gmail = GmailClient(gmail_service)
        account = store.get_setting("account_email", "") or gmail.profile()["emailAddress"]
        engine = WorkflowEngine(
            store,
            GmailQueryMatcher(gmail_service),
            gmail,
            HermesRunner(),
            TelegramNotifier(),
            account,
        )
        subscriber = pubsub_v1.SubscriberClient(credentials=credentials)
        worker = cls(store, gmail, engine, subscriber)
        worker.subscription_path = subscription
        return worker

    def start_if_configured(self) -> bool:
        if not self.subscriber or not getattr(self, "subscription_path", ""):
            self.store.set_setting("worker_status", "waiting_for_oauth")
            return False
        self._future = self.subscriber.subscribe(
            self.subscription_path, callback=self.handle_message
        )
        self.store.set_setting("worker_status", "running")
        self._renewer = threading.Thread(
            target=self._renew_loop, daemon=True, name="gmail-watch-renewer"
        )
        self._renewer.start()
        logger.info("Pub/Sub subscriber started: %s", self.subscription_path)
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._future:
            self._future.cancel()
        if self.subscriber:
            self.subscriber.close()

    def handle_message(self, pubsub_message) -> None:
        try:
            payload = json.loads(pubsub_message.data.decode("utf-8"))
            account = self.store.get_setting("account_email", "") or ""
            if payload.get("emailAddress", "").lower() != account.lower():
                pubsub_message.ack()
                return
            cursor = self.store.get_setting("history_id", "") or ""
            if not cursor:
                self.store.set_setting("history_id", str(payload["historyId"]))
                pubsub_message.ack()
                return
            message_ids, latest = self.gmail.history_message_ids(cursor)
            rules = self.store.list_rules(account)
            for message_id in message_ids:
                message = self.gmail.fetch_message(message_id)
                if "UNREAD" in message.labels:
                    self.engine.process(message, rules)
            self.store.set_setting("history_id", latest)
            self.store.set_setting("last_notification_at", str(int(time.time())))
            pubsub_message.ack()
        except Exception:
            logger.exception("Failed to process Gmail Pub/Sub notification")
            self.store.set_setting("worker_status", "error")
            pubsub_message.nack()

    def _renew_loop(self) -> None:
        while not self._stop.wait(6 * 60 * 60):
            try:
                expiration = int(self.store.get_setting("watch_expiration", "0") or 0)
                if expiration - int(time.time() * 1000) < 24 * 60 * 60 * 1000:
                    topic = self.store.get_setting("topic_path", "") or ""
                    response = self.gmail.start_watch(topic)
                    # A renewal's historyId is a new watch boundary, not a safe sync
                    # cursor. Keep draining from the existing cursor to avoid gaps.
                    self.store.set_setting("watch_expiration", str(response["expiration"]))
                    self.store.set_setting("last_watch_renewal_at", str(int(time.time())))
                    self.store.set_setting("worker_status", "running")
            except Exception:
                logger.exception("Failed to renew Gmail watch")
                self.store.set_setting("worker_status", "watch_renewal_error")
