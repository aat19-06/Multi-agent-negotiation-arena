import asyncio

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int, timeout_seconds: int):
        self.app = app
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    await self._respond(
                        send,
                        413,
                        f"Request body exceeds the {self.max_bytes}-byte limit.",
                    )
                    return
            except ValueError:
                await self._respond(
                    send,
                    413,
                    f"Request body exceeds the {self.max_bytes}-byte limit.",
                )
                return

        body = bytearray()
        body_too_large = False
        try:
            async with asyncio.timeout(self.timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if message["type"] != "http.request":
                        continue
                    body.extend(message.get("body", b""))
                    if len(body) > self.max_bytes:
                        body_too_large = True
                        break
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await self._respond(send, 408, "Request body was not received in time.")
            return

        if body_too_large:
            await self._respond(
                send,
                413,
                f"Request body exceeds the {self.max_bytes}-byte limit.",
            )
            return

        body_bytes = bytes(body)
        first_message = True

        async def replay_receive() -> Message:
            nonlocal first_message
            if first_message:
                first_message = False
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _respond(send: Send, status_code: int, detail: str) -> None:
        body = f'{{"detail":"{detail}"}}'.encode("ascii")
        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; "
                        b"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                        b"img-src 'self' data: https:; font-src 'self' data: https://fonts.gstatic.com; "
                        b"connect-src 'self'; "
                        b"object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
                    ),
                ])
                if settings.ENVIRONMENT.lower() == "production":
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, add_headers)
