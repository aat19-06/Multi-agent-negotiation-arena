import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.core.limits import ai_request_gate, request_limiter, simulation_gate, sse_gate
from app.db.session import Base, get_db
from app.core.config import settings

# Use SQLite for tests (in-memory)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db(monkeypatch):
    request_limiter.reset()
    ai_request_gate.reset()
    simulation_gate.reset()
    sse_gate.reset()
    monkeypatch.setattr(settings, "USE_MOCK_AGENTS", True)
    monkeypatch.setattr(settings, "GROQ_API_KEY", None)
    monkeypatch.setattr("app.main.engine", test_engine)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.engine.negotiation_engine.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.api.v1.negotiations.AsyncSessionLocal", TestSessionLocal)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_client(client):
    """Client with registered and logged-in user."""
    resp = await client.post("/api/v1/auth/register", json={
        "name": "Test User",
        "email": "test@example.com",
        "password": "testpassword123",
    })
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
