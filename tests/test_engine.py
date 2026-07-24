from __future__ import annotations

from email_workflows.engine import WorkflowEngine
from email_workflows.models import EmailMessage, Rule, TaskResult


def test_multiple_matching_rules_run_separately_but_send_one_combined_notification():
    events: list[str] = []
    email = EmailMessage("m1", "t1", "<a@b>", "a@example.com", "me@example.com", "Hi", "Body", 1)
    rules = [
        Rule(id=1, name="summarize", gmail_query="from:a", prompt_template="Summarize ${subject}"),
        Rule(id=2, name="extract", gmail_query="subject:Hi", prompt_template="Extract ${body}"),
    ]

    class Matcher:
        def matching_rules(self, message, candidates):
            events.append("matched")
            return candidates

    class Gmail:
        def mark_read(self, message_id):
            events.append(f"read:{message_id}")

        def add_processed_label(self, message_id):
            events.append(f"label:{message_id}")

    class Runner:
        def run(self, rule, message):
            events.append(f"run:{rule.name}")
            return TaskResult(rule.id, rule.name, True, f"result-{rule.name}")

    class Notifier:
        def send(self, text):
            events.append("sent")
            self.text = text

    class Store:
        def claim_message(self, account, message_id):
            events.append("claimed")
            return True

        def finish_message(self, account, message_id, status, matched_rule_ids, notification):
            events.append(f"finished:{status}")

    notifier = Notifier()
    engine = WorkflowEngine(Store(), Matcher(), Gmail(), Runner(), notifier, "me@example.com")

    result = engine.process(email, rules)

    assert result.status == "completed"
    assert events.index("read:m1") < events.index("run:summarize")
    assert events.count("sent") == 1
    assert "summarize" in notifier.text and "extract" in notifier.text
    assert "result-summarize" in notifier.text and "result-extract" in notifier.text


def test_matching_rules_are_grouped_and_sent_to_their_destinations():
    email = EmailMessage("m1", "t1", None, "a", "b", "subject", "body", 1)
    rules = [
        Rule(1, "home one", "x", "x"),
        Rule(2, "group", "x", "x", destination="telegram:-1001234567890"),
        Rule(3, "home two", "x", "x"),
    ]

    class Store:
        def claim_message(self, account, message_id):
            return True

        def finish_message(self, *args):
            pass

    class Matcher:
        def matching_rules(self, message, candidates):
            return candidates

    class Gmail:
        def mark_read(self, message_id):
            pass

        def add_processed_label(self, message_id):
            pass

    class Runner:
        def run(self, rule, message):
            return TaskResult(rule.id, rule.name, True, f"result-{rule.name}")

    class Notifier:
        def __init__(self):
            self.deliveries = []

        def send(self, text, destination="telegram"):
            self.deliveries.append((destination, text))

    notifier = Notifier()
    result = WorkflowEngine(Store(), Matcher(), Gmail(), Runner(), notifier, "me").process(
        email, rules
    )

    assert result.status == "completed"
    assert [destination for destination, _ in notifier.deliveries] == [
        "telegram",
        "telegram:-1001234567890",
    ]
    home = notifier.deliveries[0][1]
    group = notifier.deliveries[1][1]
    assert "home one" in home and "home two" in home and "group" not in home
    assert "group" in group and "home one" not in group and "home two" not in group


def test_unmatched_email_is_not_marked_read_or_notified():
    class Store:
        def claim_message(self, account, message_id):
            return True

        def finish_message(self, *args, **kwargs):
            self.status = args[2]

    class Matcher:
        def matching_rules(self, message, candidates):
            return []

    class Gmail:
        def mark_read(self, message_id):
            raise AssertionError("must not mark read")

        def add_processed_label(self, message_id):
            raise AssertionError("must not stamp processed label")

    class Runner:
        def run(self, rule, message):
            raise AssertionError("must not run")

    class Notifier:
        def send(self, text):
            raise AssertionError("must not notify")

    email = EmailMessage("m1", "t1", None, "a", "b", "s", "body", 1)
    result = WorkflowEngine(Store(), Matcher(), Gmail(), Runner(), Notifier(), "me").process(
        email, []
    )
    assert result.status == "unmatched"


def test_exact_no_notification_result_completes_silently():
    events: list[str] = []

    class Store:
        def claim_message(self, account, message_id):
            return True

        def finish_message(self, account, message_id, status, matched_rule_ids, notification):
            events.append(f"finished:{status}:{notification}")

    class Matcher:
        def matching_rules(self, message, candidates):
            return candidates

    class Gmail:
        def mark_read(self, message_id):
            events.append("read")

        def add_processed_label(self, message_id):
            events.append("label")

    class Runner:
        def run(self, rule, message):
            return TaskResult(rule.id, rule.name, True, "NO_NOTIFICATION")

    class Notifier:
        def send(self, text):
            raise AssertionError("silent result must not notify")

    email = EmailMessage("m1", "t1", "<a@b>", "a", "b", "s", "body", 1)
    rule = Rule(id=1, name="conditional", gmail_query="from:a", prompt_template="x")

    result = WorkflowEngine(Store(), Matcher(), Gmail(), Runner(), Notifier(), "me").process(
        email, [rule]
    )

    assert result.status == "completed_silent"
    assert result.matched_rule_ids == [1]
    assert result.notification == ""
    assert events == ["label", "read", "finished:completed_silent:"]


