from __future__ import annotations

from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class FAQItem(BaseModel):
    """One FAQ entry parsed from the spreadsheet."""

    model_config = ConfigDict(extra="ignore")

    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)


class Instructions(BaseModel):
    """High-level instructions used to configure the AI agent."""

    model_config = ConfigDict(extra="ignore")

    role: str = ""
    tone: str = ""
    goal: str = ""


class AgentKnowledgeBase(BaseModel):
    """Normalized JSON schema returned by the upload endpoint."""

    model_config = ConfigDict(extra="ignore")

    company: str = ""
    faq: list[FAQItem] = Field(default_factory=list)
    instructions: Instructions = Field(default_factory=Instructions)
    limitations: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)


class ProviderWebhookConfig(BaseModel):
    """How the platform should call an external AI project."""

    model_config = ConfigDict(extra="ignore")

    url: AnyHttpUrl
    authorization_header: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("authorization_header")
    @classmethod
    def strip_authorization_header(cls, value: str) -> str:
        """Keep auth headers clean and deterministic."""
        return value.strip()


class ProjectCreateRequest(BaseModel):
    """Create a new tenant project that depends on this platform for WhatsApp transport."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=2, max_length=120)
    slug: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(default="", max_length=600)
    provider: ProviderWebhookConfig
    enabled: bool = True

    @field_validator("name", "description")
    @classmethod
    def strip_text_fields(cls, value: str) -> str:
        """Reject effectively-empty text values after trimming."""
        return value.strip()


class ProjectUpdateRequest(BaseModel):
    """Update basic tenant settings without rotating secrets."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=600)
    provider: ProviderWebhookConfig | None = None
    enabled: bool | None = None

    @field_validator("name", "description")
    @classmethod
    def strip_optional_text_fields(cls, value: str | None) -> str | None:
        """Normalize optional text updates."""
        if value is None:
            return None
        return value.strip()


class ProjectSummaryResponse(BaseModel):
    """Tenant project returned from the platform control plane."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    slug: str
    description: str
    enabled: bool
    provider_type: Literal["webhook"] = "webhook"
    provider_url: str
    channel_count: int = 0
    created_at: str
    updated_at: str


class ProjectCreateResponse(ProjectSummaryResponse):
    """Project creation response includes the generated API key once."""

    project_api_key: str


class WhatsAppChannelCreateRequest(BaseModel):
    """Bind a Green API WhatsApp instance to a tenant project."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=2, max_length=120)
    green_api_url: AnyHttpUrl
    green_api_id_instance: str = Field(..., min_length=4, max_length=64)
    green_api_token: str = Field(..., min_length=20, max_length=255)
    enabled: bool = True

    @field_validator("name", "green_api_id_instance", "green_api_token")
    @classmethod
    def strip_channel_fields(cls, value: str) -> str:
        """Normalize sensitive and display fields before storage."""
        return value.strip()


class WhatsAppChannelResponse(BaseModel):
    """Public channel view returned from the admin API."""

    model_config = ConfigDict(extra="ignore")

    id: str
    project_id: str
    name: str
    enabled: bool
    green_api_url: str
    green_api_id_instance: str
    token_preview: str
    last_error: str = ""
    last_heartbeat_at: str = ""
    created_at: str
    updated_at: str


