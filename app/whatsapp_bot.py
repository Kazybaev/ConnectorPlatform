from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from contextlib import suppress
from typing import Any

from app.models.schemas import ProviderDispatchResult
from app.services.green_api_service import GreenApiClient, GreenApiCredentials, GreenApiServiceError
from app.services.project_registry import ActiveChannelBinding, get_project_registry_service, utc_now_iso
from app.services.provider_gateway import ProviderGatewayError, get_provider_gateway_service
from app.utils.config import get_settings
from app.utils.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

MESSAGE_CHUNK_SIZE = 20000
POLLING_SETTINGS_APPLY_WAIT_SECONDS = 65


def split_text_chunks(text: str, chunk_size: int = MESSAGE_CHUNK_SIZE) -> Iterable[str]:
    """Split long text into WhatsApp-safe chunks for Green API."""
    normalized = text.strip()
    if not normalized:
        return []

    chunks: list[str] = []
    remaining = normalized
    while len(remaining) > chunk_size:
        split_at = remaining.rfind("\n", 0, chunk_size)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, chunk_size)
        if split_at <= 0:
            split_at = chunk_size

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


def build_green_api_client(binding: ActiveChannelBinding) -> GreenApiClient:
    """Create a channel-specific Green API client from runtime binding data."""
    return GreenApiClient(
        GreenApiCredentials(
            api_url=binding.green_api_url,
            id_instance=binding.green_api_id_instance,
            api_token=binding.green_api_token,
        ),
        connect_timeout_seconds=settings.connect_timeout_seconds,
        request_timeout_seconds=settings.request_timeout_seconds,
        receive_timeout_seconds=settings.green_api_receive_timeout_seconds,
    )


def extract_text_message(notification_body: dict[str, Any]) -> str:
    """Normalize supported Green API incoming text payloads."""
    message_data = notification_body.get("messageData", {})
    type_message = str(message_data.get("typeMessage", "")).strip()

    if type_message == "textMessage":
        text = message_data.get("textMessageData", {}).get("textMessage", "")
        return text.strip() if isinstance(text, str) else ""

    if type_message in {"extendedTextMessage", "quotedMessage"}:
        text = message_data.get("extendedTextMessageData", {}).get("text", "")
        if isinstance(text, str) and text.strip():
            return text.strip()

        fallback_text = message_data.get("textMessageData", {}).get("textMessage", "")
        return fallback_text.strip() if isinstance(fallback_text, str) else ""

    return ""


def build_provider_event(binding: ActiveChannelBinding, notification_body: dict[str, Any], text: str) -> dict[str, Any]:
    """Translate a Green API notification into the platform's provider contract."""
    sender_data = notification_body.get("senderData", {})
    return {
        "event": "whatsapp.message.received",
        "project": {
            "id": binding.project_id,
            "slug": binding.project_slug,
            "name": binding.project_name,
        },
        "channel": {
            "id": binding.channel_id,
            "name": binding.channel_name,
            "type": "whatsapp",
            "instanceId": binding.green_api_id_instance,
        },
        "conversation": {
            "chatId": str(sender_data.get("chatId", "")).strip(),
            "userId": str(sender_data.get("sender", "")).strip() or str(sender_data.get("chatId", "")).strip(),
        },
        "message": {
            "id": str(notification_body.get("idMessage", "")).strip(),
            "text": text,
            "timestamp": notification_body.get("timestamp"),
            "chatId": str(sender_data.get("chatId", "")).strip(),
            "sender": str(sender_data.get("sender", "")).strip(),
            "senderName": str(sender_data.get("senderName", "")).strip(),
        },
    }


async def send_provider_result(
    binding: ActiveChannelBinding,
    green_api_client: GreenApiClient,
    chat_id: str,
    result: ProviderDispatchResult,
    quoted_message_id: str | None,
) -> None:
    """Send provider-generated text messages back to WhatsApp."""
    for message in result.messages:
        chunks = list(split_text_chunks(message.text))
        for index, chunk in enumerate(chunks):
            quote_target = quoted_message_id if index == 0 else None
            await asyncio.to_thread(
                green_api_client.send_message,
                chat_id,
                chunk,
                quote_target,
            )


def is_custom_webhook_conflict_error(exc: Exception) -> bool:
    """Detect Green API polling errors caused by a non-empty webhookUrl."""
    return "custom webhook url is set" in str(exc).casefold()