def test_no_notification_terminal_sentinel_suppresses_cli_reasoning_output():
    events: list[str] = []

    class Store:
        def claim_message(self, account, message_id):
            return True

        def finish_message(self, account, message_id, status, matched_rule_ids, notification):
            events.append(f"finished:{status}:{notification}")

    class Matcher:
        def matching_rules(self, message, candidates):
            return candidates

    class Gmail:
        def mark_read(self, message_id):
            events.append("read")

        def add_processed_label(self, message_id):
            events.append("label")

    class Runner:
        def run(self, rule, message):
            return TaskResult(
                rule.id,
                rule.name,
                True,
                "Planning precise wine research strategy\n"
                "Confirming review details\n"
                "NO_NOTIFICATION",
            )

    class Notifier:
        def send(self, text):
            raise AssertionError("terminal NO_NOTIFICATION sentinel must suppress delivery")

    email = EmailMessage("m1", "t1", "<a@b>", "a", "b", "s", "body", 1)
    rule = Rule(id=1, name="conditional", gmail_query="from:a", prompt_template="x")

    result = WorkflowEngine(Store(), Matcher(), Gmail(), Runner(), Notifier(), "me").process(
        email, [rule]
    )

    assert result.status == "completed_silent"
    assert result.notification == ""
    assert events == ["label", "read", "finished:completed_silent:"]


def test_silent_result_is_omitted_when_another_rule_has_output():
    class Store:
        def claim_message(self, account, message_id):
            return True

        def finish_message(self, account, message_id, status, matched_rule_ids, notification):
            self.status = status

    class Matcher:
        def matching_rules(self, message, candidates):
            return candidates

    class Gmail:
        def mark_read(self, message_id):
            pass

        def add_processed_label(self, message_id):
            pass

    class Runner:
        def run(self, rule, message):
            output = "NO_NOTIFICATION" if rule.name == "silent" else "Buy this bottle"
            return TaskResult(rule.id, rule.name, True, output)

    class Notifier:
        def send(self, text):
            self.text = text

    email = EmailMessage("m1", "t1", "<a@b>", "a", "b", "s", "body", 1)
    rules = [
        Rule(id=1, name="silent", gmail_query="from:a", prompt_template="x"),
        Rule(id=2, name="visible", gmail_query="from:a", prompt_template="x"),
    ]
    notifier = Notifier()

    result = WorkflowEngine(Store(), Matcher(), Gmail(), Runner(), notifier, "me").process(
        email, rules
    )

    assert result.status == "completed"
    assert "Buy this bottle" in notifier.text
    assert "visible" in notifier.text
    assert "silent" not in notifier.text
    assert "NO_NOTIFICATION" not in notifier.text


def test_recently_unmatched_message_is_re_evaluated_when_rematch_allowed():
    # Simulates Gmail's search index catching up: the message was recorded ``unmatched``,
    # a rule now matches, and the safety sweep (allow_rematch=True) reprocesses it.
    events: list[str] = []

    class Store:
        def claim_message(self, account, message_id):
            return False

        def get_event(self, account, message_id):
            return {"status": "unmatched", "matched_rule_ids": "[]", "notification": ""}

        def claim_for_rematch(self, account, message_id, within_seconds):
            events.append("reclaimed")
            return True

        def finish_message(self, account, message_id, status, matched_rule_ids, notification):
            events.append(f"finished:{status}")

    class Matcher:
        def matching_rules(self, message, candidates):
            return candidates

    class Gmail:
        def mark_read(self, message_id):
            events.append("read")

        def add_processed_label(self, message_id):
            events.append("label")

    class Runner:
        def run(self, rule, message):
            return TaskResult(rule.id, rule.name, True, "ok")

    class Notifier:
        def send(self, text):
            events.append("sent")

    email = EmailMessage("m1", "t1", "<a@b>", "a", "b", "s", "body", 1)
    rules = [Rule(id=1, name="late", gmail_query="from:a", prompt_template="x")]
    engine = WorkflowEngine(Store(), Matcher(), Gmail(), Runner(), Notifier(), "me")

    result = engine.process(email, rules, allow_rematch=True)

    assert result.status == "completed"
    assert events == [
        "reclaimed",
        "label",
        "read",
        "finished:notification_pending:completed",
        "sent",
        # Persist the successful destination before finalizing so a crash does not
        # resend it while another destination is still pending.
        "finished:notification_pending:completed",
        "finished:completed",
    ]


def test_unmatched_message_is_not_re_evaluated_without_rematch():
    class Store:
        def claim_message(self, account, message_id):
            return False

        def get_event(self, account, message_id):
            return {"status": "unmatched", "matched_rule_ids": "[]", "notification": ""}

        def claim_for_rematch(self, account, message_id, within_seconds):
            raise AssertionError("must not reclaim without allow_rematch")

    class Never:
        def __getattr__(self, name):
            raise AssertionError("must stop before matching")

    email = EmailMessage("m1", "t1", None, "a", "b", "s", "body", 1)
    result = WorkflowEngine(Store(), Never(), Never(), Never(), Never(), "me").process(
        email, [Rule(id=1, name="x", gmail_query="from:a", prompt_template="x")]
    )
    assert result.status == "duplicate"


def test_duplicate_message_is_skipped():
    class Store:
        def claim_message(self, account, message_id):
            return False

    class Never:
        def __getattr__(self, name):
            raise AssertionError("duplicate must stop")

    email = EmailMessage("m1", "t1", None, "a", "b", "s", "body", 1)
    result = WorkflowEngine(Store(), Never(), Never(), Never(), Never(), "me").process(email, [])
    assert result.status == "duplicate"
