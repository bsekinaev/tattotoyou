"""Retrieval-часть активного RAG-пайплайна."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.infrastructure.db.repositories.knowledge_repository import KnowledgeBaseRepository
from app.services.ai.embedding_service import EmbeddingServiceError, generate_embedding

logger = get_logger(__name__)
settings = get_settings()
EmbeddingGenerator = Callable[[str], Awaitable[list[float]]]


class KnowledgeRetriever:
    """Semantic search с контролируемым keyword fallback."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        repository: KnowledgeBaseRepository | None = None,
        embedding_generator: EmbeddingGenerator = generate_embedding,
    ):
        self.repository = repository or KnowledgeBaseRepository(db)
        self.embedding_generator = embedding_generator

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> list[dict[str, object]]:
        normalized = query.strip()
        if not settings.rag_enabled or not normalized:
            return []

        effective_top_k = top_k or settings.rag_top_k
        effective_threshold = threshold or settings.rag_similarity_threshold

        try:
            query_vector = await self.embedding_generator(normalized)
            items = await self.repository.semantic_search(
                query_vector,
                top_k=effective_top_k,
                threshold=effective_threshold,
            )
            logger.info(
                "knowledge_retrieved",
                source="semantic",
                found=len(items),
                threshold=effective_threshold,
                top_similarity=(items[0].get("similarity") if items else None),
            )
            return items
        except EmbeddingServiceError as exc:
            logger.warning(
                "embedding_unavailable_using_keyword_fallback", error_type=type(exc).__name__
            )
            items = await self.repository.keyword_search(normalized, limit=effective_top_k)
            logger.info("knowledge_retrieved", source="keyword", found=len(items))
            return items
