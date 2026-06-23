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
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

# 🆕 Knowledge Base Admin API
from app.api.admin.knowledge import router as admin_knowledge_router
from app.api.webhooks.telegram import router as telegram_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.infrastructure.db.session import async_engine, close_db, init_db
from app.services.platforms.telegram.client import tg_client

# Инициализируем логирование ПЕРВЫМ ДЕЛОМ
setup_logging()
logger = get_logger(__name__)

# Глобальные ресурсы (будут инициализированы в lifespan)
redis_client: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Управление жизненным циклом приложения.
    Гарантирует корректную инициализацию и очистку всех ресурсов.
    """
    global redis_client
    settings = get_settings()

    logger.info(
        "application_starting",
        app_name=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
    )

    # ============================================
    # STARTUP: Инициализируем все ресурсы
    # ============================================
    try:
        # 1. Инициализируем базу данных
        await init_db()
        logger.info("database_ready")

        # 2. Инициализируем Redis
        redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        await redis_client.ping()
        app.state.redis = redis_client
        logger.info("redis_ready")

        logger.info("application_started")

    except Exception as e:
        logger.exception("application_startup_failed", error=str(e))
        raise

    # ============================================
    # ПРИЛОЖЕНИЕ РАБОТАЕТ
    # ============================================
    yield

    # ============================================
    # SHUTDOWN: Корректно закрываем все ресурсы
    # ============================================
    logger.info("application_shutting_down")

    try:
        await tg_client.close()
        logger.info("telegram_client_closed")
    except Exception as e:
        logger.error("telegram_client_close_failed", error=str(e))

    try:
        if redis_client:
            await redis_client.close()
            logger.info("redis_closed")
    except Exception as e:
        logger.error("redis_close_failed", error=str(e))

    try:
        await close_db()
        logger.info("database_closed")
    except Exception as e:
        logger.error("database_close_failed", error=str(e))

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
        """Добавляет уникальный request_id для каждого запроса."""
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

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
        logger.exception(
            "unhandled_exception",
            request_id=request_id,
            path=request.url.path,
            error=str(exc),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
        )

    # ============================================
    # ROUTES
    # ============================================

    @app.get("/health", tags=["system"])
    async def health_check():
        """Health check эндпоинт для мониторинга."""
        health_status = {
            "status": "ok",
            "version": settings.app_version,
            "checks": {},
        }

        # PostgreSQL
        try:
            async with async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            health_status["checks"]["database"] = "ok"
        except Exception as e:
            health_status["checks"]["database"] = f"error: {str(e)}"
            health_status["status"] = "degraded"

        # Redis
        try:
            if redis_client:
                await redis_client.ping()
                health_status["checks"]["redis"] = "ok"
            else:
                health_status["checks"]["redis"] = "not_initialized"
                health_status["status"] = "degraded"
        except Exception as e:
            health_status["checks"]["redis"] = f"error: {str(e)}"
            health_status["status"] = "degraded"

        status_code = 200 if health_status["status"] == "ok" else 503
        return JSONResponse(content=health_status, status_code=status_code)

    @app.get("/", tags=["system"])
    async def root():
        """Корневой эндпоинт."""
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs" if settings.debug else "disabled in production",
            "health": "/health",
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
