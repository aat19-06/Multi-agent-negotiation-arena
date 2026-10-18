from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limits import request_limiter
from app.db.session import get_db
from app.db.models import User
from app.core.security import get_current_user, hash_password, verify_password
from app.schemas.schemas import UserPublic, UserUpdateRequest, PasswordChangeRequest

router = APIRouter()


@router.get("/me", response_model=UserPublic)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserPublic.model_validate(current_user)


@router.patch("/me", response_model=UserPublic)
async def update_me(
    body: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.name is not None:
        current_user.name = body.name
    if body.avatar_url is not None:
        current_user.avatar_url = body.avatar_url
    await db.flush()
    await db.refresh(current_user)
    return UserPublic.model_validate(current_user)


@router.post("/me/change-password", status_code=204)
async def change_password(
    request: Request,
    body: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(
        request,
        "change-password",
        user_id=current_user.id,
        ip_limit=10,
        user_limit=5,
        window_seconds=3600,
    )
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.hashed_password = hash_password(body.new_password)
    current_user.token_version += 1
    await db.flush()
