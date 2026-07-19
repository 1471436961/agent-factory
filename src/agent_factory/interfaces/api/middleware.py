"""Pure ASGI request correlation and bounded-body middleware."""

from __future__ import annotations

from collections import deque
from http import HTTPStatus
from uuid import UUID

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent_factory.application.ports import CorrelationContext, IdGenerator
from agent_factory.interfaces.api.errors import error_response


class RequestContextMiddleware:
    """Set one correlation context and enforce a body-size ceiling per request."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        correlation_context: CorrelationContext,
        id_generator: IdGenerator,
        max_request_bytes: int,
    ) -> None:
        if max_request_bytes < 1:
            raise ValueError("max_request_bytes must be positive")
        self._app = app
        self._correlation_context = correlation_context
        self._id_generator = id_generator
        self._max_request_bytes = max_request_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        correlation_id = self._id_generator.new()
        supplied_id = headers.get("x-correlation-id")
        if supplied_id is not None:
            try:
                correlation_id = UUID(supplied_id)
            except ValueError:
                await self._send_error(
                    scope,
                    receive,
                    send,
                    status_code=HTTPStatus.BAD_REQUEST,
                    code="INVALID_CORRELATION_ID",
                    message="X-Correlation-ID must be a UUID",
                    correlation_id=correlation_id,
                )
                return

        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                await self._send_error(
                    scope,
                    receive,
                    send,
                    status_code=HTTPStatus.BAD_REQUEST,
                    code="INVALID_CONTENT_LENGTH",
                    message="Content-Length must be an integer",
                    correlation_id=correlation_id,
                )
                return
            if declared_size > self._max_request_bytes:
                await self._send_too_large(scope, receive, send, correlation_id)
                return

        state = scope.setdefault("state", {})
        state["correlation_id"] = correlation_id
        token = self._correlation_context.set(str(correlation_id))
        try:
            buffered_messages = await self._receive_bounded(receive)
            if buffered_messages is None:
                await self._send_too_large(scope, receive, send, correlation_id)
                return

            async def replay_receive() -> Message:
                if buffered_messages:
                    return buffered_messages.popleft()
                return {"type": "http.disconnect"}

            async def correlated_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    MutableHeaders(scope=message)["X-Correlation-ID"] = str(
                        correlation_id
                    )
                await send(message)

            await self._app(scope, replay_receive, correlated_send)
        finally:
            self._correlation_context.reset(token)

    async def _receive_bounded(
        self,
        receive: Receive,
    ) -> deque[Message] | None:
        messages: deque[Message] = deque()
        received_bytes = 0

        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                return messages

            received_bytes += len(message.get("body", b""))
            if received_bytes > self._max_request_bytes:
                return None
            if not message.get("more_body", False):
                return messages

    async def _send_too_large(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        correlation_id: UUID,
    ) -> None:
        await self._send_error(
            scope,
            receive,
            send,
            status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            code="REQUEST_TOO_LARGE",
            message="Request body exceeds the configured size limit",
            correlation_id=correlation_id,
            details={"max_bytes": self._max_request_bytes},
        )

    @staticmethod
    async def _send_error(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        code: str,
        message: str,
        correlation_id: UUID,
        details: dict[str, object] | None = None,
    ) -> None:
        response = error_response(
            status_code=status_code,
            code=code,
            message=message,
            details=details,
            correlation_id=correlation_id,
        )
        await response(scope, receive, send)
