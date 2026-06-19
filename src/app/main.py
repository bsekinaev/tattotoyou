"""
Главная точка входа в приложение.
"""
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.infrastructure.db.session import init_db, close_db
from app.api.webhooks.telegram import router as telegram_router
from app.services.platforms.telegram.client import tg_client

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "application_starting",
        app_name=settings.app_name,
        version=settings.app_version,
    )

    await init_db()
    logger.info("application_started")

    yield  # <-- приложение работает

    logger.info("application_shutting_down")
    await close_db()
    await tg_client.close()
    logger.info("application_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"] if settings.debug else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"])
    async def health_check():
        return {"status": "ok", "version": settings.app_version}

    @app.get("/", tags=["system"])
    async def root():
        return {"service": settings.app_name, "version": settings.app_version}

    app.include_router(telegram_router, prefix="/webhook", tags=["webhooks"])

    return app


app = create_app()