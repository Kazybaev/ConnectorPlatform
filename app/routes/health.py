from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models.schemas import HealthResponse
from app.utils.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Return basic service state for uptime checks."""
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        database_ready=True,
        active_projects=0,
        active_channels=1,
    )
