from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.models.schemas import (
    BotCreateRequest,
    BotDetailResponse,
    BotSummaryResponse,
    BotTestConnectionResponse,
)
from app.services.bot_registry import BotRecord, get_bot_registry_service
from app.services.platform_bot_runtime import get_platform_bot_runtime_service
from app.services.tenant import request_user, user_channel_key
from app.utils.config import get_settings

router = APIRouter(include_in_schema=False)
api_router = APIRouter(tags=["bot-console"])


def mask_authorization_header(value: str) -> str:
    """Avoid exposing full bot secrets in the browser/API."""
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 16:
        return "*" * len(cleaned)
    return f"{cleaned[:10]}...{cleaned[-6:]}"


def serialize_bot_summary(record: BotRecord, *, channel_key: str = "") -> BotSummaryResponse:
    """Project one stored bot into the compact catalog card shape."""
    settings = get_settings()
    resolved_channel = channel_key or settings.runtime_platform_channel_key
    return BotSummaryResponse(
        id=record.id,
        name=record.name,
        slug=record.slug,
        description=record.description,
        engine_type=record.engine_type if record.engine_type in {"dify", "n8n", "webhook", "custom"} else "custom",
        endpoint_url=record.endpoint_url,
        owner_label=record.owner_label,
        linked_project_id=record.linked_project_id,
        linked_channel_key=record.linked_channel_key,
        enabled=record.enabled,
        is_default_template=record.is_default_template,
        test_connected=resolved_channel in record.connected_channel_keys,
        connected_channel_keys=record.connected_channel_keys,
        variable_count=len(record.variables),
        api_binding_count=len(record.api_bindings),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def build_platform_instructions(record: BotRecord, *, channel_key: str = "") -> list[str]:
    """Explain how this bot should be wired into the platform and external tools."""
    settings = get_settings()
    resolved_channel = channel_key or settings.runtime_platform_channel_key
    engine_label = {
        "dify": "Dify",
        "n8n": "n8n",
        "webhook": "Webhook",
        "custom": "Custom bot",
    }.get(record.engine_type, "Custom bot")

    instructions = [
        f"Создайте или выберите проект в платформе и укажите endpoint вашего {engine_label}-бота как provider URL, если хотите использовать проектную схему.",
        (
            "Для обратной отправки сообщений платформа использует "
            f"{settings.platform_public_base_url}/api/v1/platform/chats/{{chat_id}}/send "
            "и отправляет их через WhatsApp Web JS runtime."
        ),
        (
            f"Если нужен Dify-бот без отдельного backend, подключите его прямо к {resolved_channel}. "
            "Тогда платформа сама будет отправлять query в Dify и возвращать answer в WhatsApp."
        ),
        (
            "Входящий webhook от платформы содержит project, channel, conversation и message. "
            "Для Dify достаточно поля query, а для n8n и custom-ботов можно использовать весь payload."
        ),
    ]

    if record.engine_type == "dify":
        instructions.append(
            "Для Dify рекомендуемый сценарий такой: Start -> Knowledge -> LLM -> Answer. "
            "Если нужны дополнительные действия или handoff менеджеру, их лучше делать через n8n webhook."
        )
    elif record.engine_type == "n8n":
        instructions.append(
            "Для n8n удобно принять webhook, обогатить данные, вызвать внешний проект или AI и отправить итоговый ответ обратно через API платформы."
        )
    else:
        instructions.append(
            "Для кастомного бота достаточно соблюдать inbound webhook contract и outbound send contract платформы."
        )

    if resolved_channel in record.connected_channel_keys:
        instructions.insert(
            0,
            f"Этот бот подключён к каналу {resolved_channel} и будет отвечать на новые входящие сообщения автоматически.",
        )

    return instructions


def build_env_example(record: BotRecord, *, channel_key: str = "") -> dict[str, str]:
    """Prepare one example env bundle for the selected bot."""
    settings = get_settings()
    env_example = {item.key: item.default_value for item in record.variables}
    env_example.setdefault("PLATFORM_BASE_URL", settings.platform_public_base_url)
    env_example.setdefault("PLATFORM_CHANNEL_KEY", channel_key or record.linked_channel_key or settings.runtime_platform_channel_key)
    return env_example


def build_inbound_example() -> dict[str, object]:
    """Show the webhook payload shape that bots receive from the platform."""
    return {
        "event": "whatsapp.message.received",
        "project": {
            "id": "proj_xxx",
            "slug": "support-bot",
            "name": "Support Bot",
        },
        "channel": {
            "id": "wa_xxx",
            "name": "Main WA",
            "type": "whatsapp",
            "instanceId": "platform-main",
        },
        "conversation": {
            "chatId": "996500000000@c.us",
            "userId": "996500000000@c.us",
        },
        "message": {
            "id": "ABCD1234",
            "text": "Здравствуйте, где мой заказ?",
            "timestamp": 1770000000,
            "chatId": "996500000000@c.us",
            "sender": "996500000000@c.us",
            "senderName": "Aizada",
        },
    }


def build_outbound_example(record: BotRecord) -> dict[str, object]:
    """Show how a bot should send a reply back through the platform."""
    return {
        "method": "POST",
        "url": f"{get_settings().platform_public_base_url}/api/v1/platform/chats/996500000000@c.us/send",
        "headers": {
            "Content-Type": "application/json",
        },
        "json": {
            "text": "Здравствуйте! Проверяю ваш заказ.",
        },
    }


def serialize_bot_detail(record: BotRecord, *, channel_key: str = "") -> BotDetailResponse:
    """Return the full configuration plus platform-side setup guidance."""
    summary = serialize_bot_summary(record, channel_key=channel_key)
    return BotDetailResponse(
        **summary.model_dump(),
        authorization_header=mask_authorization_header(record.authorization_header),
        workflow_summary=record.workflow_summary,
        variables=record.variables,
        api_bindings=record.api_bindings,
        platform_instructions=build_platform_instructions(record, channel_key=channel_key),
        env_example=build_env_example(record, channel_key=channel_key),
        inbound_example=build_inbound_example(),
        outbound_example=build_outbound_example(record),
    )


@router.get("/bots", response_class=HTMLResponse)
def bot_console_page() -> str:
    """Render the built-in bot registry and setup assistant."""
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Connector | Боты платформы</title>
  <meta name="description" content="Каталог дефолтных и кастомных ботов платформы: Dify, n8n и webhook-интеграции с инструкциями по переменным." />
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
        <a class="is-active" href="/bots">Боты</a>
        <a href="/connect/whatsapp">WhatsApp</a>
      </nav>
    </header>

    <main class="onboarding-main bot-studio-main">
      <section class="onboarding-intro bot-simple-intro reveal is-visible">
        <span class="eyebrow">Боты WhatsApp</span>
        <h1>Подключите проект к WhatsApp</h1>
        <p class="hero-text">
          Укажите проект, URL и путь webhook. Платформа будет получать сообщения из WhatsApp и отправлять их в этот проект.
        </p>
      </section>

      <section class="bot-studio-grid bot-studio-grid-simple">
        <section class="wizard-card bot-builder-card reveal is-visible">
          <div class="card-label">Интеграция проекта</div>
          <h2 class="simple-connection-name bot-section-title">Новый проект</h2>

          <form class="stack-form bot-simple-form" id="bot-create-form">
            <label>
              <span>Название интеграции</span>
              <input id="bot-name-input" name="name" placeholder="Например: Мой проект" required />
            </label>

            <label>
              <span>Название или ID проекта</span>
              <input id="bot-project-input" name="linked_project_id" placeholder="Например: project-main" />
            </label>

            <label>
              <span>Тип подключения</span>
              <select id="bot-engine-input" name="engine_type">
                <option value="webhook">Интеграция проекта</option>
                <option value="n8n">n8n webhook</option>
                <option value="dify">Dify</option>
              </select>
            </label>

            <div class="bot-type-fields" id="project-integration-fields">
              <label>
                <span>Базовый URL проекта</span>
                <input
                  id="project-base-url-input"
                  name="project_base_url"
                  type="url"
                  placeholder="https://project.example.com"
                />
              </label>

              <label>
                <span>Путь для WhatsApp-сообщений</span>
                <input id="project-path-input" name="project_path" placeholder="/api/whatsapp/incoming" value="/api/whatsapp/incoming" />
              </label>
            </div>

            <div class="bot-type-fields" id="dify-fields" hidden>
              <label>
                <span>Dify API URL</span>
                <input id="dify-base-url-input" name="dify_base_url" type="url" placeholder="https://api.dify.ai/v1" value="https://api.dify.ai/v1" />
              </label>

              <label>
                <span>Dify API Key</span>
                <input id="dify-api-key-input" name="dify_api_key" placeholder="app-..." />
              </label>
            </div>

            <label>
              <span>Токен или Authorization header</span>
              <input id="bot-auth-input" name="authorization_header" placeholder="Можно оставить пустым" />
            </label>

            <div class="bot-contract-note">
              <strong>Куда будут уходить сообщения</strong>
              <code id="project-full-url-preview">https://project.example.com/api/whatsapp/incoming</code>
              <strong>Что должен вернуть проект</strong>
              <code>{"answer":"Текст ответа клиенту"}</code>
            </div>

            <div class="wizard-actions">
              <button class="button button-primary" type="submit">Сохранить и подключить</button>
              <button class="button button-secondary" type="reset">Очистить</button>
            </div>
          </form>
        </section>

        <section class="wizard-card bot-detail-card reveal is-visible">
          <div class="simple-status-row bot-detail-head">
            <div>
              <div class="card-label card-label-no-margin">Активный бот</div>
              <h2 class="simple-connection-name bot-detail-title" id="bot-detail-name">Выберите бота</h2>
            </div>
            <span class="status-badge status-badge-pending" id="bot-detail-badge">Ожидание</span>
          </div>

          <div class="status-grid bot-summary-grid bot-summary-grid-simple">
            <div class="status-tile">
              <span class="status-kicker">Тип</span>
              <strong id="bot-engine-value">-</strong>
            </div>
            <div class="status-tile">
              <span class="status-kicker">Проект</span>
              <strong id="bot-project-value">-</strong>
            </div>
            <div class="status-tile">
              <span class="status-kicker">Канал</span>
              <strong id="bot-channel-value">platform-main</strong>
            </div>
          </div>

          <div class="status-tile bot-endpoint-tile">
            <span class="status-kicker">URL</span>
            <strong id="bot-endpoint-value">-</strong>
          </div>

          <div class="simple-actions bot-live-actions">
            <button class="button button-primary" id="connect-test-bot-btn" type="button" disabled>Подключить к WhatsApp</button>
            <button class="button button-secondary" id="disconnect-test-bot-btn" type="button" disabled>Отключить</button>
            <button class="button button-secondary" id="delete-bot-btn" type="button" disabled>Удалить</button>
          </div>

          <div class="bot-description-card" id="bot-description-card">
            Выберите бота из списка или добавьте нового. Активный бот будет отвечать на входящие сообщения WhatsApp.
          </div>
        </section>
      </section>

      <section class="wizard-card bot-catalog-card bot-catalog-card-simple reveal is-visible">
        <div class="bot-panel-head">
          <div>
            <div class="card-label card-label-no-margin">Боты</div>
            <h2 class="simple-connection-name bot-section-title">Список подключений</h2>
          </div>
          <button class="button button-secondary" id="bot-refresh-btn" type="button">Обновить</button>
        </div>

        <div class="bot-catalog-list" id="bot-list">
          <div class="empty-state-card">Пока нет ботов. Добавьте своего бота выше или проверьте настройки дефолтного бота.</div>
        </div>
      </section>
    </main>
  </div>
  <script src="/static/bot-studio.js?v=bots-20260520-project-only"></script>
</body>
</html>"""


@api_router.get("/api/v1/platform/bots", response_model=list[BotSummaryResponse])
def list_platform_bots(request: Request) -> list[BotSummaryResponse]:
    """List default and custom bots available inside the platform."""
    user = request_user(request)
    channel_key = user_channel_key(user)
    return [
        serialize_bot_summary(record, channel_key=channel_key)
        for record in get_bot_registry_service().list_bots(owner_user_id=user.id, channel_key=channel_key)
    ]


@api_router.get("/api/v1/platform/bots/{bot_id}", response_model=BotDetailResponse)
def get_platform_bot(request: Request, bot_id: str) -> BotDetailResponse:
    """Return one bot definition with all platform-side instructions."""
    user = request_user(request)
    channel_key = user_channel_key(user)
    record = get_bot_registry_service().get_bot(bot_id, owner_user_id=user.id, channel_key=channel_key)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found.")
    return serialize_bot_detail(record, channel_key=channel_key)


@api_router.get("/api/v1/platform/bots/{bot_id}/diagnostics")
def diagnose_platform_bot(request: Request, bot_id: str) -> dict[str, object]:
    """Return the resolved runtime route for one bot."""
    user = request_user(request)
    channel_key = user_channel_key(user)
    record = get_bot_registry_service().get_bot(bot_id, owner_user_id=user.id, channel_key=channel_key)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found.")
    diagnostics = get_platform_bot_runtime_service().diagnose_bot_route(
        record,
        channel_key=channel_key,
    )
    return diagnostics


@api_router.post("/api/v1/platform/bots", response_model=BotDetailResponse, status_code=status.HTTP_201_CREATED)
def create_platform_bot(request: Request, payload: BotCreateRequest) -> BotDetailResponse:
    """Create one custom bot integration in the platform catalog."""
    user = request_user(request)
    channel_key = user_channel_key(user)
    try:
        record = get_bot_registry_service().create_bot(payload, owner_user_id=user.id, channel_key=channel_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return serialize_bot_detail(record, channel_key=channel_key)


@api_router.post("/api/v1/platform/bots/default", response_model=BotDetailResponse, status_code=status.HTTP_201_CREATED)
def create_default_platform_bot(request: Request) -> BotDetailResponse:
    """Seed the optional default Dify bot template for the platform workspace."""
    channel_key = user_channel_key(request_user(request))
    record = get_bot_registry_service().sync_default_bot_from_settings()
    return serialize_bot_detail(record, channel_key=channel_key)


@api_router.post(
    "/api/v1/platform/bots/{bot_id}/connect",
    response_model=BotTestConnectionResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/api/v1/platform/bots/{bot_id}/connect-test",
    response_model=BotTestConnectionResponse,
    status_code=status.HTTP_200_OK,
)
def connect_platform_bot(request: Request, bot_id: str) -> BotTestConnectionResponse:
    """Attach one enabled bot as the active bot for the platform-owned WhatsApp channel."""
    user = request_user(request)
    channel_key = user_channel_key(user)
    try:
        record = get_bot_registry_service().connect_bot_to_channel(bot_id, channel_key, owner_user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    diagnostics = get_platform_bot_runtime_service().diagnose_bot_route(
        record,
        channel_key=channel_key,
    )
    return BotTestConnectionResponse(
        ok=True,
        bot_id=bot_id,
        channel_key=channel_key,
        enabled=True,
        bot_ready=bool(diagnostics.get("ok")),
        diagnostics=diagnostics,
    )


@api_router.post(
    "/api/v1/platform/bots/{bot_id}/disconnect",
    response_model=BotTestConnectionResponse,
    status_code=status.HTTP_200_OK,
)
@api_router.post(
    "/api/v1/platform/bots/{bot_id}/disconnect-test",
    response_model=BotTestConnectionResponse,
    status_code=status.HTTP_200_OK,
)
def disconnect_platform_bot(request: Request, bot_id: str) -> BotTestConnectionResponse:
    """Detach one bot from the active runtime slot for the platform WhatsApp channel."""
    user = request_user(request)
    channel_key = user_channel_key(user)
    try:
        get_bot_registry_service().disconnect_bot_from_channel(bot_id, channel_key, owner_user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return BotTestConnectionResponse(
        ok=True,
        bot_id=bot_id,
        channel_key=channel_key,
        enabled=False,
        bot_ready=False,
        diagnostics={},
    )


@api_router.delete("/api/v1/platform/bots/{bot_id}", status_code=status.HTTP_200_OK)
def delete_platform_bot(request: Request, bot_id: str) -> dict[str, object]:
    """Delete one custom bot integration from the platform catalog."""
    user = request_user(request)
    channel_key = user_channel_key(user)
    try:
        record = get_bot_registry_service().delete_bot(bot_id, owner_user_id=user.id, channel_key=channel_key)
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_403_FORBIDDEN if "Default bot" in detail else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return {
        "ok": True,
        "bot_id": record.id,
        "slug": record.slug,
        "deleted": True,
    }


@api_router.post("/api/v1/platform/bots/{bot_id}/activate", status_code=status.HTTP_200_OK)
def activate_platform_bot(request: Request, bot_id: str) -> dict[str, object]:
    """Enable one registered bot in the catalog."""
    user = request_user(request)
    channel_key = user_channel_key(user)
    try:
        record = get_bot_registry_service().set_bot_enabled(bot_id, True, owner_user_id=user.id, channel_key=channel_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    diagnostics = get_platform_bot_runtime_service().diagnose_bot_route(
        record,
        channel_key=channel_key,
    )
    return {
        "ok": True,
        "bot_id": record.id,
        "enabled": record.enabled,
        "bot_ready": bool(diagnostics.get("ok")),
        "diagnostics": diagnostics,
    }


@api_router.post("/api/v1/platform/bots/{bot_id}/deactivate", status_code=status.HTTP_200_OK)
def deactivate_platform_bot(request: Request, bot_id: str) -> dict[str, object]:
    """Disable one registered bot in the catalog and disconnect it from active channels."""
    user = request_user(request)
    channel_key = user_channel_key(user)
    try:
        record = get_bot_registry_service().set_bot_enabled(bot_id, False, owner_user_id=user.id, channel_key=channel_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"ok": True, "bot_id": record.id, "enabled": record.enabled}
