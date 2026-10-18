import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    _token_user_id,
    _token_version_matches,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.limits import request_limiter
from app.db.models import User
from app.db.session import get_db
from app.schemas.schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
)

router = APIRouter()


def _email_rate_key(email: str) -> str:
    return hashlib.sha256(email.casefold().encode("utf-8")).hexdigest()


def _tokens_for(user: User) -> TokenResponse:
    claims = {"sub": str(user.id), "ver": user.token_version}
    return TokenResponse(
        access_token=create_access_token(claims),
        refresh_token=create_refresh_token(claims),
        user=UserPublic.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(request: Request, body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    request_limiter.check(
        request,
        "register",
        user_id=_email_rate_key(body.email),
        ip_limit=10,
        user_limit=3,
        window_seconds=3600,
    )
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        name=body.name,
        email=body.email,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return _tokens_for(user)


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    request_limiter.check(
        request,
        "login",
        user_id=_email_rate_key(body.email),
        ip_limit=5,
        user_limit=10,
        window_seconds=3600,
    )
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    return _tokens_for(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(request, "refresh", ip_limit=10, window_seconds=60)
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = _token_user_id(payload)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Refresh token is invalid or has already been used")
    request_limiter.check(
        request,
        "refresh-user",
        user_id=user.id,
        ip_limit=20,
        user_limit=10,
    )
    if not _token_version_matches(payload, user):
        raise HTTPException(status_code=401, detail="Refresh token is invalid or has already been used")

    new_version_result = await db.execute(
        update(User)
        .where(User.id == user.id, User.token_version == user.token_version)
        .values(token_version=User.token_version + 1)
        .returning(User.token_version)
    )
    new_version = new_version_result.scalar_one_or_none()
    if new_version is None:
        raise HTTPException(status_code=401, detail="Refresh token is invalid or has already been used")
    user.token_version = new_version
    return _tokens_for(user)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(request, "logout-attempt", ip_limit=20)
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = _token_user_id(payload)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not _token_version_matches(payload, user):
        raise HTTPException(status_code=401, detail="Refresh token is invalid or has already been used")
    request_limiter.check(
        request,
        "logout-user",
        user_id=user.id,
        ip_limit=20,
        user_limit=10,
    )

    new_version_result = await db.execute(
        update(User)
        .where(User.id == user.id, User.token_version == user.token_version)
        .values(token_version=User.token_version + 1)
        .returning(User.token_version)
    )
    if new_version_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=401, detail="Refresh token is invalid or has already been used")
    return Response(status_code=204)
