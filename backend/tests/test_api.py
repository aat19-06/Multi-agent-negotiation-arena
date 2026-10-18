import math
import pytest
from app.core.security import create_access_token
from app.core.limits import sse_gate


def test_token_subject_is_bounded_to_database_id_range():
    from app.core.security import _token_user_id

    assert _token_user_id({"sub": "2147483647"}) == 2147483647
    assert _token_user_id({"sub": "2147483648"}) is None
    assert _token_user_id({"sub": "1.5"}) is None
    assert _token_user_id({"sub": 1}) is None


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_register(client):
    resp = await client.post("/api/v1/auth/register", json={
        "name": "Alice",
        "email": "alice@example.com",
        "password": "password123",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert data["user"]["email"] == "alice@example.com"
    assert data["user"]["name"] == "Alice"


@pytest.mark.asyncio
async def test_register_duplicate_email(client):
    payload = {"name": "Bob", "email": "bob@example.com", "password": "password123"}
    await client.post("/api/v1/auth/register", json=payload)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_login(client):
    await client.post("/api/v1/auth/register", json={
        "name": "Charlie", "email": "charlie@example.com", "password": "secret123",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "charlie@example.com", "password": "secret123",
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post("/api/v1/auth/register", json={
        "name": "Dave", "email": "dave@example.com", "password": "secret123",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "dave@example.com", "password": "wrongpassword",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_me(auth_client):
    resp = await auth_client.get("/api/v1/users/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_create_negotiation(auth_client):
    resp = await auth_client.post("/api/v1/negotiations", json={
        "title": "Test Vendor Negotiation",
        "scenario_type": "vendor_pricing",
        "mode": "simulation",
        "config": {"starting_price": 100000, "buyer_target_price": 80000},
        "max_rounds": 5,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test Vendor Negotiation"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_get_negotiation_detail(auth_client):
    created = await auth_client.post("/api/v1/negotiations", json={
        "title": "Negotiation Detail",
        "scenario_type": "vendor_pricing",
        "mode": "simulation",
        "config": {"starting_price": 100000},
        "max_rounds": 5,
    })
    negotiation_id = created.json()["id"]

    resp = await auth_client.get(f"/api/v1/negotiations/{negotiation_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == negotiation_id
    assert data["config"] == {"starting_price": 100000}
    assert data["messages"] == []
    assert data["offers"] == []


@pytest.mark.asyncio
async def test_list_negotiations(auth_client):
    await auth_client.post("/api/v1/negotiations", json={
        "title": "Neg 1", "scenario_type": "vendor_pricing",
        "mode": "simulation", "config": {}, "max_rounds": 5,
    })
    await auth_client.post("/api/v1/negotiations", json={
        "title": "Neg 2", "scenario_type": "job_offer",
        "mode": "practice", "config": {}, "max_rounds": 8,
    })
    resp = await auth_client.get("/api/v1/negotiations")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_negotiation_ownership(auth_client, client):
    """Other user cannot access negotiation."""
    resp = await auth_client.post("/api/v1/negotiations", json={
        "title": "Secret", "scenario_type": "vendor_pricing",
        "mode": "simulation", "config": {}, "max_rounds": 5,
    })
    neg_id = resp.json()["id"]

    # Register a second user
    resp2 = await client.post("/api/v1/auth/register", json={
        "name": "Eve", "email": "eve@example.com", "password": "evepass123",
    })
    other_token = resp2.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {other_token}"

    resp3 = await client.get(f"/api/v1/negotiations/{neg_id}")
    assert resp3.status_code == 403


@pytest.mark.asyncio
async def test_practice_offer_statuses_show_human_turn_and_ai_counter(auth_client):
    created = await auth_client.post("/api/v1/negotiations", json={
        "title": "Practice Vendor Negotiation",
        "scenario_type": "vendor_pricing",
        "mode": "practice",
        "config": {
            "starting_price": 100000,
            "buyer_target_price": 80000,
            "buyer_max_price": 95000,
            "vendor_min_price": 85000,
            "quantity": 100,
            "agent_b_personality": "collaborative",
        },
        "max_rounds": 5,
    })
    negotiation_id = created.json()["id"]
    started = await auth_client.post(f"/api/v1/negotiations/{negotiation_id}/start")
    assert started.status_code == 200

    submitted = await auth_client.post(
        f"/api/v1/negotiations/{negotiation_id}/offer",
        json={"offer_data": {"price": 80000}, "message": "My counter-offer"},
    )
    assert submitted.status_code == 200
    assert submitted.json()["decision"] == "counter"

    detail = await auth_client.get(f"/api/v1/negotiations/{negotiation_id}")
    assert detail.status_code == 200
    offers = detail.json()["offers"]
    assert offers[0]["made_by"] == "human"
    assert offers[0]["status"] == "countered"
    assert offers[1]["made_by"] == "agent_b"
    assert offers[1]["status"] == "pending"


@pytest.mark.asyncio
async def test_negotiation_report_returns_outcome_and_offer_timeline(auth_client):
    created = await auth_client.post("/api/v1/negotiations", json={
        "title": "Vendor Report",
        "scenario_type": "vendor_pricing",
        "mode": "practice",
        "config": {"starting_price": 100000},
        "max_rounds": 5,
    })
    negotiation_id = created.json()["id"]

    report = await auth_client.get(f"/api/v1/reports/{negotiation_id}")

    assert report.status_code == 200
    data = report.json()
    assert data["negotiation_id"] == negotiation_id
    assert data["scenario_type"] == "vendor_pricing"
    assert data["concession_timeline"] == []
    assert data["suggestions"]


@pytest.mark.asyncio
async def test_update_negotiation_title(auth_client):
    resp = await auth_client.post("/api/v1/negotiations", json={
        "title": "Old Title", "scenario_type": "vendor_pricing",
        "mode": "simulation", "config": {}, "max_rounds": 5,
    })
    neg_id = resp.json()["id"]

    resp2 = await auth_client.patch(f"/api/v1/negotiations/{neg_id}", json={"title": "New Title"})
    assert resp2.status_code == 200
    assert resp2.json()["title"] == "New Title"


@pytest.mark.asyncio
async def test_delete_negotiation(auth_client):
    resp = await auth_client.post("/api/v1/negotiations", json={
        "title": "To Delete", "scenario_type": "vendor_pricing",
        "mode": "simulation", "config": {}, "max_rounds": 5,
    })
    neg_id = resp.json()["id"]

    resp2 = await auth_client.delete(f"/api/v1/negotiations/{neg_id}")
    assert resp2.status_code == 204

    resp3 = await auth_client.get(f"/api/v1/negotiations/{neg_id}")
    assert resp3.status_code == 404


@pytest.mark.asyncio
async def test_scenario_defaults(auth_client):
    for endpoint in [
        "/api/v1/scenarios/defaults/vendor-pricing",
        "/api/v1/scenarios/defaults/job-offer",
        "/api/v1/scenarios/defaults/project-budget",
    ]:
        resp = await auth_client.get(endpoint)
        assert resp.status_code == 200

    job_offer = (await auth_client.get("/api/v1/scenarios/defaults/job-offer")).json()
    project_budget = (await auth_client.get("/api/v1/scenarios/defaults/project-budget")).json()
    assert job_offer["starting_salary"] == 900000
    assert project_budget["total_budget"] == 5000000
    assert project_budget["alpha_requested"] == 3000000


@pytest.mark.asyncio
async def test_personalities(auth_client):
    resp = await auth_client.get("/api/v1/scenarios/personalities")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    ids = [p["id"] for p in data]
    assert "aggressive" in ids
    assert "collaborative" in ids
    assert "risk_averse" in ids


@pytest.mark.asyncio
async def test_login_is_rate_limited_per_ip(client):
    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": "incorrect"},
        )
        assert response.status_code == 401

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "incorrect"},
    )
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_request_body_size_is_limited(client):
    response = await client.post(
        "/api/v1/auth/login",
        content=b"x" * 65537,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_negotiation_rejects_invalid_mode_and_financial_values(auth_client):
    base = {
        "title": "Invalid configuration",
        "scenario_type": "vendor_pricing",
        "mode": "simulation",
        "config": {"starting_price": 100000},
        "max_rounds": 5,
    }
    invalid_mode = {**base, "mode": "paid"}
    response = await auth_client.post("/api/v1/negotiations", json=invalid_mode)
    assert response.status_code == 422

    negative_config = {**base, "config": {"starting_price": -1}}
    response = await auth_client.post("/api/v1/negotiations", json=negative_config)
    assert response.status_code == 422

    too_many_rounds = {**base, "max_rounds": 31}
    response = await auth_client.post("/api/v1/negotiations", json=too_many_rounds)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_assistant_requests_are_rate_limited_per_user(auth_client):
    payload = {"message": "Hello", "history": []}
    for _ in range(10):
        response = await auth_client.post("/api/v1/conversations/chat", json=payload)
        assert response.status_code == 200

    response = await auth_client.post("/api/v1/conversations/chat", json=payload)
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_assistant_message_and_history_lengths_are_bounded(auth_client):
    too_long_message = await auth_client.post(
        "/api/v1/conversations/chat",
        json={"message": "x" * 2001, "history": []},
    )
    assert too_long_message.status_code == 422

    too_many_messages = await auth_client.post(
        "/api/v1/conversations/chat",
        json={
            "message": "Hello",
            "history": [{"role": "user", "content": "Earlier message"} for _ in range(21)],
        },
    )
    assert too_many_messages.status_code == 422


@pytest.mark.asyncio
async def test_refresh_rotation_and_logout_revoke_tokens(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"name": "Token User", "email": "tokens@example.com", "password": "tokenpass123"},
    )
    assert response.status_code == 201
    first_tokens = response.json()

    rotated = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_tokens["refresh_token"]},
    )
    assert rotated.status_code == 200
    second_tokens = rotated.json()

    reused = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_tokens["refresh_token"]},
    )
    assert reused.status_code == 401

    client.headers["Authorization"] = f"Bearer {second_tokens['access_token']}"
    logout = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": second_tokens["refresh_token"]},
    )
    assert logout.status_code == 204
    profile = await client.get("/api/v1/users/me")
    assert profile.status_code == 401


@pytest.mark.asyncio
async def test_password_change_revokes_existing_tokens(auth_client):
    response = await auth_client.post(
        "/api/v1/users/me/change-password",
        json={"current_password": "testpassword123", "new_password": "newpassword456"},
    )
    assert response.status_code == 204
    profile = await auth_client.get("/api/v1/users/me")
    assert profile.status_code == 401


@pytest.mark.asyncio
async def test_malformed_signed_token_subject_returns_unauthorized(client):
    client.headers["Authorization"] = f"Bearer {create_access_token({'sub': 'not-an-id'})}"
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_detail_and_history_requests_are_capped(auth_client):
    for title in ("First", "Second"):
        response = await auth_client.post(
            "/api/v1/negotiations",
            json={
                "title": title,
                "scenario_type": "vendor_pricing",
                "mode": "simulation",
                "config": {},
                "max_rounds": 5,
            },
        )
        assert response.status_code == 201
    limited = await auth_client.get("/api/v1/negotiations?limit=1")
    assert limited.status_code == 200
    assert len(limited.json()) == 1


@pytest.mark.asyncio
async def test_sse_connection_limit_returns_429(auth_client):
    created = await auth_client.post(
        "/api/v1/negotiations",
        json={
            "title": "SSE limit",
            "scenario_type": "vendor_pricing",
            "mode": "practice",
            "config": {},
            "max_rounds": 5,
        },
    )
    negotiation_id = created.json()["id"]
    for _ in range(20):
        assert sse_gate.try_acquire()
    response = await auth_client.get(f"/api/v1/negotiations/{negotiation_id}/stream")
    assert response.status_code == 429
    for _ in range(20):
        sse_gate.release()


@pytest.mark.asyncio
async def test_sse_closes_after_terminal_status(auth_client, monkeypatch):
    created = await auth_client.post(
        "/api/v1/negotiations",
        json={
            "title": "Finished stream",
            "scenario_type": "vendor_pricing",
            "mode": "practice",
            "config": {},
            "max_rounds": 5,
        },
    )
    negotiation_id = created.json()["id"]
    ended = await auth_client.post(f"/api/v1/negotiations/{negotiation_id}/end")
    assert ended.status_code == 200

    response = await auth_client.get(f"/api/v1/negotiations/{negotiation_id}/stream")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert "event: completed" in response.text


@pytest.mark.asyncio
async def test_assistant_provider_quota_error_is_returned_to_client(auth_client, monkeypatch):
    from app.agents.errors import AIProviderError

    async def quota_error(self, **kwargs):
        raise AIProviderError(429, "The AI provider rate limit or free quota has been reached.")

    monkeypatch.setattr("app.api.v1.conversations.AssistantAgent.chat", quota_error)
    response = await auth_client.post(
        "/api/v1/conversations/chat",
        json={"message": "Set up a negotiation", "history": []},
    )
    assert response.status_code == 429
    assert "quota" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_simulation_provider_failure_pauses_without_mock_fallback(auth_client, monkeypatch):
    from app.core.config import settings
    from app.engine.negotiation_engine import NegotiationEngine
    from app.db.models import Negotiation, NegotiationStatus
    from app.db import session as db_session
    from sqlalchemy import select

    created = await auth_client.post(
        "/api/v1/negotiations",
        json={
            "title": "Provider unavailable",
            "scenario_type": "vendor_pricing",
            "mode": "simulation",
            "config": {},
            "max_rounds": 5,
        },
    )
    assert created.status_code == 201
    negotiation_id = created.json()["id"]
    monkeypatch.setattr(settings, "USE_MOCK_AGENTS", False)
    monkeypatch.setattr(settings, "GROQ_API_KEY", None)

    async with db_session.AsyncSessionLocal() as db:
        negotiation = await db.scalar(select(Negotiation).where(Negotiation.id == negotiation_id))
        negotiation.status = NegotiationStatus.ACTIVE
        await db.commit()
    await NegotiationEngine.run_simulation(negotiation_id)

    detail = await auth_client.get(f"/api/v1/negotiations/{negotiation_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "paused"
    assert any("no Groq API key" in message["content"] for message in detail.json()["messages"])


@pytest.mark.asyncio
async def test_malformed_human_offers_return_422_without_advancing_round(auth_client):
    created = await auth_client.post(
        "/api/v1/negotiations",
        json={
            "title": "Malformed offer",
            "scenario_type": "vendor_pricing",
            "mode": "practice",
            "config": {},
            "max_rounds": 5,
        },
    )
    negotiation_id = created.json()["id"]
    started = await auth_client.post(f"/api/v1/negotiations/{negotiation_id}/start")
    assert started.status_code == 200

    invalid_offers = (
        {"price": None},
        {"price": "90000"},
        {"price": 0},
        {"price": float("nan")},
        {"unknown_field": 1},
        {"quantity": 100},
        {f"field_{index}": index for index in range(17)},
    )
    for offer_data in invalid_offers:
        payload = {"offer_data": offer_data, "message": "Try this offer"}
        if not (
            isinstance(offer_data.get("price"), float)
            and math.isnan(offer_data["price"])
        ):
            response = await auth_client.post(
                f"/api/v1/negotiations/{negotiation_id}/offer",
                json=payload,
            )
        else:
            import json

            response = await auth_client.post(
                f"/api/v1/negotiations/{negotiation_id}/offer",
                content=json.dumps(payload, allow_nan=True),
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 422

    empty = await auth_client.post(
        f"/api/v1/negotiations/{negotiation_id}/offer",
        json={"offer_data": {}, "message": "   "},
    )
    assert empty.status_code == 422
    detail = await auth_client.get(f"/api/v1/negotiations/{negotiation_id}")
    assert detail.json()["current_round"] == 0
    assert detail.json()["offers"] == []


@pytest.mark.asyncio
async def test_malformed_stored_configuration_fails_with_client_error(auth_client):
    from sqlalchemy import select

    from app.db import session as db_session
    from app.db.models import Negotiation

    created = await auth_client.post(
        "/api/v1/negotiations",
        json={
            "title": "Corrupted config",
            "scenario_type": "vendor_pricing",
            "mode": "practice",
            "config": {},
            "max_rounds": 5,
        },
    )
    negotiation_id = created.json()["id"]
    async with db_session.AsyncSessionLocal() as db:
        negotiation = await db.scalar(
            select(Negotiation).where(Negotiation.id == negotiation_id)
        )
        negotiation.config = ["unexpected"]
        await db.commit()

    response = await auth_client.post(f"/api/v1/negotiations/{negotiation_id}/start")
    assert response.status_code == 422
    assert "configuration is invalid" in response.json()["detail"]


@pytest.mark.asyncio
async def test_terminal_negotiations_reject_all_mutating_transitions(auth_client):
    from sqlalchemy import select

    from app.db import session as db_session
    from app.db.models import Negotiation, NegotiationStatus

    created = await auth_client.post(
        "/api/v1/negotiations",
        json={
            "title": "Terminal state",
            "scenario_type": "vendor_pricing",
            "mode": "practice",
            "config": {},
            "max_rounds": 5,
        },
    )
    negotiation_id = created.json()["id"]
    for status in (
        NegotiationStatus.COMPLETED,
        NegotiationStatus.DEADLOCKED,
        NegotiationStatus.CANCELLED,
    ):
        async with db_session.AsyncSessionLocal() as db:
            negotiation = await db.scalar(
                select(Negotiation).where(Negotiation.id == negotiation_id)
            )
            negotiation.status = status
            await db.commit()

        start = await auth_client.post(f"/api/v1/negotiations/{negotiation_id}/start")
        pause = await auth_client.post(f"/api/v1/negotiations/{negotiation_id}/pause")
        end = await auth_client.post(f"/api/v1/negotiations/{negotiation_id}/end")
        offer = await auth_client.post(
            f"/api/v1/negotiations/{negotiation_id}/offer",
            json={"offer_data": {"price": 90000}, "message": "Late offer"},
        )
        assert [start.status_code, pause.status_code, end.status_code, offer.status_code] == [
            409, 409, 409, 409,
        ]


@pytest.mark.asyncio
async def test_paused_simulation_resumes_without_duplicating_previous_round(auth_client):
    from sqlalchemy import select

    from app.db import session as db_session
    from app.db.models import (
        MessageRole,
        Negotiation,
        NegotiationMessage,
        NegotiationStatus,
        Offer,
    )

    created = await auth_client.post(
        "/api/v1/negotiations",
        json={
            "title": "Resume simulation",
            "scenario_type": "vendor_pricing",
            "mode": "simulation",
            "config": {},
            "max_rounds": 3,
        },
    )
    negotiation_id = created.json()["id"]
    async with db_session.AsyncSessionLocal() as db:
        negotiation = await db.scalar(
            select(Negotiation).where(Negotiation.id == negotiation_id)
        )
        negotiation.status = NegotiationStatus.PAUSED
        negotiation.current_round = 1
        negotiation.outcome_data = {"final_outcome": "stale result"}
        db.add_all([
            Offer(
                negotiation_id=negotiation_id, round_number=1, made_by="agent_a",
                offer_data={"price": 80000, "quantity": 100}, rationale="first",
            ),
            Offer(
                negotiation_id=negotiation_id, round_number=1, made_by="agent_b",
                offer_data={"price": 100000, "quantity": 100}, rationale="counter",
            ),
            NegotiationMessage(
                negotiation_id=negotiation_id, role=MessageRole.AGENT_A,
                content="First offer", round_number=1,
            ),
            NegotiationMessage(
                negotiation_id=negotiation_id, role=MessageRole.AGENT_B,
                content="Counter", round_number=1,
            ),
            NegotiationMessage(
                negotiation_id=negotiation_id, role=MessageRole.AGENT_A,
                content="Still negotiating", round_number=1,
            ),
        ])
        await db.commit()

    started = await auth_client.post(f"/api/v1/negotiations/{negotiation_id}/start")
    assert started.status_code == 200
    detail = await auth_client.get(f"/api/v1/negotiations/{negotiation_id}")
    assert detail.status_code == 200
    assert [offer["round_number"] for offer in detail.json()["offers"]].count(1) == 2
    assert "stale result" not in str(detail.json()["outcome_data"])
    assert any(
        "resumed at round 2" in message["content"]
        for message in detail.json()["messages"]
    )


@pytest.mark.asyncio
async def test_completed_simulation_cannot_be_started_again(auth_client):
    from sqlalchemy import select

    from app.db import session as db_session
    from app.db.models import Negotiation, NegotiationStatus

    created = await auth_client.post(
        "/api/v1/negotiations",
        json={
            "title": "Cannot restart terminal simulation",
            "scenario_type": "vendor_pricing",
            "mode": "simulation",
            "config": {},
            "max_rounds": 3,
        },
    )
    negotiation_id = created.json()["id"]
    async with db_session.AsyncSessionLocal() as db:
        negotiation = await db.scalar(
            select(Negotiation).where(Negotiation.id == negotiation_id)
        )
        negotiation.status = NegotiationStatus.COMPLETED
        await db.commit()

    first = await auth_client.post(f"/api/v1/negotiations/{negotiation_id}/start")
    second = await auth_client.post(f"/api/v1/negotiations/{negotiation_id}/start")
    assert first.status_code == second.status_code == 409


@pytest.mark.asyncio
async def test_report_ignores_malformed_persisted_events(auth_client):
    from sqlalchemy import select

    from app.db import session as db_session
    from app.db.models import Negotiation, Offer

    created = await auth_client.post(
        "/api/v1/negotiations",
        json={
            "title": "Malformed report history",
            "scenario_type": "vendor_pricing",
            "mode": "practice",
            "config": {},
            "max_rounds": 5,
        },
    )
    negotiation_id = created.json()["id"]
    async with db_session.AsyncSessionLocal() as db:
        negotiation = await db.scalar(
            select(Negotiation).where(Negotiation.id == negotiation_id)
        )
        negotiation.outcome_data = ["not", "an", "object"]
        db.add(Offer(
            negotiation_id=negotiation_id,
            round_number=99,
            made_by="agent_a",
            offer_data={"price": None},
            rationale="malformed historical offer",
        ))
        await db.commit()

    report = await auth_client.get(f"/api/v1/reports/{negotiation_id}")
    assert report.status_code == 200
    assert report.json()["total_rounds"] == 0
    assert report.json()["concession_timeline"] == []
    assert report.json()["agreed_terms"] is None
    assert all("Heuristic" in item for item in report.json()["suggestions"])
    assert "config" not in report.json()
