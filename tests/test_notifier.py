from __future__ import annotations

import pytest

from email_workflows.notifier import TelegramNotifier


def test_telegram_notifier_uses_hermes_send_home_channel():
    captured = {}

    def execute(argv, timeout):
        captured["argv"] = argv
        return 0, '{"ok":true}', ""

    TelegramNotifier(execute=execute).send("hello")
    assert captured["argv"] == ["hermes", "send", "--to", "telegram", "--json", "hello"]


def test_telegram_notifier_uses_configured_destination():
    captured = {}

    def execute(argv, timeout):
        captured["argv"] = argv
        return 0, '{"ok":true}', ""

    TelegramNotifier(execute=execute).send("hello group", "telegram:-1001234567890")

    assert captured["argv"] == [
        "hermes",
        "send",
        "--to",
        "telegram:-1001234567890",
        "--json",
        "hello group",
    ]


def test_telegram_notifier_accepts_topic_destination():
    captured = {}

    def execute(argv, timeout):
        captured["argv"] = argv
        return 0, "", ""

    TelegramNotifier(execute=execute).send("topic", "telegram:-1001234567890:17585")
    assert captured["argv"][3] == "telegram:-1001234567890:17585"


def test_telegram_notifier_raises_on_delivery_error():
    def execute(argv, timeout):
        return 1, "", "failed"

    try:
        TelegramNotifier(execute=execute).send("hello")
    except RuntimeError as exc:
        assert "failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_telegram_notifier_rejects_non_telegram_destination():
    invalid = ["discord:#general", "telegram:", "telegram:group-name", "telegram:1:2:3"]
    for destination in invalid:
        with pytest.raises(ValueError, match="Destination must be telegram"):
            TelegramNotifier(execute=lambda *_: (0, "", "")).send("hello", destination)
