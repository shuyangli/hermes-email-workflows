"""Domain models for email workflows."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Rule:
    id: int | None
    name: str
    gmail_query: str
    prompt_template: str
    enabled: bool = True
    priority: int = 100
    account_email: str | None = None
    toolsets: str = "web"
    skills: str = ""
    timeout_seconds: int = 300


@dataclass(slots=True)
class EmailMessage:
    gmail_id: str
    thread_id: str
    rfc822_message_id: str | None
    sender: str
    recipients: str
    subject: str
    body: str
    internal_date_ms: int
    labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TaskResult:
    rule_id: int | None
    rule_name: str
    success: bool
    output: str


@dataclass(slots=True)
class ProcessResult:
    status: str
    message_id: str
    matched_rule_ids: list[int] = field(default_factory=list)
    notification: str = ""
