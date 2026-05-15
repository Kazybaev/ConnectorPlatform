from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def landing_page() -> str:
    """Render a product-style landing page for the SaaS transport platform."""
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MINIGREENAPI Platform</title>
  <meta name="description" content="MINIGREENAPI is a self-hosted WhatsApp transport layer for AI products. Connect local runtime channels, route messages to external AI webhooks, and run WhatsApp as a reusable platform." />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/brand.css" />
</head>
<body>
  <div class="page-shell">
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
        <span class="brand-subtitle">Platform Core</span>
      </div>
      <nav class="nav-links">
        <a href="#platform">Платформа</a>
        <a href="#flow">Как это работает</a>
        <a href="#contract">Контракт</a>
        <a href="/bots">Боты</a>
        <a href="/connect/whatsapp">Connect WA</a>
        <a href="/docs">API Docs</a>
      </nav>
    </header>

    <main>
      <section class="hero">
        <div class="hero-copy">
          <div class="eyebrow">WhatsApp-first SaaS core</div>
          <h1>Пусть внешние AI подключаются к вам, а не наоборот.</h1>
          <p class="hero-text">
            MINIGREENAPI превращает ваш собственный WhatsApp runtime в транспортный слой уровня платформы:
            проекты регистрируются у вас, подключают свои каналы, отдают webhook своего AI,
            а вы гарантированно принимаете входящие сообщения, маршрутизируете их в нужный AI и возвращаете ответ в WhatsApp.
          </p>
          <div class="hero-actions">
            <a class="button button-primary" href="/connect/whatsapp">Подключить WhatsApp</a>
            <a class="button button-secondary" href="/bots">Открыть ботов</a>
            <a class="button button-secondary" href="/docs">Открыть OpenAPI</a>
            <a class="button button-secondary" href="#contract">Смотреть контракт интеграции</a>
          </div>
        </div>
        <div class="hero-panel">
          <div class="hero-panel-card glow">
            <div class="card-label">Platform primitives</div>
            <div class="card-grid">
              <article>
                <span class="card-kicker">Project</span>
                <h3>Tenant + AI webhook</h3>
                <p>Каждая компания получает свой проект, API key и provider webhook.</p>
              </article>
              <article>
                <span class="card-kicker">Channel</span>
                <h3>WhatsApp runtime</h3>
                <p>Каждый WhatsApp-канал подключается отдельно и живёт как независимая self-hosted runtime-сессия.</p>
              </article>
              <article>
                <span class="card-kicker">Runtime</span>
                <h3>Routing worker</h3>
                <p>Входящий message event попадает в нужный AI, а ответ уходит обратно через наш transport API.</p>
              </article>
              <article>
                <span class="card-kicker">Future SaaS</span>
                <h3>Ready for scale</h3>
                <p>Управляющий API, project-scoped send endpoint и операционный runtime status уже заложены в ядро.</p>
              </article>
            </div>
          </div>
        </div>
      </section>

      <section class="feature-strip" id="platform">
        <article class="feature-card reveal">
          <span class="feature-index">01</span>
          <h2>Multi-tenant control plane</h2>
          <p>Регистрация проектов, webhook AI, project API key, подключение каналов и runtime-статусы в одном месте.</p>
        </article>
        <article class="feature-card reveal">
          <span class="feature-index">02</span>
          <h2>Provider-agnostic AI contract</h2>
          <p>Любой внешний AI может отвечать синхронно через webhook или асинхронно отправлять сообщения обратно через наш project API.</p>
        </article>
        <article class="feature-card reveal">
          <span class="feature-index">03</span>
          <h2>Self-hosted runtime core</h2>
          <p>Runtime сам поднимает локальную WhatsApp-сессию, хранит авторизацию и отдаёт QR без внешнего провайдера.</p>
        </article>
      </section>

      <section class="flow-section" id="flow">
        <div class="section-heading reveal">
          <span class="eyebrow">Flow</span>
          <h2>Как теперь выглядит путь сообщения</h2>
        </div>
        <div class="timeline">
          <div class="timeline-step reveal">
            <span>1</span>
            <div>
              <h3>Клиент подключает проект</h3>
              <p>Создаётся tenant project с URL внешнего AI webhook и project API key.</p>
            </div>
          </div>
          <div class="timeline-step reveal">
            <span>2</span>
            <div>
              <h3>К проекту привязывается runtime-канал</h3>
              <p>Каждая WhatsApp-сессия живёт как отдельный канал внутри вашего ядра.</p>
            </div>
          </div>
          <div class="timeline-step reveal">
            <span>3</span>
            <div>
              <h3>Runtime получает входящее сообщение</h3>
              <p>Worker забирает notification, определяет binding и отправляет event в нужный AI webhook.</p>
            </div>
          </div>
          <div class="timeline-step reveal">
            <span>4</span>
            <div>
              <h3>Ответ уходит обратно в WhatsApp</h3>
              <p>Синхронный ответ отправляется автоматически. Для long-running AI есть отдельный outbound send endpoint вашего проекта.</p>
            </div>
          </div>
        </div>
      </section>

      <section class="contract-section" id="contract">
        <div class="section-heading reveal">
          <span class="eyebrow">Integration contract</span>
          <h2>Что внешний AI должен уметь</h2>
        </div>
        <div class="contract-grid">
          <div class="contract-card reveal">
            <h3>Inbound webhook request</h3>
            <pre><code>{
  "event": "whatsapp.message.received",
  "project": {
    "id": "proj_xxx",
    "slug": "acme-support",
    "name": "ACME Support"
  },
  "channel": {
    "id": "wa_xxx",
    "name": "Main WA",
    "type": "whatsapp",
    "instanceId": "7107598500"
  },
  "conversation": {
    "chatId": "996555000111@c.us",
    "userId": "996555000111@c.us"
  },
  "message": {
    "id": "ABCD1234",
    "text": "Где мой заказ?",
    "timestamp": 1763115112,
    "chatId": "996555000111@c.us",
    "sender": "996555000111@c.us",
    "senderName": "Aizada"
  }
}</code></pre>
          </div>
          <div class="contract-card reveal">
            <h3>Synchronous provider response</h3>
            <pre><code>{
  "messages": [
    {
      "type": "text",
      "text": "Здравствуйте! Проверяю ваш заказ."
    }
  ],
  "metadata": {
    "providerRequestId": "req_123"
  }
}</code></pre>
            <p class="contract-note">
              Если AI отвечает асинхронно, он может позже вызвать project send endpoint платформы и отправить сообщение через ваш transport layer.
            </p>
          </div>
        </div>
      </section>
    </main>
  </div>
  <script src="/static/brand.js"></script>
</body>
</html>"""
