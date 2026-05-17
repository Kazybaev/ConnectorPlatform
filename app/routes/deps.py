from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.utils.config import get_settings


def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    """Protect admin control-plane routes when a platform token is configured."""
    settings = get_settings()
    if not settings.admin_auth_enabled:
        return

    if x_admin_token != settings.platform_admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Token.",
        )
