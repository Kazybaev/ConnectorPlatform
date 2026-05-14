from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.models.schemas import SimpleWhatsAppConnectionResponse
from app.services.green_api_service import GreenApiClient, GreenApiCredentials, GreenApiServiceError
from app.utils.config import get_settings

router = APIRouter(include_in_schema=False)
api_router = APIRouter(prefix="/api/v1/connect/whatsapp", tags=["connect"])
T = TypeVar("T")


def is_rate_limited_error(exc: GreenApiServiceError) -> bool:
    """Detect Green API throttling that should not break the whole page."""
    return "429" in str(exc)


def is_qr_warming_up_error(exc: GreenApiServiceError) -> bool:
    """Detect transient QR generation delays after logout."""
    message = str(exc).casefold()
    return "received timeout from websocket" in message or "timeout" in message


def safe_green_call(operation: Callable[[], T], default: T) -> tuple[T, str]:
    """Run one Green API call and downgrade soft failures into partial data."""
    try:
        return operation(), ""
    except GreenApiServiceError as exc:
        return default, str(exc)


def build_simple_connect_client() -> GreenApiClient | None:
    """Build the platform-owned Green API client for the simple QR connect page."""
    settings = get_settings()
    if not settings.simple_connect_configured:
        return None

    return GreenApiClient(
        GreenApiCredentials(
            api_url=settings.simple_connect_green_api_url,
            id_instance=settings.simple_connect_green_api_id_instance,
            api_token=settings.simple_connect_green_api_token,
        ),
        connect_timeout_seconds=settings.connect_timeout_seconds,
        request_timeout_seconds=settings.request_timeout_seconds,
        receive_timeout_seconds=settings.green_api_receive_timeout_seconds,
    )


def ensure_platform_green_api_settings(green_api_client: GreenApiClient) -> tuple[dict[str, object], bool]:
    """Push the instance toward the polling mode expected by the platform."""
    settings_payload = green_api_client.get_settings()
    webhook_url = str(settings_payload.get("webhookUrl", "")).strip()
    incoming_webhook = str(settings_payload.get("incomingWebhook", "")).strip().lower()
    polling_ready = not webhook_url and incoming_webhook == "yes"

    if polling_ready:
        return settings_payload, True

    green_api_client.set_settings(
        {
            "webhookUrl": "",
            "incomingWebhook": "yes",
            "outgoingWebhook": "yes",
            "stateWebhook": "yes",
        }
    )
    settings_payload = green_api_client.get_settings()
    webhook_url = str(settings_payload.get("webhookUrl", "")).strip()
    incoming_webhook = str(settings_payload.get("incomingWebhook", "")).strip().lower()
    polling_ready = not webhook_url and incoming_webhook == "yes"
    return settings_payload, polling_ready


def build_not_configured_response() -> SimpleWhatsAppConnectionResponse:
    """Explain that the simple connect page needs one server-side Green API instance."""
    settings = get_settings()
    return SimpleWhatsAppConnectionResponse(
        configured=False,
        connection_name=settings.simple_connect_name,
        connection_status="not_configured",
        qr_type="error",
        qr_message=(
            "Simple connect is not configured yet. Add SIMPLE_CONNECT_GREEN_API_URL, "
            "SIMPLE_CONNECT_GREEN_API_ID_INSTANCE and SIMPLE_CONNECT_GREEN_API_TOKEN to .env."
        ),
        last_error=(
            "Missing SIMPLE_CONNECT_GREEN_API_URL / SIMPLE_CONNECT_GREEN_API_ID_INSTANCE / "
            "SIMPLE_CONNECT_GREEN_API_TOKEN in .env."
        ),
    )


def build_error_response(error_message: str, *, logout_performed: bool = False) -> SimpleWhatsAppConnectionResponse:
    """Return a stable error payload for the simple connect page."""
    settings = get_settings()
    return SimpleWhatsAppConnectionResponse(
        configured=settings.simple_connect_configured,
        connection_name=settings.simple_connect_name,
        connection_status="error",
        qr_type="error",
        qr_message=error_message,
        last_error=error_message,
        logout_performed=logout_performed,
    )


def build_logout_in_progress_response(logout_performed: bool) -> SimpleWhatsAppConnectionResponse:
    """Return a calm response immediately after logout while Green API prepares a new QR."""
    settings = get_settings()
    return SimpleWhatsAppConnectionResponse(
        configured=settings.simple_connect_configured,
        connection_name=settings.simple_connect_name,
        connection_status="disconnected",
        qr_type="unavailable",
        qr_message=(
            "Сессия завершена, instance перезапущен. Green API подготавливает новый QR-код. "
            "Обычно это занимает 1-2 минуты. Оставьте страницу открытой или нажмите «Обновить сейчас» позже."
        ),
        logout_performed=logout_performed,
        polling_ready=False,
        last_error="",
    )


