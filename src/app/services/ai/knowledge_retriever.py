"""
Retriever для семантического поиска релевантных FAQ через pgvector.
Часть RAG-пайплайна: Retrieval-Augmented Generation.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.ai.embedding_service import generate_embedding

logger = get_logger(__name__)


class KnowledgeRetriever:
    """
    Semantic search по базе знаний студии.
    Использует pgvector cosine distance operator <=> для поиска.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def retrieve(
        self,
        query: str,
        top_k: int = 3,
        threshold: float = 0.6,
    ) -> list[dict]:
        """
        Находит top_k самых релевантных FAQ для запроса клиента.

        Args:
            query: текст запроса клиента
            top_k: сколько FAQ вернуть
            threshold: минимальный cosine similarity (0.6 = 60% схожести)

        Returns:
            Список словарей с вопросами и ответами
        """
        # 1. Генерируем эмбеддинг для запроса
        query_vector = await generate_embedding(query)

        # 2. Cosine distance = 1 - similarity
        #    similarity >= 0.6  <=>  distance <= 0.4
        max_distance = 1.0 - threshold

        # 3. SQL с pgvector оператором <=>
        stmt = text("""
                    SELECT id,
                           category,
                           question,
                           answer,
                           keywords,
                           priority,
                           1 - (question_vector <=> :query_vec::vector) AS similarity
                    FROM knowledge_base
                    WHERE is_active = true
                      AND question_vector IS NOT NULL
                      AND (question_vector <=> :query_vec::vector) < :max_dist
                    ORDER BY question_vector <=> :query_vec::vector ASC
            LIMIT :limit
                    """)

        result = await self.db.execute(
            stmt,
            {
                "query_vec": query_vector,
                "max_dist": max_distance,
                "limit": top_k,
            },
        )

        rows = result.fetchall()

        if not rows:
            logger.debug("no_relevant_faq_found", query=query[:50])
            return []

        faq_items = [
            {
                "id": row.id,
                "category": row.category,
                "question": row.question,
                "answer": row.answer,
                "similarity": float(row.similarity),
            }
            for row in rows
        ]

        logger.info(
            "faq_retrieved",
            query=query[:50],
            found=len(faq_items),
            top_similarity=faq_items[0]["similarity"],
        )
        return faq_items
