from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
import logging

from app.core.config import settings, validate_production_secret
from app.db.session import engine, Base
from app.api.v1 import auth, users, negotiations, conversations, scenarios, reports
from app.core.middleware import RequestBodyLimitMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


async def initialize_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.ENVIRONMENT.lower() == "production":
        validate_production_secret(settings.SECRET_KEY)
        if settings.USE_SQLITE_FALLBACK:
            raise RuntimeError("Disable USE_SQLITE_FALLBACK in production.")
        if not settings.DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
            raise RuntimeError("Production requires a PostgreSQL DATABASE_URL.")
    else:
        await initialize_database()
    yield


app = FastAPI(
    title="Negotiation Arena API",
    description="AI-powered multi-agent negotiation platform",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = [
        {
            "loc": list(error.get("loc", ())),
            "msg": error.get("msg", "Invalid request value"),
            "type": error.get("type", "value_error"),
        }
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


allowed_origins = []
frontend_origin = settings.FRONTEND_URL.rstrip("/")
if settings.ENVIRONMENT.lower() != "production" or not frontend_origin.startswith(
    ("http://localhost", "http://127.0.0.1")
):
    allowed_origins.append(frontend_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=settings.MAX_REQUEST_BODY_BYTES,
    timeout_seconds=settings.REQUEST_BODY_TIMEOUT_SECONDS,
)
app.add_middleware(SecurityHeadersMiddleware)

# Routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(negotiations.router, prefix="/api/v1/negotiations", tags=["negotiations"])
app.include_router(conversations.router, prefix="/api/v1/conversations", tags=["conversations"])
app.include_router(scenarios.router, prefix="/api/v1/scenarios", tags=["scenarios"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["reports"])


@app.get("/health")
async def health():
    try:
        async with engine.connect() as connection:
            if settings.ENVIRONMENT.lower() == "production":
                await connection.execute(text("SELECT token_version FROM users LIMIT 1"))
            else:
                await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.error("Database readiness check failed (%s).", type(exc).__name__)
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok", "version": "1.0.0"}


frontend_index = Path(__file__).resolve().parents[1] / "static" / "index.html"
if frontend_index.is_file():
    @app.get("/{frontend_path:path}", include_in_schema=False)
    async def serve_frontend(frontend_path: str):
        static_root = frontend_index.parent.resolve()
        requested_file = (static_root / frontend_path).resolve()
        if requested_file.is_relative_to(static_root) and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(frontend_index)
