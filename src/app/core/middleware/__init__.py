"""ASGI middleware приложения."""

from app.core.middleware.request_body_limit import RequestBodyLimitMiddleware

__all__ = ["RequestBodyLimitMiddleware"]
