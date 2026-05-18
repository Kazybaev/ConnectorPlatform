from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import HTMLResponse

from app.models.schemas import (
    PlatformChatMessageResponse,
    PlatformChatSendRequest,
    PlatformChatSendResponse,
    PlatformConversationResponse,
    RuntimeIncomingMessageRequest,
)
from app.services.platform_bot_runtime import PlatformBotRuntimeError, get_platform_bot_runtime_service
from app.services.chat_store import ChatConversationRecord, ChatMessageRecord, get_chat_store_service
from app.services.self_hosted_runtime_service import SelfHostedRuntimeServiceError, get_self_hosted_runtime_service
from app.utils.config import get_settings

router = APIRouter(include_in_schema=False)
api_router = APIRouter(tags=["chat-console"])
logger = logging.getLogger(__name__)


def serialize_conversation(record: ChatConversationRecord) -> PlatformConversationResponse:
    """Translate one stored conversation into the API response shape."""
    return PlatformConversationResponse(
        channel_key=record.channel_key,
        chat_id=record.chat_id,
        display_name=record.display_name,
        phone=record.phone,
        avatar_url=record.avatar_url,
        last_message_text=record.last_message_text,
        last_message_at=record.last_message_at,
        last_direction=record.last_direction if record.last_direction in {"inbound", "outbound"} else "inbound",
        last_sender_name=record.last_sender_name,
        unread_count=record.unread_count,
    )


def serialize_message(record: ChatMessageRecord) -> PlatformChatMessageResponse:
    """Translate one stored chat message into the API response shape."""
    return PlatformChatMessageResponse(
        record_id=record.record_id,
        channel_key=record.channel_key,
        chat_id=record.chat_id,
        external_message_id=record.external_message_id,
        direction=record.direction if record.direction in {"inbound", "outbound"} else "inbound",
        sender_id=record.sender_id,
        sender_name=record.sender_name,
        text=record.text,
        message_type=record.message_type,
        source=record.source,
        status=record.status,
        created_at=record.created_at,
    )


def ensure_runtime_callback_authorized(x_runtime_callback_token: str | None) -> None:
    """Protect runtime callback routes when a callback token is configured."""
    expected = get_settings().runtime_callback_token
    if not expected:
        return

    if x_runtime_callback_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Runtime-Callback-Token.",
        )


def should_store_personal_runtime_message(payload: RuntimeIncomingMessageRequest) -> bool:
    """Accept only direct WhatsApp chats and ignore groups/broadcasts."""
    message = payload.message if isinstance(payload.message, dict) else {}
    chat_id = str(message.get("chat_id", "")).strip().lower()
    if not chat_id:
        return False

    if bool(message.get("self_chat", False)):
        return True

    if bool(message.get("is_group", False)) or chat_id.endswith("@g.us"):
        return False

    if bool(message.get("is_broadcast", False)) or chat_id == "status@broadcast" or chat_id.endswith("@broadcast"):
        return False

    if bool(message.get("is_newsletter", False)) or chat_id.endswith("@newsletter"):
        return False

    return True


@router.get("/chats", response_class=HTMLResponse)
def chat_console_page() -> str:
    """Render the platform inbox UI for monitoring and replying to chats."""
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Connector | Чаты платформы</title>
  <meta name="description" content="Мониторинг WhatsApp-чатов и ручные ответы из самой платформы." />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/brand.css?v=community-20260515d" />
</head>
<body>
  <div class="page-shell inbox-shell">
    <div class="ambient ambient-left"></div>
    <div class="ambient ambient-right"></div>
    <header class="topbar connect-topbar">
      <a class="brand-home" href="/" aria-label="COMMUNITY">
        <img class="brand-logo-image" src="/static/community-mark-clean.svg?v=community-20260515d" alt="COMMUNITY mark" />
      </a>
      <nav class="nav-links">
        <a href="/">Платформа</a>
        <a href="/bots">Боты</a>
        <a href="/connect/whatsapp">Подключить WhatsApp</a>
      </nav>
    </header>

    <main class="onboarding-main inbox-main">
      <section class="onboarding-intro reveal is-visible">
        <span class="eyebrow">Chat monitoring</span>
        <h1>Чаты платформы и ручные ответы</h1>
        <p class="hero-text">
          Здесь можно видеть входящие сообщения, следить за новыми диалогами и отвечать клиентам прямо из платформы,
          даже если поверх этой же линии работает отдельный бот или интеграция.
        </p>
      </section>

      <section class="inbox-grid">
        <aside class="wizard-card inbox-sidebar reveal is-visible">
          <div class="inbox-sidebar-head">
            <div>
              <div class="card-label card-label-no-margin">Диалоги</div>
              <h2 class="simple-connection-name inbox-title">Список чатов</h2>
            </div>
            <button class="button button-secondary inbox-refresh-button" id="conversation-refresh-btn" type="button">
              Обновить
            </button>
          </div>

          <div class="inbox-conversation-list" id="conversation-list">
            <div class="empty-state-card">Пока нет сообщений. Как только в WhatsApp придет новый чат, он появится здесь.</div>
          </div>
        </aside>

        <section class="wizard-card inbox-chat-panel reveal is-visible">
          <div class="inbox-chat-head">
            <div>
              <div class="card-label card-label-no-margin">Открытый чат</div>
              <h2 class="simple-connection-name inbox-chat-name" id="active-chat-name">Выберите диалог</h2>
              <p class="section-copy section-copy-tight" id="active-chat-meta">
                Откройте чат слева, чтобы видеть историю сообщений и отвечать вручную.
              </p>
            </div>
            <div class="inbox-chat-badges">
              <span class="pill" id="chat-channel-pill">platform-main</span>
              <span class="pill" id="chat-status-pill">Нет активного чата</span>
            </div>
          </div>

          <div class="inbox-message-stream" id="message-stream">
            <div class="empty-state-card">
              История чата появится здесь после выбора диалога.
            </div>
          </div>

          <div class="inbox-composer">
            <label class="inbox-composer-label" for="manual-reply-input">Ответить из платформы</label>
            <textarea id="manual-reply-input" rows="4" placeholder="Введите сообщение для клиента..."></textarea>
            <div class="inbox-composer-actions">
              <button class="button button-secondary" id="message-refresh-btn" type="button">Обновить чат</button>
              <button class="button button-primary" id="send-reply-btn" type="button">Отправить сообщение</button>
            </div>
          </div>

          <div class="result-console-shell inbox-console-shell">
            <div class="status-kicker">Лог консоли</div>
            <pre class="result-console" id="chat-console">Чат-монитор готов.</pre>
          </div>
        </section>
      </section>
    </main>
  </div>
  <script src="/static/chat-monitor.js?v=chat-20260514a"></script>
