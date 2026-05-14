from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.models.schemas import (
    ProjectWithWhatsAppOnboardingRequest,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectSummaryResponse,
    ProjectUpdateRequest,
    RuntimeChannelStatusResponse,
    WhatsAppChannelCreateRequest,
    WhatsAppChannelResponse,
)
from app.utils.config import Settings, get_settings

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """Return a sortable UTC timestamp string."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def hash_project_key(value: str) -> str:
    """Store project API keys as hashes rather than plaintext."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mask_token(token: str) -> str:
    """Return a short token preview for admin listings."""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


@dataclass(slots=True)
class ProjectRecord:
    """Internal representation of a tenant project."""

    id: str
    name: str
    slug: str
    description: str
    enabled: bool
    provider_url: str
    provider_authorization_header: str
    provider_extra_headers: dict[str, str]
    project_api_key_hash: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class WhatsAppChannelRecord:
    """Internal representation of a project-bound Green API channel."""

    id: str
    project_id: str
    name: str
    enabled: bool
    green_api_url: str
    green_api_id_instance: str
    green_api_token: str
    last_error: str
    last_heartbeat_at: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ActiveChannelBinding:
    """Merged project and channel configuration used by the runtime."""

    project_id: str
    project_name: str
    project_slug: str
    project_enabled: bool
    provider_url: str
    provider_authorization_header: str
    provider_extra_headers: dict[str, str]
    channel_id: str
    channel_name: str
    channel_enabled: bool
    green_api_url: str
    green_api_id_instance: str
    green_api_token: str
    last_error: str
    last_heartbeat_at: str

    @property
    def runtime_key(self) -> str:
        """A stable cache key for runtime task reconciliation."""
        return self.channel_id

    @property
    def fingerprint(self) -> str:
        """Detect configuration changes that require a worker restart."""
        return "|".join(
            (
                self.project_id,
                self.provider_url,
                self.provider_authorization_header,
                json.dumps(self.provider_extra_headers, ensure_ascii=True, sort_keys=True),
                self.channel_id,
                self.green_api_url,
                self.green_api_id_instance,
                self.green_api_token,
                str(self.project_enabled),
                str(self.channel_enabled),
            )
        )


