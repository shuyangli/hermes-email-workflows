"""Provision the Pub/Sub resources required by Gmail push notifications."""

from __future__ import annotations

from dataclasses import dataclass

GMAIL_PUBLISHER = "serviceAccount:gmail-api-push@system.gserviceaccount.com"


@dataclass(slots=True)
class PubSubResources:
    topic: str
    subscription: str


class GoogleSetup:
    def __init__(self, pubsub_service):
        self.pubsub = pubsub_service

    def ensure_pubsub(
        self, project_id: str, topic_id: str, subscription_id: str
    ) -> PubSubResources:
        topic = f"projects/{project_id}/topics/{topic_id}"
        subscription = f"projects/{project_id}/subscriptions/{subscription_id}"
        topics = self.pubsub.projects().topics()
        subscriptions = self.pubsub.projects().subscriptions()

        if not self._resource_exists(lambda: topics.get(topic=topic).execute()):
            topics.create(name=topic, body={}).execute()

        policy = topics.getIamPolicy(resource=topic, body={}).execute()
        bindings = list(policy.get("bindings", []))
        publisher = next(
            (binding for binding in bindings if binding.get("role") == "roles/pubsub.publisher"),
            None,
        )
        if publisher is None:
            bindings.append({"role": "roles/pubsub.publisher", "members": [GMAIL_PUBLISHER]})
        elif GMAIL_PUBLISHER not in publisher.setdefault("members", []):
            publisher["members"].append(GMAIL_PUBLISHER)
        policy["bindings"] = bindings
        topics.setIamPolicy(resource=topic, body={"policy": policy}).execute()

        if not self._resource_exists(
            lambda: subscriptions.get(subscription=subscription).execute()
        ):
            subscriptions.create(
                name=subscription,
                body={
                    "topic": topic,
                    "ackDeadlineSeconds": 60,
                    "messageRetentionDuration": "604800s",
                    "expirationPolicy": {},
                },
            ).execute()
        else:
            existing = subscriptions.get(subscription=subscription).execute()
            if existing.get("topic") != topic:
                raise RuntimeError(
                    f"Existing subscription {subscription} points to {existing.get('topic')}, "
                    f"not {topic}"
                )
        return PubSubResources(topic, subscription)

    @staticmethod
    def _resource_exists(getter) -> bool:
        try:
            getter()
            return True
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status == 404:
                return False
            raise
