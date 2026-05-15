from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

from app.models.schemas import (
    BotCreateRequest,
    BotDetailResponse,
    BotSummaryResponse,
    BotTestConnectionResponse,
)
from app.services.bot_registry import BotRecord, get_bot_registry_service
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


def serialize_bot_summary(record: BotRecord) -> BotSummaryResponse:
    """Project one stored bot into the compact catalog card shape."""
    settings = get_settings()
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
        test_connected=settings.runtime_platform_channel_key in record.connected_channel_keys,
        connected_channel_keys=record.connected_channel_keys,
        variable_count=len(record.variables),
        api_binding_count=len(record.api_bindings),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def build_platform_instructions(record: BotRecord) -> list[str]:
    """Explain how this bot should be wired into the platform and external tools."""
    settings = get_settings()
    engine_label = {
        "dify": "Dify",
        "n8n": "n8n",
        "webhook": "Webhook",
        "custom": "Custom bot",
    }.get(record.engine_type, "Custom bot")

    instructions = [
        f"Создайте или выберите проект в платформе и укажите endpoint вашего {engine_label}-бота как provider URL, если хотите использовать проектную схему.",
        (
            "Для обратной отправки сообщений через проектный режим бот должен вызывать "
            f"{settings.platform_public_base_url}/api/v1/projects/{{project_id}}/messages/send "
            "с заголовком X-Project-Key."
        ),
        (
            "Если нужен только дефолтный тестовый бот без отдельного backend, можно подключить его прямо к platform-main. "
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
            "Если нужны CRM-действия или handoff менеджеру, их лучше делать через n8n webhook."
        )
    elif record.engine_type == "n8n":
        instructions.append(
            "Для n8n удобно принять webhook, обогатить данные, сходить в CRM/AI и отправить итоговый ответ обратно через API платформы."
        )
    else:
        instructions.append(
            "Для кастомного бота достаточно соблюдать inbound webhook contract и outbound send contract платформы."
        )

    if settings.runtime_platform_channel_key in record.connected_channel_keys:
        instructions.insert(
            0,
            f"Тестовый бот уже подключён к каналу {settings.runtime_platform_channel_key} и будет отвечать на новые входящие сообщения автоматически.",
        )

    return instructions


def build_env_example(record: BotRecord) -> dict[str, str]:
    """Prepare one example env bundle for the selected bot."""
    settings = get_settings()
    env_example = {item.key: item.default_value for item in record.variables}
    env_example.setdefault("PLATFORM_BASE_URL", settings.platform_public_base_url)
    env_example.setdefault("PLATFORM_PROJECT_ID", record.linked_project_id or "proj_xxx")
    env_example.setdefault("PLATFORM_PROJECT_API_KEY", "project_key_xxx")
    env_example.setdefault("PLATFORM_CHANNEL_ID", "wa_xxx")
    env_example.setdefault("PLATFORM_CHANNEL_KEY", record.linked_channel_key or settings.runtime_platform_channel_key)
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
        "url": f"{get_settings().platform_public_base_url}/api/v1/projects/{record.linked_project_id or 'proj_xxx'}/messages/send",
        "headers": {
            "X-Project-Key": "project_key_xxx",
            "Content-Type": "application/json",
        },
        "json": {
            "channel_id": "wa_xxx",
            "chat_id": "996500000000@c.us",
            "text": "Здравствуйте! Проверяю ваш заказ.",
        },
    }


