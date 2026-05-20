from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routes.health import router as health_router
from app.routes.chat_console import api_router as chat_console_api_router
from app.routes.chat_console import router as chat_console_router
from app.routes.bot_console import api_router as bot_console_api_router
from app.routes.bot_console import router as bot_console_router
from app.routes.onboarding import api_router as onboarding_api_router
from app.routes.onboarding import router as onboarding_router
from app.routes.site import router as site_router
from app.routes.upload import router as upload_router
from app.services.chat_store import get_chat_store_service
from app.services.bot_registry import get_bot_registry_service
from app.services.self_hosted_runtime_service import SelfHostedRuntimeServiceError, get_self_hosted_runtime_service
from app.utils.config import get_settings
from app.utils.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
MEDIA_DIR = Path(__file__).resolve().parents[1] / "data" / "chat_media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting %s", settings.app_name)
    get_chat_store_service()
    bot_registry = get_bot_registry_service()
    try:
        get_self_hosted_runtime_service().ensure_channel(
            settings.runtime_platform_channel_key,
            settings.simple_connect_name,
        )
        logger.info("Runtime channel %s is ready", settings.runtime_platform_channel_key)
        connected_bot = bot_registry.ensure_default_bot_connected()
        logger.info(
            "Bot channel binding is ready: channel=%s bot=%s",
            settings.runtime_platform_channel_key,
            connected_bot.slug,
        )
    except SelfHostedRuntimeServiceError as exc:
        logger.warning("Runtime channel bootstrap failed: %s", exc)
    yield
    logger.info("Stopping %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description="Multi-tenant WhatsApp transport platform that routes self-hosted WhatsApp runtime traffic into external AI systems.",
    version="2.0.0",
    debug=settings.debug,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(upload_router)
app.include_router(site_router)
app.include_router(onboarding_router)
app.include_router(onboarding_api_router)
app.include_router(chat_console_router)
app.include_router(chat_console_api_router)
app.include_router(bot_console_router)
app.include_router(bot_console_api_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Log unexpected errors and hide internal details from the client."""
    logger.error(
        "Unhandled application error on %s",
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
