from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.schemas import (
    ProjectWithWhatsAppOnboardingRequest,
    ProjectWithWhatsAppOnboardingResponse,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectSummaryResponse,
    ProjectUpdateRequest,
    ProviderDispatchTestRequest,
    ProviderDispatchResult,
    RuntimeChannelStatusResponse,
    SendProjectMessageRequest,
    SendProjectMessageResponse,
    WhatsAppChannelCreateRequest,
    WhatsAppChannelConnectionResponse,
    WhatsAppChannelResponse,
)
from app.routes.deps import require_admin_token, require_project_api_key
from app.services.green_api_service import GreenApiClient, GreenApiCredentials, GreenApiServiceError
from app.services.project_registry import ProjectRecord, WhatsAppChannelRecord, get_project_registry_service, utc_now_iso
from app.services.provider_gateway import get_provider_gateway_service
from app.utils.config import get_settings

admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
project_router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def build_channel_green_api_client(channel: WhatsAppChannelRecord) -> GreenApiClient:
    """Create an admin-side Green API client from a stored project channel."""
    settings = get_settings()
    return GreenApiClient(
        GreenApiCredentials(
            api_url=channel.green_api_url,
            id_instance=channel.green_api_id_instance,
            api_token=channel.green_api_token,
        ),
        connect_timeout_seconds=settings.connect_timeout_seconds,
        request_timeout_seconds=settings.request_timeout_seconds,
        receive_timeout_seconds=settings.green_api_receive_timeout_seconds,
    )


def ensure_platform_green_api_settings(green_api_client: GreenApiClient) -> tuple[dict[str, object], bool]:
    """Push the instance toward the polling mode expected by the platform."""
    settings_payload = green_api_client.get_settings()
    webhook_url = str(settings_payload.get("webhookUrl", "")).strip()
    incoming_webhook = str(settings_payload.get("incomingWebhook", "")).strip().lower()
    polling_ready = not webhook_url and incoming_webhook == "yes"

    if polling_ready:
        return settings_payload, True

    green_api_client.set_settings(
        {
            "webhookUrl": "",
            "incomingWebhook": "yes",
            "outgoingWebhook": "yes",
            "stateWebhook": "yes",
        }
    )
    settings_payload = green_api_client.get_settings()
    webhook_url = str(settings_payload.get("webhookUrl", "")).strip()
    incoming_webhook = str(settings_payload.get("incomingWebhook", "")).strip().lower()
    polling_ready = not webhook_url and incoming_webhook == "yes"
    return settings_payload, polling_ready


def build_failed_connection_response(
    project_id: str,
    channel: WhatsAppChannelRecord,
    error_message: str,
    *,
    logout_performed: bool = False,
) -> WhatsAppChannelConnectionResponse:
    """Return a UI-friendly connection response even when Green API is unavailable."""
    return WhatsAppChannelConnectionResponse(
        project_id=project_id,
        channel_id=channel.id,
        channel_name=channel.name,
        enabled=channel.enabled,
        qr_type="error",
        qr_message=error_message,
        logout_performed=logout_performed,
        last_error=error_message,
    )


