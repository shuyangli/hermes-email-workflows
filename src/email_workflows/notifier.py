"""Telegram delivery through Hermes' configured platform credentials."""

from __future__ import annotations

import subprocess
from collections.abc import Callable

Executor = Callable[[list[str], int], tuple[int, str, str]]


def _execute(argv: list[str], timeout: int) -> tuple[int, str, str]:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


class TelegramNotifier:
    def __init__(self, execute: Executor = _execute):
        self.execute = execute

    def send(self, message: str) -> None:
        code, stdout, stderr = self.execute(
            ["hermes", "send", "--to", "telegram", "--json", message], 60
        )
        if code != 0:
            raise RuntimeError(stderr or stdout or f"hermes send exited {code}")
