"""Pydantic-схемы для Admin API."""
from pydantic import BaseModel, Field
from datetime import datetime


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

    class Config:
        from_attributes = True  # Pydantic v2: совместимость с ORM


class KnowledgeBaseListResponse(BaseModel):
    """Схема ответа со списком FAQ."""
    items: list[KnowledgeBaseResponse]
    total: int