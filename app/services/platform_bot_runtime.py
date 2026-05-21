from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import requests

from app.services.bot_registry import BotRecord, get_bot_registry_service
from app.services.chat_presence import get_chat_presence_service
from app.services.chat_store import get_chat_store_service
from app.services.self_hosted_runtime_service import SelfHostedRuntimeServiceError, get_self_hosted_runtime_service
from app.utils.config import Settings, get_settings

logger = logging.getLogger(__name__)
MESSAGE_CHUNK_SIZE = 12000
CONTEXT_MESSAGE_LIMIT = 14
CONTEXT_CHAR_LIMIT = 4500
BOT_HTTP_RETRY_ATTEMPTS = 3
BOT_HTTP_RETRY_DELAY_SECONDS = 1.5


class PlatformBotRuntimeError(RuntimeError):
    """Raised when a configured platform bot cannot process one message."""


class DifyRequestError(PlatformBotRuntimeError):
    """Raised when a Dify HTTP request fails with a known status code."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def split_text_chunks(text: str, chunk_size: int = MESSAGE_CHUNK_SIZE) -> list[str]:
    """Split long text into smaller WhatsApp-safe chunks."""
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


def epoch_ms(value: Any) -> int:
    """Convert runtime timestamps or ISO strings to epoch milliseconds."""
    if value is None or value == "":
        return 0

    if isinstance(value, (int, float)):
        parsed = float(value)
        if parsed <= 0:
            return 0
        return int(parsed if parsed > 10_000_000_000 else parsed * 1000)

    text = str(value).strip()
    if not text:
        return 0

    try:
        parsed_number = float(text)
    except ValueError:
        parsed_number = 0
    if parsed_number > 0:
        return int(parsed_number if parsed_number > 10_000_000_000 else parsed_number * 1000)

    try:
        parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0

    if parsed_datetime.tzinfo is None:
        parsed_datetime = parsed_datetime.replace(tzinfo=UTC)
    return int(parsed_datetime.astimezone(UTC).timestamp() * 1000)


def message_sent_epoch_ms(message: dict[str, Any]) -> int:
    """Prefer the WhatsApp sent time, then fall back to runtime receive time."""
    return (
        epoch_ms(message.get("timestamp_ms"))
        or epoch_ms(message.get("timestamp"))
        or epoch_ms(message.get("runtime_received_at"))
    )


class PlatformBotRuntimeService:
    """Execute platform-managed bots directly from incoming runtime events."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._dify_app_cache: dict[str, dict[str, Any]] = {}

    def process_runtime_incoming_message(self, channel_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one inbound WhatsApp message through the active platform bot when configured."""
        message = payload.get("message", {}) if isinstance(payload.get("message"), dict) else {}
        is_self_chat = bool(message.get("self_chat", False))
        allow_bot_reply = bool(message.get("allow_bot_reply", False))
        if bool(message.get("from_me", False)) and not (is_self_chat and allow_bot_reply):
            return {"handled": False, "reason": "from_me"}

        bot_skip_reason = str(message.get("bot_skip_reason") or "").strip()
        if message.get("bot_eligible") is False:
            return {
                "handled": False,
                "reason": bot_skip_reason or "not_bot_eligible",
            }

        chat_id = str(message.get("chat_id", "")).strip()
        text = str(message.get("text", "")).strip()
        media = message.get("media", {}) if isinstance(message.get("media"), dict) else {}
        has_media = bool(message.get("has_media")) or bool(media.get("data") or media.get("url"))
        if not chat_id or (not text and not has_media):
            return {"handled": False, "reason": "empty_message"}

        bot = get_bot_registry_service().get_connected_bot_for_channel(channel_key)
        if bot is None:
            return {"handled": False, "reason": "no_connected_bot"}

        if not bot.enabled:
            return {"handled": False, "reason": "bot_disabled", "bot_id": bot.id}

        runtime_service = get_self_hosted_runtime_service()
        typing_started = self._start_typing(runtime_service, channel_key, chat_id)
        if typing_started:
            get_chat_presence_service().mark_typing(channel_key, chat_id)
        try:
            used_fallback_reply = False
            try:
                answer_payload = self._dispatch_connected_bot_message(bot, channel_key, payload)
            except PlatformBotRuntimeError as exc:
                if not self._settings.bot_failure_reply_enabled:
                    raise

                logger.warning(
                    "Connected bot failed; sending fallback reply",
                    extra={
                        "channel_key": channel_key,
                        "chat_id": chat_id,
                        "bot_id": bot.id,
                        "bot_slug": bot.slug,
                        "error": str(exc)[:500],
                    },
                )
                answer_payload = {
                    "answer": self._settings.bot_failure_reply_text,
                    "fallback_reply": True,
                    "admin_handoff": True,
                    "error": str(exc),
                }
            answer_text = self._extract_bot_answer(answer_payload).strip()
            if not answer_text:
                if not self._settings.bot_failure_reply_enabled:
                    return {"handled": False, "reason": "empty_answer", "bot_id": bot.id}
                answer_payload = {
                    "answer": self._settings.bot_failure_reply_text,
                    "fallback_reply": True,
                    "admin_handoff": True,
                    "error": "empty_answer",
                }
                answer_text = self._settings.bot_failure_reply_text

            used_fallback_reply = bool(answer_payload.get("fallback_reply") or answer_payload.get("admin_handoff"))

            logger.info(
                "Platform bot handling WhatsApp message",
                extra={
                    "channel_key": channel_key,
                    "chat_id": chat_id,
                    "bot_id": bot.id,
                    "self_chat": is_self_chat,
                    "input_preview": text[:160],
                },
            )

            self._wait_like_human_typing(answer_text)
            chat_store = get_chat_store_service()
            chat_store.mark_admin_handoff(channel_key, chat_id, used_fallback_reply)
            sent_message_ids: list[str] = []
            for chunk in split_text_chunks(answer_text):
                try:
                    runtime_response = runtime_service.send_message(channel_key, chat_id, chunk)
                except SelfHostedRuntimeServiceError as exc:
                    raise PlatformBotRuntimeError(str(exc)) from exc

                external_message_id = str(runtime_response.get("id_message", "")).strip()
                sent_message_ids.append(external_message_id)
                chat_store.store_outgoing_message(
                    channel_key=channel_key,
                    chat_id=chat_id,
                    text=chunk,
                    external_message_id=external_message_id,
                    source="system:admin_handoff" if used_fallback_reply else f"bot:{bot.slug}",
                    sender_name=bot.name,
                    status="sent",
                    raw_payload=answer_payload,
                )

            return {
                "handled": True,
                "bot_id": bot.id,
                "bot_name": bot.name,
                "sent_message_ids": sent_message_ids,
            }
        finally:
            if typing_started:
                self._stop_typing(runtime_service, channel_key, chat_id)
                get_chat_presence_service().mark_online(channel_key, chat_id)

    def _start_typing(self, runtime_service: Any, channel_key: str, chat_id: str) -> bool:
        if not self._settings.bot_typing_enabled:
            return False

        try:
            runtime_service.set_typing(channel_key, chat_id, True)
            return True
        except SelfHostedRuntimeServiceError as exc:
            logger.warning("Could not start WhatsApp typing indicator: %s", exc)
            return False

    def _stop_typing(self, runtime_service: Any, channel_key: str, chat_id: str) -> None:
        try:
            runtime_service.set_typing(channel_key, chat_id, False)
        except SelfHostedRuntimeServiceError as exc:
            logger.warning("Could not stop WhatsApp typing indicator: %s", exc)

    def _wait_like_human_typing(self, answer_text: str) -> None:
        if not self._settings.bot_typing_enabled:
            return

        chars_per_second = max(1.0, self._settings.bot_typing_chars_per_second)
        min_seconds = self._settings.bot_typing_min_seconds
        max_seconds = max(min_seconds, self._settings.bot_typing_max_seconds)
        delay_seconds = min(max_seconds, max(min_seconds, len(answer_text) / chars_per_second))
        time.sleep(delay_seconds)

    def _dispatch_connected_bot_message(
        self,
        bot: BotRecord,
        channel_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        engine_type = bot.engine_type.strip().lower()
        logger.info(
            "Dispatching WhatsApp message to connected bot",
            extra={
                "channel_key": channel_key,
                "bot_id": bot.id,
                "bot_slug": bot.slug,
                "engine_type": engine_type,
            },
        )

        if engine_type == "dify":
            return self._dispatch_dify_message(bot, channel_key, payload)

        if engine_type in {"webhook", "custom", "n8n"}:
            return self._dispatch_chat_bridge_message(
                bot,
                channel_key,
                payload,
                allow_generic_endpoint=True,
            )

        raise PlatformBotRuntimeError(
            f"Connected bot '{bot.name}' uses unsupported engine '{bot.engine_type}'."
        )

    def _dispatch_dify_message(self, bot: BotRecord, channel_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        message = payload.get("message", {}) if isinstance(payload.get("message"), dict) else {}
        chat_id = str(message.get("chat_id", "")).strip()
        text = str(message.get("text", "")).strip()
        external_message_id = str(message.get("external_message_id", "")).strip()

        if self._resolve_chat_bridge_config(bot) is not None:
            logger.info("Using configured chat bridge as primary route for Dify bot %s", bot.slug)
            return self._dispatch_chat_bridge_message(bot, channel_key, payload)

        endpoint_base = bot.endpoint_url.rstrip("/")
        try:
            app_info = self._get_dify_app_info(bot)
        except PlatformBotRuntimeError:
            if self._resolve_chat_bridge_config(bot) is not None:
                logger.info(
                    "Dify app info is unavailable; using chat bridge for bot %s",
                    bot.slug,
                )
                return self._dispatch_chat_bridge_message(bot, channel_key, payload)
            raise

        app_mode = str(app_info.get("mode", "")).strip().lower()
        request_payload: dict[str, Any]
        endpoint_url: str
        thread = get_bot_registry_service().get_thread(bot.id, channel_key, chat_id)
        user_id = f"{channel_key}:{chat_id}"
        context_text = self._build_chat_context(channel_key, chat_id, exclude_external_message_id=external_message_id)
        query_text = text
        if context_text and not (thread and thread.provider_conversation_id):
            query_text = self._build_contextual_query(text, context_text)
        input_variables = self._build_dify_inputs(bot, text, context_text)

        if app_mode in {"chat", "advanced-chat", "agent-chat"}:
            request_payload = {
                "inputs": input_variables,
                "query": query_text,
                "response_mode": "blocking",
                "user": user_id,
                "auto_generate_name": True,
            }
            if thread and thread.provider_conversation_id:
                request_payload["conversation_id"] = thread.provider_conversation_id
            endpoint_url = f"{endpoint_base}/chat-messages"
        elif app_mode == "completion":
            request_payload = {
                "inputs": self._build_dify_inputs(bot, query_text, context_text),
                "response_mode": "blocking",
                "user": user_id,
            }
            endpoint_url = f"{endpoint_base}/completion-messages"
        elif app_mode == "workflow":
            request_payload = {
                "inputs": self._build_dify_inputs(bot, query_text, context_text),
                "response_mode": "blocking",
                "user": user_id,
            }
            endpoint_url = f"{endpoint_base}/workflows/run"
        else:
            raise PlatformBotRuntimeError(f"Unsupported Dify app mode '{app_mode or 'unknown'}' for bot '{bot.name}'.")

        headers = self._build_dify_headers(bot)

        try:
            response_payload = self._post_json(
                endpoint_url,
                headers=headers,
                request_payload=request_payload,
                timeout_error=f"Dify bot timed out: {bot.name}",
                request_error_prefix=f"Dify bot request failed for '{bot.name}'.",
                invalid_json_error=f"Dify bot '{bot.name}' returned invalid JSON.",
                unexpected_payload_error=f"Dify bot '{bot.name}' returned an unexpected payload.",
            )
        except DifyRequestError as exc:
            if exc.status_code in {404, 405} and self._resolve_chat_bridge_config(bot) is not None:
                logger.info(
                    "Dify standard route returned %s; using chat bridge for bot %s",
                    exc.status_code,
                    bot.slug,
                )
                return self._dispatch_chat_bridge_message(bot, channel_key, payload)
            raise

        conversation_id = str(response_payload.get("conversation_id", "")).strip()
        if conversation_id:
            get_bot_registry_service().save_thread(bot.id, channel_key, chat_id, conversation_id)

        if app_mode == "workflow":
            response_payload["answer"] = self._extract_workflow_output_text(response_payload)

        return response_payload

    def _dispatch_chat_bridge_message(
        self,
        bot: BotRecord,
        channel_key: str,
        payload: dict[str, Any],
        *,
        allow_generic_endpoint: bool = False,
    ) -> dict[str, Any]:
        config = self._resolve_chat_bridge_config(bot, allow_generic_endpoint=allow_generic_endpoint)
        if config is None:
            raise PlatformBotRuntimeError(
                f"Connected bot '{bot.name}' has no callable chat endpoint configured."
            )

        message = payload.get("message", {}) if isinstance(payload.get("message"), dict) else {}
        chat_id = str(message.get("chat_id", "")).strip()
        text = str(message.get("text", "")).strip()
        external_message_id = str(message.get("external_message_id", "")).strip()
        thread = get_bot_registry_service().get_thread(bot.id, channel_key, chat_id)
        context_text = self._build_chat_context(channel_key, chat_id, exclude_external_message_id=external_message_id)
        query_text = text
        if context_text and not (thread and thread.provider_conversation_id):
            query_text = self._build_contextual_query(text, context_text)

        conversation_id = thread.provider_conversation_id if thread else ""
        request_payload = self._build_bridge_request_payload(
            config,
            payload,
            query_text=query_text,
            conversation_id=conversation_id,
            context_text=context_text,
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if config["authorization_header"]:
            headers["Authorization"] = config["authorization_header"]

        response_payload = self._post_json(
            config["endpoint_url"],
            headers=headers,
            request_payload=request_payload,
            timeout_error=f"Chat bridge bot timed out: {bot.name}",
            request_error_prefix=f"Chat bridge request failed for '{bot.name}'.",
            invalid_json_error=f"Chat bridge bot '{bot.name}' returned invalid JSON.",
            unexpected_payload_error=f"Chat bridge bot '{bot.name}' returned an unexpected payload.",
        )

        conversation_id = str(response_payload.get("conversation_id", "")).strip()
        if not conversation_id:
            conversation_id = str(response_payload.get("session_id", "")).strip()
        if conversation_id:
            get_bot_registry_service().save_thread(bot.id, channel_key, chat_id, conversation_id)

        if not str(response_payload.get("answer", "")).strip():
            response_payload["answer"] = self._extract_bot_answer(response_payload)
        return response_payload

    def diagnose_bot_route(self, bot: BotRecord, *, channel_key: str = "") -> dict[str, Any]:
        """Describe how this bot will be called without sending a user message."""
        engine_type = bot.engine_type.strip().lower()
        diagnostics: dict[str, Any] = {
            "ok": False,
            "bot_id": bot.id,
            "bot_slug": bot.slug,
            "engine_type": engine_type,
            "route_type": "",
            "endpoint_url": "",
            "fallback_endpoint_url": "",
            "has_authorization": bool(bot.authorization_header.strip()),
            "channel_key": channel_key or bot.linked_channel_key,
            "warnings": [],
            "reason": "",
        }

        if engine_type == "dify":
            bridge_config = self._resolve_chat_bridge_config(bot)
            if bridge_config is not None:
                diagnostics["fallback_endpoint_url"] = bridge_config["endpoint_url"]
                fallback_probe = self._probe_endpoint(bridge_config)
                diagnostics["fallback_probe"] = fallback_probe
                diagnostics.update(
                    {
                        "ok": bool(fallback_probe.get("reachable", False)),
                        "route_type": bridge_config["payload_style"],
                        "endpoint_url": bridge_config["endpoint_url"],
                        "has_authorization": bool(bridge_config["authorization_header"]),
                        "reason": "Using configured chat bridge as primary route for this Dify bot.",
                    }
                )
                if not fallback_probe.get("reachable", False):
                    diagnostics["reason"] = str(fallback_probe.get("reason") or diagnostics["reason"])
                try:
                    app_info = self._get_dify_app_info(bot)
                    diagnostics["dify_app_mode"] = str(app_info.get("mode", "")).strip().lower()
                    diagnostics["dify_app_name"] = str(app_info.get("name", "")).strip()
                except PlatformBotRuntimeError as exc:
                    diagnostics["warnings"].append(str(exc))
                return diagnostics

            try:
                app_info = self._get_dify_app_info(bot)
            except PlatformBotRuntimeError as exc:
                if bridge_config is not None:
                    diagnostics.update(
                        {
                            "ok": True,
                            "route_type": bridge_config["payload_style"],
                            "endpoint_url": bridge_config["endpoint_url"],
                            "has_authorization": bool(bridge_config["authorization_header"]),
                            "reason": "Dify info is not available, using configured chat bridge fallback.",
                        }
                    )
                    diagnostics["warnings"].append(str(exc))
                    if not diagnostics.get("fallback_probe", {}).get("reachable", False):
                        diagnostics["ok"] = False
                        diagnostics["reason"] = str(diagnostics["fallback_probe"].get("reason") or diagnostics["reason"])
                    return diagnostics

                diagnostics["reason"] = str(exc)
                return diagnostics

            app_mode = str(app_info.get("mode", "")).strip().lower()
            if app_mode in {"chat", "advanced-chat", "agent-chat"}:
                route_path = "/chat-messages"
                route_type = "dify_chat"
            elif app_mode == "completion":
                route_path = "/completion-messages"
                route_type = "dify_completion"
            elif app_mode == "workflow":
                route_path = "/workflows/run"
                route_type = "dify_workflow"
            else:
                diagnostics["reason"] = f"Unsupported Dify app mode '{app_mode or 'unknown'}'."
                return diagnostics

            diagnostics.update(
                {
                    "ok": True,
                    "route_type": route_type,
                    "endpoint_url": f"{bot.endpoint_url.rstrip('/')}{route_path}",
                    "dify_app_mode": app_mode,
                    "dify_app_name": str(app_info.get("name", "")).strip(),
                }
            )
            if bridge_config is not None:
                diagnostics["warnings"].append(
                    "Chat bridge fallback is configured and will be used if the standard Dify route returns 404/405."
                )
                if not diagnostics.get("fallback_probe", {}).get("reachable", False):
                    diagnostics["warnings"].append(
                        str(diagnostics["fallback_probe"].get("reason") or "Fallback endpoint is not reachable.")
                    )
            return diagnostics

        if engine_type in {"webhook", "custom", "n8n"}:
            config = self._resolve_chat_bridge_config(bot, allow_generic_endpoint=True)
            if config is None:
                diagnostics["reason"] = "No callable endpoint URL was found in Endpoint URL or API bindings."
                return diagnostics

            diagnostics.update(
                {
                    "ok": True,
                    "route_type": config["payload_style"],
                    "endpoint_url": config["endpoint_url"],
                    "has_authorization": bool(config["authorization_header"]),
                    "reason": "Ready to call configured endpoint.",
                }
            )
            endpoint_probe = self._probe_endpoint(config)
            diagnostics["endpoint_probe"] = endpoint_probe
            if not endpoint_probe.get("reachable", False):
                diagnostics["ok"] = False
                diagnostics["reason"] = str(endpoint_probe.get("reason") or "Endpoint is not reachable.")
            return diagnostics

        diagnostics["reason"] = f"Unsupported engine type '{bot.engine_type}'."
        return diagnostics

    def _post_json(
        self,
        endpoint_url: str,
        *,
        headers: dict[str, str],
        request_payload: dict[str, Any],
        timeout_error: str,
        request_error_prefix: str,
        invalid_json_error: str,
        unexpected_payload_error: str,
    ) -> dict[str, Any]:
        response: requests.Response | None = None
        for attempt in range(1, BOT_HTTP_RETRY_ATTEMPTS + 1):
            try:
                response = requests.post(
                    endpoint_url,
                    headers=headers,
                    json=request_payload,
                    timeout=(self._settings.connect_timeout_seconds, self._settings.request_timeout_seconds),
                )
                if (
                    attempt < BOT_HTTP_RETRY_ATTEMPTS
                    and self._is_retryable_bot_response(response)
                ):
                    logger.warning(
                        "Bot endpoint returned retryable status %s on attempt %s/%s",
                        response.status_code,
                        attempt,
                        BOT_HTTP_RETRY_ATTEMPTS,
                    )
                    time.sleep(BOT_HTTP_RETRY_DELAY_SECONDS * attempt)
                    continue
                response.raise_for_status()
                break
            except requests.Timeout as exc:
                if attempt < BOT_HTTP_RETRY_ATTEMPTS:
                    logger.warning(
                        "Bot endpoint timed out on attempt %s/%s",
                        attempt,
                        BOT_HTTP_RETRY_ATTEMPTS,
                    )
                    time.sleep(BOT_HTTP_RETRY_DELAY_SECONDS * attempt)
                    continue
                raise PlatformBotRuntimeError(timeout_error) from exc
            except requests.RequestException as exc:
                response = getattr(exc, "response", None)
                if (
                    attempt < BOT_HTTP_RETRY_ATTEMPTS
                    and response is not None
                    and self._is_retryable_bot_response(response)
                ):
                    logger.warning(
                        "Bot endpoint failed with retryable status %s on attempt %s/%s",
                        response.status_code,
                        attempt,
                        BOT_HTTP_RETRY_ATTEMPTS,
                    )
                    time.sleep(BOT_HTTP_RETRY_DELAY_SECONDS * attempt)
                    continue

                status_code = getattr(response, "status_code", None)
                detail = self._response_text(response)
                message_text = request_error_prefix
                if detail:
                    message_text = f"{message_text} Response: {detail}"
                raise DifyRequestError(message_text, status_code=status_code) from exc

        if response is None:
            raise PlatformBotRuntimeError(request_error_prefix)

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise PlatformBotRuntimeError(invalid_json_error) from exc

        if not isinstance(response_payload, dict):
            raise PlatformBotRuntimeError(unexpected_payload_error)
        return response_payload

    def _is_retryable_bot_response(self, response: requests.Response) -> bool:
        status_code = response.status_code
        if status_code in {429, 500, 502, 503, 504}:
            return True

        text = self._response_text(response).lower()
        retry_markers = (
            "503 unavailable",
            "high demand",
            "temporarily unavailable",
            "rate limit",
            "timeout",
        )
        return any(marker in text for marker in retry_markers)

    def _response_text(self, response: requests.Response | None) -> str:
        if response is None:
            return ""
        try:
            return response.text.strip()
        except Exception:
            return ""

    def _probe_endpoint(self, config: dict[str, str]) -> dict[str, Any]:
        endpoint_url = config.get("endpoint_url", "")
        headers = {"Accept": "application/json"}
        if config.get("authorization_header"):
            headers["Authorization"] = config["authorization_header"]

        try:
            response = requests.request(
                "HEAD",
                endpoint_url,
                headers=headers,
                timeout=(self._settings.connect_timeout_seconds, min(10.0, self._settings.request_timeout_seconds)),
                allow_redirects=True,
            )
        except requests.Timeout:
            return {
                "reachable": False,
                "status_code": 0,
                "reason": "Endpoint probe timed out.",
            }
        except requests.RequestException as exc:
            return {
                "reachable": False,
                "status_code": 0,
                "reason": f"Endpoint probe failed: {exc}",
            }

        status_code = response.status_code
        server_header = str(response.headers.get("server", "")).lower()
        reason = f"Endpoint probe returned HTTP {status_code}."
        if status_code in {401, 403, 404, 405, 422}:
            return {
                "reachable": True,
                "status_code": status_code,
                "reason": reason,
            }

        if status_code >= 500 or ("cloudflare" in server_header and status_code >= 520):
            return {
                "reachable": False,
                "status_code": status_code,
                "reason": reason,
            }

        return {
            "reachable": 200 <= status_code < 500,
            "status_code": status_code,
            "reason": reason,
        }

    def _build_bridge_request_payload(
        self,
        config: dict[str, str],
        runtime_payload: dict[str, Any],
        *,
        query_text: str,
        conversation_id: str,
        context_text: str,
    ) -> dict[str, Any]:
        if config.get("payload_style") == "chat_bridge":
            message = runtime_payload.get("message", {}) if isinstance(runtime_payload.get("message"), dict) else {}
            return {
                "query": query_text,
                "conversation_id": conversation_id,
                "message": message,
                "media": message.get("media") if isinstance(message.get("media"), dict) else None,
            }

        message = runtime_payload.get("message", {}) if isinstance(runtime_payload.get("message"), dict) else {}
        return {
            "query": query_text,
            "conversation_id": conversation_id,
            "channel_key": str(runtime_payload.get("channel_key", "")).strip(),
            "channel_name": str(runtime_payload.get("channel_name", "")).strip(),
            "chat_id": str(message.get("chat_id", "")).strip(),
            "message": message,
            "context": context_text,
        }

    def _resolve_chat_bridge_config(
        self,
        bot: BotRecord,
        *,
        allow_generic_endpoint: bool = False,
    ) -> dict[str, str] | None:
        binding_authorization_header = self._extract_binding_authorization_header(bot)
        for candidate in self._iter_endpoint_candidates(bot):
            endpoint_url = candidate["endpoint_url"]
            looks_like_chat = self._looks_like_chat_bridge_url(endpoint_url)
            if not looks_like_chat and not allow_generic_endpoint:
                continue

            authorization_header = (
                candidate["authorization_header"]
                or binding_authorization_header
                or bot.authorization_header.strip()
            )
            if authorization_header and not authorization_header.lower().startswith("bearer "):
                authorization_header = f"Bearer {authorization_header}"

            return {
                "endpoint_url": endpoint_url.rstrip("/"),
                "authorization_header": authorization_header,
                "payload_style": "chat_bridge"
                if self._uses_compact_chat_payload(bot, endpoint_url)
                else "webhook",
                "source": candidate["source"],
            }

        return None

    def _iter_endpoint_candidates(self, bot: BotRecord) -> list[dict[str, str]]:
        candidates: list[dict[str, str]] = []

        if bot.endpoint_url.strip().startswith(("http://", "https://")):
            candidates.append(
                {
                    "endpoint_url": bot.endpoint_url.strip().rstrip("/"),
                    "authorization_header": bot.authorization_header.strip(),
                    "source": "endpoint_url",
                }
            )

        for index, binding in enumerate(bot.api_bindings):
            combined = "\n".join(
                part for part in (binding.endpoint_url, binding.name, binding.notes) if part
            )
            endpoint_url = binding.endpoint_url.strip() or self._extract_first_url(combined)
            if not endpoint_url:
                continue

            candidates.append(
                {
                    "endpoint_url": endpoint_url.rstrip("/"),
                    "authorization_header": self._extract_authorization_header(combined),
                    "source": f"api_binding:{index}",
                }
            )

        return candidates

    def _extract_binding_authorization_header(self, bot: BotRecord) -> str:
        for binding in bot.api_bindings:
            combined = "\n".join(
                part for part in (binding.endpoint_url, binding.name, binding.notes) if part
            )
            authorization_header = self._extract_authorization_header(combined)
            if authorization_header:
                return authorization_header
        return ""

    def _extract_first_url(self, value: str) -> str:
        match = re.search(r"https?://[^\s|]+", value)
        return match.group(0).rstrip(".,);") if match else ""

    def _extract_authorization_header(self, value: str) -> str:
        auth_match = re.search(r"authorization\s*:\s*([^\r\n|]+)", value, flags=re.IGNORECASE)
        if auth_match:
            return auth_match.group(1).strip()

        bearer_match = re.search(r"\bBearer\s+[^\r\n|]+", value, flags=re.IGNORECASE)
        return bearer_match.group(0).strip() if bearer_match else ""

    def _looks_like_chat_bridge_url(self, value: str) -> bool:
        normalized = value.strip().lower().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            return False
        return any(marker in normalized for marker in ("/api/chat", "/chat", "/webhook", "/callback"))

    def _uses_compact_chat_payload(self, bot: BotRecord, endpoint_url: str) -> bool:
        normalized = endpoint_url.strip().lower().rstrip("/")
        if any(marker in normalized for marker in ("/api/chat", "/chat")):
            return True
        return bot.engine_type.strip().lower() == "dify"

    def _build_dify_headers(self, bot: BotRecord) -> dict[str, str]:
        authorization_header = bot.authorization_header.strip()
        if authorization_header and not authorization_header.lower().startswith("bearer "):
            authorization_header = f"Bearer {authorization_header}"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if authorization_header:
            headers["Authorization"] = authorization_header
        return headers

    def _build_chat_context(
        self,
        channel_key: str,
        chat_id: str,
        *,
        exclude_external_message_id: str = "",
    ) -> str:
        """Render recent local inbox messages for bots that need WhatsApp-side memory."""
        messages = get_chat_store_service().list_messages(channel_key, chat_id, limit=CONTEXT_MESSAGE_LIMIT)
        lines: list[str] = []
        for item in messages:
            if exclude_external_message_id and item.external_message_id == exclude_external_message_id:
                continue

            text = " ".join((item.text or "").split())
            if not text:
                continue

            if item.direction == "inbound":
                speaker = item.sender_name or "Customer"
            elif item.source.startswith("bot:"):
                speaker = item.sender_name or "Bot"
            else:
                speaker = item.sender_name or "Operator"

            lines.append(f"{item.created_at} | {speaker}: {text}")

        context = "\n".join(lines).strip()
        if len(context) <= CONTEXT_CHAR_LIMIT:
            return context

        return context[-CONTEXT_CHAR_LIMIT:].lstrip()

    def _build_contextual_query(self, text: str, context_text: str) -> str:
        """Keep the current user message clear while giving Dify recent WhatsApp context."""
        return (
            "You are replying in an existing WhatsApp conversation.\n"
            "Use the recent WhatsApp context only to understand the dialogue, but answer only the latest user message.\n"
            "Do not answer old messages from the context.\n"
            "Do not greet again, do not introduce yourself again, and do not restart the conversation if the context is not empty.\n"
            "If template variables such as {{role}} or {{company}} are unknown, do not print the placeholders.\n"
            "Continue naturally in the same language and tone as the latest user message.\n\n"
            "Recent WhatsApp context:\n"
            f"{context_text}\n\n"
            "Latest user message to answer now:\n"
            f"{text}"
        )

    def _get_dify_app_info(self, bot: BotRecord) -> dict[str, Any]:
        cache_key = f"{bot.endpoint_url}|{bot.authorization_header}"
        cached = self._dify_app_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            response = requests.get(
                f"{bot.endpoint_url.rstrip('/')}/info",
                headers=self._build_dify_headers(bot),
                timeout=(self._settings.connect_timeout_seconds, self._settings.request_timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PlatformBotRuntimeError(f"Failed to read Dify app info for '{bot.name}'.") from exc
        except ValueError as exc:
            raise PlatformBotRuntimeError(f"Dify app info for '{bot.name}' returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise PlatformBotRuntimeError(f"Dify app info for '{bot.name}' returned an unexpected payload.")

        self._dify_app_cache[cache_key] = payload
        return payload

    def _get_dify_app_parameters(self, bot: BotRecord) -> dict[str, Any]:
        cache_key = f"{bot.endpoint_url}|{bot.authorization_header}|parameters"
        cached = self._dify_app_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            response = requests.get(
                f"{bot.endpoint_url.rstrip('/')}/parameters",
                headers=self._build_dify_headers(bot),
                timeout=(self._settings.connect_timeout_seconds, self._settings.request_timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PlatformBotRuntimeError(f"Failed to read Dify app parameters for '{bot.name}'.") from exc
        except ValueError as exc:
            raise PlatformBotRuntimeError(f"Dify app parameters for '{bot.name}' returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise PlatformBotRuntimeError(f"Dify app parameters for '{bot.name}' returned an unexpected payload.")

        self._dify_app_cache[cache_key] = payload
        return payload

    def _build_dify_inputs(self, bot: BotRecord, text: str, context_text: str = "") -> dict[str, Any]:
        parameters = self._get_dify_app_parameters(bot)
        user_input_form = parameters.get("user_input_form")
        if not isinstance(user_input_form, list) or not user_input_form:
            return {"query": text}

        variables: list[str] = []
        for item in user_input_form:
            if not isinstance(item, dict):
                continue
            for field_config in item.values():
                if not isinstance(field_config, dict):
                    continue
                variable = str(field_config.get("variable", "")).strip()
                if variable and variable not in variables:
                    variables.append(variable)

        if not variables:
            return {"query": text}

        preferred_text_variables = ("query", "question", "message", "text", "input", "prompt")
        chosen_variable = next(
            (variable for variable in variables if variable.casefold() in preferred_text_variables),
            variables[0],
        )
        context_variables = {
            "context",
            "conversation_context",
            "chat_context",
            "history",
            "chat_history",
            "conversation_history",
            "whatsapp_context",
            "whatsapp_history",
        }

        configured_variables = {
            variable.key.casefold(): variable.default_value
            for variable in bot.variables
            if variable.default_value and variable.default_value != "configured-server-side"
        }

        inputs: dict[str, Any] = {chosen_variable: text}
        if context_text:
            for variable in variables:
                normalized = variable.casefold()
                if variable == chosen_variable:
                    continue
                if normalized in context_variables or "context" in normalized or "history" in normalized:
                    inputs[variable] = context_text
                    continue
                if normalized in configured_variables:
                    inputs[variable] = configured_variables[normalized]

        if not context_text:
            for variable in variables:
                normalized = variable.casefold()
                if variable == chosen_variable or variable in inputs:
                    continue
                if normalized in configured_variables:
                    inputs[variable] = configured_variables[normalized]

        return inputs

    def _extract_workflow_output_text(self, payload: dict[str, Any]) -> str:
        data = payload.get("data")
        if not isinstance(data, dict):
            return ""

        outputs = data.get("outputs")
        if not isinstance(outputs, dict):
            return ""

        for key in ("text", "result", "answer", "output"):
            candidate = outputs.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        for candidate in outputs.values():
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        return ""

    def _extract_bot_answer(self, payload: dict[str, Any]) -> str:
        for key in ("answer", "text", "result", "output", "response", "message"):
            answer = payload.get(key)
            if isinstance(answer, str) and answer.strip():
                return answer.strip()

        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("answer", "text", "result", "output", "response", "message"):
                candidate = data.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()

            outputs = data.get("outputs")
            if isinstance(outputs, dict):
                for key in ("answer", "text", "result", "output", "response", "message"):
                    candidate = outputs.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
                for candidate in outputs.values():
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()

        return ""


@lru_cache
def get_platform_bot_runtime_service() -> PlatformBotRuntimeService:
    """Return the shared runtime dispatcher for platform-managed bots."""
    return PlatformBotRuntimeService(settings=get_settings())