def serialize_bot_detail(record: BotRecord) -> BotDetailResponse:
    """Return the full configuration plus platform-side setup guidance."""
    summary = serialize_bot_summary(record)
    return BotDetailResponse(
        **summary.model_dump(),
        authorization_header=mask_authorization_header(record.authorization_header),
        workflow_summary=record.workflow_summary,
        variables=record.variables,
        api_bindings=record.api_bindings,
        platform_instructions=build_platform_instructions(record),
        env_example=build_env_example(record),
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
  <title>MINIGREENAPI | Боты платформы</title>
  <meta name="description" content="Каталог дефолтных и кастомных ботов платформы: Dify, n8n и webhook-интеграции с инструкциями по переменным." />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/brand.css?v=community-20260515e" />
</head>
<body>
  <div class="page-shell onboarding-shell">
    <div class="ambient ambient-left"></div>
    <div class="ambient ambient-right"></div>
    <header class="topbar connect-topbar">
      <a class="brand-home" href="/" aria-label="COMMUNITY">
        <img class="brand-logo-image" src="/static/community-mark-clean.svg?v=community-20260515d" alt="COMMUNITY mark" />
      </a>
      <nav class="nav-links">
        <a href="/">Платформа</a>
        <a href="/chats">Чаты</a>
        <a href="/connect/whatsapp">Connect WA</a>
      </nav>
    </header>

    <main class="onboarding-main bot-studio-main">
      <section class="onboarding-intro reveal is-visible">
        <span class="eyebrow">Bot registry</span>
        <h1>Дефолтный Dify-бот и свои интеграции в одном месте</h1>
        <p class="hero-text">
          Здесь можно держать дефолтного бота платформы, кастомных ботов, n8n-связки и все нужные переменные.
          Для тестового режима платформа может отвечать в WhatsApp напрямую через Dify без отдельного backend.
        </p>
        <p class="contract-note connect-hero-note">
          Ваш текущий WhatsApp-runtime остаётся как есть. Этот раздел управляет только тем,
          какой бот подключён к сообщениями и как именно он должен отвечать.
        </p>
      </section>

      <section class="bot-studio-grid">
        <aside class="wizard-card bot-catalog-card reveal is-visible">
          <div class="bot-panel-head">
            <div>
              <div class="card-label card-label-no-margin">Каталог ботов</div>
              <h2 class="simple-connection-name bot-section-title">Реестр подключений</h2>
              <p class="section-copy section-copy-tight">
                Дефолтный шаблон можно добавить одной кнопкой, а потом подключить его как тестового бота к platform-main.
              </p>
            </div>
            <div class="bot-panel-actions">
              <button class="button button-secondary" id="bot-refresh-btn" type="button">Обновить</button>
              <button class="button button-primary" id="seed-default-bot-btn" type="button">Добавить дефолтный бот</button>
            </div>
          </div>

          <div class="bot-catalog-list" id="bot-list">
            <div class="empty-state-card">Пока нет зарегистрированных ботов. Можно начать с дефолтного Dify-бота или добавить свой.</div>
          </div>
        </aside>

        <section class="wizard-card bot-detail-card reveal is-visible">
          <div class="simple-status-row bot-detail-head">
            <div>
              <div class="card-label card-label-no-margin">Детали бота</div>
              <h2 class="simple-connection-name bot-detail-title" id="bot-detail-name">Выберите бота</h2>
            </div>
            <span class="status-badge status-badge-pending" id="bot-detail-badge">Ожидание</span>
          </div>

          <div class="status-grid bot-summary-grid" id="bot-summary-grid">
            <div class="status-tile">
              <span class="status-kicker">Engine</span>
              <strong id="bot-engine-value">-</strong>
            </div>
            <div class="status-tile">
              <span class="status-kicker">Endpoint</span>
              <strong id="bot-endpoint-value">-</strong>
            </div>
            <div class="status-tile">
              <span class="status-kicker">Проект</span>
              <strong id="bot-project-value">-</strong>
            </div>
            <div class="status-tile">
              <span class="status-kicker">Канал</span>
              <strong id="bot-channel-value">-</strong>
            </div>
          </div>

          <div class="simple-actions bot-live-actions">
            <button class="button button-primary" id="connect-test-bot-btn" type="button" disabled>Подключить тестового бота</button>
            <button class="button button-secondary" id="disconnect-test-bot-btn" type="button" disabled>Отключить тестового бота</button>
            <button class="button button-secondary" id="activate-bot-btn" type="button" disabled>Активировать бота</button>
            <button class="button button-secondary" id="deactivate-bot-btn" type="button" disabled>Деактивировать бота</button>
          </div>

          <div class="bot-description-card" id="bot-description-card">
            Выберите бота слева, чтобы посмотреть его переменные, API-связки и инструкцию по подключению.
          </div>

          <div class="bot-detail-grid">
            <div class="bot-detail-column">
              <div class="card-label">Переменные</div>
              <div class="bot-data-list" id="bot-variable-list">
                <div class="empty-state-card">Здесь появится список переменных бота.</div>
              </div>
            </div>
            <div class="bot-detail-column">
              <div class="card-label">API и n8n</div>
              <div class="bot-data-list" id="bot-api-list">
                <div class="empty-state-card">Здесь появятся внешние API и webhook-связки.</div>
              </div>
            </div>
          </div>

          <div class="bot-instruction-stack">
            <div class="result-console-shell">
              <div class="status-kicker">Инструкция платформы</div>
              <div class="instruction-list" id="bot-instruction-list">
                <div class="empty-state-card">После выбора бота здесь появится пошаговая инструкция.</div>
              </div>
            </div>

            <div class="result-console-shell">
              <div class="status-kicker">ENV пример</div>
              <pre class="result-console" id="bot-env-example"># Выберите бота, чтобы увидеть рекомендованный .env набор.</pre>
            </div>

            <div class="result-console-shell">
              <div class="status-kicker">Inbound webhook contract</div>
              <pre class="result-console" id="bot-inbound-example">{}</pre>
            </div>

            <div class="result-console-shell">
              <div class="status-kicker">Outbound send example</div>
              <pre class="result-console" id="bot-outbound-example">{}</pre>
            </div>
          </div>
        </section>
      </section>

      <section class="simple-connect-grid bot-builder-grid">
        <div class="wizard-card reveal is-visible">
          <div class="card-label">Добавить своего бота</div>
          <h2 class="simple-connection-name bot-section-title">Новый bot integration</h2>
          <p class="section-copy section-copy-tight">
            Здесь можно зарегистрировать собственного бота, связать его с проектом, указать переменные и внешние API.
          </p>

          <form class="stack-form" id="bot-create-form">
            <div class="form-split">
              <label>
                <span>Название</span>
                <input id="bot-name-input" name="name" placeholder="Sales Assistant" required />
              </label>
              <label>
                <span>Slug</span>
                <input id="bot-slug-input" name="slug" placeholder="sales-assistant" required />
              </label>
            </div>

            <div class="form-split">
              <label>
                <span>Engine</span>
                <select id="bot-engine-input" name="engine_type">
                  <option value="dify">Dify</option>
                  <option value="n8n">n8n</option>
                  <option value="webhook">Webhook</option>
                  <option value="custom">Custom</option>
                </select>
              </label>
              <label>
                <span>Владелец</span>
                <input id="bot-owner-input" name="owner_label" placeholder="Platform team / Client team" />
              </label>
            </div>

            <label>
              <span>Описание</span>
              <textarea id="bot-description-input" name="description" rows="3" placeholder="Что делает этот бот и когда его использовать."></textarea>
            </label>

            <div class="form-split">
              <label>
                <span>Endpoint URL</span>
                <input id="bot-endpoint-input" name="endpoint_url" placeholder="https://your-bot.example.com/webhook" />
              </label>
              <label>
                <span>Authorization header</span>
                <input id="bot-auth-input" name="authorization_header" placeholder="Bearer secret-token" />
              </label>
            </div>

            <div class="form-split">
              <label>
                <span>Linked project ID</span>
                <input id="bot-project-input" name="linked_project_id" placeholder="proj_xxx" />
              </label>
              <label>
                <span>Linked channel key</span>
                <input id="bot-channel-input" name="linked_channel_key" placeholder="platform-main" value="platform-main" />
              </label>
            </div>

            <label>
              <span>Кратко про workflow</span>
              <textarea id="bot-workflow-input" name="workflow_summary" rows="4" placeholder="Например: Start -> CRM lookup -> LLM -> Answer -> n8n handoff"></textarea>
            </label>

            <label>
              <span>Переменные бота</span>
              <textarea id="bot-variables-input" rows="5" placeholder="KEY|required|default|description&#10;PLATFORM_BASE_URL|true|http://127.0.0.1:8000|Базовый адрес платформы&#10;PLATFORM_PROJECT_API_KEY|true||Ключ для outbound send endpoint"></textarea>
            </label>

            <label>
              <span>API и n8n-связки</span>
              <textarea id="bot-api-bindings-input" rows="5" placeholder="name|kind|url|notes&#10;Primary n8n flow|n8n|https://n8n.example.com/webhook/main|CRM sync and escalation"></textarea>
            </label>

            <label class="bot-checkbox-row">
              <input id="bot-enabled-input" name="enabled" type="checkbox" checked />
              <span>Сразу активировать бота в реестре</span>
            </label>

            <div class="wizard-actions">
              <button class="button button-primary" type="submit">Сохранить бота</button>
              <button class="button button-secondary" id="bot-form-reset-btn" type="reset">Очистить форму</button>
            </div>
          </form>
        </div>

        <aside class="wizard-card wizard-sidebar reveal is-visible">
          <div class="card-label">Как это работает</div>
          <h2 class="simple-connection-name bot-section-title">Быстрый onboarding</h2>

          <div class="connect-summary-list">
            <article>
              <strong>1. Тестовый режим</strong>
              <p>Кнопка подключения тестового бота включает прямую схему WhatsApp -> Платформа -> Dify -> WhatsApp без отдельного backend.</p>
            </article>
            <article>
              <strong>2. Свои боты</strong>
              <p>Любой свой бот можно хранить рядом в каталоге и потом привязать к отдельной логике или внешнему API.</p>
            </article>
            <article>
              <strong>3. n8n и CRM</strong>
              <p>n8n удобно использовать для побочных действий: CRM, handoff менеджеру, уведомлений и фоновых интеграций.</p>
            </article>
            <article>
              <strong>4. Чаты рядом</strong>
              <p>История диалогов всё равно остаётся в разделе Чаты, поэтому можно и смотреть сообщения, и вручную отвечать из платформы.</p>
            </article>
          </div>
        </aside>
      </section>
    </main>
  </div>
  <script src="/static/bot-studio.js?v=bots-20260515c"></script>
</body>
</html>"""


@api_router.get("/api/v1/platform/bots", response_model=list[BotSummaryResponse])
def list_platform_bots() -> list[BotSummaryResponse]:
    """List default and custom bots available inside the platform."""
    return [serialize_bot_summary(record) for record in get_bot_registry_service().list_bots()]


@api_router.get("/api/v1/platform/bots/{bot_id}", response_model=BotDetailResponse)
def get_platform_bot(bot_id: str) -> BotDetailResponse:
    """Return one bot definition with all platform-side instructions."""
    record = get_bot_registry_service().get_bot(bot_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found.")
    return serialize_bot_detail(record)


@api_router.post("/api/v1/platform/bots", response_model=BotDetailResponse, status_code=status.HTTP_201_CREATED)
def create_platform_bot(payload: BotCreateRequest) -> BotDetailResponse:
    """Create one custom bot integration in the platform catalog."""
    try:
        record = get_bot_registry_service().create_bot(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return serialize_bot_detail(record)


@api_router.post("/api/v1/platform/bots/default", response_model=BotDetailResponse, status_code=status.HTTP_201_CREATED)
def create_default_platform_bot() -> BotDetailResponse:
    """Seed the optional default Dify bot template for the platform workspace."""
    record = get_bot_registry_service().sync_default_bot_from_settings()
    return serialize_bot_detail(record)


@api_router.post(
    "/api/v1/platform/bots/{bot_id}/connect-test",
    response_model=BotTestConnectionResponse,
    status_code=status.HTTP_200_OK,
)
def connect_platform_test_bot(bot_id: str) -> BotTestConnectionResponse:
    """Attach one bot as the active test bot for the platform-owned WhatsApp channel."""
    settings = get_settings()
    try:
        get_bot_registry_service().connect_bot_to_channel(bot_id, settings.runtime_platform_channel_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return BotTestConnectionResponse(
        bot_id=bot_id,
        channel_key=settings.runtime_platform_channel_key,
        enabled=True,
    )


@api_router.post(
    "/api/v1/platform/bots/{bot_id}/disconnect-test",
    response_model=BotTestConnectionResponse,
    status_code=status.HTTP_200_OK,
)
def disconnect_platform_test_bot(bot_id: str) -> BotTestConnectionResponse:
    """Detach one bot from the active test-bot slot for the platform WhatsApp channel."""
    settings = get_settings()
    try:
        get_bot_registry_service().disconnect_bot_from_channel(bot_id, settings.runtime_platform_channel_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return BotTestConnectionResponse(
        bot_id=bot_id,
        channel_key=settings.runtime_platform_channel_key,
        enabled=False,
    )


@api_router.post("/api/v1/platform/bots/{bot_id}/activate", status_code=status.HTTP_200_OK)
def activate_platform_bot(bot_id: str) -> dict[str, object]:
    """Enable one registered bot in the catalog."""
    try:
        record = get_bot_registry_service().set_bot_enabled(bot_id, True)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"ok": True, "bot_id": record.id, "enabled": record.enabled}


@api_router.post("/api/v1/platform/bots/{bot_id}/deactivate", status_code=status.HTTP_200_OK)
def deactivate_platform_bot(bot_id: str) -> dict[str, object]:
    """Disable one registered bot in the catalog and disconnect it from active channels."""
    try:
        record = get_bot_registry_service().set_bot_enabled(bot_id, False)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"ok": True, "bot_id": record.id, "enabled": record.enabled}
