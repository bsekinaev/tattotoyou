"""Ограничение тела HTTP-запроса до разбора JSON и Pydantic-валидации."""

from collections.abc import Iterable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger

logger = get_logger(__name__)


class RequestBodyLimitMiddleware:
    """Отклонять слишком большие тела запросов только для выбранных путей.

    ``Content-Length`` используется для быстрого отказа, но не считается
    доверенным источником. Middleware также считает фактически полученные байты
    ASGI-stream и поэтому защищает от отсутствующего или поддельного заголовка.
    В памяти удерживается не более ``max_body_bytes`` плюс один входной chunk.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        paths: Iterable[str],
    ) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")

        self.app = app
        self.max_body_bytes = max_body_bytes
        self.paths = frozenset(paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") not in self.paths:
            await self.app(scope, receive, send)
            return

        declared_size = self._content_length(scope)
        if declared_size is not None and declared_size > self.max_body_bytes:
            await self._reject(
                scope,
                receive,
                send,
                reason="content_length",
                declared_size=declared_size,
            )
            return

        buffered_messages: list[Message] = []
        observed_size = 0

        while True:
            message = await receive()
            buffered_messages.append(message)

            if message["type"] == "http.disconnect":
                break

            if message["type"] != "http.request":
                continue

            observed_size += len(message.get("body", b""))
            if observed_size > self.max_body_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    reason="stream_size",
                    declared_size=declared_size,
                    observed_size=observed_size,
                )
                return

            if not message.get("more_body", False):
                break

        message_index = 0

        async def replay_receive() -> Message:
            nonlocal message_index
            if message_index < len(buffered_messages):
                message = buffered_messages[message_index]
                message_index += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        values: list[int] = []
        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                parsed = int(value.decode("ascii").strip())
            except (UnicodeDecodeError, ValueError):
                continue
            if parsed >= 0:
                values.append(parsed)

        return max(values) if values else None

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        reason: str,
        declared_size: int | None = None,
        observed_size: int | None = None,
    ) -> None:
        logger.warning(
            "request_body_rejected",
            path=scope.get("path"),
            reason=reason,
            declared_size=declared_size,
            observed_size=observed_size,
            max_body_bytes=self.max_body_bytes,
        )
        response = JSONResponse(
            status_code=413,
            content={"detail": "Request body too large"},
        )
        await response(scope, receive, send)
