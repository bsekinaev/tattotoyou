"""Проверка изоляции SQLAlchemy engine между event loop Celery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.pool import NullPool

from app.infrastructure.db import worker_session as module


class SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_worker_session_uses_null_pool_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = SimpleEngine()
    create_engine = MagicMock(return_value=engine)
    session_factory = MagicMock(return_value=SessionContext())
    sessionmaker = MagicMock(return_value=session_factory)
    monkeypatch.setattr(module, "create_async_engine", create_engine)
    monkeypatch.setattr(module, "async_sessionmaker", sessionmaker)

    async with module.worker_session_scope():
        pass

    assert create_engine.call_args.kwargs["poolclass"] is NullPool
    engine.dispose.assert_awaited_once()


class SimpleEngine:
    def __init__(self) -> None:
        self.dispose = AsyncMock()
