from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import HTMLResponse

from app.models.schemas import (
    PlatformChatMessageResponse,
    PlatformChatSendRequest,
    PlatformChatSendResponse,
    PlatformConversationResponse,
    RuntimeIncomingMessageRequest,
)
from app.services.platform_bot_runtime import (
    PlatformBotRuntimeError,
    epoch_ms,
    get_platform_bot_runtime_service,
    message_sent_epoch_ms,
)
from app.services.chat_store import (
    ChatConversationRecord,
    ChatMessageRecord,
    get_chat_store_service,
    is_personal_whatsapp_chat_id,
)
from app.services.chat_presence import get_chat_presence_service
from app.services.self_hosted_runtime_service import SelfHostedRuntimeServiceError, get_self_hosted_runtime_service
from app.utils.config import get_settings

router = APIRouter(include_in_schema=False)
api_router = APIRouter(tags=["chat-console"])
logger = logging.getLogger(__name__)
PROFILE_HYDRATION_TTL_SECONDS = 180.0
PROFILE_HYDRATION_BATCH_SIZE = 30
APPLICATION_STARTED_AT_MS = int(time.time() * 1000)
_profile_hydration_attempts: dict[tuple[str, str], float] = {}


def serialize_conversation(record: ChatConversationRecord) -> PlatformConversationResponse:
    """Translate one stored conversation into the API response shape."""
    presence = get_chat_presence_service().get_presence(record.channel_key, record.chat_id)
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
        needs_admin_reply=record.needs_admin_reply,
        presence_status=presence.status if presence.status in {"offline", "online", "typing"} else "offline",
        presence_label=presence.label,
        presence_expires_at=presence.expires_at,
    )


def serialize_message(record: ChatMessageRecord) -> PlatformChatMessageResponse:
    """Translate one stored chat message into the API response shape."""
    raw_message = record.raw_payload.get("message", {}) if isinstance(record.raw_payload.get("message"), dict) else {}
    media = raw_message.get("media", {}) if isinstance(raw_message.get("media"), dict) else {}
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
        media_url=str(media.get("url", "")).strip(),
        media_mime_type=str(media.get("mimetype", "")).strip(),
        media_filename=str(media.get("filename", "")).strip(),
        media_caption=str(media.get("caption", "")).strip(),
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

    if not is_personal_whatsapp_chat_id(chat_id):
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


def mark_pre_activation_message_ineligible(runtime_payload: dict[str, object]) -> str:
    """Keep old WhatsApp messages visible in the inbox without letting the bot answer them."""
    message = runtime_payload.get("message", {})
    if not isinstance(message, dict):
        return ""

    sent_at_ms = message_sent_epoch_ms(message)
    runtime_activated_at_ms = epoch_ms(message.get("runtime_activated_at"))
    activation_threshold_ms = max(APPLICATION_STARTED_AT_MS, runtime_activated_at_ms)
    event_source = str(message.get("event_source") or "").strip()
    reason = ""

    if event_source == "history_sync" and not sent_at_ms:
        reason = "missing_message_timestamp"
    elif sent_at_ms and sent_at_ms < activation_threshold_ms:
        reason = "before_runtime_activation"

    if not reason:
        return ""

    message["bot_eligible"] = False
    message["bot_skip_reason"] = reason
    return reason


def is_placeholder_contact_name(display_name: str, chat_id: str) -> bool:
    """Return True when the inbox can safely replace a weak stored name."""
    cleaned = display_name.strip()
    return not cleaned or cleaned == "." or cleaned == chat_id or cleaned == chat_id.split("@", 1)[0]


