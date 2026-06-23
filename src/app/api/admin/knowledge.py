"""
Admin API для управления базой знаний (FAQ).

Endpoints:
- GET    /admin/knowledge          - список всех FAQ
- POST   /admin/knowledge          - создать FAQ
- GET    /admin/knowledge/{id}     - получить FAQ по ID
- PATCH  /admin/knowledge/{id}     - обновить FAQ
- DELETE /admin/knowledge/{id}     - мягкое удаление (деактивация)
"""

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

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/knowledge", response_model=KnowledgeBaseListResponse)
async def list_knowledge(
    category: str | None = Query(None, description="Фильтр по категории"),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Получить список всех активных FAQ-записей.

    Опциональная фильтрация по категории:
    - `pricing` - вопросы о ценах
    - `aftercare` - уход за тату
    - `styles` - стили тату
    - `faq` - общие вопросы
    - `contraindications` - противопоказания
    - `booking` - запись на сеанс
    """
    repo = KnowledgeBaseRepository(db)
    items = await repo.get_active_by_category(category=category, limit=limit)

    # Подсчёт общего количества
    count_query = (
        select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.is_active.is_(True))
    )
    if category:
        count_query = count_query.where(KnowledgeBase.category == category)

    total = (await db.execute(count_query)).scalar_one()

    return KnowledgeBaseListResponse(
        items=[KnowledgeBaseResponse.model_validate(item) for item in items],
        total=total,
    )


@router.post("/knowledge", response_model=KnowledgeBaseResponse, status_code=201)
async def create_knowledge(
    data: KnowledgeBaseCreate,
    db: AsyncSession = Depends(get_db_session),
):
    """Создать новую FAQ-запись."""
    repo = KnowledgeBaseRepository(db)

    kb = await repo.create(
        category=data.category,
        question=data.question,
        answer=data.answer,
        keywords=data.keywords,
        priority=data.priority,
        is_active=True,
    )

    logger.info("knowledge_created", id=kb.id, category=kb.category)
    return KnowledgeBaseResponse.model_validate(kb)


@router.get("/knowledge/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge(
    kb_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Получить FAQ-запись по ID."""
    repo = KnowledgeBaseRepository(db)
    kb = await repo.get_by_id(kb_id)

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base entry not found")

    return KnowledgeBaseResponse.model_validate(kb)


@router.patch("/knowledge/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge(
    kb_id: int,
    data: KnowledgeBaseUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Обновить FAQ-запись (частичное обновление).

    Передавайте только те поля, которые нужно изменить.
    """
    repo = KnowledgeBaseRepository(db)
    kb = await repo.get_by_id(kb_id)

    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base entry not found")

    # Фильтруем только переданные поля
    update_data = data.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated_kb = await repo.update(kb, **update_data)

    logger.info("knowledge_updated", id=kb_id, fields=list(update_data.keys()))
    return KnowledgeBaseResponse.model_validate(updated_kb)


@router.delete("/knowledge/{kb_id}", status_code=204)
async def delete_knowledge(
    kb_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Мягкое удаление FAQ-записи (деактивация).

    Запись остаётся в БД для аудита, но перестаёт использоваться в AI.
    """
    repo = KnowledgeBaseRepository(db)

    success = await repo.deactivate(kb_id)
    if not success:
        raise HTTPException(status_code=404, detail="Knowledge base entry not found")

    logger.info("knowledge_deactivated", id=kb_id)
    return None
