from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.models.schemas import SimpleWhatsAppConnectionResponse
from app.routes.auth import current_user_from_request
from app.services.self_hosted_runtime_service import (
    SelfHostedRuntimeServiceError,
    get_self_hosted_runtime_service,
)
from app.services.tenant import user_channel_key, user_connection_name
from app.utils.config import get_settings

router = APIRouter(include_in_schema=False)
api_router = APIRouter(prefix="/api/v1/connect/whatsapp", tags=["connect"])


def resolve_connection_identity(request: Request) -> tuple[str, str]:
    """Use the logged-in user's channel when available, otherwise the default platform channel."""
    user = current_user_from_request(request)
    if user is not None:
        return user_channel_key(user), user_connection_name(user)

    settings = get_settings()
    return settings.runtime_platform_channel_key, settings.simple_connect_name


def build_error_response(
    error_message: str,
    *,
    connection_name: str = "",
    channel_key: str = "",
    logout_performed: bool = False,
) -> SimpleWhatsAppConnectionResponse:
    """Return a stable error payload for the self-hosted connect page."""
    settings = get_settings()
    return SimpleWhatsAppConnectionResponse(
        configured=True,
        connection_name=connection_name or settings.simple_connect_name,
        connection_status="error",
        device_id=channel_key,
        qr_type="error",
        qr_message=error_message,
        last_error=error_message,
        logout_performed=logout_performed,
    )


def build_runtime_snapshot_response(
    runtime_payload: dict[str, object],
    *,
    connection_name: str,
    channel_key: str,
    logout_performed: bool = False,
) -> SimpleWhatsAppConnectionResponse:
    """Convert the local runtime payload into the lightweight connect response."""
    settings = get_settings()
    runtime_profile = runtime_payload.get("profile", {})
    profile = runtime_profile if isinstance(runtime_profile, dict) else {}

    connection_status_raw = str(runtime_payload.get("connection_status", "disconnected")).strip().lower()
    normalized_status = "connected" if connection_status_raw == "connected" else "disconnected"
    qr_available = bool(runtime_payload.get("qr_available", False))
    qr_code_data_url = str(runtime_payload.get("qr_code_data_url", "")).strip()
    last_error = str(runtime_payload.get("last_error", "")).strip()
    phone = str(runtime_payload.get("phone", "")).strip()
    push_name = str(runtime_payload.get("push_name", "")).strip()
    wid = str(runtime_payload.get("wid", "")).strip()
    display_name = str(runtime_payload.get("display_name", connection_name)).strip()
    platform = str(runtime_payload.get("platform", "")).strip()
    profile_name = (
        str(profile.get("name", "")).strip()
        or push_name
        or phone
        or display_name
    )
    contact_name = (
        str(profile.get("push_name", "")).strip()
        or str(profile.get("short_name", "")).strip()
        or push_name
    )
    description = str(profile.get("about", "")).strip()
    avatar = str(profile.get("avatar_url", "")).strip()

    if normalized_status == "connected":
        qr_type = "alreadyLogged"
        qr_message = "WhatsApp уже подключен к нашему self-hosted runtime."
    elif qr_available and qr_code_data_url:
        qr_type = "qrCode"
        qr_message = "Откройте WhatsApp на телефоне и отсканируйте QR-код."
    elif last_error:
        qr_type = "error"
        qr_message = last_error
    else:
        qr_type = "unavailable"
        qr_message = "Локальный runtime подготавливает сессию и скоро покажет QR-код."

    return SimpleWhatsAppConnectionResponse(
        configured=True,
        connection_name=connection_name,
        connection_status=normalized_status,
        state_instance=connection_status_raw or "disconnected",
        status_instance=platform or connection_status_raw or "runtime",
        phone=phone,
        chat_id=wid,
        device_id=channel_key,
        avatar=avatar,
        base64_avatar=avatar,
        profile_name=profile_name,
        contact_name=contact_name,
        email="",
        category="",
        description=description,
        is_business=bool(profile.get("is_business", False)),
        polling_ready=True,
        qr_type=qr_type,
        qr_message=qr_message,
        qr_code_data_url=qr_code_data_url,
        last_error=last_error,
        logout_performed=logout_performed,
    )


def collect_simple_connection_snapshot(
    *,
    request: Request,
    include_qr: bool,
    reset_session: bool,
) -> SimpleWhatsAppConnectionResponse:
    """Read the current user's WhatsApp connection state from the local runtime."""
    del include_qr

    runtime_service = get_self_hosted_runtime_service()
    channel_key, connection_name = resolve_connection_identity(request)

    try:
        if reset_session:
            runtime_payload = runtime_service.reset_channel(channel_key)
            return build_runtime_snapshot_response(
                runtime_payload,
                connection_name=connection_name,
                channel_key=channel_key,
                logout_performed=True,
            )

        runtime_payload = runtime_service.ensure_channel(channel_key, connection_name)
        return build_runtime_snapshot_response(
            runtime_payload,
            connection_name=connection_name,
            channel_key=channel_key,
        )
    except SelfHostedRuntimeServiceError as exc:
        return build_error_response(
            str(exc),
            connection_name=connection_name,
            channel_key=channel_key,
            logout_performed=reset_session,
        )


@router.get("/connect/whatsapp", response_class=HTMLResponse)
def whatsapp_connect_page() -> str:
    """Render the simplified WhatsApp QR connection screen."""
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Connector | Подключение WhatsApp</title>
  <meta name="description" content="Простое подключение платформенного WhatsApp по QR с понятным статусом connected или disconnected." />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/brand.css?v=ai-connector-20260519c" />
