import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.core.limits as limits
from app.core.limits import ConcurrencyGate, SlidingWindowLimiter
from app.core.middleware import RequestBodyLimitMiddleware


def _request(ip: str) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "client": (ip, 12345),
        "server": ("test", 80),
        "scheme": "http",
    })


def test_rate_limiter_capacity_fails_closed_without_evicting_live_buckets():
    limiter = SlidingWindowLimiter(max_keys=2)
    limiter.check(_request("192.0.2.1"), "login", ip_limit=1)
    limiter.check(_request("192.0.2.2"), "login", ip_limit=1)

    with pytest.raises(HTTPException) as full:
        limiter.check(_request("192.0.2.3"), "login", ip_limit=1)
    assert full.value.status_code == 429

    with pytest.raises(HTTPException) as retained:
        limiter.check(_request("192.0.2.1"), "login", ip_limit=1)
    assert retained.value.status_code == 429


def test_rate_limiter_reclaims_expired_keys_only_when_capacity_is_reached(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(limits, "monotonic", lambda: clock[0])
    limiter = SlidingWindowLimiter(max_keys=1)
    limiter.check(_request("192.0.2.1"), "login", ip_limit=1)

    clock[0] = 60.0
    limiter.check(_request("192.0.2.2"), "login", ip_limit=1)

    assert set(limiter._buckets) == {"ip:login:192.0.2.2"}


def test_concurrency_gate_enforces_and_releases_capacity():
    gate = ConcurrencyGate(limit=1)
    assert gate.try_acquire()
    assert not gate.try_acquire()

    gate.release()
    assert gate.try_acquire()
    gate.release()


@pytest.mark.asyncio
async def test_slow_request_body_is_rejected_with_timeout():
    async def app(scope, receive, send):
        raise AssertionError("Timed-out request must not reach the app")

    middleware = RequestBodyLimitMiddleware(app, max_bytes=10, timeout_seconds=0.01)
    messages = []

    async def receive():
        await asyncio.sleep(1)

    async def send(message):
        messages.append(message)

    await middleware(
        {"type": "http", "headers": [], "method": "POST", "path": "/"},
        receive,
        send,
    )

    assert messages[0]["status"] == 408