def get_channel_connection_snapshot(
    project_id: str,
    channel: WhatsAppChannelRecord,
    *,
    include_qr: bool,
    reset_session: bool,
) -> WhatsAppChannelConnectionResponse:
    """Return a live Green API onboarding snapshot for one channel."""
    green_api_client = build_channel_green_api_client(channel)
    logout_performed = False

    if reset_session:
        logout_performed = green_api_client.logout_instance()

    settings_payload, polling_ready = ensure_platform_green_api_settings(green_api_client)
    state_instance = green_api_client.get_state_instance()
    status_instance = green_api_client.get_status_instance()
    wa_settings = green_api_client.get_wa_settings()

    qr_type = "unavailable"
    qr_message = ""
    qr_code_data_url = ""
    if include_qr:
        qr_payload = green_api_client.get_qr_code()
        raw_qr_type = qr_payload["type"]
        qr_type = raw_qr_type if raw_qr_type in {"qrCode", "error", "alreadyLogged"} else "error"
        qr_message = qr_payload["message"].strip()
        if qr_type == "qrCode":
            qr_code_data_url = f"data:image/png;base64,{qr_payload['message']}"

    state_from_wa = str(wa_settings.get("stateInstance", "")).strip()
    phone = str(wa_settings.get("phone", "")).strip()
    chat_id = str(wa_settings.get("chatId", "")).strip()
    device_id = str(wa_settings.get("deviceId", "")).strip()
    avatar = str(wa_settings.get("avatar", "")).strip()
    base64_avatar = str(wa_settings.get("base64Avatar", "")).strip()
    webhook_url = str(settings_payload.get("webhookUrl", "")).strip()
    incoming_webhook = str(settings_payload.get("incomingWebhook", "")).strip()
    profile_name = ""
    contact_name = ""
    email = ""
    category = ""
    description = ""
    is_business = False

    candidate_contact_ids: list[str] = []
    if phone:
        candidate_contact_ids.append(f"{phone}@c.us")
    if chat_id and chat_id not in candidate_contact_ids:
        candidate_contact_ids.append(chat_id)

    for candidate_chat_id in candidate_contact_ids:
        try:
            contact_info = green_api_client.get_contact_info(candidate_chat_id)
        except GreenApiServiceError:
            continue

        profile_name = str(contact_info.get("name", "")).strip()
        contact_name = str(contact_info.get("contactName", "")).strip()
        email = str(contact_info.get("email", "")).strip()
        category = str(contact_info.get("category", "")).strip()
        description = str(contact_info.get("description", "")).strip()
        is_business = bool(contact_info.get("isBusiness", False))

        if not avatar:
            avatar = str(contact_info.get("avatar", "")).strip()
        if not base64_avatar:
            base64_avatar = str(contact_info.get("base64Avatar", "")).strip()

        if profile_name or contact_name or email or category or description or avatar or base64_avatar:
            break

    return WhatsAppChannelConnectionResponse(
        project_id=project_id,
        channel_id=channel.id,
        channel_name=channel.name,
        enabled=channel.enabled,
        state_instance=state_from_wa or state_instance,
        status_instance=status_instance,
        phone=phone,
        chat_id=chat_id,
        device_id=device_id,
        avatar=avatar,
        base64_avatar=base64_avatar,
        profile_name=profile_name,
        contact_name=contact_name,
        email=email,
        category=category,
        description=description,
        is_business=is_business,
        polling_ready=polling_ready,
        webhook_url=webhook_url,
        incoming_webhook=incoming_webhook,
        qr_type=qr_type,
        qr_message=qr_message,
        qr_code_data_url=qr_code_data_url,
        logout_performed=logout_performed,
        last_error="",
    )


def build_manual_provider_event(
    project: ProjectSummaryResponse,
    channel: WhatsAppChannelResponse,
    payload: ProviderDispatchTestRequest,
) -> dict[str, object]:
    """Build a provider webhook event without needing a real WhatsApp message."""
    return {
        "event": "whatsapp.message.received",
        "project": {
            "id": project.id,
            "slug": project.slug,
            "name": project.name,
        },
        "channel": {
            "id": channel.id,
            "name": channel.name,
            "type": "whatsapp",
            "instanceId": channel.green_api_id_instance,
        },
        "conversation": {
            "chatId": payload.chat_id,
            "userId": payload.sender,
        },
        "message": {
            "id": "manual-test-message",
            "text": payload.text,
            "timestamp": "manual-test",
            "chatId": payload.chat_id,
            "sender": payload.sender,
            "senderName": payload.sender_name,
        },
    }


@admin_router.get("/projects", response_model=list[ProjectSummaryResponse], dependencies=[Depends(require_admin_token)])
def list_projects() -> list[ProjectSummaryResponse]:
    """List all onboarded projects."""
    return get_project_registry_service().list_projects()


