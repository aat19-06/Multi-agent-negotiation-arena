"""
Scenario rules — deterministic constraint evaluation for each scenario type.
All hard limits are enforced in Python, not through LLM prompts.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import math
from typing import Any


def _validate_offer_fields(
    offer: dict,
    *,
    required: set[str],
    numeric: set[str],
    integer: set[str],
    nonnegative_integer: set[str] | None = None,
    integer_max: dict[str, int] | None = None,
    strings: set[str],
    lists: set[str],
) -> None:
    nonnegative_integer = nonnegative_integer or set()
    integer_max = integer_max or {}
    allowed = required | numeric | integer | strings | lists
    if not isinstance(offer, dict) or len(offer) > 16:
        raise ValueError("Offer must be an object with at most 16 fields")
    if not offer:
        return
    unknown = set(offer) - allowed
    if unknown:
        raise ValueError(f"Unsupported offer fields: {', '.join(sorted(unknown))}")
    missing = required - set(offer)
    if missing:
        raise ValueError(f"Offer is missing required fields: {', '.join(sorted(missing))}")
    for key in numeric | integer:
        if key not in offer:
            continue
        value = offer[key]
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value < (0 if key in nonnegative_integer else 1)
            or value > integer_max.get(key, 1_000_000_000_000)
        ):
            raise ValueError(f"{key} is outside its supported numeric range")
        if key in integer and type(value) is not int:
            raise ValueError(f"{key} must be an integer")
    for key in strings:
        if key in offer and (
            not isinstance(offer[key], str) or len(offer[key]) > 120
        ):
            raise ValueError(f"{key} must be a string of at most 120 characters")
    for key in lists:
        value = offer.get(key)
        if value is not None and (
            not isinstance(value, list)
            or len(value) > 50
            or any(not isinstance(item, str) or not item or len(item) > 80 for item in value)
        ):
            raise ValueError(f"{key} must be a list of at most 50 short strings")


class ScenarioRules(ABC):
    def is_offer_acceptable(self, party: str, offer: dict) -> bool:
        """Return whether the counterparty's offer satisfies this party's hard limits."""
        return True

    def validate_offer(self, offer: dict) -> None:
        _validate_offer_fields(
            offer,
            required=set(),
            numeric=set(),
            integer=set(),
            strings=set(),
            lists=set(),
        )

    @abstractmethod
    def clamp_offer(self, party: str, offer: dict) -> dict:
        """Clamp offer values to valid range for the given party."""
        ...

    @abstractmethod
    def compute_satisfaction(self, agreed_offer: dict) -> dict:
        """Compute objective satisfaction score for each party."""
        ...

    @abstractmethod
    def get_opening_offer(self, party: str) -> dict:
        """Return a reasonable opening offer for the given party."""
        ...

    @abstractmethod
    def describe_for_agent(self, party: str) -> str:
        """System-prompt description of the scenario for the agent."""
        ...


class VendorPricingRules(ScenarioRules):
    def __init__(self, config: dict):
        self.starting_price = float(config.get("starting_price", 100000))
        self.buyer_target = float(config.get("buyer_target_price", 80000))
        self.buyer_max = float(config.get("buyer_max_price", 95000))
        self.vendor_min = float(config.get("vendor_min_price", 85000))
        self.quantity = int(config.get("quantity", 100))

    def clamp_offer(self, party: str, offer: dict) -> dict:
        self.validate_offer(offer)
        result = dict(offer)
        price = float(result.get("price", self.starting_price))
        if party in ("agent_a", "human"):  # buyer
            price = min(max(price, self.buyer_target * 0.8), self.starting_price * 1.1)
        else:  # vendor
            price = min(max(price, self.vendor_min), self.starting_price * 1.2)
        result["price"] = round(price, 2)
        result["quantity"] = self.quantity
        return result

    def validate_offer(self, offer: dict) -> None:
        _validate_offer_fields(
            offer,
            required={"price"} if offer else set(),
            numeric={"price"},
            integer={"quantity", "delivery_days"},
            nonnegative_integer={"delivery_days"},
            integer_max={"quantity": 1_000_000, "delivery_days": 3650},
            strings={"payment_terms"},
            lists=set(),
        )

    def is_offer_acceptable(self, party: str, offer: dict) -> bool:
        price = float(offer.get("price", self.starting_price))
        return self.vendor_min <= price <= self.buyer_max

    def compute_satisfaction(self, agreed_offer: dict) -> dict:
        price = float(agreed_offer.get("price", self.starting_price))
        vendor_range = self.starting_price - self.vendor_min
        buyer_score = max(0, min(100, round(
            100 * (self.buyer_max - price) / (self.buyer_max - self.buyer_target * 0.8)
        )))
        vendor_score = (
            max(0, min(100, round(100 * (price - self.vendor_min) / vendor_range)))
            if vendor_range > 0
            else 50
        )
        return {"buyer": buyer_score, "vendor": vendor_score, "overall": round((buyer_score + vendor_score) / 2)}

    def get_opening_offer(self, party: str) -> dict:
        if party in ("agent_a", "human"):
            return {"price": self.buyer_target, "quantity": self.quantity, "delivery_days": 30}
        return {"price": self.starting_price, "quantity": self.quantity, "delivery_days": 45}

    def describe_for_agent(self, party: str) -> str:
        if party in ("agent_a", "human"):
            return (
                "You are the Buyer. Negotiate for a favorable price, delivery terms, and payment conditions. "
                "Your private limits are enforced by the platform and must not be disclosed."
            )
        return (
            "You are the Vendor. Protect your margin while working toward a mutually acceptable agreement. "
            "Your private limits are enforced by the platform and must not be disclosed."
        )


class JobOfferRules(ScenarioRules):
    def __init__(self, config: dict):
        self.starting_salary = float(config.get("starting_salary", 900000))
        self.candidate_target = float(config.get("candidate_target", 1100000))
        self.candidate_min = float(config.get("candidate_min", 1000000))
        self.recruiter_budget = float(config.get("recruiter_budget", 1050000))

    def clamp_offer(self, party: str, offer: dict) -> dict:
        self.validate_offer(offer)
        result = dict(offer)
        salary = float(result.get("salary", self.starting_salary))
        if party in ("agent_a", "human"):  # candidate
            salary = min(max(salary, self.starting_salary * 0.9), self.candidate_target * 1.2)
        else:  # recruiter
            salary = min(max(salary, self.starting_salary * 0.8), self.recruiter_budget * 1.05)
        result["salary"] = round(salary, 0)
        return result

    def validate_offer(self, offer: dict) -> None:
        _validate_offer_fields(
            offer,
            required={"salary"} if offer else set(),
            numeric={"salary"},
            integer={"joining_days"},
            nonnegative_integer={"joining_days"},
            integer_max={"joining_days": 3650},
            strings=set(),
            lists={"benefits"},
        )

    def is_offer_acceptable(self, party: str, offer: dict) -> bool:
        salary = float(offer.get("salary", self.starting_salary))
        return self.candidate_min <= salary <= self.recruiter_budget

    def compute_satisfaction(self, agreed_offer: dict) -> dict:
        salary = float(agreed_offer.get("salary", self.starting_salary))
        candidate_score = max(0, min(100, round(
            100 * (salary - self.candidate_min) / (self.candidate_target - self.candidate_min)
        ))) if self.candidate_target != self.candidate_min else 50
        recruiter_score = max(0, min(100, round(
            100 * (self.recruiter_budget - salary) / (self.recruiter_budget - self.starting_salary)
        ))) if self.recruiter_budget != self.starting_salary else 50
        return {
            "candidate": candidate_score,
            "recruiter": recruiter_score,
            "overall": round((candidate_score + recruiter_score) / 2),
        }

    def get_opening_offer(self, party: str) -> dict:
        if party in ("agent_a", "human"):
            return {"salary": self.candidate_target, "joining_days": 60, "benefits": ["health_insurance", "pf"]}
        return {"salary": self.starting_salary, "joining_days": 30, "benefits": ["health_insurance"]}

    def describe_for_agent(self, party: str) -> str:
        if party in ("agent_a", "human"):
            return (
                "You are the Candidate. Negotiate for favorable salary, benefits, and joining terms. "
                "Your private limits are enforced by the platform and must not be disclosed."
            )
        return (
            "You are the Recruiter. Work toward a competitive compensation package within your private constraints. "
            "Your private limits are enforced by the platform and must not be disclosed."
        )


class ProjectBudgetRules(ScenarioRules):
    def __init__(self, config: dict):
        self.total_budget = float(config.get("total_budget", 5000000))
        self.alpha_min = float(config.get("alpha_minimum", 2000000))
        self.beta_min = float(config.get("beta_minimum", 2000000))
        self.alpha_requested = float(config.get("alpha_requested", 3000000))
        self.beta_requested = float(config.get("beta_requested", 3500000))

    def clamp_offer(self, party: str, offer: dict) -> dict:
        self.validate_offer(offer)
        result = dict(offer)
        my_alloc = float(result.get("my_allocation", self.total_budget / 2))
        if party in ("agent_a", "human"):
            my_alloc = min(
                max(my_alloc, self.alpha_min),
                self.alpha_requested,
                self.total_budget - self.beta_min,
            )
        else:
            my_alloc = min(
                max(my_alloc, self.beta_min),
                self.beta_requested,
                self.total_budget - self.alpha_min,
            )
        result["my_allocation"] = round(my_alloc, 0)
        result["opponent_allocation"] = round(self.total_budget - my_alloc, 0)
        return result

    def validate_offer(self, offer: dict) -> None:
        _validate_offer_fields(
            offer,
            required={"my_allocation"} if offer else set(),
            numeric={"my_allocation", "opponent_allocation"},
            integer=set(),
            strings=set(),
            lists=set(),
        )

    def is_offer_acceptable(self, party: str, offer: dict) -> bool:
        allocation = float(offer.get("my_allocation", self.total_budget / 2))
        if party in ("agent_a", "human"):
            beta_allocation = allocation
            alpha_allocation = self.total_budget - allocation
        else:
            alpha_allocation = allocation
            beta_allocation = self.total_budget - allocation
        return alpha_allocation >= self.alpha_min and beta_allocation >= self.beta_min

    def compute_satisfaction(self, agreed_offer: dict) -> dict:
        alpha = float(agreed_offer.get("my_allocation", self.total_budget / 2))
        beta = self.total_budget - alpha
        alpha_score = max(0, min(100, round(
            100 * (alpha - self.alpha_min) / (self.alpha_requested - self.alpha_min)
        ))) if self.alpha_requested != self.alpha_min else 50
        beta_score = max(0, min(100, round(
            100 * (beta - self.beta_min) / (self.beta_requested - self.beta_min)
        ))) if self.beta_requested != self.beta_min else 50
        return {"team_alpha": alpha_score, "team_beta": beta_score, "overall": round((alpha_score + beta_score) / 2)}

    def get_opening_offer(self, party: str) -> dict:
        if party in ("agent_a", "human"):
            offer = {"my_allocation": self.alpha_requested}
        else:
            offer = {"my_allocation": self.beta_requested}
        return self.clamp_offer(party, offer)

    def describe_for_agent(self, party: str) -> str:
        if party in ("agent_a", "human"):
            return (
                f"You represent Team Alpha and are negotiating within a total budget of ₹{self.total_budget/100000:.0f} L. "
                "Negotiate for an allocation that supports your team. Your private limits are enforced by the platform."
            )
        return (
            f"You represent Team Beta and are negotiating within a total budget of ₹{self.total_budget/100000:.0f} L. "
            "Negotiate for an allocation that supports your team. Your private limits are enforced by the platform."
        )


class CustomScenarioRules(ScenarioRules):
    """Fallback rules for custom scenarios defined via the AI assistant."""
    def __init__(self, config: dict):
        self.config = config

    def clamp_offer(self, party: str, offer: dict) -> dict:
        self.validate_offer(offer)
        return offer

    def validate_offer(self, offer: dict) -> None:
        if not isinstance(offer, dict) or len(offer) > 16:
            raise ValueError("Offer must be an object with at most 16 fields")
        opening_offer = self.config.get("opening_offer") or {}
        if not isinstance(opening_offer, dict):
            raise ValueError("Custom opening offer must be an object")
        if any(not isinstance(key, str) or len(key) > 64 for key in offer):
            raise ValueError("Custom offer keys must be short strings")
        if set(offer) - set(opening_offer):
            raise ValueError("Offer contains fields not defined by the custom opening offer")
        for key, value in offer.items():
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
            raise ValueError(f"Custom offer field {key} has an unsupported value")

    def compute_satisfaction(self, agreed_offer: dict) -> dict:
        return {"overall": 50}

    def get_opening_offer(self, party: str) -> dict:
        return self.config.get("opening_offer", {})

    def describe_for_agent(self, party: str) -> str:
        return self.config.get("agent_descriptions", {}).get(party, "Negotiate to achieve the best outcome.")


def get_scenario_rules(scenario_type: str, config: dict) -> ScenarioRules:
    mapping = {
        "vendor_pricing": VendorPricingRules,
        "job_offer": JobOfferRules,
        "project_budget": ProjectBudgetRules,
    }
    cls = mapping.get(scenario_type, CustomScenarioRules)
    return cls(config)