def collect_simple_connection_snapshot(*, include_qr: bool, reset_session: bool) -> SimpleWhatsAppConnectionResponse:
    """Read the current platform-owned WhatsApp connection state from Green API."""
    settings = get_settings()
    green_api_client = build_simple_connect_client()
    if green_api_client is None:
        return build_not_configured_response()

    logout_performed = False
    if reset_session:
        logout_performed = green_api_client.logout_instance()
        safe_green_call(green_api_client.reboot_instance, False)
        return build_logout_in_progress_response(logout_performed)

    settings_payload, polling_ready = ensure_platform_green_api_settings(green_api_client)
    state_instance, state_error = safe_green_call(green_api_client.get_state_instance, "")
    status_instance, status_error = safe_green_call(green_api_client.get_status_instance, "")
    wa_settings, wa_error = safe_green_call(green_api_client.get_wa_settings, {})

    qr_type = "unavailable"
    qr_message = ""
    qr_code_data_url = ""
    last_error = ""
    if include_qr:
        qr_payload, qr_error = safe_green_call(green_api_client.get_qr_code, {"type": "unavailable", "message": ""})
        if qr_error:
            if is_qr_warming_up_error(GreenApiServiceError(qr_error)):
                qr_type = "unavailable"
                qr_message = (
                    "Green API еще не успел выдать новый QR-код. После разлогина это может занять 1-2 минуты."
                )
            elif is_rate_limited_error(GreenApiServiceError(qr_error)):
                qr_type = "unavailable"
                qr_message = "Green API временно ограничил частые запросы. Подождите несколько секунд."
            else:
                qr_type = "error"
                qr_message = qr_error
                last_error = qr_error
        else:
            raw_qr_type = str(qr_payload.get("type", "")).strip()
            qr_type = raw_qr_type if raw_qr_type in {"qrCode", "error", "alreadyLogged"} else "error"
            qr_message = str(qr_payload.get("message", "")).strip()
            if qr_type == "qrCode":
                qr_code_data_url = f"data:image/png;base64,{qr_payload['message']}"

    state_from_wa = str(wa_settings.get("stateInstance", "")).strip() if isinstance(wa_settings, dict) else ""
    phone = str(wa_settings.get("phone", "")).strip() if isinstance(wa_settings, dict) else ""
    chat_id = str(wa_settings.get("chatId", "")).strip() if isinstance(wa_settings, dict) else ""
    device_id = str(wa_settings.get("deviceId", "")).strip() if isinstance(wa_settings, dict) else ""
    avatar = str(wa_settings.get("avatar", "")).strip() if isinstance(wa_settings, dict) else ""
    base64_avatar = str(wa_settings.get("base64Avatar", "")).strip() if isinstance(wa_settings, dict) else ""
    webhook_url = str(settings_payload.get("webhookUrl", "")).strip()
    incoming_webhook = str(settings_payload.get("incomingWebhook", "")).strip()
    effective_state = state_from_wa or state_instance

    profile_name = ""
    contact_name = ""
    email = ""
    category = ""
    description = ""
    is_business = False

    candidate_contact_ids: list[str] = []
    if phone:
        candidate_contact_ids.append(f"{phone}@c.us")
    if chat_id and chat_id not in candidate_contact_ids:
        candidate_contact_ids.append(chat_id)

    for candidate_chat_id in candidate_contact_ids:
        contact_info, contact_error = safe_green_call(
            lambda: green_api_client.get_contact_info(candidate_chat_id),
            {},
        )
        if contact_error:
            continue

        profile_name = str(contact_info.get("name", "")).strip()
        contact_name = str(contact_info.get("contactName", "")).strip()
        email = str(contact_info.get("email", "")).strip()
        category = str(contact_info.get("category", "")).strip()
        description = str(contact_info.get("description", "")).strip()
        is_business = bool(contact_info.get("isBusiness", False))

        if not avatar:
            avatar = str(contact_info.get("avatar", "")).strip()
        if not base64_avatar:
            base64_avatar = str(contact_info.get("base64Avatar", "")).strip()

        if profile_name or contact_name or email or category or description or avatar or base64_avatar:
            break

    if qr_type == "alreadyLogged":
        connection_status = "connected"
    else:
        connection_status = "connected" if effective_state == "authorized" else "disconnected"

    if webhook_url or incoming_webhook.lower() != "yes":
        connection_status = "disconnected"

    if not last_error:
        for soft_error in (state_error, status_error, wa_error):
            if not soft_error:
                continue
            if is_rate_limited_error(GreenApiServiceError(soft_error)):
                qr_message = qr_message or "Green API временно ограничил частые запросы. Подождите несколько секунд."
                continue
            last_error = soft_error
            break

    return SimpleWhatsAppConnectionResponse(
        configured=True,
        connection_name=settings.simple_connect_name,
        connection_status=connection_status,
        state_instance=effective_state,
        status_instance=status_instance,
        phone=phone,
        chat_id=chat_id,
        device_id=device_id,
        avatar=avatar,
        base64_avatar=base64_avatar,
        profile_name=profile_name,
        contact_name=contact_name,
        email=email,
        category=category,
        description=description,
        is_business=is_business,
        polling_ready=polling_ready,
        qr_type=qr_type,
        qr_message=qr_message,
        qr_code_data_url=qr_code_data_url,
        last_error=last_error,
        logout_performed=logout_performed,
    )


