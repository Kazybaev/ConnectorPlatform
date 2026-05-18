# AI Connector

Self-hosted WhatsApp bot platform based on `whatsapp-web.js`.

The app has two parts:

- Python/FastAPI control app: UI, chat storage, bot registry, Dify bridge.
- Node runtime: owns the WhatsApp Web session, QR login, incoming events, and message sending.

## Main Paths

- `app/main.py` starts the FastAPI app and autostarts the local WhatsApp runtime.
- `runtime/server.js` runs the `whatsapp-web.js` session on `127.0.0.1:8011`.
- `app/routes/onboarding.py` serves `/connect/whatsapp` for QR login/status/reset.
- `app/routes/chat_console.py` serves `/chats` and stores incoming/outgoing messages.
- `app/routes/bot_console.py` serves `/bots` and manages the active Dify bot.
- `app/services/platform_bot_runtime.py` sends incoming WhatsApp text into Dify and sends the answer back.
- `app/services/self_hosted_runtime_service.py` talks to the Node runtime.
- `app/services/chat_store.py` stores chat history in SQLite.
- `app/services/bot_registry.py` stores bot configuration in SQLite.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Set-Location runtime
npm install
Set-Location ..
Copy-Item .env.example .env
```

Recommended `.env`:

```env
APP_NAME=AI Connector
DEBUG=false
LOG_LEVEL=INFO
CORS_ORIGINS=*
DATABASE_PATH=data/whatsapp_platform.sqlite3
PLATFORM_PUBLIC_BASE_URL=http://127.0.0.1:8000
PLATFORM_ADMIN_TOKEN=change_me_before_production
CONNECT_TIMEOUT_SECONDS=5
REQUEST_TIMEOUT_SECONDS=90
RUNTIME_CHANNELS_REFRESH_SECONDS=15
RUNTIME_CHANNEL_HEARTBEAT_SECONDS=60
RUNTIME_SERVICE_BASE_URL=http://127.0.0.1:8011
RUNTIME_SERVICE_PORT=8011
RUNTIME_SERVICE_TOKEN=
RUNTIME_CALLBACK_TOKEN=
RUNTIME_SERVICE_AUTOSTART=true
RUNTIME_PLATFORM_CHANNEL_KEY=platform-main
RUNTIME_STARTUP_REPLAY_GRACE_MS=0
SIMPLE_CONNECT_NAME=Platform WhatsApp
DEFAULT_BOT_DIFY_BASE_URL=https://api.dify.ai/v1
DEFAULT_BOT_DIFY_API_KEY=
BOT_TYPING_ENABLED=true
BOT_TYPING_MIN_SECONDS=1.2
BOT_TYPING_MAX_SECONDS=6
BOT_TYPING_CHARS_PER_SECOND=18
BOT_FAILURE_REPLY_ENABLED=true
BOT_FAILURE_REPLY_TEXT=Сейчас ассистент временно недоступен. Попробуйте чуть позже.
```

## Run

```powershell
.\start_bot.ps1
```

Or directly:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The FastAPI app autostarts `runtime/server.js` when `/connect/whatsapp` or startup bootstrap touches the runtime.

Open:

```text
http://127.0.0.1:8000/connect/whatsapp
```

Then scan the QR code from WhatsApp. The session is stored under `data/runtime/sessions`.

## Useful Pages

- `/connect/whatsapp` - QR login, connection state, reset session.
- `/chats` - incoming conversations and manual replies.
- `/bots` - Dify bot configuration and channel connection.
- `/health` - FastAPI health.
- `http://127.0.0.1:8011/health` - local Node runtime health.

## Message Flow

1. WhatsApp receives a direct personal chat message.
2. `runtime/server.js` filters groups/broadcasts and posts the event to `/api/v1/runtime/incoming`.
3. FastAPI stores the message in SQLite.
4. If a bot is connected to `platform-main`, text messages are sent to Dify.
5. The Dify answer is sent back through `runtime/server.js` and stored as outbound chat history.

## Production Notes

- Run FastAPI and Node runtime under a process manager or service supervisor.
- Persist `data/whatsapp_platform.sqlite3` and `data/runtime/sessions`.
- Put FastAPI behind HTTPS/reverse proxy for browser access.
- Keep `RUNTIME_SERVICE_BASE_URL` local/private unless you add a runtime token.
- Use one active runtime process per WhatsApp session directory.
- Do not delete `data/runtime/sessions` unless you intentionally want a fresh QR login.
