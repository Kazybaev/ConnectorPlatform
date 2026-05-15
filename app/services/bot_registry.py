from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from app.models.schemas import BotApiBinding, BotCreateRequest, BotVariableDefinition
from app.services.project_registry import utc_now_iso
from app.utils.config import Settings, get_settings


@dataclass(slots=True)
class BotRecord:
    """Internal representation of one reusable platform bot."""

    id: str
    name: str
    slug: str
    description: str
    engine_type: str
    endpoint_url: str
    authorization_header: str
    owner_label: str
    workflow_summary: str
    linked_project_id: str
    linked_channel_key: str
    enabled: bool
    is_default_template: bool
    connected_channel_keys: list[str]
    variables: list[BotVariableDefinition]
    api_bindings: list[BotApiBinding]
    created_at: str
    updated_at: str


@dataclass(slots=True)
class BotThreadRecord:
    """Conversation memory for one bot/chat pair."""

    bot_id: str
    channel_key: str
    chat_id: str
    provider_conversation_id: str
    created_at: str
    updated_at: str


class BotRegistryService:
    """SQLite-backed registry for default and custom bot integrations."""

    DEFAULT_BOT_SLUG = "default-dify-bot"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database_path = Path(settings.database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()
        self.sync_default_bot_from_settings()

    def list_bots(self) -> list[BotRecord]:
        """Return all known bots with the default template pinned first."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    name,
                    slug,
                    description,
                    engine_type,
                    endpoint_url,
                    authorization_header,
                    owner_label,
                    workflow_summary,
                    linked_project_id,
                    linked_channel_key,
                    enabled,
                    is_default_template,
                    variables_json,
                    api_bindings_json,
                    created_at,
                    updated_at
                FROM platform_bots
                ORDER BY is_default_template DESC, created_at DESC
                """
            ).fetchall()
            connections = self._load_connected_channels(connection)

        return [self._row_to_record(row, connections) for row in rows]

    def get_bot(self, bot_id: str) -> BotRecord | None:
        """Return one bot by its stable id."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    name,
                    slug,
                    description,
                    engine_type,
                    endpoint_url,
                    authorization_header,
                    owner_label,
                    workflow_summary,
                    linked_project_id,
                    linked_channel_key,
                    enabled,
                    is_default_template,
                    variables_json,
                    api_bindings_json,
                    created_at,
                    updated_at
                FROM platform_bots
                WHERE id = ?
                """,
                (bot_id,),
            ).fetchone()
            connections = self._load_connected_channels(connection)

        return self._row_to_record(row, connections) if row is not None else None

    def get_bot_by_slug(self, slug: str) -> BotRecord | None:
        """Find one bot by slug for idempotent template creation."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    name,
                    slug,
                    description,
                    engine_type,
                    endpoint_url,
                    authorization_header,
                    owner_label,
                    workflow_summary,
                    linked_project_id,
                    linked_channel_key,
                    enabled,
                    is_default_template,
                    variables_json,
                    api_bindings_json,
                    created_at,
                    updated_at
                FROM platform_bots
                WHERE slug = ?
                LIMIT 1
                """,
                (slug,),
            ).fetchone()
            connections = self._load_connected_channels(connection)

        return self._row_to_record(row, connections) if row is not None else None

    def create_bot(self, payload: BotCreateRequest, *, is_default_template: bool = False) -> BotRecord:
        """Persist one bot definition and its linked variables and APIs."""
        existing = self.get_bot_by_slug(payload.slug)
        if existing is not None:
            raise ValueError(f"Bot slug '{payload.slug}' already exists.")

        now = utc_now_iso()
        bot_id = f"bot_{uuid4().hex[:12]}"
        linked_channel_key = payload.linked_channel_key or self._settings.runtime_platform_channel_key

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO platform_bots (
                    id,
                    name,
                    slug,
                    description,
                    engine_type,
                    endpoint_url,
                    authorization_header,
                    owner_label,
                    workflow_summary,
                    linked_project_id,
                    linked_channel_key,
                    enabled,
                    is_default_template,
                    variables_json,
                    api_bindings_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bot_id,
                    payload.name,
                    payload.slug,
                    payload.description,
                    payload.engine_type,
                    payload.endpoint_url.rstrip("/"),
                    payload.authorization_header,
                    payload.owner_label,
                    payload.workflow_summary,
                    payload.linked_project_id,
                    linked_channel_key,
                    1 if payload.enabled else 0,
                    1 if is_default_template else 0,
                    json.dumps([item.model_dump() for item in payload.variables], ensure_ascii=False),
                    json.dumps([item.model_dump() for item in payload.api_bindings], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            connection.commit()

        record = self.get_bot(bot_id)
        if record is None:
            raise RuntimeError("Bot was created but could not be loaded back.")
        return record

    def ensure_default_bot(self) -> BotRecord:
        """Create the optional default Dify bot once and reuse it afterwards."""
        existing = self.get_bot_by_slug(self.DEFAULT_BOT_SLUG)
        if existing is not None:
            return existing

        return self.create_bot(self._build_default_bot_payload(), is_default_template=True)

    def sync_default_bot_from_settings(self) -> BotRecord:
        """Create or update the default Dify bot using the current environment settings."""
        record = self.ensure_default_bot()

        base_url = self._settings.default_bot_dify_base_url.rstrip("/")
        api_key = self._settings.default_bot_dify_api_key.strip()
        if not base_url and not api_key:
            return record

        configured_payload = self._build_default_bot_payload(
            endpoint_url=base_url or record.endpoint_url,
            authorization_header=f"Bearer {api_key}" if api_key else record.authorization_header,
            enabled=record.enabled,
        )
        self._update_bot_record(record.id, configured_payload, is_default_template=True)
        refreshed = self.get_bot(record.id)
        if refreshed is None:
            raise RuntimeError("Default bot was updated but could not be reloaded.")
        return refreshed

    def ensure_default_bot_connected(self) -> BotRecord:
        """Reconnect the default bot to the platform runtime when no other active bot is attached."""
        record = self.sync_default_bot_from_settings()
        if not record.enabled:
            return record

        channel_key = self._settings.runtime_platform_channel_key
        connected = self.get_connected_bot_for_channel(channel_key)
        if connected is not None:
            return connected

        return self.connect_bot_to_channel(record.id, channel_key)

    def set_bot_enabled(self, bot_id: str, enabled: bool) -> BotRecord:
        """Activate or deactivate one registered bot."""
        record = self.get_bot(bot_id)
        if record is None:
            raise ValueError("Bot not found.")

        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE platform_bots
                SET enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if enabled else 0, now, bot_id),
            )
            if not enabled:
                connection.execute(
                    """
                    UPDATE platform_bot_connections
                    SET enabled = 0, updated_at = ?
                    WHERE bot_id = ?
                    """,
                    (now, bot_id),
                )
            connection.commit()

        refreshed = self.get_bot(bot_id)
        if refreshed is None:
            raise RuntimeError("Bot activation state was saved but could not be reloaded.")
        return refreshed

    def connect_bot_to_channel(self, bot_id: str, channel_key: str) -> BotRecord:
        """Activate one bot as the test bot for a specific runtime channel."""
        record = self.get_bot(bot_id)
        if record is None:
            raise ValueError("Bot not found.")

        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE platform_bot_connections
                SET enabled = 0, updated_at = ?
                WHERE channel_key = ?
                """,
                (now, channel_key),
            )
            connection.execute(
                """
                INSERT INTO platform_bot_connections (
                    bot_id,
                    channel_key,
                    enabled,
                    created_at,
                    updated_at
                ) VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(bot_id, channel_key) DO UPDATE SET
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (bot_id, channel_key, now, now),
            )
            connection.commit()

        refreshed = self.get_bot(bot_id)
        if refreshed is None:
            raise RuntimeError("Bot connection was saved but could not be reloaded.")
        return refreshed

    def disconnect_bot_from_channel(self, bot_id: str, channel_key: str) -> BotRecord:
        """Disable one test bot connection for the given runtime channel."""
        record = self.get_bot(bot_id)
        if record is None:
            raise ValueError("Bot not found.")

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE platform_bot_connections
                SET enabled = 0, updated_at = ?
                WHERE bot_id = ? AND channel_key = ?
                """,
                (utc_now_iso(), bot_id, channel_key),
            )
            connection.commit()

        refreshed = self.get_bot(bot_id)
        if refreshed is None:
            raise RuntimeError("Bot disconnection was saved but could not be reloaded.")
        return refreshed

    def get_connected_bot_for_channel(self, channel_key: str) -> BotRecord | None:
        """Return the currently active test bot for one runtime channel."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    b.id,
                    b.name,
                    b.slug,
                    b.description,
                    b.engine_type,
                    b.endpoint_url,
                    b.authorization_header,
                    b.owner_label,
                    b.workflow_summary,
                    b.linked_project_id,
                    b.linked_channel_key,
                    b.enabled,
                    b.is_default_template,
                    b.variables_json,
                    b.api_bindings_json,
                    b.created_at,
                    b.updated_at
                FROM platform_bot_connections c
                JOIN platform_bots b ON b.id = c.bot_id
                WHERE c.channel_key = ? AND c.enabled = 1 AND b.enabled = 1
                ORDER BY c.updated_at DESC
                LIMIT 1
                """,
                (channel_key,),
            ).fetchone()
            connections = self._load_connected_channels(connection)

        return self._row_to_record(row, connections) if row is not None else None

    def get_thread(self, bot_id: str, channel_key: str, chat_id: str) -> BotThreadRecord | None:
        """Read one persisted provider conversation id for a WhatsApp chat."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    bot_id,
                    channel_key,
                    chat_id,
                    provider_conversation_id,
                    created_at,
                    updated_at
                FROM platform_bot_threads
                WHERE bot_id = ? AND channel_key = ? AND chat_id = ?
                """,
                (bot_id, channel_key, chat_id),
            ).fetchone()

        if row is None:
            return None

        return BotThreadRecord(
            bot_id=row["bot_id"],
            channel_key=row["channel_key"],
            chat_id=row["chat_id"],
            provider_conversation_id=row["provider_conversation_id"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save_thread(self, bot_id: str, channel_key: str, chat_id: str, provider_conversation_id: str) -> None:
        """Persist the provider-side conversation id for one WhatsApp chat."""
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO platform_bot_threads (
                    bot_id,
                    channel_key,
                    chat_id,
                    provider_conversation_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, channel_key, chat_id) DO UPDATE SET
                    provider_conversation_id = excluded.provider_conversation_id,
                    updated_at = excluded.updated_at
                """,
                (bot_id, channel_key, chat_id, provider_conversation_id, now, now),
            )
            connection.commit()

    def _build_default_bot_payload(
        self,
        *,
        endpoint_url: str = "https://api.dify.ai/v1",
        authorization_header: str = "Bearer YOUR_DIFY_API_KEY",
        enabled: bool = True,
    ) -> BotCreateRequest:
        configured_base_url = endpoint_url.rstrip("/") if endpoint_url else "https://api.dify.ai/v1"
        return BotCreateRequest(
            name="Default Dify Bot",
            slug=self.DEFAULT_BOT_SLUG,
            description=(
                "Опциональный дефолтный бот платформы на базе Dify. "
                "Подходит для схемы Start -> Knowledge -> LLM -> Answer без отдельного backend."
            ),
            engine_type="dify",
            endpoint_url=configured_base_url,
            authorization_header=authorization_header,
            owner_label="Platform default bot",
            workflow_summary=(
                "Этот бот работает напрямую через Dify Service API. "
                "Платформа принимает входящее сообщение из WhatsApp, отправляет query в Dify и возвращает answer обратно в тот же чат."
            ),
            linked_project_id="",
            linked_channel_key=self._settings.runtime_platform_channel_key,
            enabled=enabled,
            variables=[
                BotVariableDefinition(
                    key="DIFY_BASE_URL",
                    required=True,
                    default_value=configured_base_url,
                    description="Базовый URL Dify API для дефолтного бота.",
                ),
                BotVariableDefinition(
                    key="DIFY_API_KEY",
                    required=True,
                    default_value="configured-server-side",
                    description="API key хранится на стороне платформы и не требуется на клиенте.",
                ),
                BotVariableDefinition(
                    key="PLATFORM_CHANNEL_KEY",
                    required=True,
                    default_value=self._settings.runtime_platform_channel_key,
                    description="Канал runtime, к которому подключается тестовый бот.",
                ),
            ],
            api_bindings=[
                BotApiBinding(
                    name="Dify service API",
                    kind="http",
                    endpoint_url=configured_base_url,
                    notes="Платформа сама определяет режим Dify app и вызывает нужный route: chat-messages, completion-messages или workflows/run.",
                ),
            ],
        )

    def _update_bot_record(self, bot_id: str, payload: BotCreateRequest, *, is_default_template: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE platform_bots
                SET
                    name = ?,
                    slug = ?,
                    description = ?,
                    engine_type = ?,
                    endpoint_url = ?,
                    authorization_header = ?,
                    owner_label = ?,
                    workflow_summary = ?,
                    linked_project_id = ?,
                    linked_channel_key = ?,
                    enabled = ?,
                    is_default_template = ?,
                    variables_json = ?,
                    api_bindings_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.name,
                    payload.slug,
                    payload.description,
                    payload.engine_type,
                    payload.endpoint_url.rstrip("/"),
                    payload.authorization_header,
                    payload.owner_label,
                    payload.workflow_summary,
                    payload.linked_project_id,
                    payload.linked_channel_key or self._settings.runtime_platform_channel_key,
                    1 if payload.enabled else 0,
                    1 if is_default_template else 0,
                    json.dumps([item.model_dump() for item in payload.variables], ensure_ascii=False),
                    json.dumps([item.model_dump() for item in payload.api_bindings], ensure_ascii=False),
                    utc_now_iso(),
                    bot_id,
                ),
            )
            connection.commit()

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS platform_bots (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    engine_type TEXT NOT NULL,
                    endpoint_url TEXT NOT NULL DEFAULT '',
                    authorization_header TEXT NOT NULL DEFAULT '',
                    owner_label TEXT NOT NULL DEFAULT '',
                    workflow_summary TEXT NOT NULL DEFAULT '',
                    linked_project_id TEXT NOT NULL DEFAULT '',
                    linked_channel_key TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    is_default_template INTEGER NOT NULL DEFAULT 0,
                    variables_json TEXT NOT NULL DEFAULT '[]',
                    api_bindings_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS platform_bot_connections (
                    bot_id TEXT NOT NULL,
                    channel_key TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (bot_id, channel_key),
                    FOREIGN KEY (bot_id) REFERENCES platform_bots (id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS platform_bot_threads (
                    bot_id TEXT NOT NULL,
                    channel_key TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    provider_conversation_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (bot_id, channel_key, chat_id),
                    FOREIGN KEY (bot_id) REFERENCES platform_bots (id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_platform_bots_default_created
                ON platform_bots(is_default_template, created_at);

                CREATE INDEX IF NOT EXISTS idx_platform_bot_connections_channel
                ON platform_bot_connections(channel_key, enabled, updated_at);
                """
            )
            connection.commit()

    def _load_connected_channels(self, connection: sqlite3.Connection) -> dict[str, list[str]]:
        rows = connection.execute(
            """
            SELECT bot_id, channel_key
            FROM platform_bot_connections
            WHERE enabled = 1
            """
        ).fetchall()

        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row["bot_id"], []).append(row["channel_key"])
        return grouped

    def _row_to_record(self, row: sqlite3.Row, connections: dict[str, list[str]]) -> BotRecord:
        variables_payload = json.loads(row["variables_json"] or "[]")
        api_bindings_payload = json.loads(row["api_bindings_json"] or "[]")
        return BotRecord(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            description=row["description"] or "",
            engine_type=row["engine_type"],
            endpoint_url=row["endpoint_url"] or "",
            authorization_header=row["authorization_header"] or "",
            owner_label=row["owner_label"] or "",
            workflow_summary=row["workflow_summary"] or "",
            linked_project_id=row["linked_project_id"] or "",
            linked_channel_key=row["linked_channel_key"] or "",
            enabled=bool(row["enabled"]),
            is_default_template=bool(row["is_default_template"]),
            connected_channel_keys=connections.get(row["id"], []),
            variables=[BotVariableDefinition.model_validate(item) for item in variables_payload],
            api_bindings=[BotApiBinding.model_validate(item) for item in api_bindings_payload],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()


@lru_cache
def get_bot_registry_service() -> BotRegistryService:
    """Return the shared bot registry bound to the platform database."""
    return BotRegistryService(settings=get_settings())
