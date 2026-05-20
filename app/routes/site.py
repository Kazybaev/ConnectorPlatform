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
  <meta name="description" content="AI Connector connects WhatsApp Web JS, chat monitoring, Dify bots, and operator replies in one self-hosted platform." />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/brand.css?v=ai-connector-20260519c" />
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
            Платформа связывает WhatsApp Web JS, мониторинг чатов, Dify-ботов и ручные ответы оператора в одной системе.
            WhatsApp-сессия живет в локальном runtime, а FastAPI хранит диалоги,
            управляет ботами и показывает рабочие страницы для подключения, поддержки и проверки статуса.
          </p>
          <div class="hero-actions">
            <a class="button button-primary" href="/connect/whatsapp">Подключить WhatsApp</a>
            <a class="button button-secondary" href="/chats">Открыть чаты</a>
            <a class="button button-secondary" href="/bots">Настроить бота</a>
          </div>
        </div>
        <div class="hero-panel">
          <div class="hero-panel-card glow">
            <div class="card-label">Platform status model</div>
            <div class="card-grid">
              <article>
                <span class="card-kicker">01</span>
                <h3>WhatsApp Runtime</h3>
                <p>Node.js runtime держит WhatsApp Web JS-сессию, QR-login, reconnect и отправку сообщений.</p>
              </article>
              <article>
                <span class="card-kicker">02</span>
                <h3>Inbox Sync</h3>
                <p>FastAPI принимает события runtime, сохраняет новые личные диалоги и показывает их в `/chats`.</p>
              </article>
              <article>
                <span class="card-kicker">03</span>
                <h3>AI Bot Layer</h3>
                <p>Dify-бот подключается как активный обработчик канала и отвечает только на разрешенные новые сообщения.</p>
              </article>
              <article>
                <span class="card-kicker">04</span>
                <h3>Operator Control</h3>
                <p>Оператор может видеть историю, отвечать вручную и отключать бота без потери WhatsApp-синхронизации.</p>
              </article>
            </div>
          </div>
        </div>
      </section>

      <section class="feature-strip">
        <article class="feature-card reveal is-visible">
          <span class="feature-index">Transport</span>
          <h2>WhatsApp без внешнего провайдера</h2>
          <p>
            Вся транспортная часть работает через `whatsapp-web.js`: QR-код, сохраненная сессия, локальный Chromium,
            проверка `connected`, защита от отправки при обрыве и повторное подключение после disconnect.
          </p>
        </article>
        <article class="feature-card reveal is-visible">
          <span class="feature-index">Storage</span>
          <h2>Единая история чатов</h2>
          <p>
            Новые личные сообщения из WhatsApp попадают в SQLite, групповые и broadcast-события фильтруются,
            а исходящие ответы оператора и бота сохраняются в той же ленте.
          </p>
        </article>
        <article class="feature-card reveal is-visible">
          <span class="feature-index">Automation</span>
          <h2>Бот включается отдельно</h2>
          <p>
            Платформа может принимать сообщения уже после запуска, но бот отвечает только после активации
            и не обрабатывает старые replay-сообщения как новые обращения.
          </p>
        </article>
      </section>

      <section class="flow-section">
        <div class="section-heading">
          <span class="eyebrow">Runtime flow</span>
          <h2>Как проходит одно сообщение</h2>
        </div>
        <div class="timeline">
          <article class="timeline-step reveal is-visible">
            <span>1</span>
            <div>
              <h3>WhatsApp Web JS получает событие</h3>
              <p>Runtime слушает `message` и `message_create`, определяет чат, направление, тип сообщения и источник.</p>
            </div>
          </article>
          <article class="timeline-step reveal is-visible">
            <span>2</span>
            <div>
              <h3>Платформа сохраняет диалог</h3>
              <p>FastAPI записывает событие в `/api/v1/runtime/incoming`, обновляет список разговоров и защищает историю от дублей.</p>
            </div>
          </article>
          <article class="timeline-step reveal is-visible">
            <span>3</span>
            <div>
              <h3>Бот проверяет право на ответ</h3>
              <p>Если сообщение старше запуска runtime или старше активации бота, оно остается только контекстом и не получает автоответ.</p>
            </div>
          </article>
          <article class="timeline-step reveal is-visible">
            <span>4</span>
            <div>
              <h3>Ответ возвращается в WhatsApp</h3>
              <p>Операторский или AI-ответ отправляется через тот же runtime и фиксируется в истории как исходящее сообщение.</p>
            </div>
          </article>
        </div>
      </section>

      <section class="contract-section">
        <div class="section-heading">
          <span class="eyebrow">Production notes</span>
          <h2>Что важно для сервера</h2>
        </div>
        <div class="contract-grid">
          <article class="contract-card reveal is-visible">
            <h3>Постоянная сессия</h3>
            <p class="contract-note">
              Папка `data/runtime/sessions` должна жить на постоянном диске. Если ее удалить, WhatsApp попросит новый QR-login.
            </p>
          </article>
          <article class="contract-card reveal is-visible">
            <h3>Один runtime</h3>
            <p class="contract-note">
              Для одного WhatsApp-аккаунта должен работать один Node runtime. Два процесса на одну session-папку могут ломать синхронизацию.
            </p>
          </article>
          <article class="contract-card reveal is-visible">
            <h3>Supervisor</h3>
            <p class="contract-note">
              В проде FastAPI и Node runtime нужно держать под process manager, чтобы после рестарта сервера они поднялись автоматически.
            </p>
          </article>
        </div>
      </section>
    </main>
  </div>
  <script src="/static/brand.js"></script>
</body>
</html>"""