class ProjectWithWhatsAppOnboardingRequest(BaseModel):
    """Create a tenant project and its first WhatsApp channel in one request."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=2, max_length=120)
    slug: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(default="", max_length=600)
    provider: ProviderWebhookConfig
    project_enabled: bool = True
    channel_name: str = Field(..., min_length=2, max_length=120)
    green_api_url: AnyHttpUrl
    green_api_id_instance: str = Field(..., min_length=4, max_length=64)
    green_api_token: str = Field(..., min_length=20, max_length=255)
    channel_enabled: bool = True

    @field_validator("name", "description", "channel_name", "green_api_id_instance", "green_api_token")
    @classmethod
    def strip_onboarding_fields(cls, value: str) -> str:
        """Normalize one-step onboarding fields before persistence."""
        return value.strip()


class WhatsAppChannelConnectionResponse(BaseModel):
    """Live onboarding and connection state for one WhatsApp channel."""

    model_config = ConfigDict(extra="ignore")

    project_id: str
    channel_id: str
    channel_name: str
    enabled: bool
    state_instance: str = ""
    status_instance: str = ""
    phone: str = ""
    chat_id: str = ""
    device_id: str = ""
    avatar: str = ""
    base64_avatar: str = ""
    profile_name: str = ""
    contact_name: str = ""
    email: str = ""
    category: str = ""
    description: str = ""
    is_business: bool = False
    polling_ready: bool = False
    webhook_url: str = ""
    incoming_webhook: str = ""
    qr_type: Literal["qrCode", "error", "alreadyLogged", "unavailable"] = "unavailable"
    qr_message: str = ""
    qr_code_data_url: str = ""
    logout_performed: bool = False
    last_error: str = ""


class ProjectWithWhatsAppOnboardingResponse(BaseModel):
    """One-step onboarding result for a project plus its first WhatsApp channel."""

    model_config = ConfigDict(extra="ignore")

    project: ProjectCreateResponse
    channel: WhatsAppChannelResponse
    connection: WhatsAppChannelConnectionResponse


class RuntimeChannelStatusResponse(BaseModel):
    """Operational status of a live WhatsApp connector."""

    model_config = ConfigDict(extra="ignore")

    channel_id: str
    project_id: str
    project_slug: str
    channel_name: str
    enabled: bool
    last_error: str = ""
    last_heartbeat_at: str = ""


class OutboundTextMessage(BaseModel):
    """Normalized message payload sent back to WhatsApp."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["text"] = "text"
    text: str = Field(..., min_length=1, max_length=20000)

    @field_validator("text")
    @classmethod
    def strip_message_text(cls, value: str) -> str:
        """Avoid blank outgoing messages."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Message text must not be empty.")
        return cleaned


class ProviderDispatchResult(BaseModel):
    """Normalized result returned by an external AI project."""

    model_config = ConfigDict(extra="ignore")

    messages: list[OutboundTextMessage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderDispatchTestRequest(BaseModel):
    """Manually test dispatch to an external AI project without WhatsApp traffic."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(..., min_length=1)
    chat_id: str = Field(default="manual-test@c.us", min_length=1)
    sender: str = Field(default="manual-test@c.us", min_length=1)
    sender_name: str = Field(default="Manual Test", min_length=1)

    @field_validator("text", "chat_id", "sender", "sender_name")
    @classmethod
    def strip_dispatch_test_fields(cls, value: str) -> str:
        """Normalize manually-entered test data."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value must not be empty.")
        return cleaned


class SendProjectMessageRequest(BaseModel):
    """Allow external projects to send WhatsApp messages through the platform."""

    model_config = ConfigDict(extra="ignore")

    channel_id: str | None = None
    chat_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1, max_length=20000)

    @field_validator("channel_id", "chat_id", "text")
    @classmethod
    def strip_send_fields(cls, value: str | None) -> str | None:
        """Normalize outbound send requests."""
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value must not be empty.")
        return cleaned


class SendProjectMessageResponse(BaseModel):
    """Result of an outbound send executed through a project channel."""

    model_config = ConfigDict(extra="ignore")

    project_id: str
    channel_id: str
    chat_id: str
    id_message: str


class HealthResponse(BaseModel):
    """Health-check response payload."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["ok"]
    app_name: str
    database_ready: bool
    active_projects: int
    active_channels: int


class SimpleWhatsAppConnectionResponse(BaseModel):
    """Minimal QR-connect status for the platform-owned WhatsApp account."""

    model_config = ConfigDict(extra="ignore")

    configured: bool
    connection_name: str
    connection_status: Literal["connected", "disconnected", "not_configured", "error"]
    state_instance: str = ""
    status_instance: str = ""
    phone: str = ""
    chat_id: str = ""
    device_id: str = ""
    avatar: str = ""
    base64_avatar: str = ""
    profile_name: str = ""
    contact_name: str = ""
    email: str = ""
    category: str = ""
    description: str = ""
    is_business: bool = False
    polling_ready: bool = False
    qr_type: Literal["qrCode", "error", "alreadyLogged", "unavailable"] = "unavailable"
    qr_message: str = ""
    qr_code_data_url: str = ""
    last_error: str = ""
    logout_performed: bool = False