async def recover_from_custom_webhook_conflict(binding: ActiveChannelBinding, green_api_client: GreenApiClient) -> None:
    """Reapply polling settings and wait for Green API to switch modes."""
    logger.warning(
        "Channel %s (%s) switched out of polling mode because webhookUrl is set. Reapplying polling settings.",
        binding.channel_id,
        binding.project_slug,
    )

    save_settings = await asyncio.to_thread(
        green_api_client.set_settings,
        {
            "webhookUrl": "",
            "incomingWebhook": "yes",
            "outgoingWebhook": "yes",
            "stateWebhook": "yes",
        },
    )
    if not save_settings:
        raise RuntimeError("Green API did not confirm saving polling settings after webhook conflict.")

    logger.warning(
        "Channel %s is waiting %s seconds for Green API polling settings to apply.",
        binding.channel_id,
        POLLING_SETTINGS_APPLY_WAIT_SECONDS,
    )
    await asyncio.sleep(POLLING_SETTINGS_APPLY_WAIT_SECONDS)


async def ensure_polling_mode_settings(binding: ActiveChannelBinding, green_api_client: GreenApiClient) -> None:
    """Ensure the channel stays in HTTP API polling mode instead of webhook mode."""
    settings_payload = await asyncio.to_thread(green_api_client.get_settings)
    webhook_url = str(settings_payload.get("webhookUrl", "")).strip()
    incoming_webhook = str(settings_payload.get("incomingWebhook", "")).strip().lower()

    if not webhook_url and incoming_webhook == "yes":
        return

    logger.warning(
        "Channel %s (%s) is not in polling mode. Applying webhookUrl='' and incomingWebhook='yes'.",
        binding.channel_id,
        binding.project_slug,
    )

    save_settings = await asyncio.to_thread(
        green_api_client.set_settings,
        {
            "webhookUrl": "",
            "incomingWebhook": "yes",
            "outgoingWebhook": "yes",
            "stateWebhook": "yes",
        },
    )
    if not save_settings:
        raise RuntimeError("Green API did not confirm saving polling settings.")

    deadline = asyncio.get_running_loop().time() + POLLING_SETTINGS_APPLY_WAIT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(5)
        settings_payload = await asyncio.to_thread(green_api_client.get_settings)
        webhook_url = str(settings_payload.get("webhookUrl", "")).strip()
        incoming_webhook = str(settings_payload.get("incomingWebhook", "")).strip().lower()
        if not webhook_url and incoming_webhook == "yes":
            return

    raise RuntimeError("Green API settings did not switch into polling mode within the expected interval.")


async def run_channel_preflight(binding: ActiveChannelBinding, green_api_client: GreenApiClient) -> None:
    """Validate one channel before starting its long-running polling loop."""
    state = await asyncio.to_thread(green_api_client.get_state_instance)
    status = await asyncio.to_thread(green_api_client.get_status_instance)
    await ensure_polling_mode_settings(binding, green_api_client)

    if state != "authorized":
        raise RuntimeError(
            f"Channel {binding.channel_id} is not authorized in Green API. Current state: {state}."
        )

    if status != "online":
        logger.warning(
            "Channel %s statusInstance is '%s'. It will keep polling, but WhatsApp delivery may be degraded.",
            binding.channel_id,
            status,
        )


async def maybe_update_heartbeat(channel_id: str, last_tick: float) -> float:
    """Persist runtime heartbeats without writing on every poll iteration."""
    loop = asyncio.get_running_loop()
    now = loop.time()
    if now - last_tick < settings.runtime_channel_heartbeat_seconds:
        return last_tick

    registry = get_project_registry_service()
    registry.update_channel_runtime_state(
        channel_id,
        last_error="",
        heartbeat_at=utc_now_iso(),
    )
    return now


async def process_incoming_notification(
    binding: ActiveChannelBinding,
    green_api_client: GreenApiClient,
    notification_body: dict[str, Any],
) -> bool:
    """Process one incoming Green API notification and report whether it is safe to ack."""
    if notification_body.get("typeWebhook") != "incomingMessageReceived":
        return True

    sender_data = notification_body.get("senderData", {})
    chat_id = str(sender_data.get("chatId", "")).strip()
    if not chat_id:
        logger.warning("Skipping notification without chatId for channel %s.", binding.channel_id)
        return True

    incoming_message_id = str(notification_body.get("idMessage", "")).strip() or None
    text = extract_text_message(notification_body)
    if not text:
        return True

    event_payload = build_provider_event(binding, notification_body, text)
    result = await asyncio.to_thread(
        get_provider_gateway_service().dispatch_incoming_message,
        binding,
        event_payload,
    )

    if not result.messages:
        logger.info(
            "Provider for project %s returned no sync messages for chat %s. Waiting for async follow-up if needed.",
            binding.project_slug,
            chat_id,
        )
        return True

    await send_provider_result(binding, green_api_client, chat_id, result, incoming_message_id)
    return True


