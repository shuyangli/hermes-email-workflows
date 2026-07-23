"""SQLite persistence with idempotent message claiming."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Rule

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    gmail_query TEXT NOT NULL,
    prompt_template TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    account_email TEXT,
    toolsets TEXT NOT NULL DEFAULT 'web',
    skills TEXT NOT NULL DEFAULT '',
    timeout_seconds INTEGER NOT NULL DEFAULT 300,
    destination TEXT NOT NULL DEFAULT 'telegram',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message_events (
    account_email TEXT NOT NULL,
    message_id TEXT NOT NULL,
    status TEXT NOT NULL,
    matched_rule_ids TEXT NOT NULL DEFAULT '[]',
    notification TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_email, message_id)
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(descriptor)
        os.chmod(self.path, 0o600)
        self._repair_sidecar_permissions()
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript(_SCHEMA)
            rule_columns = {row["name"] for row in db.execute("PRAGMA table_info(rules)")}
            if "destination" not in rule_columns:
                db.execute(
                    "ALTER TABLE rules ADD COLUMN destination TEXT NOT NULL DEFAULT 'telegram'"
                )
            db.execute("UPDATE message_events SET status='retryable' WHERE status='processing'")
        os.chmod(self.path, 0o600)
        self._repair_sidecar_permissions()

    def _repair_sidecar_permissions(self) -> None:
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(f"{self.path}{suffix}")
            if sidecar.exists():
                os.chmod(sidecar, 0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def create_rule(self, rule: Rule) -> Rule:
        now = self._now()
        with self._lock, self._connect() as db:
            cur = db.execute(
                """INSERT INTO rules
                (name,gmail_query,prompt_template,enabled,priority,account_email,toolsets,skills,
                 timeout_seconds,destination,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rule.name,
                    rule.gmail_query,
                    rule.prompt_template,
                    int(rule.enabled),
                    rule.priority,
                    rule.account_email,
                    rule.toolsets,
                    rule.skills,
                    rule.timeout_seconds,
                    rule.destination,
                    now,
                    now,
                ),
            )
            return Rule(**{**asdict(rule), "id": int(cur.lastrowid)})

    def update_rule(self, rule: Rule) -> None:
        if rule.id is None:
            raise ValueError("Cannot update a rule without an id")
        with self._lock, self._connect() as db:
            cur = db.execute(
                """UPDATE rules SET name=?,gmail_query=?,prompt_template=?,enabled=?,priority=?,
                account_email=?,toolsets=?,skills=?,timeout_seconds=?,destination=?,updated_at=?
                WHERE id=?""",
                (
                    rule.name,
                    rule.gmail_query,
                    rule.prompt_template,
                    int(rule.enabled),
                    rule.priority,
                    rule.account_email,
                    rule.toolsets,
                    rule.skills,
                    rule.timeout_seconds,
                    rule.destination,
                    self._now(),
                    rule.id,
                ),
            )
            if cur.rowcount != 1:
                raise KeyError(rule.id)

    def delete_rule(self, rule_id: int) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    def get_rule(self, rule_id: int) -> Rule | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        return self._row_to_rule(row) if row else None

    def list_rules(self, account_email: str | None = None) -> list[Rule]:
        query = "SELECT * FROM rules"
        params: tuple[object, ...] = ()
        if account_email:
            query += " WHERE account_email IS NULL OR account_email=?"
            params = (account_email,)
        query += " ORDER BY priority ASC, id ASC"
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._row_to_rule(row) for row in rows]

    @staticmethod
    def _row_to_rule(row: sqlite3.Row) -> Rule:
        return Rule(
            id=row["id"],
            name=row["name"],
            gmail_query=row["gmail_query"],
            prompt_template=row["prompt_template"],
            enabled=bool(row["enabled"]),
            priority=row["priority"],
            account_email=row["account_email"],
            toolsets=row["toolsets"] or "web",
            skills=row["skills"],
            timeout_seconds=row["timeout_seconds"],
            destination=row["destination"] or "telegram",
        )

    def claim_message(self, account_email: str, message_id: str) -> bool:
        now = self._now()
        with self._lock, self._connect() as db:
            cur = db.execute(
                """INSERT OR IGNORE INTO message_events
                (account_email,message_id,status,created_at,updated_at) VALUES (?,?,?,?,?)""",
                (account_email, message_id, "processing", now, now),
            )
            if cur.rowcount == 1:
                return True
            cur = db.execute(
                """UPDATE message_events SET status='processing',updated_at=?
                WHERE account_email=? AND message_id=? AND status='retryable'""",
                (now, account_email, message_id),
            )
            return cur.rowcount == 1

    def claim_for_rematch(self, account_email: str, message_id: str, within_seconds: int) -> bool:
        """Re-claim a recently-``unmatched`` message so it can be re-evaluated.

        Gmail's search index (used by the rule matcher) is eventually consistent and can
        lag behind push delivery, so a freshly arrived message may be recorded as
        ``unmatched`` before the index catches up. Bounding by ``within_seconds`` lets the
        safety sweep re-evaluate such messages without re-scanning the entire unread backlog.
        """
        cutoff = (datetime.now(UTC) - timedelta(seconds=within_seconds)).isoformat()
        with self._lock, self._connect() as db:
            cur = db.execute(
                """UPDATE message_events SET status='processing',updated_at=?
                WHERE account_email=? AND message_id=? AND status='unmatched' AND updated_at>=?""",
                (self._now(), account_email, message_id, cutoff),
            )
            return cur.rowcount == 1

    def resumable_message_ids(self, account_email: str) -> list[str]:
        """Message ids left mid-flight (``retryable`` or notification pending).

        These have already been marked read, so they never reappear in the unread-inbox
        sweep and must be re-driven explicitly during recovery and safety synchronization.
        """
        with self._connect() as db:
            rows = db.execute(
                """SELECT message_id FROM message_events
                WHERE account_email=? AND (status='retryable'
                    OR status LIKE 'notification_pending:%')
                ORDER BY updated_at ASC""",
                (account_email,),
            ).fetchall()
        return [row["message_id"] for row in rows]

    def finish_message(
        self,
        account_email: str,
        message_id: str,
        status: str,
        matched_rule_ids: list[int],
        notification: str,
    ) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """UPDATE message_events SET status=?,matched_rule_ids=?,notification=?,updated_at=?
                WHERE account_email=? AND message_id=?""",
                (
                    status,
                    json.dumps(matched_rule_ids),
                    notification,
                    self._now(),
                    account_email,
                    message_id,
                ),
            )

    def list_events(self, limit: int = 50) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM message_events ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_event(self, account_email: str, message_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM message_events WHERE account_email=? AND message_id=?",
                (account_email, message_id),
            ).fetchone()
        return dict(row) if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT INTO settings(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, value),
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def all_settings(self) -> dict[str, str]:
        with self._connect() as db:
            return {
                row["key"]: row["value"] for row in db.execute("SELECT key,value FROM settings")
            }
