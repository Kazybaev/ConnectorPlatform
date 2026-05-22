from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from app.utils.config import Settings, get_settings
from app.utils.time import utc_now_iso

PASSWORD_HASH_ITERATIONS = 260_000
SESSION_DAYS = 30
RESET_TOKEN_MINUTES = 30
OAUTH_STATE_MINUTES = 10


@dataclass(slots=True)
class AuthUser:
    id: str
    email: str
    full_name: str
    auth_provider: str
    is_active: bool
    created_at: str
    updated_at: str


class AuthService:
    """SQLite-backed users, sessions, password reset, and OAuth state storage."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database_path = Path(settings.database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self._settings.google_oauth_client_id and self._settings.google_oauth_client_secret)

    def create_user(self, *, email: str, password: str, full_name: str = "", auth_provider: str = "password") -> AuthUser:
        email = self._normalize_email(email)
        full_name = full_name.strip()
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")

        now = utc_now_iso()
        password_hash = self._hash_password(password)
        user_id = f"user_{uuid4().hex[:18]}"
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM auth_users WHERE email = ?",
                (email,),
            ).fetchone()
            if existing is not None:
                raise ValueError("User with this email already exists.")

            connection.execute(
                """
                INSERT INTO auth_users (
                    id, email, full_name, password_hash, auth_provider, google_sub,
                    is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', 1, ?, ?)
                """,
                (user_id, email, full_name, password_hash, auth_provider, now, now),
            )
            connection.commit()
        return self.get_user_by_id(user_id)  # type: ignore[return-value]

    def upsert_google_user(self, *, email: str, full_name: str, google_sub: str) -> AuthUser:
        email = self._normalize_email(email)
        google_sub = google_sub.strip()
        if not google_sub:
            raise ValueError("Google account id is missing.")

        now = utc_now_iso()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM auth_users WHERE email = ? OR google_sub = ?",
                (email, google_sub),
            ).fetchone()
            if existing is None:
                user_id = f"user_{uuid4().hex[:18]}"
                connection.execute(
                    """
                    INSERT INTO auth_users (
                        id, email, full_name, password_hash, auth_provider, google_sub,
                        is_active, created_at, updated_at
                    ) VALUES (?, ?, ?, '', 'google', ?, 1, ?, ?)
                    """,
                    (user_id, email, full_name.strip(), google_sub, now, now),
                )
            else:
                user_id = existing["id"]
                connection.execute(
                    """
                    UPDATE auth_users
                    SET full_name = ?, auth_provider = 'google', google_sub = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (full_name.strip() or existing["full_name"], google_sub, now, user_id),
                )
            connection.commit()
        return self.get_user_by_id(user_id)  # type: ignore[return-value]

    def authenticate_password(self, *, email: str, password: str) -> AuthUser | None:
        email = self._normalize_email(email)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM auth_users WHERE email = ?", (email,)).fetchone()
        if row is None or not bool(row["is_active"]):
            return None
        stored_hash = str(row["password_hash"] or "")
        if not stored_hash or not self._verify_password(password, stored_hash):
            return None
        return self._row_to_user(row)

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        now = utc_now_iso()
        expires_at = (datetime.now(tz=UTC) + timedelta(days=SESSION_DAYS)).replace(microsecond=0).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash, user_id, now, expires_at),
            )
            connection.commit()
        return token

    def get_user_by_session(self, token: str) -> AuthUser | None:
        token = token.strip()
        if not token:
            return None

        now = utc_now_iso()
        token_hash = self._hash_token(token)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT auth_users.*
                FROM auth_sessions
                JOIN auth_users ON auth_users.id = auth_sessions.user_id
                WHERE auth_sessions.token_hash = ?
                    AND auth_sessions.expires_at > ?
                    AND auth_users.is_active = 1
                """,
                (token_hash, now),
            ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def delete_session(self, token: str) -> None:
        token = token.strip()
        if not token:
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (self._hash_token(token),))
            connection.commit()

    def create_password_reset_token(self, email: str) -> str | None:
        email = self._normalize_email(email)
        user = self.get_user_by_email(email)
        if user is None:
            return None

        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        now = utc_now_iso()
        expires_at = (datetime.now(tz=UTC) + timedelta(minutes=RESET_TOKEN_MINUTES)).replace(microsecond=0).isoformat()
        with self._connect() as connection:
            connection.execute("DELETE FROM auth_password_resets WHERE user_id = ?", (user.id,))
            connection.execute(
                """
                INSERT INTO auth_password_resets (token_hash, user_id, created_at, expires_at, used_at)
                VALUES (?, ?, ?, ?, '')
                """,
                (token_hash, user.id, now, expires_at),
            )
            connection.commit()
        return token

    def reset_password(self, *, token: str, password: str) -> AuthUser | None:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")

        now = utc_now_iso()
        token_hash = self._hash_token(token.strip())
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT auth_password_resets.*, auth_users.email
                FROM auth_password_resets
                JOIN auth_users ON auth_users.id = auth_password_resets.user_id
                WHERE auth_password_resets.token_hash = ?
                    AND auth_password_resets.expires_at > ?
                    AND auth_password_resets.used_at = ''
                """,
                (token_hash, now),
            ).fetchone()
            if row is None:
                return None

            password_hash = self._hash_password(password)
            connection.execute(
                "UPDATE auth_users SET password_hash = ?, auth_provider = 'password', updated_at = ? WHERE id = ?",
                (password_hash, now, row["user_id"]),
            )
            connection.execute(
                "UPDATE auth_password_resets SET used_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (row["user_id"],))
            connection.commit()
        return self.get_user_by_id(str(row["user_id"]))

    def create_oauth_state(self) -> str:
        state = secrets.token_urlsafe(24)
        state_hash = self._hash_token(state)
        now = utc_now_iso()
        expires_at = (datetime.now(tz=UTC) + timedelta(minutes=OAUTH_STATE_MINUTES)).replace(microsecond=0).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO auth_oauth_states (state_hash, created_at, expires_at) VALUES (?, ?, ?)",
                (state_hash, now, expires_at),
            )
            connection.commit()
        return state

    def consume_oauth_state(self, state: str) -> bool:
        state_hash = self._hash_token(state.strip())
        now = utc_now_iso()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_hash FROM auth_oauth_states WHERE state_hash = ? AND expires_at > ?",
                (state_hash, now),
            ).fetchone()
            if row is None:
                return False
            connection.execute("DELETE FROM auth_oauth_states WHERE state_hash = ?", (state_hash,))
            connection.commit()
        return True

    def get_user_by_id(self, user_id: str) -> AuthUser | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM auth_users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row is not None else None

    def get_user_by_email(self, email: str) -> AuthUser | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM auth_users WHERE email = ?", (self._normalize_email(email),)).fetchone()
        return self._row_to_user(row) if row is not None else None

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            PASSWORD_HASH_ITERATIONS,
        ).hex()
        return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${digest}"

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        try:
            algorithm, iterations_raw, salt, expected = stored_hash.split("$", 3)
            iterations = int(iterations_raw)
        except ValueError:
            return False
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return hmac.compare_digest(digest, expected)

    def _hash_token(self, token: str) -> str:
        secret = self._settings.auth_session_secret or self._settings.platform_admin_token or "dev-auth-secret"
        return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()

    def _normalize_email(self, email: str) -> str:
        cleaned = email.strip().lower()
        if "@" not in cleaned or len(cleaned) > 254:
            raise ValueError("Valid email is required.")
        return cleaned

    def _row_to_user(self, row: sqlite3.Row) -> AuthUser:
        return AuthUser(
            id=row["id"],
            email=row["email"],
            full_name=row["full_name"] or "",
            auth_provider=row["auth_provider"] or "password",
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    full_name TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL DEFAULT '',
                    auth_provider TEXT NOT NULL DEFAULT 'password',
                    google_sub TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_users_google_sub
                ON auth_users(google_sub)
                WHERE google_sub != '';

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES auth_users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                ON auth_sessions(user_id);

                CREATE TABLE IF NOT EXISTS auth_password_resets (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(user_id) REFERENCES auth_users(id)
                );

                CREATE TABLE IF NOT EXISTS auth_oauth_states (
                    state_hash TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
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
def get_auth_service() -> AuthService:
    return AuthService(settings=get_settings())