class ProjectRegistryService:
    """SQLite-backed control plane for projects and WhatsApp channels."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database_path = Path(settings.database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def create_project(self, payload: ProjectCreateRequest) -> ProjectCreateResponse:
        """Create a project and generate its API key."""
        now = utc_now_iso()
        project_id = f"proj_{uuid4().hex[:12]}"
        project_api_key = secrets.token_urlsafe(32)

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM projects WHERE slug = ?",
                (payload.slug,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Project slug '{payload.slug}' already exists.")

            connection.execute(
                """
                INSERT INTO projects (
                    id,
                    name,
                    slug,
                    description,
                    enabled,
                    provider_url,
                    provider_authorization_header,
                    provider_extra_headers_json,
                    project_api_key_hash,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    payload.name,
                    payload.slug,
                    payload.description,
                    1 if payload.enabled else 0,
                    str(payload.provider.url),
                    payload.provider.authorization_header,
                    json.dumps(payload.provider.extra_headers, ensure_ascii=True, sort_keys=True),
                    hash_project_key(project_api_key),
                    now,
                    now,
                ),
            )
            connection.commit()

        return ProjectCreateResponse(
            id=project_id,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            enabled=payload.enabled,
            provider_url=str(payload.provider.url),
            channel_count=0,
            created_at=now,
            updated_at=now,
            project_api_key=project_api_key,
        )

    def create_project_with_whatsapp_channel(
        self,
        payload: ProjectWithWhatsAppOnboardingRequest,
    ) -> tuple[ProjectCreateResponse, WhatsAppChannelResponse]:
        """Create a project and its first WhatsApp channel in a single transaction."""
        now = utc_now_iso()
        project_id = f"proj_{uuid4().hex[:12]}"
        channel_id = f"wa_{uuid4().hex[:12]}"
        project_api_key = secrets.token_urlsafe(32)

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM projects WHERE slug = ?",
                (payload.slug,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Project slug '{payload.slug}' already exists.")

            connection.execute(
                """
                INSERT INTO projects (
                    id,
                    name,
                    slug,
                    description,
                    enabled,
                    provider_url,
                    provider_authorization_header,
                    provider_extra_headers_json,
                    project_api_key_hash,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    payload.name,
                    payload.slug,
                    payload.description,
                    1 if payload.project_enabled else 0,
                    str(payload.provider.url),
                    payload.provider.authorization_header,
                    json.dumps(payload.provider.extra_headers, ensure_ascii=True, sort_keys=True),
                    hash_project_key(project_api_key),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO whatsapp_channels (
                    id,
                    project_id,
                    name,
                    enabled,
                    green_api_url,
                    green_api_id_instance,
                    green_api_token,
                    last_error,
                    last_heartbeat_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    project_id,
                    payload.channel_name,
                    1 if payload.channel_enabled else 0,
                    str(payload.green_api_url).rstrip("/"),
                    payload.green_api_id_instance,
                    payload.green_api_token,
                    "",
                    "",
                    now,
                    now,
                ),
            )
            connection.commit()

        return (
            ProjectCreateResponse(
                id=project_id,
                name=payload.name,
                slug=payload.slug,
                description=payload.description,
                enabled=payload.project_enabled,
                provider_url=str(payload.provider.url),
                channel_count=1,
                created_at=now,
                updated_at=now,
                project_api_key=project_api_key,
            ),
            WhatsAppChannelResponse(
                id=channel_id,
                project_id=project_id,
                name=payload.channel_name,
                enabled=payload.channel_enabled,
                green_api_url=str(payload.green_api_url).rstrip("/"),
                green_api_id_instance=payload.green_api_id_instance,
                token_preview=mask_token(payload.green_api_token),
                last_error="",
                last_heartbeat_at="",
                created_at=now,
                updated_at=now,
            ),
        )

    def list_projects(self) -> list[ProjectSummaryResponse]:
        """Return every registered project with channel counts."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.slug,
                    p.description,
                    p.enabled,
                    p.provider_url,
                    p.created_at,
                    p.updated_at,
                    COUNT(c.id) AS channel_count
                FROM projects p
                LEFT JOIN whatsapp_channels c ON c.project_id = p.id
                GROUP BY p.id
                ORDER BY p.created_at DESC
                """
            ).fetchall()

        return [
            ProjectSummaryResponse(
                id=row["id"],
                name=row["name"],
                slug=row["slug"],
                description=row["description"] or "",
                enabled=bool(row["enabled"]),
                provider_url=row["provider_url"],
                channel_count=int(row["channel_count"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_project(self, project_id: str) -> ProjectRecord | None:
        """Fetch one project with provider details."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    name,
                    slug,
                    description,
                    enabled,
                    provider_url,
                    provider_authorization_header,
                    provider_extra_headers_json,
                    project_api_key_hash,
                    created_at,
                    updated_at
                FROM projects
                WHERE id = ?
                """,
                (project_id,),
            ).fetchone()

        if row is None:
            return None

        return ProjectRecord(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            description=row["description"] or "",
            enabled=bool(row["enabled"]),
            provider_url=row["provider_url"],
            provider_authorization_header=row["provider_authorization_header"] or "",
            provider_extra_headers=json.loads(row["provider_extra_headers_json"] or "{}"),
            project_api_key_hash=row["project_api_key_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_project_summary(self, project_id: str) -> ProjectSummaryResponse | None:
        """Return the public admin view for a single project."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.slug,
                    p.description,
                    p.enabled,
                    p.provider_url,
                    p.created_at,
                    p.updated_at,
                    COUNT(c.id) AS channel_count
                FROM projects p
                LEFT JOIN whatsapp_channels c ON c.project_id = p.id
                WHERE p.id = ?
                GROUP BY p.id
                """,
                (project_id,),
            ).fetchone()

        if row is None:
            return None

        return ProjectSummaryResponse(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            description=row["description"] or "",
            enabled=bool(row["enabled"]),
            provider_url=row["provider_url"],
            channel_count=int(row["channel_count"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def update_project(self, project_id: str, payload: ProjectUpdateRequest) -> ProjectSummaryResponse | None:
        """Update mutable project fields."""
        current = self.get_project(project_id)
        if current is None:
            return None

        next_name = payload.name if payload.name is not None else current.name
        next_description = payload.description if payload.description is not None else current.description
        next_enabled = payload.enabled if payload.enabled is not None else current.enabled
        next_provider_url = str(payload.provider.url) if payload.provider is not None else current.provider_url
        next_provider_auth = (
            payload.provider.authorization_header if payload.provider is not None else current.provider_authorization_header
        )
        next_provider_headers = (
            payload.provider.extra_headers if payload.provider is not None else current.provider_extra_headers
        )
        updated_at = utc_now_iso()

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET
                    name = ?,
                    description = ?,
                    enabled = ?,
                    provider_url = ?,
                    provider_authorization_header = ?,
                    provider_extra_headers_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    next_name,
                    next_description,
                    1 if next_enabled else 0,
                    next_provider_url,
                    next_provider_auth,
                    json.dumps(next_provider_headers, ensure_ascii=True, sort_keys=True),
                    updated_at,
                    project_id,
                ),
            )
            connection.commit()

        return self.get_project_summary(project_id)

    def create_whatsapp_channel(
        self,
        project_id: str,
        payload: WhatsAppChannelCreateRequest,
    ) -> WhatsAppChannelResponse:
        """Attach a Green API channel to an existing project."""
        if self.get_project(project_id) is None:
            raise ValueError(f"Project '{project_id}' was not found.")

        now = utc_now_iso()
        channel_id = f"wa_{uuid4().hex[:12]}"

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO whatsapp_channels (
                    id,
                    project_id,
                    name,
                    enabled,
                    green_api_url,
                    green_api_id_instance,
                    green_api_token,
                    last_error,
                    last_heartbeat_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    project_id,
                    payload.name,
                    1 if payload.enabled else 0,
                    str(payload.green_api_url).rstrip("/"),
                    payload.green_api_id_instance,
                    payload.green_api_token,
                    "",
                    "",
                    now,
                    now,
                ),
            )
            connection.commit()

        return WhatsAppChannelResponse(
            id=channel_id,
            project_id=project_id,
            name=payload.name,
            enabled=payload.enabled,
            green_api_url=str(payload.green_api_url).rstrip("/"),
            green_api_id_instance=payload.green_api_id_instance,
            token_preview=mask_token(payload.green_api_token),
            last_error="",
            last_heartbeat_at="",
            created_at=now,
            updated_at=now,
        )

    def list_whatsapp_channels(self, project_id: str) -> list[WhatsAppChannelResponse]:
        """Return every WhatsApp channel for a project."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    project_id,
                    name,
                    enabled,
                    green_api_url,
                    green_api_id_instance,
                    green_api_token,
                    last_error,
                    last_heartbeat_at,
                    created_at,
                    updated_at
                FROM whatsapp_channels
                WHERE project_id = ?
                ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()

        return [
            WhatsAppChannelResponse(
                id=row["id"],
                project_id=row["project_id"],
                name=row["name"],
                enabled=bool(row["enabled"]),
                green_api_url=row["green_api_url"],
                green_api_id_instance=row["green_api_id_instance"],
                token_preview=mask_token(row["green_api_token"]),
                last_error=row["last_error"] or "",
                last_heartbeat_at=row["last_heartbeat_at"] or "",
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def verify_project_api_key(self, project_id: str, raw_key: str) -> ProjectRecord | None:
        """Authenticate a project-scoped API key."""
        record = self.get_project(project_id)
        if record is None:
            return None
        return record if record.project_api_key_hash == hash_project_key(raw_key) else None

    def resolve_project_channel(self, project_id: str, channel_id: str | None = None) -> WhatsAppChannelRecord | None:
        """Find a channel that belongs to the given project."""
        with self._connect() as connection:
            if channel_id:
                row = connection.execute(
                    """
                    SELECT
                        id,
                        project_id,
                        name,
                        enabled,
                        green_api_url,
                        green_api_id_instance,
                        green_api_token,
                        last_error,
                        last_heartbeat_at,
                        created_at,
                        updated_at
                    FROM whatsapp_channels
                    WHERE project_id = ? AND id = ?
                    """,
                    (project_id, channel_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT
                        id,
                        project_id,
                        name,
                        enabled,
                        green_api_url,
                        green_api_id_instance,
                        green_api_token,
                        last_error,
                        last_heartbeat_at,
                        created_at,
                        updated_at
                    FROM whatsapp_channels
                    WHERE project_id = ? AND enabled = 1
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()

        if row is None:
            return None

        return WhatsAppChannelRecord(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            green_api_url=row["green_api_url"],
            green_api_id_instance=row["green_api_id_instance"],
            green_api_token=row["green_api_token"],
            last_error=row["last_error"] or "",
            last_heartbeat_at=row["last_heartbeat_at"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_active_channel_bindings(self) -> list[ActiveChannelBinding]:
        """Return every enabled project/channel binding that the worker should poll."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    p.id AS project_id,
                    p.name AS project_name,
                    p.slug AS project_slug,
                    p.enabled AS project_enabled,
                    p.provider_url AS provider_url,
                    p.provider_authorization_header AS provider_authorization_header,
                    p.provider_extra_headers_json AS provider_extra_headers_json,
                    c.id AS channel_id,
                    c.name AS channel_name,
                    c.enabled AS channel_enabled,
                    c.green_api_url AS green_api_url,
                    c.green_api_id_instance AS green_api_id_instance,
                    c.green_api_token AS green_api_token,
                    c.last_error AS last_error,
                    c.last_heartbeat_at AS last_heartbeat_at
                FROM projects p
                JOIN whatsapp_channels c ON c.project_id = p.id
                WHERE p.enabled = 1 AND c.enabled = 1
                ORDER BY p.created_at ASC, c.created_at ASC
                """
            ).fetchall()

        return [
            ActiveChannelBinding(
                project_id=row["project_id"],
                project_name=row["project_name"],
                project_slug=row["project_slug"],
                project_enabled=bool(row["project_enabled"]),
                provider_url=row["provider_url"],
                provider_authorization_header=row["provider_authorization_header"] or "",
                provider_extra_headers=json.loads(row["provider_extra_headers_json"] or "{}"),
                channel_id=row["channel_id"],
                channel_name=row["channel_name"],
                channel_enabled=bool(row["channel_enabled"]),
                green_api_url=row["green_api_url"],
                green_api_id_instance=row["green_api_id_instance"],
                green_api_token=row["green_api_token"],
                last_error=row["last_error"] or "",
                last_heartbeat_at=row["last_heartbeat_at"] or "",
            )
            for row in rows
        ]

    def list_runtime_statuses(self) -> list[RuntimeChannelStatusResponse]:
        """Return operational status rows for admin dashboards."""
        return [
            RuntimeChannelStatusResponse(
                channel_id=binding.channel_id,
                project_id=binding.project_id,
                project_slug=binding.project_slug,
                channel_name=binding.channel_name,
                enabled=binding.channel_enabled and binding.project_enabled,
                last_error=binding.last_error,
                last_heartbeat_at=binding.last_heartbeat_at,
            )
            for binding in self.list_active_channel_bindings()
        ]

    def update_channel_runtime_state(
        self,
        channel_id: str,
        *,
        last_error: str | None = None,
        heartbeat_at: str | None = None,
    ) -> None:
        """Persist runtime health data for a channel."""
        fragments: list[str] = []
        values: list[Any] = []

        if last_error is not None:
            fragments.append("last_error = ?")
            values.append(last_error)

        if heartbeat_at is not None:
            fragments.append("last_heartbeat_at = ?")
            values.append(heartbeat_at)

        fragments.append("updated_at = ?")
        values.append(utc_now_iso())
        values.append(channel_id)

        with self._connect() as connection:
            connection.execute(
                f"UPDATE whatsapp_channels SET {', '.join(fragments)} WHERE id = ?",
                tuple(values),
            )
            connection.commit()

    def get_counts(self) -> tuple[int, int]:
        """Return project and enabled-channel counts for health checks."""
        with self._connect() as connection:
            project_count = int(connection.execute("SELECT COUNT(*) FROM projects WHERE enabled = 1").fetchone()[0])
            channel_count = int(
                connection.execute("SELECT COUNT(*) FROM whatsapp_channels WHERE enabled = 1").fetchone()[0]
            )
        return project_count, channel_count

    def _initialize_schema(self) -> None:
        """Create the SQLite schema if it does not exist yet."""
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    provider_url TEXT NOT NULL,
                    provider_authorization_header TEXT NOT NULL DEFAULT '',
                    provider_extra_headers_json TEXT NOT NULL DEFAULT '{}',
                    project_api_key_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS whatsapp_channels (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    green_api_url TEXT NOT NULL,
                    green_api_id_instance TEXT NOT NULL,
                    green_api_token TEXT NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    last_heartbeat_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_whatsapp_channels_project_id
                ON whatsapp_channels(project_id);
                """
            )
            connection.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open and reliably close a SQLite connection for each operation."""
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()


@lru_cache
def get_project_registry_service() -> ProjectRegistryService:
    """Return a fresh registry service bound to the current settings."""
    return ProjectRegistryService(settings=get_settings())