def hydrate_conversation_profiles(channel_key: str, conversations: list[ChatConversationRecord]) -> None:
    """Best-effort enrichment of old inbox rows with WhatsApp names and avatars."""
    if not conversations:
        return

    now = time.monotonic()
    chat_ids: list[str] = []
    for conversation in conversations:
        display_name = conversation.display_name.strip()
        suspected_sender_profile = (
            conversation.last_direction == "outbound"
            and display_name
            and display_name == conversation.last_sender_name.strip()
        )
        should_hydrate = (
            not conversation.avatar_url.strip()
            or is_placeholder_contact_name(display_name, conversation.chat_id)
            or suspected_sender_profile
        )
        if not should_hydrate:
            continue

        cache_key = (channel_key, conversation.chat_id)
        last_attempt = _profile_hydration_attempts.get(cache_key, 0.0)
        if now - last_attempt < PROFILE_HYDRATION_TTL_SECONDS:
            continue

        _profile_hydration_attempts[cache_key] = now
        chat_ids.append(conversation.chat_id)
        if len(chat_ids) >= PROFILE_HYDRATION_BATCH_SIZE:
            break

    if not chat_ids:
        return

    try:
        payload = get_self_hosted_runtime_service().resolve_contact_profiles(channel_key, chat_ids)
    except SelfHostedRuntimeServiceError as exc:
        logger.debug("Could not hydrate WhatsApp contact profiles: %s", exc)
        return

    profiles = payload.get("profiles", [])
    if not isinstance(profiles, list):
        return

    by_chat_id = {conversation.chat_id: conversation for conversation in conversations}
    chat_store = get_chat_store_service()
    for profile in profiles:
        if not isinstance(profile, dict):
            continue

        chat_id = str(profile.get("chat_id", "")).strip()
        conversation = by_chat_id.get(chat_id)
        if conversation is None:
            continue

        profile_name = str(profile.get("display_name", "")).strip()
        profile_phone = str(profile.get("phone", "")).strip()
        profile_avatar_url = str(profile.get("avatar_url", "")).strip()
        next_display_name = conversation.display_name
        suspected_sender_profile = (
            conversation.last_direction == "outbound"
            and conversation.display_name.strip()
            and conversation.display_name.strip() == conversation.last_sender_name.strip()
        )
        if profile_name and (
            is_placeholder_contact_name(conversation.display_name, conversation.chat_id)
            or suspected_sender_profile
        ):
            next_display_name = profile_name

        next_phone = profile_phone or conversation.phone
        next_avatar_url = (
            profile_avatar_url
            if suspected_sender_profile
            else profile_avatar_url or conversation.avatar_url
        )
        if (
            next_display_name == conversation.display_name
            and next_phone == conversation.phone
            and next_avatar_url == conversation.avatar_url
        ):
            continue

        chat_store.update_conversation_profile(
            channel_key=channel_key,
            chat_id=conversation.chat_id,
            display_name=next_display_name,
            phone=next_phone,
            avatar_url=next_avatar_url,
            replace_avatar_url=suspected_sender_profile,
        )
        conversation.display_name = next_display_name
        conversation.phone = next_phone
        conversation.avatar_url = next_avatar_url


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
  <link rel="stylesheet" href="/static/brand.css?v=ai-connector-20260519d" />