async def run_channel_loop(binding: ActiveChannelBinding) -> None:
    """Long-running polling loop for one project-bound WhatsApp channel."""
    registry = get_project_registry_service()
    green_api_client = build_green_api_client(binding)
    await run_channel_preflight(binding, green_api_client)

    logger.info(
        "Channel runtime started for %s / %s (%s).",
        binding.project_slug,
        binding.channel_name,
        binding.channel_id,
    )

    heartbeat_tick = 0.0
    while True:
        receipt_id: int | None = None
        should_delete_notification = False

        try:
            notification = await asyncio.to_thread(green_api_client.receive_notification)
            heartbeat_tick = await maybe_update_heartbeat(binding.channel_id, heartbeat_tick)

            if notification is None:
                await asyncio.sleep(settings.green_api_poll_interval_seconds)
                continue

            raw_receipt_id = notification.get("receiptId")
            receipt_id = int(raw_receipt_id) if raw_receipt_id is not None else None
            body = notification.get("body")
            if not isinstance(body, dict):
                logger.warning("Skipping notification with unexpected body format for channel %s.", binding.channel_id)
                should_delete_notification = True
                continue

            should_delete_notification = await process_incoming_notification(binding, green_api_client, body)
            registry.update_channel_runtime_state(
                binding.channel_id,
                last_error="",
                heartbeat_at=utc_now_iso(),
            )
        except ProviderGatewayError as exc:
            registry.update_channel_runtime_state(
                binding.channel_id,
                last_error=str(exc),
                heartbeat_at=utc_now_iso(),
            )
            logger.error("%s", exc)
            await asyncio.sleep(settings.green_api_poll_interval_seconds)
        except GreenApiServiceError as exc:
            registry.update_channel_runtime_state(
                binding.channel_id,
                last_error=str(exc),
                heartbeat_at=utc_now_iso(),
            )
            if is_custom_webhook_conflict_error(exc):
                await recover_from_custom_webhook_conflict(binding, green_api_client)
                continue

            logger.error("%s", exc)
            await asyncio.sleep(settings.green_api_poll_interval_seconds)
        except asyncio.CancelledError:
            logger.info("Channel runtime cancelled for %s (%s).", binding.project_slug, binding.channel_id)
            raise
        except Exception as exc:
            registry.update_channel_runtime_state(
                binding.channel_id,
                last_error=str(exc),
                heartbeat_at=utc_now_iso(),
            )
            logger.exception("Unexpected runtime error on channel %s", binding.channel_id)
            await asyncio.sleep(settings.green_api_poll_interval_seconds)
        finally:
            if receipt_id is not None and should_delete_notification:
                try:
                    deleted = await asyncio.to_thread(green_api_client.delete_notification, receipt_id)
                    if not deleted:
                        logger.warning("Green API did not confirm deletion for receiptId=%s", receipt_id)
                except GreenApiServiceError as exc:
                    registry.update_channel_runtime_state(
                        binding.channel_id,
                        last_error=str(exc),
                        heartbeat_at=utc_now_iso(),
                    )
                    logger.error("Failed to delete Green API notification %s: %s", receipt_id, exc)


async def run_runtime_coordinator() -> None:
    """Continuously reconcile desired channel bindings with running asyncio tasks."""
    registry = get_project_registry_service()
    tasks: dict[str, tuple[str, asyncio.Task[None]]] = {}

    while True:
        desired_bindings = {binding.runtime_key: binding for binding in registry.list_active_channel_bindings()}

        for runtime_key, (fingerprint, task) in list(tasks.items()):
            desired = desired_bindings.get(runtime_key)
            if desired is None or desired.fingerprint != fingerprint:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                tasks.pop(runtime_key, None)

        for runtime_key, binding in desired_bindings.items():
            running = tasks.get(runtime_key)
            if running is None:
                task = asyncio.create_task(run_channel_loop(binding), name=f"channel:{runtime_key}")
                tasks[runtime_key] = (binding.fingerprint, task)
                continue

            if running[1].done():
                with suppress(Exception):
                    running[1].result()
                task = asyncio.create_task(run_channel_loop(binding), name=f"channel:{runtime_key}")
                tasks[runtime_key] = (binding.fingerprint, task)

        await asyncio.sleep(settings.runtime_channels_refresh_seconds)


async def main() -> None:
    """Start the multi-tenant WhatsApp runtime worker."""
    logger.info("Starting WhatsApp transport runtime")
    await run_runtime_coordinator()


if __name__ == "__main__":
    asyncio.run(main())
