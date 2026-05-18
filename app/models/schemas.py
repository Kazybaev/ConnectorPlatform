from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class PlatformConversationResponse(BaseModel):
    """Conversation summary shown in the platform inbox."""

    model_config = ConfigDict(extra="ignore")

    channel_key: str
    chat_id: str
    display_name: str = ""
    phone: str = ""
    avatar_url: str = ""
    last_message_text: str = ""
    last_message_at: str = ""
    last_direction: Literal["inbound", "outbound"] = "inbound"
    last_sender_name: str = ""
    unread_count: int = 0


class PlatformChatMessageResponse(BaseModel):
    """One message stored in the platform inbox timeline."""

    model_config = ConfigDict(extra="ignore")

    record_id: str
    channel_key: str
    chat_id: str
    external_message_id: str = ""
    direction: Literal["inbound", "outbound"]
    sender_id: str = ""
    sender_name: str = ""
    text: str = ""
    message_type: str = "text"
    source: str = "runtime"
    status: str = ""
    created_at: str


class PlatformChatSendRequest(BaseModel):
    """Manual operator reply sent from the platform inbox."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(..., min_length=1, max_length=20000)

    @field_validator("text")
    @classmethod
    def strip_platform_chat_send_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Message text must not be empty.")
        return cleaned


class PlatformChatSendResponse(BaseModel):
    """Result of one manual operator reply."""

    model_config = ConfigDict(extra="ignore")

    channel_key: str
    chat_id: str
    id_message: str
    message: PlatformChatMessageResponse


class BotVariableDefinition(BaseModel):
    """One runtime variable that a bot expects from the platform setup."""

    model_config = ConfigDict(extra="ignore")

    key: str = Field(..., min_length=1, max_length=120, pattern=r"^[A-Z][A-Z0-9_]*$")
    required: bool = True
    default_value: str = Field(default="", max_length=600)
    description: str = Field(default="", max_length=400)

    @field_validator("key")
    @classmethod
    def normalize_variable_key(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("Variable key must not be empty.")
        return cleaned

    @field_validator("default_value", "description")
    @classmethod
    def strip_variable_text_fields(cls, value: str) -> str:
        return value.strip()


class BotApiBinding(BaseModel):
    """One external API or webhook connected to a bot."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=120)
    kind: Literal["http", "n8n", "crm", "internal", "custom"] = "http"
    endpoint_url: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=600)

    @field_validator("name", "endpoint_url", "notes")
    @classmethod
    def strip_api_binding_fields(cls, value: str) -> str:
        return value.strip()


class BotCreateRequest(BaseModel):
    """Create one reusable bot integration managed by the platform."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=2, max_length=120)
    slug: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(default="", max_length=600)
    engine_type: Literal["dify", "n8n", "webhook", "custom"] = "custom"
    endpoint_url: str = Field(default="", max_length=500)
    authorization_header: str = Field(default="", max_length=500)
    owner_label: str = Field(default="", max_length=120)
    workflow_summary: str = Field(default="", max_length=2000)
    linked_project_id: str = Field(default="", max_length=80)
    linked_channel_key: str = Field(default="", max_length=120)
    enabled: bool = True
    variables: list[BotVariableDefinition] = Field(default_factory=list)
    api_bindings: list[BotApiBinding] = Field(default_factory=list)

    @field_validator(
        "name",
        "slug",
        "description",
        "endpoint_url",
        "authorization_header",
        "owner_label",
        "workflow_summary",
        "linked_project_id",
        "linked_channel_key",
    )
    @classmethod
    def strip_bot_create_fields(cls, value: str) -> str:
        return value.strip()


class BotSummaryResponse(BaseModel):
    """Compact bot card shown in the platform bot catalog."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    slug: str
    description: str
    engine_type: Literal["dify", "n8n", "webhook", "custom"]
    endpoint_url: str = ""
    owner_label: str = ""
    linked_project_id: str = ""
    linked_channel_key: str = ""
    enabled: bool
    is_default_template: bool = False
    test_connected: bool = False
    connected_channel_keys: list[str] = Field(default_factory=list)
    variable_count: int = 0
    api_binding_count: int = 0
    created_at: str
    updated_at: str


class BotDetailResponse(BotSummaryResponse):
    """Detailed bot configuration with setup guidance for platform operators."""

    authorization_header: str = ""
    workflow_summary: str = ""
    variables: list[BotVariableDefinition] = Field(default_factory=list)
    api_bindings: list[BotApiBinding] = Field(default_factory=list)
    platform_instructions: list[str] = Field(default_factory=list)
    env_example: dict[str, str] = Field(default_factory=dict)
    inbound_example: dict[str, Any] = Field(default_factory=dict)
    outbound_example: dict[str, Any] = Field(default_factory=dict)


class BotTestConnectionResponse(BaseModel):
    """Result of connecting or disconnecting one bot to the platform channel."""

    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    bot_id: str
    channel_key: str
    enabled: bool
    bot_ready: bool = False
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class RuntimeIncomingMessageRequest(BaseModel):
    """Inbound event posted by the local WhatsApp runtime."""

    model_config = ConfigDict(extra="ignore")

    channel_key: str = Field(..., min_length=1)
    channel_name: str = ""
    message: dict[str, Any] = Field(default_factory=dict)


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
