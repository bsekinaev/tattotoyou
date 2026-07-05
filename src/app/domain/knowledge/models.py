"""ORM-модель базы знаний тату-студии."""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class KnowledgeBase(Base):
    """Управляемая FAQ-запись, доступная семантическому поиску."""

    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="pricing, aftercare, styles, faq, contraindications, booking",
    )
    question: Mapped[str] = mapped_column(Text, nullable=False, comment="Вопрос клиента")
    answer: Mapped[str] = mapped_column(Text, nullable=False, comment="Эталонный ответ")
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        comment="Ключевые слова для резервного поиска",
    )
    question_vector: Mapped[list[float] | None] = mapped_column(
        Vector(384),
        nullable=True,
        comment="Нормализованный embedding вопроса, ответа и ключевых слов",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    @property
    def embedding_ready(self) -> bool:
        """Показывает, участвует ли запись в semantic retrieval."""
        return self.question_vector is not None
