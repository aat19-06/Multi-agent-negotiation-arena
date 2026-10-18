import math
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limits import request_limiter
from app.core.security import get_current_user
from app.db.models import Negotiation, Offer, User
from app.db.session import get_db
from app.engine.scenarios import get_scenario_rules
from app.schemas.schemas import OutcomeReport

router = APIRouter()


@router.get("/{negotiation_id}", response_model=OutcomeReport)
async def get_report(
    request: Request,
    negotiation_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request_limiter.check(
        request,
        "report",
        user_id=current_user.id,
        ip_limit=60,
        user_limit=30,
    )
    result = await db.execute(select(Negotiation).where(Negotiation.id == negotiation_id))
    neg = result.scalar_one_or_none()
    if not neg:
        raise HTTPException(status_code=404, detail="Negotiation not found")
    if neg.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    offers_result = await db.execute(
        select(Offer)
        .where(Offer.negotiation_id == negotiation_id)
        .order_by(Offer.created_at, Offer.id)
        .limit(300)
    )
    try:
        rules = get_scenario_rules(neg.scenario_type, neg.config)
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
        rules = None

    offers = []
    for offer in offers_result.scalars().all():
        if (
            rules is None
            or not isinstance(offer.offer_data, dict)
            or type(offer.round_number) is not int
            or not 1 <= offer.round_number <= neg.max_rounds
            or offer.made_by not in ("human", "agent_a", "agent_b")
        ):
            continue
        try:
            rules.validate_offer(offer.offer_data)
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
            continue
        offers.append(offer)

    raw_outcome = neg.outcome_data
    outcome = raw_outcome if isinstance(raw_outcome, dict) else {}
    valid_outcome_terms = _valid_offer(outcome.get("agreed_terms"), rules)
    initial_offer = _valid_offer(outcome.get("initial_offer"), rules)
    final_offer = _valid_offer(outcome.get("final_offer"), rules)
    if initial_offer is None and offers:
        initial_offer = offers[0].offer_data
    if final_offer is None and offers:
        final_offer = offers[-1].offer_data

    round_numbers = {offer.round_number for offer in offers}
    timeline = [
        {
            "round": offer.round_number,
            "made_by": offer.made_by,
            "offer_data": offer.offer_data,
            "status": _status_value(offer.status),
        }
        for offer in offers
    ]
    turning_points = _find_turning_points(offers)
    return OutcomeReport(
        negotiation_id=neg.id,
        title=neg.title,
        scenario_type=neg.scenario_type,
        mode=neg.mode,
        status=neg.status,
        final_outcome=_outcome_label(neg.status, outcome.get("final_outcome")),
        agreed_terms=valid_outcome_terms if _status_value(neg.status) == "completed" else None,
        breakdown_reason=_bounded_text(outcome.get("breakdown_reason"), 500),
        initial_offer=initial_offer,
        final_offer=final_offer,
        total_rounds=len(round_numbers),
        concession_timeline=timeline,
        satisfaction_scores=(
            _valid_satisfaction(outcome.get("satisfaction_scores"))
            if _status_value(neg.status) == "completed"
            else None
        ),
        key_turning_points=turning_points,
        suggestions=_generate_suggestions(neg, offers),
        completed_at=neg.completed_at,
    )


def _valid_offer(value, rules):
    if rules is None or not isinstance(value, dict):
        return None
    try:
        rules.validate_offer(value)
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
        return None
    return value


def _status_value(status) -> str:
    return getattr(status, "value", status)


def _bounded_text(value, limit: int):
    return value[:limit] if isinstance(value, str) else None


def _outcome_label(status, recorded_value) -> str:
    recorded = _bounded_text(recorded_value, 500)
    if recorded:
        return recorded
    return {
        "completed": "Agreement reached",
        "deadlocked": "Negotiation ended without agreement",
        "cancelled": "Negotiation cancelled",
        "paused": "Negotiation paused",
        "active": "Negotiation in progress",
        "pending": "Negotiation not started",
    }.get(_status_value(status), "Negotiation outcome unavailable")


def _valid_satisfaction(value):
    if not isinstance(value, dict) or len(value) > 8:
        return None
    if any(
        not isinstance(key, str)
        or not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not math.isfinite(score)
        or not 0 <= score <= 100
        for key, score in value.items()
    ):
        return None
    return value


def _generate_suggestions(neg: Negotiation, offers: List[Offer]) -> List[str]:
    suggestions = []
    if _status_value(neg.status) == "deadlocked":
        suggestions.append(
            "Heuristic (not a measured outcome): consider discussing non-monetary terms."
        )
    if len([offer for offer in offers if offer.made_by == "human"]) > 1:
        suggestions.append(
            "Heuristic (not a measured outcome): review the human offers for concession patterns."
        )
    if not suggestions:
        suggestions.append(
            "Heuristic (not a measured outcome): review the offer timeline and clarify priorities."
        )
    return suggestions


def _find_turning_points(offers: List[Offer]) -> List[str]:
    points = []
    for offer in offers:
        status = _status_value(offer.status)
        if status == "accepted":
            points.append(f"Round {offer.round_number}: Offer accepted.")
        elif status == "rejected":
            points.append(f"Round {offer.round_number}: Offer rejected.")
    return points[:5]
