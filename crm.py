"""SQLite-backed CRM client. Idempotent inserts on message hash."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    company TEXT,
    first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    message_hash TEXT UNIQUE NOT NULL,
    subject TEXT,
    body TEXT,
    category TEXT,
    urgency TEXT,
    intent TEXT,
    extracted_json TEXT,
    suggested_response TEXT,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_interactions_contact ON interactions(contact_id);
CREATE INDEX IF NOT EXISTS idx_interactions_category ON interactions(category);
CREATE INDEX IF NOT EXISTS idx_interactions_created ON interactions(created_at DESC);
"""


def _hash_message(sender: str, subject: str, body: str) -> str:
    """Stable hash for idempotency. Same (sender, subject, body) → same hash."""
    h = hashlib.sha256()
    h.update(sender.lower().encode())
    h.update(b"\x00")
    h.update(subject.encode())
    h.update(b"\x00")
    h.update(body.encode())
    return h.hexdigest()


class CRM:
    def __init__(self, db_path: str = "crm.db"):
        self.db_path = Path(db_path)
        self.conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def record_interaction(
        self,
        sender: str,
        subject: str,
        body: str,
        triage: dict[str, Any],
    ) -> tuple[int, int]:
        """Insert or upsert. Returns (contact_id, interaction_id)."""
        assert self.conn

        message_hash = _hash_message(sender, subject, body)
        extracted = triage.get("extracted", {}) or {}
        contact_name = extracted.get("contact_name")
        company = extracted.get("company")

        # Upsert contact
        cur = self.conn.execute(
            """
            INSERT INTO contacts (email, name, company)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name = COALESCE(excluded.name, contacts.name),
                company = COALESCE(excluded.company, contacts.company),
                last_seen = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (sender, contact_name, company),
        )
        contact_id = cur.fetchone()["id"]

        # Insert interaction (idempotent on message_hash)
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO interactions (
                contact_id, message_hash, subject, body,
                category, urgency, intent,
                extracted_json, suggested_response, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                contact_id,
                message_hash,
                subject,
                body,
                triage.get("category"),
                triage.get("urgency"),
                triage.get("intent"),
                json.dumps(extracted),
                triage.get("suggested_response"),
                triage.get("confidence"),
            ),
        )
        row = cur.fetchone()
        if row is None:
            # Already existed — fetch existing
            cur = self.conn.execute(
                "SELECT id FROM interactions WHERE message_hash = ?",
                (message_hash,),
            )
            row = cur.fetchone()
        interaction_id = row["id"]

        return contact_id, interaction_id

    def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        assert self.conn
        row = self.conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_interactions(self, contact_id: int) -> list[dict[str, Any]]:
        assert self.conn
        rows = self.conn.execute(
            "SELECT * FROM interactions WHERE contact_id = ? ORDER BY created_at DESC",
            (contact_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_interactions(self, limit: int = 50) -> list[dict[str, Any]]:
        assert self.conn
        rows = self.conn.execute(
            """
            SELECT i.*, c.email, c.name, c.company
            FROM interactions i
            JOIN contacts c ON c.id = i.contact_id
            ORDER BY i.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
