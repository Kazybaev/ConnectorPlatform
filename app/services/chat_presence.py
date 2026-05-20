from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from app.utils.time import utc_now_iso

ONLINE_HOLD_SECONDS = 45.0
TYPING_TIMEOUT_SECONDS = 35.0
TYPING_MIN_VISIBLE_SECONDS = 4.0


@dataclass(slots=True)
class ChatPresenceSnapshot:
    """Short-lived UI presence for one WhatsApp conversation."""

    status: str = "offline"
    label: str = ""
    updated_at: str = ""
    expires_at: str = ""


@dataclass(slots=True)
class _ChatPresenceState:
    mode: str
    typing_until: float
    typing_visible_until: float
    online_until: float
    updated_at: str


class ChatPresenceService:
    """Track typing/online state while a platform bot is handling a chat."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[tuple[str, str], _ChatPresenceState] = {}

    def mark_typing(self, channel_key: str, chat_id: str) -> None:
        channel_key = channel_key.strip()
        chat_id = chat_id.strip()
        if not channel_key or not chat_id:
            return

        now = time.monotonic()
        key = (channel_key, chat_id)
        with self._lock:
            existing = self._states.get(key)
            self._states[key] = _ChatPresenceState(
                mode="typing",
                typing_until=now + TYPING_TIMEOUT_SECONDS,
                typing_visible_until=max(
                    existing.typing_visible_until if existing else 0.0,
                    now + TYPING_MIN_VISIBLE_SECONDS,
                ),
                online_until=max(
                    existing.online_until if existing else 0.0,
                    now + ONLINE_HOLD_SECONDS,
                ),
                updated_at=utc_now_iso(),
            )

    def mark_online(self, channel_key: str, chat_id: str, hold_seconds: float = ONLINE_HOLD_SECONDS) -> None:
        channel_key = channel_key.strip()
        chat_id = chat_id.strip()
        if not channel_key or not chat_id:
            return

        now = time.monotonic()
        key = (channel_key, chat_id)
        with self._lock:
            existing = self._states.get(key)
            self._states[key] = _ChatPresenceState(
                mode="online",
                typing_until=0.0,
                typing_visible_until=existing.typing_visible_until if existing else 0.0,
                online_until=max(
                    existing.online_until if existing else 0.0,
                    now + max(1.0, hold_seconds),
                ),
                updated_at=utc_now_iso(),
            )

    def get_presence(self, channel_key: str, chat_id: str) -> ChatPresenceSnapshot:
        key = (channel_key.strip(), chat_id.strip())
        if not key[0] or not key[1]:
            return ChatPresenceSnapshot()

        now = time.monotonic()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return ChatPresenceSnapshot()

            status, expires_at_monotonic = self._resolve_state(state, now)
            if status == "offline":
                self._states.pop(key, None)
                return ChatPresenceSnapshot()

            return ChatPresenceSnapshot(
                status=status,
                label="печатает..." if status == "typing" else "online",
                updated_at=state.updated_at,
                expires_at=self._expires_at_iso(now, expires_at_monotonic),
            )

    def _resolve_state(self, state: _ChatPresenceState, now: float) -> tuple[str, float]:
        if state.mode == "typing" and now <= state.typing_until:
            return "typing", state.typing_until

        if now <= state.typing_visible_until:
            return "typing", state.typing_visible_until

        if now <= state.online_until:
            return "online", state.online_until

        return "offline", now

    def _expires_at_iso(self, now: float, expires_at_monotonic: float) -> str:
        seconds_left = max(0.0, expires_at_monotonic - now)
        return (datetime.now(UTC) + timedelta(seconds=seconds_left)).replace(microsecond=0).isoformat()


@lru_cache
def get_chat_presence_service() -> ChatPresenceService:
    """Return the shared in-process chat presence tracker."""
    return ChatPresenceService()
