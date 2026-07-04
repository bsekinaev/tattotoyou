"""Pydantic-схемы для Admin API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ============================================
# KNOWLEDGE BASE SCHEMAS
# ============================================


class KnowledgeBaseCreate(BaseModel):
    """Схема создания FAQ-записи."""

    category: str = Field(..., min_length=1, max_length=50)
    question: str = Field(..., min_length=5, max_length=1000)
    answer: str = Field(..., min_length=10, max_length=5000)
    keywords: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=100)


class KnowledgeBaseUpdate(BaseModel):
    """Схема обновления FAQ-записи (все поля опциональны)."""

    category: str | None = Field(None, min_length=1, max_length=50)
    question: str | None = Field(None, min_length=5, max_length=1000)
    answer: str | None = Field(None, min_length=10, max_length=5000)
    keywords: list[str] | None = None
    is_active: bool | None = None
    priority: int | None = Field(None, ge=0, le=100)


class KnowledgeBaseResponse(BaseModel):
    """Схема ответа с полной информацией о FAQ."""

    id: int
    category: str
    question: str
    answer: str
    keywords: list[str]
    is_active: bool
    priority: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KnowledgeBaseListResponse(BaseModel):
    """Схема ответа со списком FAQ."""

    items: list[KnowledgeBaseResponse]
    total: int


# ============================================
# CONVERSATION AND DELIVERY SCHEMAS
# ============================================


class ConversationStateResponse(BaseModel):
    """Текущее состояние handoff-диалога."""

    id: str
    client_id: int
    status: str
    assigned_to_human: bool
    ai_messages_count: int
    human_messages_count: int
    last_activity_at: datetime
    closed_at: datetime | None


class OutboundDeliveryResponse(BaseModel):
    """Состояние одной исходящей доставки."""

    id: str
    message_id: int
    platform: str
    destination_id: str
    status: str
    attempts: int
    next_attempt_at: datetime | None
    last_error_code: str | None
    platform_message_id: str | None
    sent_at: datetime | None
    failed_at: datetime | None
