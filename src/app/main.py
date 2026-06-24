"""
Главная точка входа в приложение.
Production-ready FastAPI application с:
- Graceful startup/shutdown (lifespan)
- Redis для кэширования и дедупликации
- Health checks с реальной проверкой зависимостей
- Request ID middleware для трейсинга
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from structlog.contextvars import bind_contextvars, clear_contextvars

# 🆕 Knowledge Base Admin API
from app.api.admin.knowledge import router as admin_knowledge_router
from app.api.webhooks.telegram import router as telegram_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.infrastructure.db.session import async_engine, close_db, init_db

# Инициализируем логирование ПЕРВЫМ ДЕЛОМ
setup_logging()
logger = get_logger(__name__)

# Глобальные ресурсы (будут инициализированы в lifespan)
redis_client: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Управлять ресурсами приложения и состоянием инфраструктуры.

    В локальной разработке приложение может стартовать в degraded-режиме:
    ``/live`` остаётся доступным, а ``/ready`` возвращает 503 до восстановления
    PostgreSQL и Redis. В Docker/production включён строгий режим, поэтому
    недоступная обязательная зависимость останавливает startup.
    """
    global redis_client
    settings = get_settings()

    logger.info(
        "application_starting",
        app_name=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        startup_require_dependencies=settings.startup_require_dependencies,
        postgres_target=f"{settings.postgres_host}:{settings.postgres_port}",
        redis_target=f"{settings.redis_host}:{settings.redis_port}",
    )

    unavailable: list[str] = []

    async with AsyncExitStack() as resources:
        resources.push_async_callback(close_db)

        redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_connect_timeout_seconds,
            retry_on_timeout=True,
        )
        resources.push_async_callback(redis_client.aclose)
        app.state.redis = redis_client

        try:
            await init_db()
            logger.info("database_ready")
        except Exception as exc:
            unavailable.append("database")
            logger.error(
                "database_startup_check_failed",
                error_type=type(exc).__name__,
                postgres_target=f"{settings.postgres_host}:{settings.postgres_port}",
            )

        try:
            await redis_client.ping()
            logger.info("redis_ready")
        except Exception as exc:
            unavailable.append("redis")
            logger.error(
                "redis_startup_check_failed",
                error_type=type(exc).__name__,
                redis_target=f"{settings.redis_host}:{settings.redis_port}",
            )

        app.state.startup_unavailable_dependencies = tuple(unavailable)

        if unavailable and settings.startup_require_dependencies:
            logger.error(
                "application_startup_failed",
                unavailable_dependencies=unavailable,
            )
            raise RuntimeError(
                "Required infrastructure is unavailable: " + ", ".join(unavailable)
            )

        if unavailable:
            logger.warning(
                "application_started_degraded",
                unavailable_dependencies=unavailable,
            )
        else:
            logger.info("application_started")

        try:
            yield
        finally:
            logger.info("application_shutting_down")

    redis_client = None
    logger.info("application_stopped")


def create_app() -> FastAPI:
    """
    Factory function для создания FastAPI приложения.
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # ============================================
    # MIDDLEWARE
    # ============================================

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        """Добавить request ID и связать его со структурированными логами."""
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        clear_contextvars()
        bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            clear_contextvars()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=(
            ["http://localhost:3000", "http://localhost:8080"] if settings.debug else []
        ),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # ============================================
    # EXCEPTION HANDLERS
    # ============================================

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Глобальный обработчик необработанных исключений."""
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error(
            "unhandled_exception",
            request_id=request_id,
            path=request.url.path,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )

    # ============================================
    # ROUTES
    # ============================================

    async def readiness_response() -> JSONResponse:
        """Проверить обязательные зависимости без раскрытия деталей ошибок."""
        checks: dict[str, str] = {}

        try:
            async with async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = "error"
            logger.exception(
                "database_readiness_failed",
                error_type=type(exc).__name__,
            )

        try:
            if redis_client is None:
                checks["redis"] = "error"
                logger.warning("redis_readiness_failed", reason="not_initialized")
            else:
                await redis_client.ping()
                checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = "error"
            logger.exception(
                "redis_readiness_failed",
                error_type=type(exc).__name__,
            )

        is_ready = all(status == "ok" for status in checks.values())
        return JSONResponse(
            content={
                "status": "ok" if is_ready else "not_ready",
                "version": settings.app_version,
                "checks": checks,
            },
            status_code=200 if is_ready else 503,
        )

    @app.get("/live", tags=["system"])
    async def liveness_check() -> dict[str, str]:
        """Подтвердить, что HTTP-процесс приложения работает."""
        return {
            "status": "ok",
            "service": settings.app_name,
            "version": settings.app_version,
        }

    @app.get("/ready", tags=["system"])
    async def readiness_check() -> JSONResponse:
        """Подтвердить доступность PostgreSQL и Redis."""
        return await readiness_response()

    @app.get("/health", include_in_schema=False)
    async def legacy_health_check() -> JSONResponse:
        """Обратная совместимость: старый health endpoint равен readiness."""
        return await readiness_response()

    @app.get("/", tags=["system"])
    async def root():
        """Корневой эндпоинт."""
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs" if settings.debug else "disabled in production",
            "liveness": "/live",
            "readiness": "/ready",
        }

    # ============================================
    # ПОДКЛЮЧЕНИЕ РОУТЕРОВ
    # ============================================
    app.include_router(telegram_router, prefix="/webhook", tags=["webhooks"])
    app.include_router(
        admin_knowledge_router,
        prefix="/admin",
        tags=["admin"],
    )

    return app


app = create_app()