from __future__ import annotations

from email_workflows.google_setup import GoogleSetup


class Response:
    def __init__(self, status: int):
        self.status = status


class ApiError(RuntimeError):
    def __init__(self, status):
        super().__init__(str(status))
        self.resp = Response(status)


class Request:
    def __init__(self, value=None):
        self.value = value or {}

    def execute(self):
        return self.value


class Topics:
    def __init__(self):
        self.created = []
        self.policy = None

    def get(self, **kwargs) -> Request:
        raise ApiError(404)

    def create(self, **kwargs):
        self.created.append(kwargs)
        return Request({"name": kwargs["name"]})

    def getIamPolicy(self, **kwargs):
        return Request({"bindings": []})

    def setIamPolicy(self, **kwargs):
        self.policy = kwargs["body"]
        return Request(self.policy)


class Subscriptions:
    def __init__(self):
        self.created = []

    def get(self, **kwargs) -> Request:
        raise ApiError(404)

    def create(self, **kwargs):
        self.created.append(kwargs)
        return Request({"name": kwargs["name"]})


class Projects:
    def __init__(self, topics, subs):
        self._topics = topics
        self._subs = subs

    def topics(self):
        return self._topics

    def subscriptions(self):
        return self._subs


class PubSub:
    def __init__(self):
        self.topic_api = Topics()
        self.sub_api = Subscriptions()
        self.p = Projects(self.topic_api, self.sub_api)

    def projects(self):
        return self.p


def test_ensure_pubsub_creates_resources_and_grants_gmail_publisher():
    pubsub = PubSub()
    setup = GoogleSetup(pubsub)
    result = setup.ensure_pubsub("my-project", "gmail-events", "gmail-events-local")

    assert result.topic == "projects/my-project/topics/gmail-events"
    assert pubsub.topic_api.policy["policy"]["bindings"] == [
        {
            "role": "roles/pubsub.publisher",
            "members": ["serviceAccount:gmail-api-push@system.gserviceaccount.com"],
        }
    ]
    assert pubsub.sub_api.created[0]["body"]["topic"] == result.topic


def test_resource_lookup_propagates_permission_errors():
    try:
        GoogleSetup._resource_exists(lambda: (_ for _ in ()).throw(ApiError(403)))
    except ApiError as exc:
        assert exc.resp.status == 403
    else:
        raise AssertionError("expected permission error")


class RecordingTopics(Topics):
    """Fails the test if any topic API call is made."""

    def get(self, **kwargs):
        raise AssertionError("preprovisioned mode must not call topics.get")

    def create(self, **kwargs):
        raise AssertionError("preprovisioned mode must not create topics")

    def getIamPolicy(self, **kwargs):
        raise AssertionError("preprovisioned mode must not read IAM")

    def setIamPolicy(self, **kwargs):
        raise AssertionError("preprovisioned mode must not write IAM")


def _preprovisioned_pubsub(subscriptions_cls):
    pubsub = PubSub()
    pubsub.topic_api = RecordingTopics()
    pubsub.sub_api = subscriptions_cls()
    pubsub.p = Projects(pubsub.topic_api, pubsub.sub_api)
    return pubsub


def test_preprovisioned_mode_validates_without_touching_topic_or_iam():
    class ExistingSubscriptions(Subscriptions):
        def get(self, **kwargs):
            return Request({"topic": "projects/my-project/topics/gmail-events"})

    pubsub = _preprovisioned_pubsub(ExistingSubscriptions)
    result = GoogleSetup(pubsub).ensure_pubsub(
        "my-project", "gmail-events", "gmail-events-local", preprovisioned=True
    )
    assert result.topic == "projects/my-project/topics/gmail-events"
    assert result.subscription == "projects/my-project/subscriptions/gmail-events-local"
    assert pubsub.sub_api.created == []


def test_preprovisioned_mode_rejects_subscription_on_wrong_topic():
    class WrongTopicSubscriptions(Subscriptions):
        def get(self, **kwargs):
            return Request({"topic": "projects/my-project/topics/other"})

    pubsub = _preprovisioned_pubsub(WrongTopicSubscriptions)
    try:
        GoogleSetup(pubsub).ensure_pubsub(
            "my-project", "gmail-events", "gmail-events-local", preprovisioned=True
        )
    except RuntimeError as exc:
        assert "topics/other" in str(exc)
    else:
        raise AssertionError("expected topic mismatch")


def test_preprovisioned_mode_explains_missing_subscription():
    pubsub = _preprovisioned_pubsub(Subscriptions)  # get() raises 404
    try:
        GoogleSetup(pubsub).ensure_pubsub(
            "my-project", "gmail-events", "gmail-events-local", preprovisioned=True
        )
    except RuntimeError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("expected missing-subscription error")


def test_preprovisioned_mode_explains_permission_denial():
    class DeniedSubscriptions(Subscriptions):
        def get(self, **kwargs):
            raise ApiError(403)

    pubsub = _preprovisioned_pubsub(DeniedSubscriptions)
    try:
        GoogleSetup(pubsub).ensure_pubsub(
            "my-project", "gmail-events", "gmail-events-local", preprovisioned=True
        )
    except RuntimeError as exc:
        assert "roles/pubsub.subscriber" in str(exc)
    else:
        raise AssertionError("expected permission error")


def test_existing_subscription_must_target_requested_topic():
    class ExistingTopics(Topics):
        def get(self, **kwargs):
            return Request({"name": kwargs["topic"]})

    class ExistingSubscriptions(Subscriptions):
        def get(self, **kwargs):
            return Request({"topic": "projects/my-project/topics/other"})

    pubsub = PubSub()
    pubsub.topic_api = ExistingTopics()
    pubsub.sub_api = ExistingSubscriptions()
    pubsub.p = Projects(pubsub.topic_api, pubsub.sub_api)
    try:
        GoogleSetup(pubsub).ensure_pubsub("my-project", "gmail-events", "gmail-events-local")
    except RuntimeError as exc:
        assert "topics/other" in str(exc)
    else:
        raise AssertionError("expected topic mismatch")
