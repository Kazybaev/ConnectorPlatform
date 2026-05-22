from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import requests

from app.utils.config import ROOT_DIR, get_settings

RUNTIME_STARTUP_WAIT_SECONDS = 30
RUNTIME_START_RETRY_INTERVAL_SECONDS = 1
_runtime_start_lock = threading.Lock()
_last_start_attempt_monotonic = 0.0


class SelfHostedRuntimeServiceError(RuntimeError):
    """Raised when the local self-hosted WhatsApp runtime is unavailable."""


class SelfHostedRuntimeService:
    """HTTP client plus autostart helper for the local Node WhatsApp runtime."""

    def __init__(self) -> None:
        self._runtime_dir = ROOT_DIR / "runtime"
        self._runtime_entrypoint = self._runtime_dir / "server.js"
        self._runtime_log_path = ROOT_DIR / "data" / "runtime" / "runtime.log"

    @property
    def _base_url(self) -> str:
        return get_settings().runtime_service_base_url

    def _headers(self) -> dict[str, str]:
        token = get_settings().runtime_service_token
        return {"X-Runtime-Token": token} if token else {}

    def ensure_runtime_started(self) -> None:
        """Start the Node runtime automatically when the health endpoint is down."""
        if self.is_healthy():
            return

        settings = get_settings()
        if not settings.runtime_service_autostart:
            raise SelfHostedRuntimeServiceError("Local WhatsApp runtime is not running.")

        global _last_start_attempt_monotonic
        with _runtime_start_lock:
            if self.is_healthy():
                return

            now = time.monotonic()
            if now - _last_start_attempt_monotonic < 3:
                self._wait_until_healthy()
                return

            _last_start_attempt_monotonic = now
            self._spawn_runtime_process()
            self._wait_until_healthy()

    def is_healthy(self) -> bool:
        """Return True when the local runtime answers its health check."""
        try:
            response = requests.get(
                f"{self._base_url}/health",
                headers=self._headers(),
                timeout=3,
            )
        except requests.RequestException:
            return False

        return response.ok

    def ensure_channel(self, channel_key: str, display_name: str) -> dict[str, Any]:
        """Create or wake up one runtime session."""
        self.ensure_runtime_started()
        return self._request(
            method="PUT",
            path=f"/api/v1/channels/{channel_key}",
            json={"display_name": display_name},
            timeout_seconds=90,
        )

    def get_channel_status(self, channel_key: str) -> dict[str, Any]:
        """Return the current runtime state for one session."""
        self.ensure_runtime_started()
        return self._request(
            method="GET",
            path=f"/api/v1/channels/{channel_key}",
        )

    def reset_channel(self, channel_key: str) -> dict[str, Any]:
        """Force a fresh QR by dropping the local session."""
        self.ensure_runtime_started()
        return self._request(
            method="POST",
            path=f"/api/v1/channels/{channel_key}/reset",
            json={},
            timeout_seconds=90,
        )

    def send_message(
        self,
        channel_key: str,
        chat_id: str,
        text: str,
        *,
        simulate_typing: bool = True,
        typing_delay_ms: int | None = None,
    ) -> dict[str, Any]:
        """Send one outbound WhatsApp message through the local runtime."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "simulate_typing": simulate_typing,
        }
        if typing_delay_ms is not None:
            payload["typing_delay_ms"] = typing_delay_ms

        self.ensure_runtime_started()
        return self._request(
            method="POST",
            path=f"/api/v1/channels/{channel_key}/messages/send",
            json=payload,
        )

    def set_typing(self, channel_key: str, chat_id: str, active: bool) -> dict[str, Any]:
        """Start or stop the WhatsApp typing indicator for one chat."""
        self.ensure_runtime_started()
        return self._request(
            method="POST",
            path=f"/api/v1/channels/{channel_key}/typing",
            json={
                "chat_id": chat_id,
                "active": active,
            },
            timeout_seconds=10,
        )

    def resolve_contact_profiles(self, channel_key: str, chat_ids: list[str]) -> dict[str, Any]:
        """Fetch WhatsApp contact names and avatars for stored direct chats."""
        cleaned_chat_ids = [str(chat_id).strip() for chat_id in chat_ids if str(chat_id).strip()]
        if not cleaned_chat_ids:
            return {"profiles": []}

        self.ensure_runtime_started()
        return self._request(
            method="POST",
            path=f"/api/v1/channels/{channel_key}/contacts/resolve",
            json={"chat_ids": cleaned_chat_ids[:50]},
            timeout_seconds=30,
        )

    def _spawn_runtime_process(self) -> None:
        if not self._runtime_entrypoint.exists():
            raise SelfHostedRuntimeServiceError(
                f"Local runtime entrypoint was not found: {self._runtime_entrypoint}"
            )

        self._runtime_log_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_log = self._runtime_log_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        env["RUNTIME_PORT"] = str(get_settings().runtime_service_port)
        if get_settings().runtime_service_token:
            env["RUNTIME_TOKEN"] = get_settings().runtime_service_token
        env["RUNTIME_PLATFORM_CALLBACK_URL"] = (
            f"{get_settings().platform_public_base_url}/api/v1/runtime/incoming"
        )
        env["RUNTIME_PLATFORM_CHANNEL_KEY"] = get_settings().runtime_platform_channel_key
        env["SIMPLE_CONNECT_NAME"] = get_settings().simple_connect_name
        if get_settings().runtime_callback_token:
            env["RUNTIME_PLATFORM_CALLBACK_TOKEN"] = get_settings().runtime_callback_token

        popen_kwargs: dict[str, Any] = {
            "args": ["node", str(self._runtime_entrypoint)],
            "cwd": str(self._runtime_dir),
            "env": env,
            "stdout": runtime_log,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "close_fds": False,
        }
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            subprocess.Popen(**popen_kwargs)
        except FileNotFoundError as exc:
            raise SelfHostedRuntimeServiceError(
                "Node.js was not found in PATH. Install Node.js to run the self-hosted WhatsApp runtime."
            ) from exc

    def _wait_until_healthy(self) -> None:
        deadline = time.monotonic() + RUNTIME_STARTUP_WAIT_SECONDS
        while time.monotonic() < deadline:
            if self.is_healthy():
                return
            time.sleep(RUNTIME_START_RETRY_INTERVAL_SECONDS)

        raise SelfHostedRuntimeServiceError(
            "Local WhatsApp runtime did not start in time. Check data/runtime/runtime.log."
        )

    def _request(
        self,
        *,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"

        try:
            response = requests.request(
                method=method,
                url=url,
                json=json,
                headers=self._headers(),
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            raise SelfHostedRuntimeServiceError(
                f"WhatsApp runtime request failed: {method} {path}"
            ) from exc

        if not response.ok:
            detail = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    detail = str(payload.get("detail") or payload.get("error") or "").strip()
            except ValueError:
                detail = response.text.strip()

            message = f"WhatsApp runtime request failed: {method} {path}"
            if detail:
                message = f"{message}. Response: {detail}"
            raise SelfHostedRuntimeServiceError(message)

        try:
            payload = response.json()
        except ValueError as exc:
            raise SelfHostedRuntimeServiceError(
                f"WhatsApp runtime returned a non-JSON payload for {method} {path}."
            ) from exc

        if not isinstance(payload, dict):
            raise SelfHostedRuntimeServiceError(
                f"WhatsApp runtime returned an unexpected payload for {method} {path}."
            )

        return payload


_service: SelfHostedRuntimeService | None = None


def get_self_hosted_runtime_service() -> SelfHostedRuntimeService:
    """Return the shared runtime client/autostart service."""
    global _service
    if _service is None:
        _service = SelfHostedRuntimeService()
    return _service
