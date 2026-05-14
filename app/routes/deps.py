from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.services.project_registry import ProjectRecord, get_project_registry_service
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


def require_project_api_key(
    project_id: str,
    x_project_key: str | None = Header(default=None),
) -> ProjectRecord:
    """Authenticate project-scoped outbound messaging requests."""
    if not x_project_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Project-Key header.",
        )

    registry = get_project_registry_service()
    project = registry.verify_project_api_key(project_id, x_project_key)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid project API key.",
        )

    return project