</body>
</html>"""


@api_router.post("/api/v1/runtime/incoming")
def receive_runtime_incoming_message(
    payload: RuntimeIncomingMessageRequest,
    x_runtime_callback_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Persist one inbound WhatsApp event posted by the local runtime."""
    ensure_runtime_callback_authorized(x_runtime_callback_token)
    if not should_store_personal_runtime_message(payload):
        return {
            "ok": True,
            "skipped": True,
            "reason": "non_personal_chat",
        }

    chat_store = get_chat_store_service()
    raw_message = payload.message if isinstance(payload.message, dict) else {}
    external_message_id = str(raw_message.get("external_message_id", "")).strip()
    existing_message = chat_store.get_message_by_external_id(payload.channel_key, external_message_id)
    message = chat_store.store_incoming_message(payload.channel_key, payload.model_dump())
    if existing_message is not None:
        return {
            "ok": True,
            "record_id": message.record_id,
            "skipped": True,
            "reason": "duplicate_message",
        }

    bot_result: dict[str, object] = {"handled": False, "reason": "not_processed"}
    try:
        bot_result = get_platform_bot_runtime_service().process_runtime_incoming_message(
            payload.channel_key,
            payload.model_dump(),
        )
    except PlatformBotRuntimeError as exc:
        bot_result = {"handled": False, "reason": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected bot processing error for runtime message")
        bot_result = {"handled": False, "reason": f"bot_processing_error:{exc}"}
    return {
        "ok": True,
        "record_id": message.record_id,
        "bot_result": bot_result,
    }


@api_router.get("/api/v1/platform/chats/conversations", response_model=list[PlatformConversationResponse])
def list_platform_conversations(channel_key: str | None = None) -> list[PlatformConversationResponse]:
    """Return the latest chat conversations for the platform inbox."""
    resolved_channel = channel_key or get_settings().runtime_platform_channel_key
    conversations = get_chat_store_service().list_conversations(resolved_channel)
    return [serialize_conversation(conversation) for conversation in conversations]


@api_router.get("/api/v1/platform/chats/{chat_id}/messages", response_model=list[PlatformChatMessageResponse])
def list_platform_chat_messages(
    chat_id: str,
    channel_key: str | None = None,
    limit: int = 200,
) -> list[PlatformChatMessageResponse]:
    """Return one chat timeline from the platform inbox."""
    resolved_channel = channel_key or get_settings().runtime_platform_channel_key
    messages = get_chat_store_service().list_messages(resolved_channel, chat_id, limit=limit)
    return [serialize_message(message) for message in messages]


@api_router.post("/api/v1/platform/chats/{chat_id}/read")
def mark_platform_conversation_read(chat_id: str, channel_key: str | None = None) -> dict[str, object]:
    """Clear unread counters for one conversation."""
    resolved_channel = channel_key or get_settings().runtime_platform_channel_key
    get_chat_store_service().mark_conversation_read(resolved_channel, chat_id)
    return {"ok": True}


@api_router.post("/api/v1/platform/chats/{chat_id}/send", response_model=PlatformChatSendResponse)
def send_platform_chat_reply(
    chat_id: str,
    payload: PlatformChatSendRequest,
    channel_key: str | None = None,
) -> PlatformChatSendResponse:
    """Send one manual operator message through the platform runtime."""
    resolved_channel = channel_key or get_settings().runtime_platform_channel_key
    runtime_service = get_self_hosted_runtime_service()

    try:
        runtime_payload = runtime_service.send_message(resolved_channel, chat_id, payload.text)
    except SelfHostedRuntimeServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    external_message_id = str(runtime_payload.get("id_message", "")).strip()
    message = get_chat_store_service().store_outgoing_message(
        channel_key=resolved_channel,
        chat_id=chat_id,
        text=payload.text,
        external_message_id=external_message_id,
        source="operator",
        sender_name="Platform operator",
        status="sent",
        raw_payload=runtime_payload,
    )
    get_chat_store_service().mark_conversation_read(resolved_channel, chat_id)
    return PlatformChatSendResponse(
        channel_key=resolved_channel,
        chat_id=chat_id,
        id_message=external_message_id,
        message=serialize_message(message),
    )
