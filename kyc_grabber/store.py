"""SQLite persistence for jobs, per-code results and the event log.

The store is intentionally synchronous: SQLite writes for a few hundred rows are
sub-millisecond and the single shared connection is guarded by a lock, so async
callers can hand work to it with ``asyncio.to_thread`` when a batch is large.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'filesystem',
    sender          TEXT NOT NULL DEFAULT '',
    subject         TEXT NOT NULL DEFAULT '',
    message_id      TEXT NOT NULL DEFAULT '',
    filename        TEXT NOT NULL DEFAULT '',
    total_codes     INTEGER NOT NULL DEFAULT 0,
    processed_codes INTEGER NOT NULL DEFAULT 0,
    failed_codes    INTEGER NOT NULL DEFAULT 0,
    warnings        INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    reject_reason   TEXT,
    workbook_path   TEXT,
    delivered_to    TEXT,
    delivery_mode   TEXT,
    raw_mail_path   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_items (
    job_id        TEXT NOT NULL,
    code          TEXT NOT NULL,
    status        TEXT NOT NULL,
    match_status  TEXT,
    internal_json TEXT,
    external_json TEXT,
    risk_rating   TEXT,
    differences   TEXT,
    warnings      TEXT,
    error         TEXT,
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (job_id, code)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  TEXT,
    at      TEXT NOT NULL,
    level   TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS mail_seen (
    message_id TEXT PRIMARY KEY,
    job_id     TEXT,
    seen_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_created  ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_at     ON events(id DESC);
CREATE INDEX IF NOT EXISTS idx_events_job    ON events(job_id, id DESC);
"""


def new_job_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"J-{stamp}-{uuid4().hex[:4].upper()}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ jobs
    def create_job(
        self,
        *,
        job_id: str,
        sender: str,
        subject: str,
        message_id: str,
        filename: str,
        source: str,
        total_codes: int,
        status: str = "received",
        raw_mail_path: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        stamp = (created_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (id, status, source, sender, subject, message_id, filename,
                                  total_codes, raw_mail_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, status, source, sender, subject, message_id, filename,
                 total_codes, raw_mail_path, stamp, stamp),
            )
            self._conn.commit()

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "status", "processed_codes", "failed_codes", "warnings", "error",
            "reject_reason", "workbook_path", "delivered_to", "delivery_mode",
            "filename", "total_codes",
        }
        payload = {key: value for key, value in fields.items() if key in allowed}
        if not payload:
            return
        payload["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in payload)
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",  # noqa: S608 - keys are allow-listed
                (*payload.values(), job_id),
            )
            self._conn.commit()

    def increment_job_counters(self, job_id: str, *, processed: int = 0, failed: int = 0, warnings: int = 0) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                   SET processed_codes = processed_codes + ?,
                       failed_codes    = failed_codes + ?,
                       warnings        = warnings + ?,
                       updated_at      = ?
                 WHERE id = ?
                """,
                (processed, failed, warnings, _now(), job_id),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 50, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM jobs"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def job_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {row["status"]: row["n"] for row in rows}

    # ----------------------------------------------------------------- items
    def upsert_item(self, job_id: str, code: str, **fields: Any) -> None:
        allowed = {
            "status", "match_status", "risk_rating", "differences", "warnings",
            "error", "duration_ms", "internal_json", "external_json",
        }
        payload: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            payload[key] = json.dumps(value) if key.endswith("_json") or key in {"differences", "warnings"} else value
        payload.setdefault("status", "pending")
        payload["updated_at"] = _now()

        columns = ", ".join(payload)
        placeholders = ", ".join("?" for _ in payload)
        updates = ", ".join(f"{key} = excluded.{key}" for key in payload if key != "job_id")
        with self._lock:
            self._conn.execute(
                f"""  -- noqa: S608 - keys are allow-listed
                INSERT INTO job_items (job_id, code, {columns})
                VALUES (?, ?, {placeholders})
                ON CONFLICT(job_id, code) DO UPDATE SET {updates}
                """,
                (job_id, code, *payload.values()),
            )
            self._conn.commit()

    def list_items(self, job_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM job_items WHERE job_id = ? ORDER BY rowid", (job_id,)
            ).fetchall()
        items: list[dict] = []
        for row in rows:
            record = dict(row)
            record["internal"] = _loads(record.pop("internal_json", None))
            record["external"] = _loads(record.pop("external_json", None))
            record["differences"] = _loads(record.pop("differences", None)) or []
            record["warnings"] = _loads(record.pop("warnings", None)) or []
            items.append(record)
        return items

    # ---------------------------------------------------------------- events
    def add_event(
        self,
        message: str,
        *,
        level: str = "info",
        job_id: str | None = None,
        payload: dict | None = None,
    ) -> dict:
        event = {
            "job_id": job_id,
            "at": _now(),
            "level": level,
            "message": message,
            "payload": payload or {},
        }
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO events (job_id, at, level, message, payload) VALUES (?, ?, ?, ?, ?)",
                (event["job_id"], event["at"], event["level"], event["message"], json.dumps(event["payload"])),
            )
            self._conn.commit()
        event["id"] = cursor.lastrowid
        return event

    def list_events(self, limit: int = 200, job_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM events"
        params: list[Any] = []
        if job_id:
            query += " WHERE job_id = ?"
            params.append(job_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        events = []
        for row in reversed(rows):
            record = dict(row)
            record["payload"] = _loads(record.get("payload"))
            events.append(record)
        return events

    def prune_events(self, keep: int = 5000) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
                (keep,),
            )
            self._conn.commit()

    # ------------------------------------------------------------- dedupe
    def already_seen(self, message_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM mail_seen WHERE message_id = ?", (message_id,)
            ).fetchone()
        return row is not None

    def mark_seen(self, message_id: str, job_id: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO mail_seen (message_id, job_id, seen_at) VALUES (?, ?, ?)",
                (message_id, job_id, _now()),
            )
            self._conn.commit()
