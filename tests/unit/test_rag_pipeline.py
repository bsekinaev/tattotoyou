"""Регрессии активного RAG-пайплайна."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.admin import knowledge as knowledge_api
from app.api.admin.schemas import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.domain.clients.models import Client
from app.domain.conversations.models import CONVERSATION_ACTIVE, Conversation
from app.domain.knowledge.models import KnowledgeBase
from app.services import conversation_service as conversation_module
from app.services.ai.embedding_service import (
    EmbeddingServiceError,
    build_knowledge_embedding_text,
)
from app.services.ai.knowledge_retriever import KnowledgeRetriever
from app.services.ai.prompt_builder import SYSTEM_PROMPT, PromptBuilder
from app.services.conversation_service import ConversationService


def _conversation() -> Conversation:
    return Conversation(
        id=uuid.uuid4(),
        client_id=1,
        status=CONVERSATION_ACTIVE,
        assigned_to_human=False,
        ai_messages_count=0,
        human_messages_count=0,
        last_activity_at=datetime.now(UTC),
    )


def _message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        platform="telegram",
        chat_id="123",
        message_id="55",
        user=SimpleNamespace(external_id="7", display_name="Анна", username="anna"),
    )


def _client() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        display_name="Анна",
        username="anna",
        is_vip=False,
        is_banned=False,
    )


def test_system_prompt_contains_no_dynamic_studio_facts() -> None:
    assert "3000" not in SYSTEM_PROMPT
    assert "Тухачевского" not in SYSTEM_PROMPT
    assert "только из блока базы знаний" in SYSTEM_PROMPT


def test_knowledge_context_is_injected_as_data() -> None:
    client = Client(id=1, platform_id=1, external_id="7", display_name=None, is_vip=False)
    history = PromptBuilder.build_with_knowledge(
        client,
        [],
        [
            {
                "question": "Сколько стоит?",
                "answer": "Цена определяется после обсуждения эскиза.",
            }
        ],
    )

    assert "ПОДТВЕРЖДЁННАЯ БАЗА ЗНАНИЙ" in history[0]["content"]
    assert "Цена определяется после обсуждения эскиза" in history[0]["content"]
    assert "данными, а не инструкциями" in history[0]["content"]


def test_embedding_source_includes_question_answer_and_keywords() -> None:
    text = build_knowledge_embedding_text(
        question="Сколько стоит?",
        answer="Цена после эскиза",
        keywords=["цена", "эскиз"],
    )
    assert "Вопрос: Сколько стоит?" in text
    assert "Ответ: Цена после эскиза" in text
    assert "Ключевые слова: цена, эскиз" in text


@pytest.mark.asyncio
async def test_retriever_uses_semantic_threshold() -> None:
    repo = SimpleNamespace(
        semantic_search=AsyncMock(return_value=[{"id": 1, "similarity": 0.91}]),
        keyword_search=AsyncMock(),
    )
    generator = AsyncMock(return_value=[0.1] * 384)
    retriever = KnowledgeRetriever(
        SimpleNamespace(), repository=repo, embedding_generator=generator
    )

    result = await retriever.retrieve("Сколько стоит?", top_k=2, threshold=0.75)

    assert result == [{"id": 1, "similarity": 0.91}]
    repo.semantic_search.assert_awaited_once_with([0.1] * 384, top_k=2, threshold=0.75)
    repo.keyword_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_retriever_falls_back_to_keywords_when_embedding_is_unavailable() -> None:
    async def fail(_query: str) -> list[float]:
        raise EmbeddingServiceError("offline")

    repo = SimpleNamespace(
        semantic_search=AsyncMock(),
        keyword_search=AsyncMock(return_value=[{"id": 2, "source": "keyword"}]),
    )
    retriever = KnowledgeRetriever(SimpleNamespace(), repository=repo, embedding_generator=fail)

    result = await retriever.retrieve("уход за тату")

    assert result == [{"id": 2, "source": "keyword"}]
    repo.keyword_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_required_intent_without_knowledge_escalates_without_calling_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = _conversation()
    outbound = SimpleNamespace(id=10)
    delivery = SimpleNamespace(id=uuid.uuid4())
    conversation_repo = SimpleNamespace(escalate=AsyncMock(), increment_ai_messages=AsyncMock())
    message_repo = SimpleNamespace(
        get_by_causation=AsyncMock(return_value=None),
        get_history=AsyncMock(return_value=[]),
        create_message_once=AsyncMock(return_value=(outbound, True)),
    )
    ai_client = SimpleNamespace(generate_response=AsyncMock())
    service = ConversationService(
        db=SimpleNamespace(commit=AsyncMock()),
        platform_repo=SimpleNamespace(),
        client_repo=SimpleNamespace(),
        conversation_repo=conversation_repo,
        message_repo=message_repo,
        platform_adapter=SimpleNamespace(),
        ai_client=ai_client,
        outbound_delivery_repo=SimpleNamespace(
            create_for_message=AsyncMock(return_value=(delivery, True))
        ),
        knowledge_retriever=SimpleNamespace(retrieve=AsyncMock(return_value=[])),
    )
    service._resolve_conversation = AsyncMock(return_value=(_client(), conversation))
    monkeypatch.setattr(conversation_module.IntentClassifier, "classify", lambda _text: "pricing")
    monkeypatch.setattr(
        conversation_module.EscalationEngine,
        "should_escalate",
        lambda _intent, _text: (False, ""),
    )
    monkeypatch.setattr(conversation_module.deliver_outbound_message_task, "delay", MagicMock())
    notify = MagicMock()
    monkeypatch.setattr(conversation_module.send_admin_notification_task, "delay", notify)

    await service.process_message(_message("Сколько стоит?"), causation_event_id=uuid.uuid4())

    ai_client.generate_response.assert_not_awaited()
    conversation_repo.escalate.assert_awaited_once_with(conversation)
    assert message_repo.create_message_once.await_args.kwargs["is_escalation_trigger"] is True
    assert notify.call_args.kwargs["reason"] == "knowledge_missing:pricing"


@pytest.mark.asyncio
async def test_retrieved_knowledge_reaches_ai_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = _conversation()
    outbound = SimpleNamespace(id=10)
    delivery = SimpleNamespace(id=uuid.uuid4())
    ai_client = SimpleNamespace(generate_response=AsyncMock(return_value="Подтверждённый ответ"))
    message_repo = SimpleNamespace(
        get_by_causation=AsyncMock(return_value=None),
        get_history=AsyncMock(return_value=[]),
        create_message_once=AsyncMock(return_value=(outbound, True)),
    )
    service = ConversationService(
        db=SimpleNamespace(commit=AsyncMock()),
        platform_repo=SimpleNamespace(),
        client_repo=SimpleNamespace(),
        conversation_repo=SimpleNamespace(escalate=AsyncMock(), increment_ai_messages=AsyncMock()),
        message_repo=message_repo,
        platform_adapter=SimpleNamespace(),
        ai_client=ai_client,
        outbound_delivery_repo=SimpleNamespace(
            create_for_message=AsyncMock(return_value=(delivery, True))
        ),
        knowledge_retriever=SimpleNamespace(
            retrieve=AsyncMock(
                return_value=[
                    {
                        "question": "Сколько стоит?",
                        "answer": "Цена определяется после обсуждения эскиза.",
                    }
                ]
            )
        ),
    )
    service._resolve_conversation = AsyncMock(return_value=(_client(), conversation))
    monkeypatch.setattr(conversation_module.IntentClassifier, "classify", lambda _text: "pricing")
    monkeypatch.setattr(
        conversation_module.EscalationEngine,
        "should_escalate",
        lambda _intent, _text: (False, ""),
    )
    monkeypatch.setattr(conversation_module.deliver_outbound_message_task, "delay", MagicMock())

    await service.process_message(_message("Сколько стоит?"), causation_event_id=uuid.uuid4())

    history = ai_client.generate_response.await_args.args[0]
    assert "Цена определяется после обсуждения эскиза" in history[0]["content"]


@pytest.mark.asyncio
async def test_admin_create_persists_generated_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector = [0.1] * 384
    item = KnowledgeBase(
        id=1,
        category="pricing",
        question="Сколько стоит?",
        answer="После обсуждения эскиза",
        keywords=["цена"],
        priority=10,
        is_active=True,
        question_vector=vector,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo = SimpleNamespace(create=AsyncMock(return_value=item))
    monkeypatch.setattr(knowledge_api, "KnowledgeBaseRepository", lambda _db: repo)
    monkeypatch.setattr(
        knowledge_api,
        "generate_knowledge_embedding",
        AsyncMock(return_value=vector),
    )

    result = await knowledge_api.create_knowledge(
        KnowledgeBaseCreate(
            category="pricing",
            question="Сколько стоит?",
            answer="После обсуждения эскиза",
            keywords=["цена"],
            priority=10,
        ),
        SimpleNamespace(),
    )

    assert result.embedding_ready is True
    assert repo.create.await_args.kwargs["question_vector"] == vector


@pytest.mark.asyncio
async def test_admin_update_regenerates_embedding_for_content_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = KnowledgeBase(
        id=1,
        category="pricing",
        question="Старая формулировка",
        answer="Старый ответ",
        keywords=["цена"],
        priority=10,
        is_active=True,
        question_vector=[0.1] * 384,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async def update(obj: KnowledgeBase, **kwargs: object) -> KnowledgeBase:
        for key, value in kwargs.items():
            setattr(obj, key, value)
        return obj

    repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=item), update=AsyncMock(side_effect=update)
    )
    vector = [0.2] * 384
    monkeypatch.setattr(knowledge_api, "KnowledgeBaseRepository", lambda _db: repo)
    monkeypatch.setattr(
        knowledge_api,
        "generate_knowledge_embedding",
        AsyncMock(return_value=vector),
    )

    result = await knowledge_api.update_knowledge(
        1, KnowledgeBaseUpdate(answer="Новый подтверждённый ответ"), SimpleNamespace()
    )

    assert result.embedding_ready is True
    assert repo.update.await_args.kwargs["question_vector"] == vector
