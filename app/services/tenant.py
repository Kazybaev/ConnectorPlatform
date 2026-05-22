from __future__ import annotations

import re

from fastapi import Request

from app.services.auth_service import AuthUser
from app.utils.config import get_settings


def user_channel_key(user: AuthUser) -> str:
    """Return the runtime/storage channel owned by one authenticated user."""
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", user.id).strip("-").lower()
    return f"user-{normalized or 'unknown'}"


def user_connection_name(user: AuthUser) -> str:
    """Return the display name for the user's personal WhatsApp runtime."""
    label = (user.full_name or user.email or "").strip()
    if label:
        return f"{label} WhatsApp"
    return get_settings().simple_connect_name


def request_user(request: Request) -> AuthUser:
    user = getattr(request.state, "current_user", None)
    if user is None:
        raise RuntimeError("Authenticated user is missing from request state.")
    return user