</head>
<body>
  <div class="page-shell onboarding-shell">
    <div class="ambient ambient-left"></div>
    <div class="ambient ambient-right"></div>
    <header class="topbar connect-topbar">
      <a class="brand-home" href="/" aria-label="AI Connector">
        <img class="brand-logo-image" src="/static/image.png?v=logo-20260520" alt="AI Connector" />
      </a>
      <nav class="nav-links">
        <a href="/">Платформа</a>
        <a href="/chats">Чаты</a>
        <a href="/bots">Боты</a>
        <a class="is-active" href="/connect/whatsapp">WhatsApp</a>
      </nav>
    </header>

    <main class="onboarding-main simple-connect-main">
      <section class="onboarding-intro reveal is-visible">
        <span class="eyebrow">Self-hosted runtime</span>
        <h1>Подключение WhatsApp через локальный QR</h1>
        <p class="hero-text">
          Платформа поднимает локальную WhatsApp Web JS-сессию через собственный runtime
          и отдает QR-код напрямую из него.
        </p>
        <p class="contract-note connect-hero-note">
          После первого сканирования текущая сессия сохраняется внутри нашего runtime, поэтому страницу
          можно обновлять и открывать заново без повторного входа. Новый QR нужен только после ручного сброса.
        </p>
      </section>

      <section class="simple-connect-grid simple-connect-grid-compact">
        <div class="wizard-card connect-primary-card reveal is-visible">
          <div class="simple-status-row">
            <div>
              <div class="card-label card-label-no-margin">Статус подключения</div>
              <h2 class="simple-connection-name" id="connection-name">Platform WhatsApp</h2>
            </div>
            <span class="status-badge status-badge-pending" id="connection-badge">Проверяем...</span>
          </div>

          <div class="status-grid">
            <div class="status-tile">
              <span class="status-kicker">Состояние runtime</span>
              <strong id="instance-state">Неизвестно</strong>
            </div>
            <div class="status-tile">
              <span class="status-kicker">Статус QR</span>
              <strong id="instance-status">Неизвестно</strong>
            </div>
          </div>

          <div class="qr-panel qr-panel-spaced">
            <div class="qr-frame">
              <img id="qr-image" alt="QR-код WhatsApp" hidden />
              <div id="qr-placeholder" class="qr-placeholder">
                QR-код загружается...
              </div>
            </div>
            <div class="qr-meta">
              <span class="pill" id="polling-pill">Проверяем runtime</span>
              <span class="pill" id="qr-pill">Проверяем QR</span>
            </div>
            <p class="contract-note" id="qr-message">
              Ждем ответ локального runtime.
            </p>
          </div>

          <div class="simple-actions">
            <button class="button button-secondary" id="refresh-btn" type="button">Обновить сейчас</button>
            <button class="button button-primary" id="reset-btn" type="button">Получить новый QR</button>
          </div>
        </div>

        <aside class="wizard-card wizard-sidebar connect-secondary-card reveal is-visible" id="account-session">
          <div class="card-label">Данные аккаунта</div>

          <div class="profile-shell profile-shell-no-margin">
            <div class="profile-head">
              <div class="avatar-shell">
                <img id="avatar-image" alt="Аватар WhatsApp" hidden />
                <div id="avatar-placeholder" class="avatar-placeholder">WA</div>
              </div>
              <div class="profile-summary">
                <span class="status-kicker">Имя профиля</span>
                <strong id="profile-name-value">Пока нет данных</strong>
                <span class="profile-subline" id="contact-name-value">Имя контакта: -</span>
              </div>
            </div>

            <div class="profile-grid">
              <div class="profile-item">
                <span class="status-kicker">Номер</span>
                <strong id="phone-value">Не подключен</strong>
              </div>
              <div class="profile-item">
                <span class="status-kicker">Chat ID</span>
                <strong id="chat-id-value">-</strong>
              </div>
              <div class="profile-item">
                <span class="status-kicker">Device ID</span>
                <strong id="device-id-value">-</strong>
              </div>
              <div class="profile-item">
                <span class="status-kicker">Бизнес-аккаунт</span>
                <strong id="business-value">-</strong>
              </div>
              <div class="profile-item">
                <span class="status-kicker">Категория</span>
                <strong id="category-value">-</strong>
              </div>
              <div class="profile-item">
                <span class="status-kicker">Email</span>
                <strong id="email-value">-</strong>
              </div>
            </div>

            <div class="profile-description">
              <span class="status-kicker">Описание профиля</span>
              <strong id="description-value">-</strong>
            </div>
          </div>

          <div class="result-console-shell">
            <div class="status-kicker">Диагностика runtime</div>
            <pre class="result-console" id="result-console">Готово.</pre>
          </div>
        </aside>
      </section>
    </main>
  </div>
  <script src="/static/onboarding.js?v=connect-20260514b"></script>
</body>
</html>"""


@api_router.get("/status", response_model=SimpleWhatsAppConnectionResponse)
def get_simple_whatsapp_status(request: Request, include_qr: bool = True) -> SimpleWhatsAppConnectionResponse:
    """Return the current status for the current user's WhatsApp account."""
    return collect_simple_connection_snapshot(request=request, include_qr=include_qr, reset_session=False)


@api_router.post("/reset", response_model=SimpleWhatsAppConnectionResponse)
def reset_simple_whatsapp_connection(request: Request) -> SimpleWhatsAppConnectionResponse:
    """Reset the current user's local session and request a fresh QR code."""
    return collect_simple_connection_snapshot(request=request, include_qr=True, reset_session=True)
