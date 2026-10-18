from datetime import datetime
import math
from typing import Any, Literal, Optional, List
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


def _validate_json_value(value: Any, depth: int = 0) -> None:
    if depth > 4:
        raise ValueError("Configuration nesting is too deep")
    if value is None:
        raise ValueError("Configuration values must not be null")
    if isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > 500:
            raise ValueError("Configuration strings must not exceed 500 characters")
        return
    if isinstance(value, int):
        if value < 0 or value > 1_000_000_000_000:
            raise ValueError("Configuration numbers must be non-negative and within the supported range")
        return
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or value > 1_000_000_000_000:
            raise ValueError("Configuration numbers must be finite, non-negative, and within the supported range")
        return
    if isinstance(value, list):
        if len(value) > 50:
            raise ValueError("Configuration lists must not exceed 50 items")
        for item in value:
            _validate_json_value(item, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 32 or any(not isinstance(key, str) or len(key) > 64 for key in value):
            raise ValueError("Configuration objects contain too many or invalid keys")
        for item in value.values():
            _validate_json_value(item, depth + 1)
        return
    raise ValueError("Configuration values must be JSON-compatible")


def _validate_password_bytes(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Passwords must not exceed 72 UTF-8 bytes")
    return password


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── Auth ──────────────────────────────────────────────────────────────────────

class RegisterRequest(RequestModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)

    _password_length = field_validator("password")(_validate_password_bytes)


class LoginRequest(RequestModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=72)

    _password_length = field_validator("password")(_validate_password_bytes)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserPublic"


class RefreshRequest(RequestModel):
    refresh_token: str = Field(..., min_length=1, max_length=4096)


# ── Users ─────────────────────────────────────────────────────────────────────

class UserPublic(BaseModel):
    id: int
    name: str
    email: str
    avatar_url: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdateRequest(RequestModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    avatar_url: Optional[str] = Field(None, max_length=500)


class PasswordChangeRequest(RequestModel):
    current_password: str = Field(..., min_length=1, max_length=72)
    new_password: str = Field(..., min_length=8, max_length=72)

    _password_lengths = field_validator("current_password", "new_password")(_validate_password_bytes)


# ── Negotiations ──────────────────────────────────────────────────────────────

class NegotiationCreate(RequestModel):
    title: str = Field(..., min_length=1, max_length=255)
    scenario_type: Literal["vendor_pricing", "job_offer", "project_budget", "custom"]
    mode: Literal["simulation", "practice"]
    config: dict[str, Any] = Field(..., max_length=32)
    max_rounds: int = Field(default=10, ge=3, le=30)

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value)
        for key in ("agent_a_personality", "agent_b_personality"):
            if key in value and value[key] not in ("aggressive", "collaborative", "risk_averse"):
                raise ValueError(f"{key} is not a supported personality")
        for key in ("agent_a_name", "agent_b_name"):
            if key in value and (not isinstance(value[key], str) or len(value[key]) > 80):
                raise ValueError(f"{key} must be a string of at most 80 characters")
        return value

    @model_validator(mode="after")
    def validate_scenario_values(self):
        common_fields = {
            "scenario_type", "mode", "title", "agent_a_name", "agent_b_name",
            "agent_a_personality", "agent_b_personality", "max_rounds",
        }
        scenario_fields = {
            "vendor_pricing": {
                "starting_price", "buyer_target_price", "buyer_max_price",
                "vendor_min_price", "quantity", "delivery_days", "payment_terms",
            },
            "job_offer": {
                "starting_salary", "candidate_target", "candidate_min",
                "recruiter_budget", "benefits", "joining_days",
            },
            "project_budget": {
                "total_budget", "alpha_requested", "beta_requested",
                "alpha_minimum", "beta_minimum", "priority_weights",
            },
            "custom": {"opening_offer", "agent_descriptions"},
        }
        unexpected = set(self.config) - common_fields - scenario_fields[self.scenario_type]
        if unexpected:
            raise ValueError(f"Unsupported configuration fields: {', '.join(sorted(unexpected))}")
        for key, expected in (("scenario_type", self.scenario_type), ("mode", self.mode)):
            if key in self.config and self.config[key] != expected:
                raise ValueError(f"config.{key} must match the top-level value")

        for key in ("title", "agent_a_name", "agent_b_name"):
            value = self.config.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{key} must be a non-empty string")
        if "title" in self.config and len(self.config["title"]) > 255:
            raise ValueError("config.title must not exceed 255 characters")
        if "max_rounds" in self.config and (
            type(self.config["max_rounds"]) is not int
            or not 3 <= self.config["max_rounds"] <= 30
        ):
            raise ValueError("config.max_rounds must be an integer between 3 and 30")
        for key in ("agent_a_name", "agent_b_name"):
            if key in self.config and (
                not isinstance(self.config[key], str)
                or not self.config[key].strip()
                or len(self.config[key]) > 80
            ):
                raise ValueError(f"{key} must be a non-empty string of at most 80 characters")

        def finite_positive(field: str, default: float) -> float:
            value = self.config.get(field, default)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field} must be a finite positive number")
            return float(value)

        positive_fields = {
            "vendor_pricing": ("starting_price", "buyer_target_price", "buyer_max_price", "vendor_min_price", "quantity"),
            "job_offer": ("starting_salary", "candidate_target", "candidate_min", "recruiter_budget"),
            "project_budget": ("total_budget",),
        }
        for field in positive_fields.get(self.scenario_type, ()):
            value = self.config.get(field)
            if value is not None and (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{field} must be a positive number")
        for field in ("delivery_days", "joining_days"):
            value = self.config.get(field)
            if value is not None and (type(value) is not int or value > 3650):
                raise ValueError(f"{field} must be an integer between 0 and 3650")
        for field in ("alpha_requested", "beta_requested", "alpha_minimum", "beta_minimum"):
            value = self.config.get(field)
            if value is not None and (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value > 1_000_000_000_000
            ):
                raise ValueError(f"{field} is outside the supported range")
        weights = self.config.get("priority_weights")
        if weights is not None:
            if not isinstance(weights, dict) or set(weights) - {"alpha", "beta"} or any(
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= 1
                for value in weights.values()
            ):
                raise ValueError("priority_weights must contain numbers between 0 and 1")
        if self.scenario_type == "vendor_pricing":
            starting_price = finite_positive("starting_price", 100000)
            target = finite_positive("buyer_target_price", 80000)
            buyer_max = finite_positive("buyer_max_price", 95000)
            vendor_min = finite_positive("vendor_min_price", 85000)
            quantity = self.config.get("quantity", 100)
            if type(quantity) is not int or quantity > 1_000_000:
                raise ValueError("quantity must be an integer between 1 and 1000000")
            if target > buyer_max:
                raise ValueError("buyer_target_price must not exceed buyer_max_price")
            if buyer_max < vendor_min:
                raise ValueError("buyer_max_price must be at least vendor_min_price")
            if starting_price <= vendor_min:
                raise ValueError("starting_price must exceed vendor_min_price")
            if target > starting_price:
                raise ValueError("buyer_target_price must not exceed starting_price")
            if "payment_terms" in self.config and (
                not isinstance(self.config["payment_terms"], str)
                or len(self.config["payment_terms"]) > 120
            ):
                raise ValueError("payment_terms must be a string of at most 120 characters")
        elif self.scenario_type == "job_offer":
            starting_salary = finite_positive("starting_salary", 900000)
            candidate_min = finite_positive("candidate_min", 1000000)
            candidate_target = finite_positive("candidate_target", 1100000)
            recruiter_budget = finite_positive("recruiter_budget", 1050000)
            if candidate_min > candidate_target:
                raise ValueError("candidate_min must not exceed candidate_target")
            if candidate_target < starting_salary:
                raise ValueError("candidate_target must not be below starting_salary")
            if candidate_min > recruiter_budget:
                raise ValueError("recruiter_budget must be at least candidate_min")
            if recruiter_budget < starting_salary:
                raise ValueError("recruiter_budget must not be below starting_salary")
            benefits = self.config.get("benefits", [])
            if not isinstance(benefits, list) or len(benefits) > 50 or any(
                not isinstance(item, str) or not item or len(item) > 80 for item in benefits
            ):
                raise ValueError("benefits must be a list of at most 50 short strings")
        elif self.scenario_type == "project_budget":
            total_budget = finite_positive("total_budget", 5000000)
            alpha_minimum = finite_positive("alpha_minimum", 2000000)
            beta_minimum = finite_positive("beta_minimum", 2000000)
            alpha_requested = finite_positive("alpha_requested", 3000000)
            beta_requested = finite_positive("beta_requested", 3500000)
            if alpha_minimum + beta_minimum > total_budget:
                raise ValueError("Project minimum allocations cannot exceed total_budget")
            if alpha_requested < alpha_minimum:
                raise ValueError("alpha_requested must meet alpha_minimum")
            if beta_requested < beta_minimum:
                raise ValueError("beta_requested must meet beta_minimum")
        else:
            opening_offer = self.config.get("opening_offer")
            descriptions = self.config.get("agent_descriptions")
            if opening_offer is not None and (
                not isinstance(opening_offer, dict)
                or not opening_offer
                or len(opening_offer) > 16
            ):
                raise ValueError("custom opening_offer must be a non-empty object")
            if isinstance(opening_offer, dict):
                for key, value in opening_offer.items():
                    if not isinstance(key, str) or not key or len(key) > 64:
                        raise ValueError("custom opening offer keys must be short strings")
                    if isinstance(value, str) and len(value) <= 120:
                        continue
                    if isinstance(value, list) and len(value) <= 20 and all(
                        isinstance(item, str) and 0 < len(item) <= 80 for item in value
                    ):
                        continue
                    if (
                        type(value) in (int, float)
                        and math.isfinite(value)
                        and 0 <= value <= 1_000_000_000_000
                    ):
                        continue
                    raise ValueError(f"custom opening offer field {key} has an unsupported value")
            if descriptions is not None and (
                not isinstance(descriptions, dict)
                or not descriptions
                or set(descriptions) - {"agent_a", "agent_b"}
            ):
                raise ValueError("custom agent_descriptions must name supported parties")
            if descriptions is not None and any(
                not isinstance(value, str) or len(value) > 500
                for value in descriptions.values()
            ):
                raise ValueError("custom agent descriptions must be strings of at most 500 characters")
        for key, expected in (("scenario_type", self.scenario_type), ("mode", self.mode)):
            if key in self.config and self.config[key] != expected:
                raise ValueError(f"config.{key} must match the top-level value")
        config_rounds = self.config.get("max_rounds")
        if config_rounds is not None and (
            type(config_rounds) is not int or not 3 <= config_rounds <= 30
        ):
            raise ValueError("config.max_rounds must be an integer between 3 and 30")
        return self


class NegotiationUpdate(RequestModel):
    title: Optional[str] = Field(None, max_length=255)
    is_archived: Optional[bool] = None


class NegotiationSummary(BaseModel):
    id: int
    title: str
    scenario_type: str
    mode: str
    status: str
    current_round: int
    max_rounds: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class NegotiationDetail(NegotiationSummary):
    config: dict
    outcome_data: Optional[dict] = None
    messages: List["MessageOut"] = []
    offers: List["OfferOut"] = []


# ── Messages ──────────────────────────────────────────────────────────────────

class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    round_number: Optional[int] = None
    metadata_: Optional[dict] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Offers ────────────────────────────────────────────────────────────────────

class OfferOut(BaseModel):
    id: int
    round_number: int
    made_by: str
    offer_data: dict
    status: str
    rationale: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class HumanOfferRequest(RequestModel):
    offer_data: dict[str, Any] = Field(..., max_length=16)
    message: Optional[str] = Field(None, max_length=2000)

    @field_validator("offer_data")
    @classmethod
    def validate_offer_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value)
        return value


# ── Scenarios ─────────────────────────────────────────────────────────────────

class ScenarioConfig(BaseModel):
    """Base scenario config"""
    scenario_type: str
    mode: str
    max_rounds: int = 10
    agent_a_personality: str = "collaborative"
    agent_b_personality: str = "aggressive"
    agent_a_name: str = "Party A"
    agent_b_name: str = "Party B"


class VendorPricingConfig(ScenarioConfig):
    scenario_type: str = "vendor_pricing"
    agent_a_name: str = "Buyer"
    agent_b_name: str = "Vendor"
    starting_price: float = 100000.0
    buyer_target_price: float = 80000.0
    buyer_max_price: float = 95000.0
    vendor_min_price: float = 85000.0
    quantity: int = 100
    delivery_days: int = 30
    payment_terms: str = "Net 30"


class JobOfferConfig(ScenarioConfig):
    scenario_type: str = "job_offer"
    agent_a_name: str = "Candidate"
    agent_b_name: str = "Recruiter"
    starting_salary: float = 900000.0   # ₹9 LPA
    candidate_target: float = 1100000.0  # ₹11 LPA
    candidate_min: float = 1000000.0    # ₹10 LPA
    recruiter_budget: float = 1050000.0 # ₹10.5 LPA
    benefits: List[str] = ["health_insurance", "pf", "gratuity"]
    joining_days: int = 30


class ProjectBudgetConfig(ScenarioConfig):
    scenario_type: str = "project_budget"
    agent_a_name: str = "Team Alpha"
    agent_b_name: str = "Team Beta"
    total_budget: float = 5000000.0
    alpha_requested: float = 3000000.0
    beta_requested: float = 3500000.0
    alpha_minimum: float = 2000000.0
    beta_minimum: float = 2000000.0
    priority_weights: dict = {"alpha": 0.5, "beta": 0.5}


# ── Reports ───────────────────────────────────────────────────────────────────

class OutcomeReport(BaseModel):
    negotiation_id: int
    title: str
    scenario_type: str
    mode: str
    status: str
    final_outcome: str
    agreed_terms: Optional[dict] = None
    breakdown_reason: Optional[str] = None
    initial_offer: Optional[dict] = None
    final_offer: Optional[dict] = None
    total_rounds: int
    concession_timeline: List[dict] = []
    satisfaction_scores: Optional[dict] = None
    key_turning_points: List[str] = []
    suggestions: List[str] = []
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Assistant ─────────────────────────────────────────────────────────────────

class AssistantMessage(RequestModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=2000)


class AssistantChatRequest(RequestModel):
    negotiation_id: Optional[int] = Field(None, ge=1)
    message: str = Field(..., min_length=1, max_length=2000)
    history: List[AssistantMessage] = Field(default_factory=list, max_length=20)


class AssistantChatResponse(BaseModel):
    reply: str
    negotiation_id: Optional[int] = None
    extracted_config: Optional[dict] = None
    ready_to_start: bool = False


# Fix forward refs
NegotiationDetail.model_rebuild()
TokenResponse.model_rebuild()
