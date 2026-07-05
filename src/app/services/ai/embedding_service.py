"""Локальная генерация embeddings для базы знаний."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class EmbeddingServiceError(RuntimeError):
    """Embedding не удалось сгенерировать или он имеет неверный формат."""


@lru_cache(maxsize=1)
def get_embedding_model() -> Any:
    """Лениво загрузить sentence-transformers только при первом RAG-запросе."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - зависит от deployment extras
        raise EmbeddingServiceError(
            "sentence-transformers is not installed; install the project with [rag] extras"
        ) from exc

    logger.info("loading_embedding_model", model=settings.embedding_model_name)
    try:
        return SentenceTransformer(settings.embedding_model_name)
    except Exception as exc:  # pragma: no cover - сеть/кэш модели зависят от окружения
        raise EmbeddingServiceError("embedding model could not be loaded") from exc


def build_knowledge_embedding_text(
    *,
    question: str,
    answer: str,
    keywords: list[str] | None = None,
) -> str:
    """Собрать стабильный текст, из которого строится embedding FAQ."""
    normalized_keywords = ", ".join(
        keyword.strip() for keyword in (keywords or []) if keyword.strip()
    )
    parts = [f"Вопрос: {question.strip()}", f"Ответ: {answer.strip()}"]
    if normalized_keywords:
        parts.append(f"Ключевые слова: {normalized_keywords}")
    return "\n".join(parts)


def _validate_vector(vector: object) -> list[float]:
    values = vector.tolist() if hasattr(vector, "tolist") else list(vector)  # type: ignore[arg-type]
    result = [float(value) for value in values]
    if len(result) != settings.embedding_dimension:
        raise EmbeddingServiceError(
            f"unexpected embedding dimension: {len(result)}; expected {settings.embedding_dimension}"
        )
    return result


async def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Сгенерировать нормализованные embeddings, не блокируя event loop."""
    if not texts:
        return []

    model = get_embedding_model()
    try:
        vectors = await asyncio.to_thread(
            model.encode,
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [_validate_vector(vector) for vector in vectors]
    except EmbeddingServiceError:
        raise
    except Exception as exc:
        raise EmbeddingServiceError("embedding generation failed") from exc


async def generate_embedding(text: str) -> list[float]:
    """Сгенерировать один embedding."""
    vectors = await generate_embeddings([text])
    return vectors[0]


async def generate_knowledge_embedding(
    *,
    question: str,
    answer: str,
    keywords: list[str] | None = None,
) -> list[float]:
    """Сгенерировать embedding для управляемой FAQ-записи."""
    return await generate_embedding(
        build_knowledge_embedding_text(
            question=question,
            answer=answer,
            keywords=keywords,
        )
    )
