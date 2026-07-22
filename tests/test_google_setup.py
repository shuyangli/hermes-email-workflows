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

    def get(self, **kwargs):
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

    def get(self, **kwargs):
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
