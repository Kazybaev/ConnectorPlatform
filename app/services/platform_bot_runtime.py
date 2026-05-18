from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import requests

from app.services.bot_registry import BotRecord, get_bot_registry_service
from app.services.chat_store import get_chat_store_service
from app.services.self_hosted_runtime_service import SelfHostedRuntimeServiceError, get_self_hosted_runtime_service
from app.utils.config import Settings, get_settings

logger = logging.getLogger(__name__)
MESSAGE_CHUNK_SIZE = 12000
CONTEXT_MESSAGE_LIMIT = 14
CONTEXT_CHAR_LIMIT = 4500


class PlatformBotRuntimeError(RuntimeError):
    """Raised when a configured platform bot cannot process one message."""


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
        if message.get("bot_eligible") is False:
            return {
                "handled": False,
                "reason": str(message.get("bot_skip_reason") or "not_bot_eligible"),
            }

        is_self_chat = bool(message.get("self_chat", False))
        allow_bot_reply = bool(message.get("allow_bot_reply", False))
        if bool(message.get("from_me", False)) and not (is_self_chat and allow_bot_reply):
            return {"handled": False, "reason": "from_me"}

        chat_id = str(message.get("chat_id", "")).strip()
        text = str(message.get("text", "")).strip()
        if not chat_id or not text:
            return {"handled": False, "reason": "empty_message"}

        message_type = str(message.get("message_type", "text")).strip().lower() or "text"
        if message_type not in {"text", "chat"}:
            return {"handled": False, "reason": f"unsupported_message_type:{message_type}"}

        bot = get_bot_registry_service().get_connected_bot_for_channel(channel_key)
        if bot is None:
            return {"handled": False, "reason": "no_connected_bot"}

        if not bot.enabled:
            return {"handled": False, "reason": "bot_disabled", "bot_id": bot.id}

        bot_activated_at = get_bot_registry_service().get_connection_activated_at(bot.id, channel_key)
        activation_cutoff_ms = max(
            epoch_ms(message.get("runtime_activated_at")),
            epoch_ms(bot_activated_at),
        )
        sent_at_ms = message_sent_epoch_ms(message)
        if activation_cutoff_ms and sent_at_ms and sent_at_ms < activation_cutoff_ms:
            return {
                "handled": False,
                "reason": "before_bot_activation",
                "bot_id": bot.id,
                "bot_activated_at": bot_activated_at,
            }

        if bot.engine_type != "dify":
            raise PlatformBotRuntimeError(
                f"Connected bot '{bot.name}' uses unsupported engine '{bot.engine_type}'."
            )

        answer_payload = self._dispatch_dify_message(bot, channel_key, payload)
        answer_text = self._extract_bot_answer(answer_payload).strip()
        if not answer_text:
            return {"handled": False, "reason": "empty_answer", "bot_id": bot.id}

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

        runtime_service = get_self_hosted_runtime_service()
        chat_store = get_chat_store_service()
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
                source=f"bot:{bot.slug}",
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

    def _dispatch_dify_message(self, bot: BotRecord, channel_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        message = payload.get("message", {}) if isinstance(payload.get("message"), dict) else {}
        chat_id = str(message.get("chat_id", "")).strip()
        text = str(message.get("text", "")).strip()
        external_message_id = str(message.get("external_message_id", "")).strip()

        endpoint_base = bot.endpoint_url.rstrip("/")
        app_info = self._get_dify_app_info(bot)
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
            response = requests.post(
                endpoint_url,
                headers=headers,
                json=request_payload,
                timeout=(self._settings.connect_timeout_seconds, self._settings.request_timeout_seconds),
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise PlatformBotRuntimeError(f"Dify bot timed out: {bot.name}") from exc
        except requests.RequestException as exc:
            detail = ""
            if getattr(exc, "response", None) is not None:
                try:
                    detail = exc.response.text.strip()
                except Exception:
                    detail = ""
            message_text = f"Dify bot request failed for '{bot.name}'."
            if detail:
                message_text = f"{message_text} Response: {detail}"
            raise PlatformBotRuntimeError(message_text) from exc

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise PlatformBotRuntimeError(f"Dify bot '{bot.name}' returned invalid JSON.") from exc

        if not isinstance(response_payload, dict):
            raise PlatformBotRuntimeError(f"Dify bot '{bot.name}' returned an unexpected payload.")

        conversation_id = str(response_payload.get("conversation_id", "")).strip()
        if conversation_id:
            get_bot_registry_service().save_thread(bot.id, channel_key, chat_id, conversation_id)

        if app_mode == "workflow":
            response_payload["answer"] = self._extract_workflow_output_text(response_payload)

        return response_payload

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
        answer = payload.get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer.strip()

        data = payload.get("data")
        if isinstance(data, dict):
            outputs = data.get("outputs")
            if isinstance(outputs, dict):
                for candidate in outputs.values():
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()

        return ""


@lru_cache
def get_platform_bot_runtime_service() -> PlatformBotRuntimeService:
    """Return the shared runtime dispatcher for platform-managed bots."""
    return PlatformBotRuntimeService(settings=get_settings())
