from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import requests

from app.models.schemas import OutboundTextMessage, ProviderDispatchResult
from app.services.project_registry import ActiveChannelBinding
from app.utils.config import Settings, get_settings

logger = logging.getLogger(__name__)


class ProviderGatewayError(RuntimeError):
    """Raised when an external AI provider cannot be reached or normalized."""


class ProviderGatewayService:
    """Dispatch WhatsApp events to external AI webhooks owned by client projects."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def dispatch_incoming_message(
        self,
        binding: ActiveChannelBinding,
        event_payload: dict[str, Any],
    ) -> ProviderDispatchResult:
        """Send an incoming WhatsApp event to a client AI webhook and normalize the response."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain;q=0.9",
            "User-Agent": f"{self._settings.app_name}/provider-gateway",
            "X-Community-Project-Id": binding.project_id,
            "X-Community-Project-Slug": binding.project_slug,
            "X-Community-Channel-Id": binding.channel_id,
        }

        if binding.provider_authorization_header:
            headers["Authorization"] = binding.provider_authorization_header

        headers.update(binding.provider_extra_headers)

        try:
            response = requests.post(
                binding.provider_url,
                headers=headers,
                json=event_payload,
                timeout=(
                    self._settings.connect_timeout_seconds,
                    self._settings.request_timeout_seconds,
                ),
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise ProviderGatewayError(
                f"Provider webhook timed out for project '{binding.project_slug}' ({binding.provider_url})."
            ) from exc
        except requests.RequestException as exc:
            details = ""
            if getattr(exc, "response", None) is not None:
                try:
                    details = exc.response.text.strip()
                except Exception:
                    details = ""

            message = f"Provider webhook failed for project '{binding.project_slug}' ({binding.provider_url})."
            if details:
                message = f"{message} Response: {details}"
            raise ProviderGatewayError(message) from exc

        return self._normalize_provider_response(response)

    def _normalize_provider_response(self, response: requests.Response) -> ProviderDispatchResult:
        """Accept a few ergonomic response shapes while keeping the wire contract strict."""
        content_type = (response.headers.get("Content-Type") or "").casefold()

        if "application/json" not in content_type:
            text = response.text.strip()
            if not text:
                return ProviderDispatchResult(messages=[])
            return ProviderDispatchResult(messages=[OutboundTextMessage(text=text)])

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderGatewayError("Provider webhook returned invalid JSON.") from exc

        if payload is None:
            return ProviderDispatchResult(messages=[])

        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return ProviderDispatchResult(messages=[])
            return ProviderDispatchResult(messages=[OutboundTextMessage(text=text)])

        if not isinstance(payload, dict):
            raise ProviderGatewayError("Provider webhook returned an unsupported JSON shape.")

        if isinstance(payload.get("messages"), list):
            return ProviderDispatchResult.model_validate(payload)

        for key in ("answer", "text", "response", "message"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return ProviderDispatchResult(messages=[OutboundTextMessage(text=candidate.strip())], metadata=payload)

        return ProviderDispatchResult(messages=[], metadata=payload)


@lru_cache
def get_provider_gateway_service() -> ProviderGatewayService:
    """Reuse a single provider gateway configuration."""
    return ProviderGatewayService(settings=get_settings())
