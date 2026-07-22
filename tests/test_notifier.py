from __future__ import annotations

from email_workflows.notifier import TelegramNotifier


def test_telegram_notifier_uses_hermes_send_home_channel():
    captured = {}

    def execute(argv, timeout):
        captured["argv"] = argv
        return 0, '{"ok":true}', ""

    TelegramNotifier(execute=execute).send("hello")
    assert captured["argv"] == ["hermes", "send", "--to", "telegram", "--json", "hello"]


def test_telegram_notifier_raises_on_delivery_error():
    def execute(argv, timeout):
        return 1, "", "failed"

    try:
        TelegramNotifier(execute=execute).send("hello")
    except RuntimeError as exc:
        assert "failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
