from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def landing_page() -> str:
    """Render the AI Connector platform overview page."""
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Connector</title>
  <meta name="description" content="AI Connector connects WhatsApp Web JS, chat monitoring, media messages, Dify, project integrations, and operator replies in one self-hosted platform." />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/brand.css?v=ai-connector-20260520-platform" />
</head>
<body>
  <div class="page-shell">
    <header class="topbar">
      <a class="brand-home" href="/" aria-label="AI Connector">
        <img class="brand-logo-image" src="/static/image.png?v=logo-20260520" alt="AI Connector" />
      </a>
      <nav class="nav-links">
        <a class="is-active" href="/">Платформа</a>
        <a href="/chats">Чаты</a>
        <a href="/bots">Боты</a>
        <a href="/connect/whatsapp">WhatsApp</a>
      </nav>
    </header>

    <main>
      <section class="hero">
        <div class="hero-copy">
          <div class="eyebrow">Self-hosted AI messaging platform</div>
          <h1>AI Connector</h1>
          <p class="hero-text">
            Платформа связывает WhatsApp Web JS, мониторинг чатов, фото из WhatsApp, Dify,
            интеграции проектов и ручные ответы оператора в одной системе.
            WhatsApp-сессия живет в локальном runtime, FastAPI хранит диалоги и media,
            а активный бот или внешний проект получает входящие сообщения и возвращает ответ клиенту.
          </p>
          <div class="hero-actions">
            <a class="button button-primary" href="/connect/whatsapp">Подключить WhatsApp</a>
            <a class="button button-secondary" href="/chats">Открыть чаты</a>
            <a class="button button-secondary" href="/bots">Настроить бота</a>
          </div>
        </div>
        <div class="hero-panel">
          <div class="hero-panel-card glow">
            <div class="card-label">Что уже подключено</div>
            <div class="card-grid">
              <article>
                <span class="card-kicker">01</span>
                <h3>WhatsApp Runtime</h3>
                <p>Node.js runtime держит WhatsApp Web JS-сессию, QR-login, reconnect, отправку сообщений и скачивание фото.</p>
              </article>
              <article>
                <span class="card-kicker">02</span>
                <h3>Чаты и история</h3>
                <p>FastAPI сохраняет личные диалоги, старые сообщения, фото и исходящие ответы в одной ленте `/chats`.</p>
              </article>
              <article>
                <span class="card-kicker">03</span>
                <h3>Dify и проекты</h3>
                <p>В `/bots` можно подключить Dify или внешний проект по webhook URL как активный обработчик WhatsApp.</p>
              </article>
              <article>
                <span class="card-kicker">04</span>
                <h3>Контроль оператора</h3>
                <p>Оператор видит историю, листает прошлые сообщения, смотрит фото и может отвечать вручную.</p>
              </article>
            </div>
          </div>
        </div>
      </section>

      <section class="feature-strip">
        <article class="feature-card reveal is-visible">
          <span class="feature-index">WhatsApp</span>
          <h2>Транспорт без внешнего провайдера</h2>
          <p>
            Вся транспортная часть работает через `whatsapp-web.js`: QR-код, сохраненная сессия, локальный Chromium,
            проверка `connected`, повторное подключение после disconnect и отправка ответов обратно в WhatsApp.
          </p>
        </article>
        <article class="feature-card reveal is-visible">
          <span class="feature-index">Media</span>
          <h2>Фото видны в чате и уходят ботам</h2>
          <p>
            Входящие изображения сохраняются в `data/chat_media`, отображаются в мониторинге и передаются подключенному
            боту или проекту в payload как `media.url`, `media.data` и `media.data_url`.
          </p>
        </article>
        <article class="feature-card reveal is-visible">
          <span class="feature-index">Bots</span>
          <h2>Dify или интеграция проекта</h2>
          <p>
            В разделе “Боты” тип подключения меняет форму: для Dify нужны API URL и API Key,
            для проекта — базовый URL и путь приема WhatsApp-сообщений.
          </p>
        </article>
      </section>

      <section class="contract-section">
        <div class="section-heading">
          <span class="eyebrow">Быстрые действия</span>
          <h2>Что можно сделать сейчас</h2>
        </div>
        <div class="contract-grid">
          <article class="contract-card reveal is-visible">
            <h3>Подключить WhatsApp</h3>
            <p class="contract-note">Откройте QR-страницу, проверьте статус сессии и убедитесь, что runtime подключен.</p>
            <div class="hero-actions">
              <a class="button button-secondary" href="/connect/whatsapp">Открыть WhatsApp</a>
            </div>
          </article>
          <article class="contract-card reveal is-visible">
            <h3>Добавить обработчик</h3>
            <p class="contract-note">Выберите Dify или “Интеграция проекта”, укажите URL и сразу подключите к WhatsApp.</p>
            <div class="hero-actions">
              <a class="button button-secondary" href="/bots">Открыть ботов</a>
            </div>
          </article>
          <article class="contract-card reveal is-visible">
            <h3>Проверить диалоги</h3>
            <p class="contract-note">Смотрите историю, листайте прошлые сообщения, открывайте фото и отвечайте вручную.</p>
            <div class="hero-actions">
              <a class="button button-secondary" href="/chats">Открыть чаты</a>
            </div>
          </article>
        </div>
      </section>

      <section class="flow-section">
        <div class="section-heading">
          <span class="eyebrow">Message flow</span>
          <h2>Как проходит одно сообщение</h2>
        </div>
        <div class="timeline">
          <article class="timeline-step reveal is-visible">
            <span>1</span>
            <div>
              <h3>WhatsApp получает текст или фото</h3>
              <p>Runtime слушает `message` и `message_create`, определяет чат, направление, тип сообщения и при наличии скачивает media.</p>
            </div>
          </article>
          <article class="timeline-step reveal is-visible">
            <span>2</span>
            <div>
              <h3>Платформа сохраняет сообщение</h3>
              <p>FastAPI записывает событие в `/api/v1/runtime/incoming`, кладет фото в `/media` и обновляет историю диалога.</p>
            </div>
          </article>
          <article class="timeline-step reveal is-visible">
            <span>3</span>
            <div>
              <h3>Активный бот получает payload</h3>
              <p>Dify или интегрированный проект получает текст, chat_id, контекст последних сообщений и данные media, если клиент отправил фото.</p>
            </div>
          </article>
          <article class="timeline-step reveal is-visible">
            <span>4</span>
            <div>
              <h3>Ответ возвращается клиенту</h3>
              <p>Проект должен вернуть `{"answer":"..."}`. Платформа отправит этот текст в WhatsApp и сохранит ответ в истории.</p>
            </div>
          </article>
        </div>
      </section>

      <section class="contract-section">
        <div class="section-heading">
          <span class="eyebrow">Что важно знать</span>
          <h2>Основные правила работы</h2>
        </div>
        <div class="contract-grid">
          <article class="contract-card reveal is-visible">
            <h3>Постоянная WhatsApp-сессия</h3>
            <p class="contract-note">
              Папка `data/runtime/sessions` должна жить на постоянном диске. Если ее удалить, WhatsApp попросит новый QR-login.
            </p>
          </article>
          <article class="contract-card reveal is-visible">
            <h3>Один активный обработчик</h3>
            <p class="contract-note">
              Для WhatsApp-канала одновременно подключается один активный бот или проект. Новый выбранный обработчик заменяет предыдущий.
            </p>
          </article>
          <article class="contract-card reveal is-visible">
            <h3>Формат ответа проекта</h3>
            <p class="contract-note">
              Интегрированный проект должен отвечать JSON-ом с полем `answer`. Именно этот текст будет отправлен клиенту в WhatsApp.
            </p>
          </article>
        </div>
      </section>
    </main>
  </div>
  <script src="/static/brand.js"></script>
</body>
</html>"""