@admin_router.post(
    "/projects",
    response_model=ProjectCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_token)],
)
def create_project(payload: ProjectCreateRequest) -> ProjectCreateResponse:
    """Create a new tenant project and return its one-time API key."""
    try:
        return get_project_registry_service().create_project(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@admin_router.post(
    "/onboarding/whatsapp",
    response_model=ProjectWithWhatsAppOnboardingResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_token)],
)
def create_project_with_whatsapp_onboarding(
    payload: ProjectWithWhatsAppOnboardingRequest,
) -> ProjectWithWhatsAppOnboardingResponse:
    """Create a project and its first Green API channel, then fetch the current QR status."""
    registry = get_project_registry_service()
    try:
        project, channel = registry.create_project_with_whatsapp_channel(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    channel_record = registry.resolve_project_channel(project.id, channel.id)
    if channel_record is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Channel was created but not found.")

    try:
        connection = get_channel_connection_snapshot(
            project.id,
            channel_record,
            include_qr=True,
            reset_session=False,
        )
    except GreenApiServiceError as exc:
        registry.update_channel_runtime_state(
            channel_record.id,
            last_error=str(exc),
            heartbeat_at=utc_now_iso(),
        )
        connection = build_failed_connection_response(project.id, channel_record, str(exc))

    return ProjectWithWhatsAppOnboardingResponse(
        project=project,
        channel=channel,
        connection=connection,
    )


@admin_router.get(
    "/projects/{project_id}",
    response_model=ProjectSummaryResponse,
    dependencies=[Depends(require_admin_token)],
)
def get_project(project_id: str) -> ProjectSummaryResponse:
    """Return one project summary."""
    project = get_project_registry_service().get_project_summary(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


@admin_router.patch(
    "/projects/{project_id}",
    response_model=ProjectSummaryResponse,
    dependencies=[Depends(require_admin_token)],
)
def update_project(project_id: str, payload: ProjectUpdateRequest) -> ProjectSummaryResponse:
    """Update a tenant project's metadata or provider webhook."""
    project = get_project_registry_service().update_project(project_id, payload)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


@admin_router.get(
    "/projects/{project_id}/channels",
    response_model=list[WhatsAppChannelResponse],
    dependencies=[Depends(require_admin_token)],
)
def list_project_channels(project_id: str) -> list[WhatsAppChannelResponse]:
    """List all WhatsApp channels connected to a project."""
    return get_project_registry_service().list_whatsapp_channels(project_id)


@admin_router.post(
    "/projects/{project_id}/channels",
    response_model=WhatsAppChannelResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_token)],
)
def create_project_channel(project_id: str, payload: WhatsAppChannelCreateRequest) -> WhatsAppChannelResponse:
    """Attach a Green API instance to a tenant project."""
    try:
        return get_project_registry_service().create_whatsapp_channel(project_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@admin_router.get(
    "/projects/{project_id}/channels/{channel_id}/connection",
    response_model=WhatsAppChannelConnectionResponse,
    dependencies=[Depends(require_admin_token)],
)
def get_project_channel_connection(
    project_id: str,
    channel_id: str,
    include_qr: bool = False,
) -> WhatsAppChannelConnectionResponse:
    """Return the live Green API connection state for one stored channel."""
    registry = get_project_registry_service()
    channel = registry.resolve_project_channel(project_id, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp channel not found.")

    try:
        snapshot = get_channel_connection_snapshot(
            project_id,
            channel,
            include_qr=include_qr,
            reset_session=False,
        )
    except GreenApiServiceError as exc:
        registry.update_channel_runtime_state(
            channel.id,
            last_error=str(exc),
            heartbeat_at=utc_now_iso(),
        )
        return build_failed_connection_response(project_id, channel, str(exc))

    registry.update_channel_runtime_state(
        channel.id,
        last_error="",
        heartbeat_at=utc_now_iso(),
    )
    return snapshot


@admin_router.post(
    "/projects/{project_id}/channels/{channel_id}/connection/reset",
    response_model=WhatsAppChannelConnectionResponse,
    dependencies=[Depends(require_admin_token)],
)
def reset_project_channel_connection(project_id: str, channel_id: str) -> WhatsAppChannelConnectionResponse:
    """Log out the current device and start a fresh QR-based authorization flow."""
    registry = get_project_registry_service()
    channel = registry.resolve_project_channel(project_id, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp channel not found.")

    try:
        snapshot = get_channel_connection_snapshot(
            project_id,
            channel,
            include_qr=True,
            reset_session=True,
        )
    except GreenApiServiceError as exc:
        registry.update_channel_runtime_state(
            channel.id,
            last_error=str(exc),
            heartbeat_at=utc_now_iso(),
        )
        return build_failed_connection_response(project_id, channel, str(exc))

    registry.update_channel_runtime_state(
        channel.id,
        last_error="",
        heartbeat_at=utc_now_iso(),
    )
    return snapshot


@admin_router.get(
    "/runtime/channels",
    response_model=list[RuntimeChannelStatusResponse],
    dependencies=[Depends(require_admin_token)],
)
def list_runtime_channels() -> list[RuntimeChannelStatusResponse]:
    """Return the runtime status visible to platform operators."""
    return get_project_registry_service().list_runtime_statuses()


@admin_router.post(
    "/projects/{project_id}/dispatch/test",
    response_model=ProviderDispatchResult,
    dependencies=[Depends(require_admin_token)],
)
def dispatch_provider_test(project_id: str, payload: ProviderDispatchTestRequest) -> ProviderDispatchResult:
    """Dry-run a project's AI webhook without waiting for a real WhatsApp message."""
    registry = get_project_registry_service()
    project = registry.get_project_summary(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")

    active_bindings = [binding for binding in registry.list_active_channel_bindings() if binding.project_id == project_id]
    if not active_bindings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project has no active WhatsApp channels yet. Create and enable a channel first.",
        )

    binding = active_bindings[0]
    channels = registry.list_whatsapp_channels(project_id)
    channel_lookup = {channel.id: channel for channel in channels}
    channel = channel_lookup[binding.channel_id]
    event_payload = build_manual_provider_event(project, channel, payload)
    return get_provider_gateway_service().dispatch_incoming_message(binding, event_payload)


@project_router.post("/{project_id}/messages/send", response_model=SendProjectMessageResponse)
def send_project_message(
    project_id: str,
    payload: SendProjectMessageRequest,
    project: ProjectRecord = Depends(require_project_api_key),
) -> SendProjectMessageResponse:
    """Allow client systems to send WhatsApp messages through their connected channel."""
    registry = get_project_registry_service()
    channel = registry.resolve_project_channel(project_id, payload.channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matching WhatsApp channel was found for this project.",
        )

    if not channel.enabled or not project.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The selected project channel is disabled.",
        )

    settings = get_settings()
    green_api_client = GreenApiClient(
        GreenApiCredentials(
            api_url=channel.green_api_url,
            id_instance=channel.green_api_id_instance,
            api_token=channel.green_api_token,
        ),
        connect_timeout_seconds=settings.connect_timeout_seconds,
        request_timeout_seconds=settings.request_timeout_seconds,
        receive_timeout_seconds=settings.green_api_receive_timeout_seconds,
    )

    try:
        id_message = green_api_client.send_message(payload.chat_id, payload.text)
    except GreenApiServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    registry.update_channel_runtime_state(
        channel.id,
        last_error="",
        heartbeat_at=utc_now_iso(),
    )
    return SendProjectMessageResponse(
        project_id=project_id,
        channel_id=channel.id,
        chat_id=payload.chat_id,
        id_message=id_message,
    )