@router.get("/connect/whatsapp", response_class=HTMLResponse)
def whatsapp_connect_page() -> str:
    """Render the simplified WhatsApp QR connection screen."""
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MINIGREENAPI | Подключение WhatsApp</title>
  <meta name="description" content="Простое подключение платформенного WhatsApp по QR с понятным статусом connected или disconnected." />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/brand.css" />
</head>
<body>
  <div class="page-shell onboarding-shell">
    <div class="ambient ambient-left"></div>
    <div class="ambient ambient-right"></div>
    <header class="topbar">
      <div class="brand-mark" aria-hidden="true">
        <span class="brand-block"></span>
        <span class="brand-arch"></span>
        <span class="brand-arch brand-arch-secondary"></span>
      </div>
      <div class="brand-copy">
        <span class="brand-label">MINIGREENAPI</span>
        <span class="brand-subtitle">Простое подключение WhatsApp</span>
      </div>
      <nav class="nav-links">
        <a href="/">Платформа</a>
        <a href="/docs">API Docs</a>
      </nav>
    </header>

    <main class="onboarding-main simple-connect-main">
      <section class="onboarding-intro reveal is-visible">
        <span class="eyebrow">Simple connect</span>
        <h1>Откройте QR, подключите WhatsApp и сразу увидьте статус.</h1>
        <p class="hero-text">
          Здесь нет настройки бота, GPT или внешнего проекта. Только один платформенный WhatsApp:
          QR-код, статус подключения, данные аккаунта и безопасное переподключение.
        </p>
      </section>

      <section class="simple-connect-grid">
        <div class="wizard-card reveal is-visible">
          <div class="simple-status-row">
            <div>
              <div class="card-label card-label-no-margin">Статус подключения</div>
              <h2 class="simple-connection-name" id="connection-name">Platform WhatsApp</h2>
            </div>
            <span class="status-badge status-badge-pending" id="connection-badge">Проверяем...</span>
          </div>

          <div class="status-grid">
            <div class="status-tile">
              <span class="status-kicker">Состояние instance</span>
              <strong id="instance-state">Неизвестно</strong>
            </div>
            <div class="status-tile">
              <span class="status-kicker">Socket status</span>
              <strong id="instance-status">Неизвестно</strong>
            </div>
          </div>

          <div class="qr-panel">
            <div class="qr-frame">
              <img id="qr-image" alt="QR-код WhatsApp" hidden />
              <div id="qr-placeholder" class="qr-placeholder">
                QR-код загружается...
              </div>
            </div>
            <div class="qr-meta">
              <span class="pill" id="polling-pill">Проверяем polling</span>
              <span class="pill" id="qr-pill">Проверяем QR</span>
            </div>
            <p class="contract-note" id="qr-message">
              Ждем ответ Green API.
            </p>
          </div>

          <div class="simple-actions">
            <button class="button button-secondary" id="refresh-btn" type="button">Обновить сейчас</button>
            <button class="button button-primary" id="reset-btn" type="button">Получить новый QR</button>
          </div>
        </div>

        <aside class="wizard-card wizard-sidebar reveal is-visible">
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

          <pre class="result-console" id="result-console">Готово.</pre>
        </aside>
      </section>
    </main>
  </div>
  <script src="/static/onboarding.js"></script>
</body>
</html>"""


@api_router.get("/status", response_model=SimpleWhatsAppConnectionResponse)
def get_simple_whatsapp_status(include_qr: bool = True) -> SimpleWhatsAppConnectionResponse:
    """Return the current status for the platform-owned WhatsApp account."""
    try:
        return collect_simple_connection_snapshot(include_qr=include_qr, reset_session=False)
    except GreenApiServiceError as exc:
        return build_error_response(str(exc))


@api_router.post("/reset", response_model=SimpleWhatsAppConnectionResponse)
def reset_simple_whatsapp_connection() -> SimpleWhatsAppConnectionResponse:
    """Log out the platform-owned device and request a fresh QR code."""
    try:
        return collect_simple_connection_snapshot(include_qr=True, reset_session=True)
    except GreenApiServiceError as exc:
        return build_error_response(str(exc))
