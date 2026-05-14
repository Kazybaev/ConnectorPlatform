from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)


class GreenApiServiceError(RuntimeError):
    """Raised when a Green API request cannot be completed safely."""


@dataclass(slots=True)
class GreenApiCredentials:
    """Per-channel Green API access credentials."""

    api_url: str
    id_instance: str
    api_token: str


class GreenApiClient:
    """HTTP client for one specific Green API WhatsApp instance."""

    def __init__(
        self,
        credentials: GreenApiCredentials,
        *,
        connect_timeout_seconds: float,
        request_timeout_seconds: float,
        receive_timeout_seconds: int,
    ) -> None:
        self._credentials = credentials
        self._connect_timeout_seconds = connect_timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._receive_timeout_seconds = receive_timeout_seconds

    def receive_notification(self) -> dict[str, Any] | None:
        """Receive one notification from the Green API queue."""
        response = self._request(
            method="GET",
            path=f"/waInstance{self._credentials.id_instance}/receiveNotification/{self._credentials.api_token}",
            params={"receiveTimeout": self._receive_timeout_seconds},
        )

        if not response.content.strip():
            return None

        try:
            payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON notification payload.") from exc

        if payload is None:
            return None

        if not isinstance(payload, dict):
            raise GreenApiServiceError("Green API returned an unexpected notification payload.")

        return payload

    def delete_notification(self, receipt_id: int) -> bool:
        """Confirm notification processing and remove it from the queue."""
        response = self._request(
            method="DELETE",
            path=(
                f"/waInstance{self._credentials.id_instance}/deleteNotification/"
                f"{self._credentials.api_token}/{receipt_id}"
            ),
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON deleteNotification payload.") from exc

        result = payload.get("result")
        if not isinstance(result, bool):
            raise GreenApiServiceError("Green API returned an unexpected deleteNotification response.")

        return result

    def get_settings(self) -> dict[str, Any]:
        """Fetch current Green API instance settings."""
        response = self._request(
            method="GET",
            path=f"/waInstance{self._credentials.id_instance}/getSettings/{self._credentials.api_token}",
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON getSettings payload.") from exc

        if not isinstance(payload, dict):
            raise GreenApiServiceError("Green API returned an unexpected getSettings response.")

        return payload

    def get_wa_settings(self) -> dict[str, Any]:
        """Fetch current WhatsApp account information for the instance."""
        response = self._request(
            method="GET",
            path=f"/waInstance{self._credentials.id_instance}/getWaSettings/{self._credentials.api_token}",
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON getWaSettings payload.") from exc

        if not isinstance(payload, dict):
            raise GreenApiServiceError("Green API returned an unexpected getWaSettings response.")

        return payload

    def get_state_instance(self) -> str:
        """Fetch the current Green API authorization state."""
        response = self._request(
            method="GET",
            path=f"/waInstance{self._credentials.id_instance}/getStateInstance/{self._credentials.api_token}",
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON getStateInstance payload.") from exc

        state = payload.get("stateInstance") if isinstance(payload, dict) else None
        if not isinstance(state, str) or not state.strip():
            raise GreenApiServiceError("Green API returned an unexpected getStateInstance response.")

        return state.strip()

    def get_status_instance(self) -> str:
        """Fetch the current Green API socket connection status."""
        response = self._request(
            method="GET",
            path=f"/waInstance{self._credentials.id_instance}/getStatusInstance/{self._credentials.api_token}",
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON getStatusInstance payload.") from exc

        status = payload.get("statusInstance") if isinstance(payload, dict) else None
        if not isinstance(status, str) or not status.strip():
            raise GreenApiServiceError("Green API returned an unexpected getStatusInstance response.")

        return status.strip()

    def logout_instance(self) -> bool:
        """Log out the linked WhatsApp account from the Green API instance."""
        response = self._request(
            method="GET",
            path=f"/waInstance{self._credentials.id_instance}/logout/{self._credentials.api_token}",
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON logout payload.") from exc

        result = payload.get("isLogout") if isinstance(payload, dict) else None
        if not isinstance(result, bool):
            raise GreenApiServiceError("Green API returned an unexpected logout response.")

        return result

    def reboot_instance(self) -> bool:
        """Restart the Green API instance process."""
        response = self._request(
            method="GET",
            path=f"/waInstance{self._credentials.id_instance}/reboot/{self._credentials.api_token}",
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON reboot payload.") from exc

        result = payload.get("isReboot") if isinstance(payload, dict) else None
        if not isinstance(result, bool):
            raise GreenApiServiceError("Green API returned an unexpected reboot response.")

        return result

    def get_qr_code(self) -> dict[str, str]:
        """Fetch the current QR code state for instance authorization."""
        response = self._request(
            method="GET",
            path=f"/waInstance{self._credentials.id_instance}/qr/{self._credentials.api_token}",
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON qr payload.") from exc

        qr_type = payload.get("type") if isinstance(payload, dict) else None
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(qr_type, str) or not isinstance(message, str):
            raise GreenApiServiceError("Green API returned an unexpected qr response.")

        return {
            "type": qr_type.strip(),
            "message": message,
        }

    def get_contact_info(self, chat_id: str) -> dict[str, Any]:
        """Fetch public profile details for a WhatsApp contact."""
        response = self._request(
            method="POST",
            path=f"/waInstance{self._credentials.id_instance}/getContactInfo/{self._credentials.api_token}",
            json={"chatId": chat_id},
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON getContactInfo payload.") from exc

        if not isinstance(payload, dict):
            raise GreenApiServiceError("Green API returned an unexpected getContactInfo response.")

        return payload

    def set_settings(self, payload: dict[str, Any]) -> bool:
        """Update Green API instance settings."""
        response = self._request(
            method="POST",
            path=f"/waInstance{self._credentials.id_instance}/setSettings/{self._credentials.api_token}",
            json=payload,
        )

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON setSettings payload.") from exc

        save_settings = response_payload.get("saveSettings") if isinstance(response_payload, dict) else None
        if not isinstance(save_settings, bool):
            raise GreenApiServiceError("Green API returned an unexpected setSettings response.")

        return save_settings

    def send_message(
        self,
        chat_id: str,
        message: str,
        quoted_message_id: str | None = None,
    ) -> str:
        """Send a text reply to the specified WhatsApp chat."""
        payload: dict[str, Any] = {
            "chatId": chat_id,
            "message": message,
        }
        if quoted_message_id:
            payload["quotedMessageId"] = quoted_message_id

        response = self._request(
            method="POST",
            path=f"/waInstance{self._credentials.id_instance}/sendMessage/{self._credentials.api_token}",
            json=payload,
        )

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise GreenApiServiceError("Green API returned a non-JSON sendMessage payload.") from exc

        message_id = response_payload.get("idMessage")
        if not isinstance(message_id, str) or not message_id.strip():
            raise GreenApiServiceError("Green API returned an unexpected sendMessage response.")

        return message_id.strip()

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> requests.Response:
        """Execute a Green API request with consistent error handling."""
        url = f"{self._credentials.api_url}{path}"

        try:
            response = requests.request(
                method=method,
                url=url,
                params=params,
                json=json,
                timeout=(self._connect_timeout_seconds, self._request_timeout_seconds),
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise GreenApiServiceError(f"Green API request timed out: {method} {path}") from exc
        except requests.RequestException as exc:
            details = ""
            if getattr(exc, "response", None) is not None:
                try:
                    details = exc.response.text.strip()
                except Exception:
                    details = ""

            message = f"Green API request failed: {method} {path}"
            if details:
                message = f"{message}. Response: {details}"

            raise GreenApiServiceError(message) from exc

        return response
