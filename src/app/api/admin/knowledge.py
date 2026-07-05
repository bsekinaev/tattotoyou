"""Защищённый Admin API для управляемой базы знаний."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.auth import require_admin
from app.api.admin.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)
from app.core.logging import get_logger
from app.domain.knowledge.models import KnowledgeBase
from app.infrastructure.db.repositories import KnowledgeBaseRepository
from app.infrastructure.db.session import get_db_session
from app.services.ai.embedding_service import (
    EmbeddingServiceError,
    generate_knowledge_embedding,
)

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])


async def _embedding_or_503(*, question: str, answer: str, keywords: list[str]) -> list[float]:
    try:
        return await generate_knowledge_embedding(
            question=question, answer=answer, keywords=keywords
        )
    except EmbeddingServiceError as exc:
        logger.error("knowledge_embedding_failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Knowledge embedding service is temporarily unavailable",
        ) from exc


@router.get("/knowledge", response_model=KnowledgeBaseListResponse)
async def list_knowledge(
    category: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
):
    repo = KnowledgeBaseRepository(db)
    items = await repo.get_active_by_category(category=category, limit=limit)
    count_query = (
        select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.is_active.is_(True))
    )
    if category:
        count_query = count_query.where(KnowledgeBase.category == category)
    total = (await db.execute(count_query)).scalar_one()
    return KnowledgeBaseListResponse(
        items=[KnowledgeBaseResponse.model_validate(item) for item in items], total=total
    )


@router.post("/knowledge", response_model=KnowledgeBaseResponse, status_code=201)
async def create_knowledge(
    data: KnowledgeBaseCreate,
    db: AsyncSession = Depends(get_db_session),
):
    embedding = await _embedding_or_503(
        question=data.question, answer=data.answer, keywords=data.keywords
    )
    kb = await KnowledgeBaseRepository(db).create(
        category=data.category,
        question=data.question,
        answer=data.answer,
        keywords=data.keywords,
        priority=data.priority,
        is_active=True,
        question_vector=embedding,
    )
    logger.info("knowledge_created", id=kb.id, category=kb.category, embedding_ready=True)
    return KnowledgeBaseResponse.model_validate(kb)


@router.get("/knowledge/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge(kb_id: int, db: AsyncSession = Depends(get_db_session)):
    kb = await KnowledgeBaseRepository(db).get_by_id(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base entry not found")
    return KnowledgeBaseResponse.model_validate(kb)


@router.patch("/knowledge/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge(
    kb_id: int,
    data: KnowledgeBaseUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    repo = KnowledgeBaseRepository(db)
    kb = await repo.get_by_id(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base entry not found")

    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    embedding_fields = {"question", "answer", "keywords"}
    needs_embedding = bool(embedding_fields.intersection(update_data))
    if update_data.get("is_active") is True and not kb.embedding_ready:
        needs_embedding = True
    if needs_embedding:
        update_data["question_vector"] = await _embedding_or_503(
            question=update_data.get("question", kb.question),
            answer=update_data.get("answer", kb.answer),
            keywords=update_data.get("keywords", kb.keywords),
        )

    updated_kb = await repo.update(kb, **update_data)
    logger.info(
        "knowledge_updated",
        id=kb_id,
        fields=sorted(update_data),
        embedding_ready=updated_kb.embedding_ready,
    )
    return KnowledgeBaseResponse.model_validate(updated_kb)


@router.delete("/knowledge/{kb_id}", status_code=204)
async def delete_knowledge(kb_id: int, db: AsyncSession = Depends(get_db_session)):
    success = await KnowledgeBaseRepository(db).deactivate(kb_id)
    if not success:
        raise HTTPException(status_code=404, detail="Knowledge base entry not found")
    logger.info("knowledge_deactivated", id=kb_id)
    return None
