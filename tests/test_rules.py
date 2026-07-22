from __future__ import annotations

from email_workflows.models import EmailMessage, Rule
from email_workflows.rules import GmailQueryMatcher


class FakeMessages:
    def __init__(self, hits: dict[str, list[str]]):
        self.hits = hits
        self.queries: list[str] = []

    def list(self, **kwargs):
        query = kwargs["q"]
        self.queries.append(query)
        ids = next((values for key, values in self.hits.items() if key in query), [])
        return FakeRequest({"messages": [{"id": value} for value in ids]})


class FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class FakeGmail:
    def __init__(self, messages):
        self._users = FakeUsers(messages)

    def users(self):
        return self._users


class FakeRequest:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


def test_matches_each_rule_independently_with_rfc822_message_id():
    messages = FakeMessages({"from:alerts@example.com": ["m-1"], "subject:urgent": ["m-1"]})
    matcher = GmailQueryMatcher(FakeGmail(messages))
    email = EmailMessage(
        gmail_id="m-1",
        thread_id="t-1",
        rfc822_message_id="<abc@example.com>",
        sender="alerts@example.com",
        recipients="me@example.com",
        subject="Urgent",
        body="hello",
        internal_date_ms=1_700_000_000_000,
    )
    rules = [
        Rule(id=1, name="sender", gmail_query="from:alerts@example.com", prompt_template="x"),
        Rule(id=2, name="subject", gmail_query="subject:urgent", prompt_template="y"),
        Rule(id=3, name="miss", gmail_query="from:nobody@example.com", prompt_template="z"),
    ]

    matched = matcher.matching_rules(email, rules)

    assert [rule.id for rule in matched] == [1, 2]
    assert all("rfc822msgid:abc@example.com" in query for query in messages.queries)


def test_disabled_rules_are_not_evaluated():
    messages = FakeMessages({"from:alerts@example.com": ["m-1"]})
    matcher = GmailQueryMatcher(FakeGmail(messages))
    email = EmailMessage("m-1", "t", "<a@b>", "a", "b", "s", "body", 1)
    rules = [
        Rule(
            id=1,
            name="off",
            gmail_query="from:alerts@example.com",
            prompt_template="x",
            enabled=False,
        )
    ]

    assert matcher.matching_rules(email, rules) == []
    assert messages.queries == []
