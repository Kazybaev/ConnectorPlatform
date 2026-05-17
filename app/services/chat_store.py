from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.utils.config import Settings, get_settings
from app.utils.time import utc_now_iso


def normalize_chat_timestamp(value: Any) -> str:
    """Convert runtime timestamps into stable UTC ISO strings."""
    if value is None or value == "":
        return utc_now_iso()

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC).replace(microsecond=0).isoformat()

    text = str(value).strip()
    if not text:
        return utc_now_iso()

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return utc_now_iso()

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat()


def derive_phone_from_chat_id(chat_id: str) -> str:
    """Extract a human-friendly phone value from a WhatsApp chat id when possible."""
    if "@" not in chat_id:
        return chat_id
    return chat_id.split("@", 1)[0].strip()


@dataclass(slots=True)
class ChatConversationRecord:
    """One conversation row shown in the operator inbox."""

    channel_key: str
    chat_id: str
    display_name: str
    phone: str
    avatar_url: str
    last_message_text: str
    last_message_at: str
    last_direction: str
    last_sender_name: str
    unread_count: int
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ChatMessageRecord:
    """One inbound or outbound message stored by the platform."""

    record_id: str
    channel_key: str
    chat_id: str
    external_message_id: str
    direction: str
    sender_id: str
    sender_name: str
    text: str
    message_type: str
    source: str
    status: str
    created_at: str
    raw_payload: dict[str, Any]


