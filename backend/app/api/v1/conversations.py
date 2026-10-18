from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.agents.errors import AIProviderError
from app.core.limits import request_limiter
from app.db.session import get_db
from app.db.models import User, Negotiation
from app.core.security import get_current_user
from app.schemas.schemas import AssistantChatRequest, AssistantChatResponse
from app.agents.assistant_agent import AssistantAgent

router = APIRouter()


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(
    request: Request,
    body: AssistantChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI Negotiation Assistant — conversational setup."""
    request_limiter.check(
        request,
        "assistant-chat",
        user_id=current_user.id,
        ip_limit=30,
        user_limit=10,
    )
    agent = AssistantAgent()
    try:
        response = await agent.chat(
            user_message=body.message,
            history=body.history,
            user_id=current_user.id,
            negotiation_id=body.negotiation_id,
            db=db,
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return response
