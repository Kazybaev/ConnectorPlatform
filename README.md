# MINIGREENAPI Platform

MINIGREENAPI is a single-platform WhatsApp transport core. External AI systems connect to MINIGREENAPI through a webhook contract, while MINIGREENAPI owns the WhatsApp delivery layer through Green API.

## What changed

- The legacy Dify bridge has been removed from the runtime architecture.
- WhatsApp is no longer tied to one global instance from `.env`.
- Each client project now has:
  - its own provider webhook
  - its own project API key
  - one or more Green API WhatsApp channels
- The worker routes incoming WhatsApp traffic to the correct external AI project and sends the answer back through Green API.

## Architecture

### Control plane

- `POST /api/v1/admin/projects` creates a tenant project.
- `POST /api/v1/admin/projects/{project_id}/channels` connects a Green API instance.
- `GET /api/v1/admin/runtime/channels` shows runtime state for active channels.

### Data plane

- `app.whatsapp_bot` is the runtime worker.
- It polls every active Green API channel from the project registry.
- For each incoming message it calls the external AI webhook of the bound project.
- If the provider returns sync messages, MINIGREENAPI sends them back to WhatsApp.
- If the provider needs async work, it can later call MINIGREENAPI's outbound send endpoint with its `X-Project-Key`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Recommended `.env`:

```env
APP_NAME=MINIGREENAPI Platform
DEBUG=false
LOG_LEVEL=INFO
CORS_ORIGINS=*
DATABASE_PATH=data/minigreenapi.sqlite3
PLATFORM_PUBLIC_BASE_URL=http://127.0.0.1:8000
PLATFORM_ADMIN_TOKEN=change_me_before_production
CONNECT_TIMEOUT_SECONDS=5
REQUEST_TIMEOUT_SECONDS=30
GREEN_API_RECEIVE_TIMEOUT_SECONDS=20
GREEN_API_POLL_INTERVAL_SECONDS=1
RUNTIME_CHANNELS_REFRESH_SECONDS=15
RUNTIME_CHANNEL_HEARTBEAT_SECONDS=60
```

## Run API

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Run worker

```powershell
powershell -ExecutionPolicy Bypass -File .\start_bot.ps1
```

Or directly:

```powershell
.\.venv\Scripts\python.exe -m app.whatsapp_bot
```

## Embedded onboarding

Open:

```text
http://127.0.0.1:8000/connect/whatsapp
```

The built-in connection screen is now intentionally simple:

- no project form
- no GPT or bot setup
- one platform-owned WhatsApp account
- QR code on page load
- connected or disconnected status
- account info after scan
- logout and get a fresh QR again

To enable this screen, set these `.env` values:

```env
SIMPLE_CONNECT_NAME=Platform WhatsApp
SIMPLE_CONNECT_GREEN_API_URL=https://7107.api.greenapi.com
SIMPLE_CONNECT_GREEN_API_ID_INSTANCE=7107598500
SIMPLE_CONNECT_GREEN_API_TOKEN=your_green_api_token
```

## Admin API

Admin routes use `X-Admin-Token` when `PLATFORM_ADMIN_TOKEN` is configured.

### One-step WhatsApp onboarding

`POST /api/v1/admin/onboarding/whatsapp`

Creates the project, creates the first WhatsApp channel, and returns the current connection snapshot with QR status.

### Get channel connection state

`GET /api/v1/admin/projects/{project_id}/channels/{channel_id}/connection?include_qr=true`

Returns:

- Green API authorization state
- socket status
- connected phone and device info
- polling readiness for the platform runtime
- QR payload when available

### Reset channel and get a new QR

`POST /api/v1/admin/projects/{project_id}/channels/{channel_id}/connection/reset`

Logs out the currently linked device and starts a fresh QR-based login flow.

### Create project

`POST /api/v1/admin/projects`

```json
{
  "name": "ACME Support",
  "slug": "acme-support",
  "description": "Primary WhatsApp support transport",
  "enabled": true,
  "provider": {
    "url": "https://acme.example.com/minigreenapi/inbound",
    "authorization_header": "Bearer super-secret-provider-token",
    "extra_headers": {
      "X-Source": "minigreenapi"
    }
  }
}
```

The response returns `project_api_key` once. Save it on the client side.

### Attach WhatsApp channel

`POST /api/v1/admin/projects/{project_id}/channels`

```json
{
  "name": "Main WhatsApp",
  "green_api_url": "https://7107.api.greenapi.com",
  "green_api_id_instance": "7107598500",
  "green_api_token": "your_green_api_token",
  "enabled": true
}
```

## External AI contract

MINIGREENAPI sends inbound WhatsApp messages to the project's provider webhook.

### Request

```json
{
  "event": "whatsapp.message.received",
  "project": {
    "id": "proj_123",
    "slug": "acme-support",
    "name": "ACME Support"
  },
  "channel": {
    "id": "wa_123",
    "name": "Main WhatsApp",
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
}
```

### Synchronous response

```json
{
  "messages": [
    {
      "type": "text",
      "text": "Здравствуйте! Проверяю ваш заказ."
    }
  ],
  "metadata": {
    "providerRequestId": "req_123"
  }
}
```

### Asynchronous outbound send

External AI can also send messages later through:

`POST /api/v1/projects/{project_id}/messages/send`

Headers:

- `X-Project-Key: <project_api_key>`

Body:

```json
{
  "channel_id": "wa_123",
  "chat_id": "996555000111@c.us",
  "text": "Ваш заказ уже в пути."
}
```

## Green API mode

MINIGREENAPI uses Green API HTTP API polling, not public webhooks.

Based on Green API docs:

- `receiveNotification` + `deleteNotification` are the FIFO polling pair
- `webhookUrl` must be empty for HTTP API polling
- `incomingWebhook=yes` must be enabled
- `setSettings` can take up to 5 minutes to apply and restarts the instance

Official docs:

- https://green-api.com/en/docs/api/receiving/technology-http-api/
- https://green-api.com/en/docs/api/account/SetSettings/
- https://green-api.com/en/docs/api/account/GetSettings/
- https://green-api.com/en/docs/api/account/QR/
- https://green-api.com/en/docs/api/account/Logout/
- https://green-api.com/en/docs/api/account/GetWaSettings/
- https://green-api.com/en/docs/api/sending/SendMessage/

## Important onboarding note

QR login inside MINIGREENAPI works with existing Green API instance credentials:

- `green_api_url`
- `idInstance`
- `apiTokenInstance`

Automatic creation of a brand-new Green API instance from inside your platform is a separate partner-level capability based on Green API `createInstance`. According to the official docs, that requires a partner token from Green API support, so it is not turned on in this project by default.

## UI

The root page `/` is now a branded landing page for MINIGREENAPI, with animated sections and an integration contract preview.

The dedicated `/connect/whatsapp` page is the operator-facing QR onboarding screen.

## Legacy utility

The Excel upload endpoint is still available as a separate utility:

- `POST /upload`
