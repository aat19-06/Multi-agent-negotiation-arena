from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
import json
import asyncio
import time

from app.agents.errors import AIProviderError
from app.core.config import settings
from app.core.limits import request_limiter, simulation_gate, sse_gate
from app.db.session import AsyncSessionLocal, get_db
from app.db.models import User, Negotiation, NegotiationMessage, Offer, NegotiationStatus, MessageRole
from app.core.security import get_current_user
from app.schemas.schemas import (
    NegotiationCreate, NegotiationUpdate, NegotiationSummary, NegotiationDetail,
    HumanOfferRequest, MessageOut, OfferOut
)
from app.engine.negotiation_engine import NegotiationEngine
from app.engine.scenarios import get_scenario_rules

router = APIRouter()


def _check_owner(negotiation: Negotiation, user: User):
    if negotiation.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("", response_model=List[NegotiationSummary])
async def list_negotiations(
    request: Request,
    archived: bool = False,
    search: Optional[str] = Query(None, max_length=120),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(
        request,
        "negotiation-list",
        user_id=current_user.id,
        ip_limit=120,
        user_limit=60,
    )
    stmt = select(Negotiation).where(
        Negotiation.user_id == current_user.id,
        Negotiation.is_archived == archived,
    )
    if search:
        stmt = stmt.where(Negotiation.title.ilike(f"%{search}%"))
    stmt = stmt.order_by(Negotiation.updated_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=NegotiationSummary, status_code=201)
async def create_negotiation(
    request: Request,
    body: NegotiationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(
        request,
        "negotiation-create",
        user_id=current_user.id,
        ip_limit=20,
        user_limit=5,
    )
    await db.execute(select(User.id).where(User.id == current_user.id).with_for_update())
    count_result = await db.execute(
        select(func.count()).select_from(Negotiation).where(Negotiation.user_id == current_user.id)
    )
    if count_result.scalar_one() >= settings.MAX_NEGOTIATIONS_PER_USER:
        raise HTTPException(
            status_code=409,
            detail="Negotiation limit reached. Delete an existing negotiation before creating another.",
        )

    neg = Negotiation(
        user_id=current_user.id,
        title=body.title,
        scenario_type=body.scenario_type,
        mode=body.mode,
        config=body.config,
        max_rounds=body.max_rounds,
        status=NegotiationStatus.PENDING,
    )
    db.add(neg)
    await db.flush()
    await db.refresh(neg)
    return NegotiationSummary.model_validate(neg)


@router.get("/{negotiation_id}", response_model=NegotiationDetail)
async def get_negotiation(
    negotiation_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Negotiation).where(Negotiation.id == negotiation_id))
    neg = result.scalar_one_or_none()
    if not neg:
        raise HTTPException(status_code=404, detail="Negotiation not found")
    _check_owner(neg, current_user)

    messages = await db.execute(
        select(NegotiationMessage).where(NegotiationMessage.negotiation_id == negotiation_id)
        .order_by(NegotiationMessage.created_at)
        .limit(300)
    )
    offers = await db.execute(
        select(Offer).where(Offer.negotiation_id == negotiation_id)
        .order_by(Offer.created_at)
        .limit(300)
    )

    summary = NegotiationSummary.model_validate(neg)
    return NegotiationDetail(
        **summary.model_dump(),
        config=neg.config,
        outcome_data=neg.outcome_data,
        messages=[MessageOut.model_validate(m) for m in messages.scalars().all()],
        offers=[OfferOut.model_validate(o) for o in offers.scalars().all()],
    )


@router.patch("/{negotiation_id}", response_model=NegotiationSummary)
async def update_negotiation(
    request: Request,
    body: NegotiationUpdate,
    negotiation_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(
        request,
        "negotiation-update",
        user_id=current_user.id,
        ip_limit=60,
        user_limit=30,
    )
    result = await db.execute(select(Negotiation).where(Negotiation.id == negotiation_id))
    neg = result.scalar_one_or_none()
    if not neg:
        raise HTTPException(status_code=404, detail="Not found")
    _check_owner(neg, current_user)

    if body.title is not None:
        neg.title = body.title
    if body.is_archived is not None:
        neg.is_archived = body.is_archived
    await db.flush()
    await db.refresh(neg)
    return NegotiationSummary.model_validate(neg)


@router.delete("/{negotiation_id}", status_code=204)
async def delete_negotiation(
    request: Request,
    negotiation_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(
        request,
        "negotiation-delete",
        user_id=current_user.id,
        ip_limit=30,
        user_limit=20,
    )
    result = await db.execute(select(Negotiation).where(Negotiation.id == negotiation_id))
    neg = result.scalar_one_or_none()
    if not neg:
        raise HTTPException(status_code=404, detail="Not found")
    _check_owner(neg, current_user)
    await db.delete(neg)


@router.post("/{negotiation_id}/start")
async def start_negotiation(
    request: Request,
    background_tasks: BackgroundTasks,
    negotiation_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(
        request,
        "negotiation-start",
        user_id=current_user.id,
        ip_limit=20,
        user_limit=6,
    )
    result = await db.execute(
        select(Negotiation)
        .where(Negotiation.id == negotiation_id)
        .with_for_update()
    )
    neg = result.scalar_one_or_none()
    if not neg:
        raise HTTPException(status_code=404, detail="Not found")
    _check_owner(neg, current_user)

    if neg.status not in (NegotiationStatus.PENDING, NegotiationStatus.PAUSED):
        raise HTTPException(status_code=409, detail=f"Cannot start negotiation in status: {neg.status}")
    if not isinstance(neg.config, dict):
        raise HTTPException(status_code=422, detail="Negotiation configuration is invalid.")
    try:
        get_scenario_rules(neg.scenario_type, neg.config)
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Negotiation configuration is invalid.") from exc

    await db.execute(select(User.id).where(User.id == current_user.id).with_for_update())
    active_result = await db.execute(
        select(func.count()).select_from(Negotiation).where(
            Negotiation.user_id == current_user.id,
            Negotiation.status == NegotiationStatus.ACTIVE,
        )
    )
    if active_result.scalar_one() >= settings.MAX_ACTIVE_NEGOTIATIONS_PER_USER:
        raise HTTPException(status_code=429, detail="Maximum concurrent negotiations reached.")

    simulation_started = neg.mode == "simulation"
    if simulation_started and not simulation_gate.try_acquire():
        raise HTTPException(status_code=429, detail="Simulation capacity is busy. Please try again shortly.")
    try:
        neg.status = NegotiationStatus.ACTIVE
        neg.outcome_data = None
        neg.completed_at = None
        await db.flush()
        await db.commit()
    except Exception:
        if simulation_started:
            simulation_gate.release()
        raise

    if simulation_started:
        background_tasks.add_task(_run_simulation_with_permit, negotiation_id)

    return {"status": "started", "negotiation_id": negotiation_id}


async def _run_simulation_with_permit(negotiation_id: int) -> None:
    try:
        await NegotiationEngine.run_simulation(negotiation_id)
    finally:
        simulation_gate.release()


@router.post("/{negotiation_id}/pause")
async def pause_negotiation(
    request: Request,
    negotiation_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(
        request,
        "negotiation-pause",
        user_id=current_user.id,
        ip_limit=60,
        user_limit=30,
    )
    result = await db.execute(
        select(Negotiation)
        .where(Negotiation.id == negotiation_id)
        .with_for_update()
    )
    neg = result.scalar_one_or_none()
    if not neg:
        raise HTTPException(status_code=404, detail="Not found")
    _check_owner(neg, current_user)
    if neg.status != NegotiationStatus.ACTIVE:
        raise HTTPException(status_code=409, detail=f"Cannot pause negotiation in status: {neg.status}")
    neg.status = NegotiationStatus.PAUSED
    await db.flush()
    return {"status": "paused"}


@router.post("/{negotiation_id}/end")
async def end_negotiation(
    request: Request,
    negotiation_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(
        request,
        "negotiation-end",
        user_id=current_user.id,
        ip_limit=60,
        user_limit=30,
    )
    result = await db.execute(
        select(Negotiation)
        .where(Negotiation.id == negotiation_id)
        .with_for_update()
    )
    neg = result.scalar_one_or_none()
    if not neg:
        raise HTTPException(status_code=404, detail="Not found")
    _check_owner(neg, current_user)
    if neg.status not in (
        NegotiationStatus.PENDING,
        NegotiationStatus.ACTIVE,
        NegotiationStatus.PAUSED,
    ):
        raise HTTPException(status_code=409, detail=f"Cannot end negotiation in status: {neg.status}")
    from datetime import datetime, timezone
    neg.status = NegotiationStatus.CANCELLED
    neg.completed_at = datetime.now(timezone.utc)
    await db.flush()
    return {"status": "ended"}


@router.post("/{negotiation_id}/offer")
async def submit_human_offer(
    request: Request,
    body: HumanOfferRequest,
    negotiation_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit a human offer in Practice Mode."""
    request_limiter.check(
        request,
        "negotiation-turn",
        user_id=current_user.id,
        ip_limit=30,
        user_limit=12,
    )
    result = await db.execute(
        select(Negotiation)
        .where(Negotiation.id == negotiation_id)
        .with_for_update()
    )
    neg = result.scalar_one_or_none()
    if not neg:
        raise HTTPException(status_code=404, detail="Not found")
    _check_owner(neg, current_user)

    if neg.status != NegotiationStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Negotiation is not active")
    if neg.mode != "practice":
        raise HTTPException(status_code=409, detail="Only Practice Mode accepts human offers")
    if neg.current_round >= neg.max_rounds:
        raise HTTPException(status_code=409, detail="Negotiation has reached its maximum number of rounds")

    try:
        rules = get_scenario_rules(neg.scenario_type, neg.config)
        if not body.offer_data and not (body.message and body.message.strip()):
            raise ValueError("Provide an offer or a negotiation statement")
        rules.validate_offer(body.offer_data)
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    engine = NegotiationEngine(negotiation_id, db)
    try:
        response = await engine.process_human_offer(body.offer_data, body.message)
    except AIProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return response


@router.get("/{negotiation_id}/stream")
async def stream_negotiation(
    request: Request,
    negotiation_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE stream for live negotiation updates."""
    request_limiter.check(
        request,
        "negotiation-stream",
        user_id=current_user.id,
        ip_limit=60,
        user_limit=10,
    )
    result = await db.execute(select(Negotiation).where(Negotiation.id == negotiation_id))
    neg = result.scalar_one_or_none()
    if not neg:
        raise HTTPException(status_code=404, detail="Not found")
    _check_owner(neg, current_user)
    await db.commit()
    if not sse_gate.try_acquire():
        raise HTTPException(status_code=429, detail="Live stream capacity is busy. Please try again shortly.")

    async def event_generator():
        last_message_id = 0
        started_at = time.monotonic()
        last_heartbeat = started_at
        try:
            while time.monotonic() - started_at < settings.SSE_MAX_SECONDS:
                if await request.is_disconnected():
                    break

                events = []
                async with AsyncSessionLocal() as stream_db:
                    new_msgs = await stream_db.execute(
                        select(NegotiationMessage)
                        .where(
                            NegotiationMessage.negotiation_id == negotiation_id,
                            NegotiationMessage.id > last_message_id,
                        )
                        .order_by(NegotiationMessage.id)
                        .limit(100)
                    )
                    for msg in new_msgs.scalars().all():
                        last_message_id = msg.id
                        events.append(f"event: message\ndata: {MessageOut.model_validate(msg).model_dump_json()}\n\n")

                    neg_result = await stream_db.execute(
                        select(Negotiation).where(Negotiation.id == negotiation_id)
                    )
                    current_neg = neg_result.scalar_one_or_none()
                    current_status = current_neg.status if current_neg else None
                    outcome = current_neg.outcome_data if current_neg else None

                for event in events:
                    yield event

                if current_status is None:
                    yield 'event: unavailable\ndata: {"status":"not_found"}\n\n'
                    break
                if current_status in (
                    NegotiationStatus.COMPLETED,
                    NegotiationStatus.DEADLOCKED,
                    NegotiationStatus.CANCELLED,
                ):
                    yield f"event: completed\ndata: {json.dumps({'status': current_status, 'outcome': outcome})}\n\n"
                    break
                if current_status == NegotiationStatus.PAUSED:
                    yield f"event: paused\ndata: {json.dumps({'status': current_status, 'outcome': outcome})}\n\n"
                    break
                if time.monotonic() - last_heartbeat >= settings.SSE_HEARTBEAT_SECONDS:
                    yield ": keep-alive\n\n"
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(1)
            else:
                yield 'event: timeout\ndata: {"message":"Live stream time limit reached."}\n\n'
        finally:
            sse_gate.release()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
