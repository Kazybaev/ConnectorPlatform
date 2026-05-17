from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def landing_page() -> str:
    """Render the current WhatsApp Web JS platform home page."""
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WhatsApp Web Bot Platform</title>
  <meta name="description" content="Self-hosted WhatsApp Web JS bot platform with QR login, chat monitor, and Dify bot bridge." />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/brand.css?v=community-20260515d" />
</head>
<body>
  <div class="page-shell">
    <header class="topbar">
      <a class="brand-home" href="/" aria-label="COMMUNITY">
        <img class="brand-logo-image" src="/static/community-mark-clean.svg?v=community-20260515d" alt="COMMUNITY mark" />
      </a>
      <nav class="nav-links">
        <a href="/connect/whatsapp">Connect WA</a>
        <a href="/chats">Чаты</a>
        <a href="/bots">Боты</a>
        <a href="/docs">API Docs</a>
      </nav>
    </header>

    <main>
      <section class="hero">
        <div class="hero-copy">
          <div class="eyebrow">WhatsApp Web JS runtime</div>
          <h1>WhatsApp-бот, чат-монитор и Dify в одной локальной связке.</h1>
          <p class="hero-text">
            Платформа поднимает собственную WhatsApp Web JS-сессию, показывает QR-код,
            принимает входящие сообщения, хранит историю чатов и отправляет ответы бота обратно в WhatsApp.
          </p>
          <div class="hero-actions">
            <a class="button button-primary" href="/connect/whatsapp">Подключить WhatsApp</a>
            <a class="button button-secondary" href="/chats">Открыть чаты</a>
            <a class="button button-secondary" href="/bots">Настроить бота</a>
          </div>
        </div>
        <div class="hero-panel">
          <div class="hero-panel-card glow">
            <div class="card-label">Runtime flow</div>
            <div class="card-grid">
              <article>
                <span class="card-kicker">1</span>
                <h3>QR login</h3>
                <p>Откройте `/connect/whatsapp`, отсканируйте QR и сохраните локальную сессию.</p>
              </article>
              <article>
                <span class="card-kicker">2</span>
                <h3>Inbox</h3>
                <p>Входящие личные чаты попадают в `/chats`, группы и broadcast фильтруются.</p>
              </article>
              <article>
                <span class="card-kicker">3</span>
                <h3>Bot</h3>
                <p>Активный Dify-бот отвечает только на текстовые сообщения подключенного канала.</p>
              </article>
              <article>
                <span class="card-kicker">4</span>
                <h3>Send</h3>
                <p>Ответы оператора и бота отправляются через тот же WhatsApp Web JS runtime.</p>
              </article>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>
  <script src="/static/brand.js"></script>
</body>
</html>"""
