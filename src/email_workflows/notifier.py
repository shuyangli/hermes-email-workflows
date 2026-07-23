"""Telegram delivery through Hermes' configured platform credentials."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable

Executor = Callable[[list[str], int], tuple[int, str, str]]
TELEGRAM_DESTINATION = re.compile(r"^telegram(?::-?\d+(?::\d+)?)?$")


def _execute(argv: list[str], timeout: int) -> tuple[int, str, str]:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


class TelegramNotifier:
    def __init__(self, execute: Executor = _execute):
        self.execute = execute

    def send(self, message: str, destination: str = "telegram") -> None:
        if not TELEGRAM_DESTINATION.fullmatch(destination):
            raise ValueError(
                "Destination must be telegram, telegram:<chat_id>, or "
                "telegram:<chat_id>:<thread_id>"
            )
        code, stdout, stderr = self.execute(
            ["hermes", "send", "--to", destination, "--json", message], 60
        )
        if code != 0:
            raise RuntimeError(stderr or stdout or f"hermes send exited {code}")