</head>
<body>
  <div class="inbox-app-shell">
    <header class="inbox-app-topbar">
      <a class="brand-home" href="/" aria-label="AI Connector">
        <img class="brand-logo-image" src="/static/image.png?v=logo-20260520" alt="AI Connector" />
      </a>
      <nav class="nav-links">
        <a href="/">Платформа</a>
        <a class="is-active" href="/chats">Чаты</a>
        <a href="/bots">Боты</a>
        <a href="/connect/whatsapp">WhatsApp</a>
      </nav>
    </header>

    <main class="inbox-workspace">
      <aside class="inbox-sidebar">
        <div class="inbox-sidebar-head">
          <div>
            <div class="inbox-title-row">
              <h1>Conversations</h1>
              <span class="inbox-count-pill" id="conversation-count-pill">0</span>
            </div>
            <p>Личные WhatsApp-чаты</p>
          </div>
          <button class="inbox-icon-button" id="conversation-refresh-btn" type="button" title="Обновить">↻</button>
        </div>

        <div class="inbox-filter-row" aria-label="Фильтры чатов">
          <button class="inbox-filter-tab is-active" type="button">Mine</button>
          <button class="inbox-filter-tab" type="button">All</button>
          <span class="inbox-filter-spacer"></span>
          <span class="inbox-filter-note">Groups hidden</span>
        </div>

        <div class="inbox-conversation-list" id="conversation-list">
          <div class="empty-state-card">Пока нет личных сообщений. Как только в WhatsApp придет новый личный чат, он появится здесь.</div>
        </div>
      </aside>

      <section class="inbox-chat-panel">
        <header class="inbox-chat-head">
          <div class="inbox-active-contact">
            <div class="inbox-avatar inbox-avatar-large" id="active-chat-avatar"><span>?</span></div>
            <div>
              <h2 class="inbox-chat-name" id="active-chat-name">Выберите диалог</h2>
              <p id="active-chat-meta">Откройте чат слева, чтобы видеть историю сообщений и отвечать вручную.</p>
            </div>
          </div>
          <div class="inbox-chat-badges">
            <span class="pill" id="chat-channel-pill">platform-main</span>
            <span class="pill" id="chat-status-pill">Нет активного чата</span>
          </div>
        </header>

        <div class="inbox-chat-tabs">
          <button class="inbox-chat-tab is-active" type="button">Messages</button>
          <button class="inbox-chat-tab" type="button">Customer Dashboard</button>
        </div>

        <div class="inbox-message-stream" id="message-stream">
          <div class="empty-state-card">
            История чата появится здесь после выбора диалога.
          </div>
        </div>

        <div class="inbox-composer">
          <div class="inbox-composer-tabs">
            <button class="inbox-composer-tab is-active" type="button">Reply</button>
            <button class="inbox-composer-tab" type="button">Private Note</button>
          </div>
          <textarea id="manual-reply-input" rows="4" placeholder="Введите сообщение для клиента..."></textarea>
          <div class="inbox-composer-actions">
            <span>Ctrl + Enter для отправки</span>
            <div>
              <button class="button button-secondary" id="message-refresh-btn" type="button">Обновить</button>
              <button class="button button-primary" id="send-reply-btn" type="button">Отправить</button>
            </div>
          </div>
        </div>

        <pre class="inbox-console" id="chat-console">Чат-монитор готов.</pre>
      </section>
    </main>
  </div>
  <script src="/static/chat-monitor.js?v=chat-20260520-admin"></script>
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

    runtime_payload = payload.model_dump()
    replay_skip_reason = mark_pre_activation_message_ineligible(runtime_payload)
    raw_message = runtime_payload.get("message", {}) if isinstance(runtime_payload.get("message"), dict) else {}
    chat_store = get_chat_store_service()
    external_message_id = str(raw_message.get("external_message_id", "")).strip()
    existing_message = chat_store.get_message_by_external_id(payload.channel_key, external_message_id)
    message = chat_store.store_incoming_message(payload.channel_key, runtime_payload)
    if existing_message is not None:
        return {
            "ok": True,
            "record_id": message.record_id,
            "skipped": True,
            "reason": "duplicate_message",
        }

    bot_result: dict[str, object] = {"handled": False, "reason": "not_processed"}
    if replay_skip_reason:
        bot_result = {"handled": False, "reason": replay_skip_reason}
    else:
        try:
            bot_result = get_platform_bot_runtime_service().process_runtime_incoming_message(
                payload.channel_key,
                runtime_payload,
            )
        except PlatformBotRuntimeError as exc:
            logger.warning("Platform bot processing failed: %s", exc)
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
    hydrate_conversation_profiles(resolved_channel, conversations)
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
    get_chat_store_service().mark_admin_handoff(resolved_channel, chat_id, False)
    return PlatformChatSendResponse(
        channel_key=resolved_channel,
        chat_id=chat_id,
        id_message=external_message_id,
        message=serialize_message(message),
    )
