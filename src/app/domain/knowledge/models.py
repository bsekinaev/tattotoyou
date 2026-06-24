"""
ORM-модель для базы знаний (FAQ) тату-студии.
"""

from __future__ import annotations

from sqlalchemy import ARRAY, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class KnowledgeBase(Base):
    """
    FAQ-запись студии ТАТТУТУЮ.

    Используется для:
    1. Semantic search (pgvector) — топ-3 релевантных FAQ
    2. Fallback responder — если LLM упал, берём эталонный ответ
    3. Admin API — Соня может редактировать через веб-интерфейс
    """

    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="pricing, aftercare, styles, faq, contraindications, booking",
    )

    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Вопрос клиента (например: 'Сколько стоит тату?')",
    )

    answer: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Эталонный ответ от имени Лики (AI-ассистента)",
    )

    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        comment="Ключевые слова для быстрого поиска",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Активен ли FAQ (можно скрыть без удаления)",
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Приоритет (выше = важнее)",
    )