from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = ROOT_DIR / ".env"


def load_env_file(env_file: Path = DEFAULT_ENV_FILE) -> None:
    """Load a local .env file without adding extra runtime dependencies."""
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class Settings(BaseModel):
    """Application settings loaded from environment variables."""

    model_config = ConfigDict(extra="ignore")

    app_name: str = "AI Connector"
    debug: bool = False
    log_level: str = "INFO"
    cors_origins: list[str] = ["*"]
    database_path: str = str(ROOT_DIR / "data" / "whatsapp_platform.sqlite3")
    platform_admin_token: str = ""
    platform_public_base_url: str = "http://127.0.0.1:8000"
    connect_timeout_seconds: float = 5.0
    request_timeout_seconds: float = 30.0
    runtime_channels_refresh_seconds: float = 15.0
    runtime_channel_heartbeat_seconds: float = 60.0
    runtime_service_base_url: str = "http://127.0.0.1:8011"
    runtime_service_port: int = 8011
    runtime_service_token: str = ""
    runtime_callback_token: str = ""
    runtime_service_autostart: bool = True
    runtime_platform_channel_key: str = "platform-main"
    simple_connect_name: str = "Platform WhatsApp"
    default_bot_dify_base_url: str = ""
    default_bot_dify_api_key: str = ""
    bot_typing_enabled: bool = True
    bot_typing_min_seconds: float = 1.2
    bot_typing_max_seconds: float = 6.0
    bot_typing_chars_per_second: float = 18.0
    bot_failure_reply_enabled: bool = True
    bot_failure_reply_text: str = "Передам ваше сообщение администратору."

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """Keep log level values consistent for the logging setup."""
        return value.upper()

    @field_validator(
        "connect_timeout_seconds",
        "request_timeout_seconds",
        "bot_typing_min_seconds",
        "bot_typing_max_seconds",
        "bot_typing_chars_per_second",
    )
    @classmethod
    def ensure_positive_timeout(cls, value: float) -> float:
        """Prevent invalid timeout configuration."""
        if value <= 0:
            raise ValueError("Numeric timing values must be positive.")
        return value

    @field_validator("database_path")
    @classmethod
    def normalize_database_path(cls, value: str) -> str:
        """Store the database path as an absolute filesystem location."""
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = ROOT_DIR / candidate
        return str(candidate.resolve())

    @property
    def admin_auth_enabled(self) -> bool:
        """Return True when admin routes are protected by a configured token."""
        return bool(self.platform_admin_token)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build a settings object from environment variables."""
        load_env_file()
        cors_raw = os.getenv("CORS_ORIGINS", "*")
        cors_origins = [item.strip() for item in cors_raw.split(",") if item.strip()] or ["*"]

        return cls(
            app_name=os.getenv("APP_NAME", cls.model_fields["app_name"].default),
            debug=os.getenv("DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"},
            log_level=os.getenv("LOG_LEVEL", cls.model_fields["log_level"].default),
            cors_origins=cors_origins,
            database_path=os.getenv("DATABASE_PATH", cls.model_fields["database_path"].default),
            platform_admin_token=os.getenv("PLATFORM_ADMIN_TOKEN", "").strip(),
            platform_public_base_url=os.getenv(
                "PLATFORM_PUBLIC_BASE_URL",
                cls.model_fields["platform_public_base_url"].default,
            ).strip().rstrip("/"),
            connect_timeout_seconds=float(
                os.getenv(
                    "CONNECT_TIMEOUT_SECONDS",
                    cls.model_fields["connect_timeout_seconds"].default,
                )
            ),
            request_timeout_seconds=float(
                os.getenv(
                    "REQUEST_TIMEOUT_SECONDS",
                    cls.model_fields["request_timeout_seconds"].default,
                )
            ),
            runtime_channels_refresh_seconds=float(
                os.getenv(
                    "RUNTIME_CHANNELS_REFRESH_SECONDS",
                    cls.model_fields["runtime_channels_refresh_seconds"].default,
                )
            ),
            runtime_channel_heartbeat_seconds=float(
                os.getenv(
                    "RUNTIME_CHANNEL_HEARTBEAT_SECONDS",
                    cls.model_fields["runtime_channel_heartbeat_seconds"].default,
                )
            ),
            runtime_service_base_url=os.getenv(
                "RUNTIME_SERVICE_BASE_URL",
                cls.model_fields["runtime_service_base_url"].default,
            ).strip().rstrip("/"),
            runtime_service_port=int(
                os.getenv(
                    "RUNTIME_SERVICE_PORT",
                    cls.model_fields["runtime_service_port"].default,
                )
            ),
            runtime_service_token=os.getenv("RUNTIME_SERVICE_TOKEN", "").strip(),
            runtime_callback_token=os.getenv("RUNTIME_CALLBACK_TOKEN", os.getenv("RUNTIME_SERVICE_TOKEN", "")).strip(),
            runtime_service_autostart=os.getenv("RUNTIME_SERVICE_AUTOSTART", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            runtime_platform_channel_key=os.getenv(
                "RUNTIME_PLATFORM_CHANNEL_KEY",
                cls.model_fields["runtime_platform_channel_key"].default,
            ).strip(),
            simple_connect_name=os.getenv(
                "SIMPLE_CONNECT_NAME",
                cls.model_fields["simple_connect_name"].default,
            ).strip(),
            default_bot_dify_base_url=os.getenv(
                "DEFAULT_BOT_DIFY_BASE_URL",
                cls.model_fields["default_bot_dify_base_url"].default,
            ).strip().rstrip("/"),
            default_bot_dify_api_key=os.getenv(
                "DEFAULT_BOT_DIFY_API_KEY",
                cls.model_fields["default_bot_dify_api_key"].default,
            ).strip(),
            bot_typing_enabled=os.getenv("BOT_TYPING_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            bot_typing_min_seconds=float(
                os.getenv(
                    "BOT_TYPING_MIN_SECONDS",
                    cls.model_fields["bot_typing_min_seconds"].default,
                )
            ),
            bot_typing_max_seconds=float(
                os.getenv(
                    "BOT_TYPING_MAX_SECONDS",
                    cls.model_fields["bot_typing_max_seconds"].default,
                )
            ),
            bot_typing_chars_per_second=float(
                os.getenv(
                    "BOT_TYPING_CHARS_PER_SECOND",
                    cls.model_fields["bot_typing_chars_per_second"].default,
                )
            ),
            bot_failure_reply_enabled=os.getenv("BOT_FAILURE_REPLY_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            bot_failure_reply_text=os.getenv(
                "BOT_FAILURE_REPLY_TEXT",
                cls.model_fields["bot_failure_reply_text"].default,
            ).strip(),
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for dependency injection."""
    return Settings.from_env()