class ChatStoreService:
    """SQLite-backed inbox storage for runtime chats and operator replies."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database_path = Path(settings.database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def list_conversations(self, channel_key: str, limit: int = 100) -> list[ChatConversationRecord]:
        """Return conversations sorted by the most recent message."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    channel_key,
                    chat_id,
                    display_name,
                    phone,
                    avatar_url,
                    last_message_text,
                    last_message_at,
                    last_direction,
                    last_sender_name,
                    unread_count,
                    created_at,
                    updated_at
                FROM chat_conversations
                WHERE channel_key = ?
                ORDER BY last_message_at DESC, updated_at DESC
                LIMIT ?
                """,
                (channel_key, limit),
            ).fetchall()

        return [
            ChatConversationRecord(
                channel_key=row["channel_key"],
                chat_id=row["chat_id"],
                display_name=row["display_name"] or row["chat_id"],
                phone=row["phone"] or "",
                avatar_url=row["avatar_url"] or "",
                last_message_text=row["last_message_text"] or "",
                last_message_at=row["last_message_at"] or "",
                last_direction=row["last_direction"] or "inbound",
                last_sender_name=row["last_sender_name"] or "",
                unread_count=int(row["unread_count"] or 0),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def list_messages(self, channel_key: str, chat_id: str, limit: int = 200) -> list[ChatMessageRecord]:
        """Return the latest messages for one chat in chronological order."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    record_id,
                    channel_key,
                    chat_id,
                    external_message_id,
                    direction,
                    sender_id,
                    sender_name,
                    text,
                    message_type,
                    source,
                    status,
                    created_at,
                    raw_payload_json
                FROM (
                    SELECT *
                    FROM chat_messages
                    WHERE channel_key = ? AND chat_id = ?
                    ORDER BY created_at DESC, record_id DESC
                    LIMIT ?
                )
                ORDER BY created_at ASC, record_id ASC
                """,
                (channel_key, chat_id, limit),
            ).fetchall()

        return [self._row_to_message(row) for row in rows]

    def get_message_by_external_id(self, channel_key: str, external_message_id: str) -> ChatMessageRecord | None:
        """Return a previously stored runtime message by its WhatsApp/runtime id."""
        external_message_id = external_message_id.strip()
        if not external_message_id:
            return None

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM chat_messages
                WHERE channel_key = ? AND external_message_id = ?
                """,
                (channel_key, external_message_id),
            ).fetchone()

        return self._row_to_message(row) if row is not None else None

    def store_incoming_message(self, channel_key: str, payload: dict[str, Any]) -> ChatMessageRecord:
        """Persist one inbound runtime event and update the conversation summary."""
        message = payload.get("message", {}) if isinstance(payload.get("message"), dict) else {}
        chat_id = str(message.get("chat_id", "")).strip()
        if not chat_id:
            raise ValueError("Incoming runtime payload is missing chat_id.")

        external_message_id = str(message.get("external_message_id", "")).strip() or None
        sender_name = str(message.get("sender_name", "")).strip()
        sender_id = str(message.get("sender_id", "")).strip() or chat_id
        text = str(message.get("text", "")).strip()
        message_type = str(message.get("message_type", "text")).strip() or "text"
        created_at = normalize_chat_timestamp(message.get("timestamp"))
        record_id = f"chatmsg_{uuid4().hex[:14]}"
        raw_payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        with self._connect() as connection:
            if external_message_id:
                existing = connection.execute(
                    """
                    SELECT *
                    FROM chat_messages
                    WHERE channel_key = ? AND external_message_id = ?
                    """,
                    (channel_key, external_message_id),
                ).fetchone()
                if existing is not None:
                    return self._row_to_message(existing)

            connection.execute(
                """
                INSERT INTO chat_messages (
                    record_id,
                    channel_key,
                    chat_id,
                    external_message_id,
                    direction,
                    sender_id,
                    sender_name,
                    text,
                    message_type,
                    source,
                    status,
                    created_at,
                    raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    channel_key,
                    chat_id,
                    external_message_id,
                    "inbound",
                    sender_id,
                    sender_name,
                    text,
                    message_type,
                    "runtime",
                    "received",
                    created_at,
                    raw_payload_json,
                ),
            )

            self._upsert_conversation(
                connection,
                channel_key=channel_key,
                chat_id=chat_id,
                display_name=sender_name or derive_phone_from_chat_id(chat_id) or chat_id,
                phone=derive_phone_from_chat_id(chat_id),
                avatar_url="",
                last_message_text=text,
                last_message_at=created_at,
                last_direction="inbound",
                last_sender_name=sender_name,
                unread_increment=1,
            )
            connection.commit()

            row = connection.execute(
                "SELECT * FROM chat_messages WHERE record_id = ?",
                (record_id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Incoming chat message was stored but could not be read back.")
        return self._row_to_message(row)

    def store_outgoing_message(
        self,
        *,
        channel_key: str,
        chat_id: str,
        text: str,
        external_message_id: str = "",
        source: str = "operator",
        sender_name: str = "Platform operator",
        status: str = "sent",
        raw_payload: dict[str, Any] | None = None,
    ) -> ChatMessageRecord:
        """Persist one outbound platform message and refresh the conversation summary."""
        created_at = utc_now_iso()
        record_id = f"chatmsg_{uuid4().hex[:14]}"
        payload_json = json.dumps(raw_payload or {}, ensure_ascii=False, sort_keys=True)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_messages (
                    record_id,
                    channel_key,
                    chat_id,
                    external_message_id,
                    direction,
                    sender_id,
                    sender_name,
                    text,
                    message_type,
                    source,
                    status,
                    created_at,
                    raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    channel_key,
                    chat_id,
                    external_message_id or None,
                    "outbound",
                    channel_key,
                    sender_name,
                    text,
                    "text",
                    source,
                    status,
                    created_at,
                    payload_json,
                ),
            )

            self._upsert_conversation(
                connection,
                channel_key=channel_key,
                chat_id=chat_id,
                display_name="",
                phone=derive_phone_from_chat_id(chat_id),
                avatar_url="",
                last_message_text=text,
                last_message_at=created_at,
                last_direction="outbound",
                last_sender_name=sender_name,
                unread_increment=0,
                reset_unread=True,
            )
            connection.commit()

            row = connection.execute(
                "SELECT * FROM chat_messages WHERE record_id = ?",
                (record_id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Outgoing chat message was stored but could not be read back.")
        return self._row_to_message(row)

    def mark_conversation_read(self, channel_key: str, chat_id: str) -> None:
        """Reset unread counters after the operator opens one chat."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE chat_conversations
                SET unread_count = 0, updated_at = ?
                WHERE channel_key = ? AND chat_id = ?
                """,
                (utc_now_iso(), channel_key, chat_id),
            )
            connection.commit()

    def _upsert_conversation(
        self,
        connection: sqlite3.Connection,
        *,
        channel_key: str,
        chat_id: str,
        display_name: str,
        phone: str,
        avatar_url: str,
        last_message_text: str,
        last_message_at: str,
        last_direction: str,
        last_sender_name: str,
        unread_increment: int,
        reset_unread: bool = False,
    ) -> None:
        existing = connection.execute(
            """
            SELECT unread_count, display_name, avatar_url
            FROM chat_conversations
            WHERE channel_key = ? AND chat_id = ?
            """,
            (channel_key, chat_id),
        ).fetchone()

        now = utc_now_iso()
        next_unread = 0 if reset_unread else unread_increment
        created_at = now
        next_display_name = display_name
        next_avatar_url = avatar_url

        if existing is not None:
            created_at = now
            if not reset_unread:
                next_unread = int(existing["unread_count"] or 0) + unread_increment
            next_display_name = display_name or str(existing["display_name"] or "").strip() or chat_id
            next_avatar_url = avatar_url or str(existing["avatar_url"] or "").strip()

        connection.execute(
            """
            INSERT INTO chat_conversations (
                channel_key,
                chat_id,
                display_name,
                phone,
                avatar_url,
                last_message_text,
                last_message_at,
                last_direction,
                last_sender_name,
                unread_count,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_key, chat_id) DO UPDATE SET
                display_name = excluded.display_name,
                phone = excluded.phone,
                avatar_url = excluded.avatar_url,
                last_message_text = excluded.last_message_text,
                last_message_at = excluded.last_message_at,
                last_direction = excluded.last_direction,
                last_sender_name = excluded.last_sender_name,
                unread_count = excluded.unread_count,
                updated_at = excluded.updated_at
            """,
            (
                channel_key,
                chat_id,
                next_display_name,
                phone,
                next_avatar_url,
                last_message_text,
                last_message_at,
                last_direction,
                last_sender_name,
                next_unread,
                created_at,
                now,
            ),
        )

    def _row_to_message(self, row: sqlite3.Row) -> ChatMessageRecord:
        return ChatMessageRecord(
            record_id=row["record_id"],
            channel_key=row["channel_key"],
            chat_id=row["chat_id"],
            external_message_id=row["external_message_id"] or "",
            direction=row["direction"],
            sender_id=row["sender_id"] or "",
            sender_name=row["sender_name"] or "",
            text=row["text"] or "",
            message_type=row["message_type"] or "text",
            source=row["source"] or "runtime",
            status=row["status"] or "",
            created_at=row["created_at"],
            raw_payload=json.loads(row["raw_payload_json"] or "{}"),
        )

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_conversations (
                    channel_key TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    avatar_url TEXT NOT NULL DEFAULT '',
                    last_message_text TEXT NOT NULL DEFAULT '',
                    last_message_at TEXT NOT NULL DEFAULT '',
                    last_direction TEXT NOT NULL DEFAULT 'inbound',
                    last_sender_name TEXT NOT NULL DEFAULT '',
                    unread_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (channel_key, chat_id)
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    record_id TEXT PRIMARY KEY,
                    channel_key TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    external_message_id TEXT,
                    direction TEXT NOT NULL,
                    sender_id TEXT NOT NULL DEFAULT '',
                    sender_name TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    message_type TEXT NOT NULL DEFAULT 'text',
                    source TEXT NOT NULL DEFAULT 'runtime',
                    status TEXT NOT NULL DEFAULT 'received',
                    created_at TEXT NOT NULL,
                    raw_payload_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_messages_external_unique
                ON chat_messages(channel_key, external_message_id);

                CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_timeline
                ON chat_messages(channel_key, chat_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_chat_conversations_channel_last_at
                ON chat_conversations(channel_key, last_message_at);
                """
            )
            connection.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


@lru_cache
def get_chat_store_service() -> ChatStoreService:
    """Reuse a single chat storage service bound to the app database."""
    return ChatStoreService(settings=get_settings())
